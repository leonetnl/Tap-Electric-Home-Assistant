"""Data coordinator for Tap Electric."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import TapElectricApiClient
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HISTORY_STORAGE_VERSION,
    MANUFACTURER,
    MAX_IMPORTED_SESSION_IDS,
    MAX_PARALLEL_STATUS_REQUESTS,
)
from .exceptions import (
    TapElectricApiAuthenticationError,
    TapElectricApiConnectionError,
    TapElectricApiError,
    TapElectricApiRateLimitError,
)

_LOGGER = logging.getLogger(__name__)


class TapElectricDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Tap Electric data updates for one account."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: TapElectricApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Tap Electric",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self._entry = entry
        self._history_store = Store[dict[str, Any]](
            hass,
            HISTORY_STORAGE_VERSION,
            f"{DOMAIN}_{entry.entry_id}_history",
        )
        self._history_state: dict[str, Any] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and normalize Tap Electric data."""
        await self._async_ensure_history_loaded()

        try:
            chargers = await self.api.async_get_chargers()
            active_sessions = await self.api.async_get_active_sessions()
            sessions = await self.api.async_get_sessions()
            statuses = await self._async_fetch_statuses(chargers)
        except TapElectricApiAuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except (
            TapElectricApiConnectionError,
            TapElectricApiRateLimitError,
            TapElectricApiError,
        ) as err:
            raise UpdateFailed(str(err)) from err

        await self._async_backfill_historical_sessions(
            chargers,
            sessions,
            active_sessions,
            statuses,
        )

        charger_snapshots: dict[str, dict[str, Any]] = {}

        for charger in chargers:
            charger_id = _extract_charger_id(charger)
            if not charger_id:
                _LOGGER.warning(
                    "Skipping Tap Electric charger without stable identifier: %s",
                    charger,
                )
                continue

            active_session = _match_active_session(charger_id, active_sessions)
            last_session = _match_latest_session(
                charger_id,
                sessions,
                exclude_session_id=_extract_session_id(active_session),
            )
            status = statuses.get(charger_id, {})

            charger_snapshots[charger_id] = _build_charger_snapshot(
                charger_id=charger_id,
                charger=charger,
                status=status,
                active_session=active_session,
                last_session=last_session,
                history_state=self._history_state or _default_history_state(),
                entry_id=self._entry.entry_id,
            )

        return {
            "chargers": charger_snapshots,
            "fetched_at": dt_util.utcnow().isoformat(),
            "raw": {
                "chargers": chargers,
                "active_sessions": active_sessions,
                "sessions": sessions,
                "statuses": statuses,
            },
            "history": self._history_state,
        }

    async def _async_fetch_statuses(
        self,
        chargers: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Fetch per-charger status payloads efficiently."""
        semaphore = asyncio.Semaphore(MAX_PARALLEL_STATUS_REQUESTS)

        async def _fetch(charger_id: str) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                try:
                    return charger_id, await self.api.async_get_charger_status(charger_id)
                except TapElectricApiAuthenticationError:
                    raise
                except TapElectricApiConnectionError:
                    raise
                except TapElectricApiRateLimitError:
                    raise
                except TapElectricApiError as err:
                    _LOGGER.warning(
                        "Tap Electric status refresh failed for charger %s: %s",
                        charger_id,
                        err,
                    )
                    return charger_id, {}

        tasks = []
        for charger in chargers:
            charger_id = _extract_charger_id(charger)
            if charger_id:
                tasks.append(_fetch(charger_id))

        if not tasks:
            return {}

        return dict(await asyncio.gather(*tasks))

    async def _async_ensure_history_loaded(self) -> None:
        """Load persisted history sync state."""
        if self._history_state is not None:
            return

        stored_state = await self._history_store.async_load()
        self._history_state = _normalize_history_state(stored_state)

    async def _async_backfill_historical_sessions(
        self,
        chargers: list[dict[str, Any]],
        sessions: list[dict[str, Any]],
        active_sessions: list[dict[str, Any]],
        statuses: dict[str, dict[str, Any]],
    ) -> None:
        """Backfill completed sessions into Home Assistant long-term statistics."""
        if "recorder" not in self.hass.config.components:
            _LOGGER.debug(
                "Skipping Tap Electric historical session sync because recorder is not loaded"
            )
            return

        history_state = self._history_state
        if history_state is None:
            return

        charger_names = {
            charger_id: (
                _coalesce(_candidate_payloads(charger), ("name", "display_name", "label", "title"))
                or f"Charger {charger_id}"
            )
            for charger in chargers
            if (charger_id := _extract_charger_id(charger))
        }

        imported_session_ids: dict[str, list[str]] = history_state["imported_session_ids"]
        imported_session_sets = {
            charger_id: set(session_ids)
            for charger_id, session_ids in imported_session_ids.items()
        }
        energy_totals: dict[str, float] = history_state["historical_energy_kwh"]
        cost_totals: dict[str, float] = history_state["historical_cost"]
        currencies: dict[str, str] = history_state["historical_cost_currency"]

        active_session_ids = {
            session_id
            for session in active_sessions
            if (session_id := _extract_session_id(session)) is not None
        }
        most_recent_open_session_ids = _build_most_recent_open_session_ids(sessions)

        _LOGGER.debug(
            "Tap Electric historical sync starting with %s charger(s), %s session(s), %s active session reference(s)",
            len(charger_names),
            len(sessions),
            len(active_session_ids),
        )

        energy_statistics: dict[str, dict[datetime, StatisticData]] = {}
        cost_statistics: dict[str, dict[datetime, StatisticData]] = {}
        latest_imported_at: datetime | None = None
        imported_count = 0

        for session in sorted(sessions, key=_session_sort_key):
            session_id = _extract_session_id(session)
            charger_id = _extract_session_charger_id(session)
            if session_id is None or charger_id is None:
                _LOGGER.debug(
                    "Skipping Tap Electric session without session_id or charger_id. Keys seen: %s",
                    sorted(_flatten_payload_keys(session)),
                )
                continue

            if (
                session_id in active_session_ids
                or _session_is_active(session)
                or _should_treat_session_as_current(
                    session=session,
                    charger_status=statuses.get(charger_id, {}),
                    most_recent_open_session_id=most_recent_open_session_ids.get(charger_id),
                )
            ):
                _LOGGER.debug(
                    "Skipping active Tap Electric session %s for charger %s",
                    session_id,
                    charger_id,
                )
                continue

            known_session_ids = imported_session_sets.setdefault(charger_id, set())
            if session_id in known_session_ids:
                _LOGGER.debug(
                    "Skipping already imported Tap Electric session %s for charger %s",
                    session_id,
                    charger_id,
                )
                continue

            imported_at = _extract_session_completed_at(session)
            if imported_at is None:
                _LOGGER.debug(
                    "Skipping Tap Electric historical session %s for charger %s because no stable session time was found",
                    session_id,
                    charger_id,
                )
                continue

            payloads = _candidate_payloads(session)
            session_energy_kwh = _extract_energy_kwh(
                payloads,
                (
                    "session_energy_kwh",
                    "energy_delivered_kwh",
                    "charged_energy_kwh",
                    "energy_kwh",
                    "sessionEnergyKwh",
                    "energyDeliveredKwh",
                    "chargedEnergyKwh",
                    "energyKwh",
                    "wh",
                    "session_energy_wh",
                    "energy_delivered_wh",
                    "sessionEnergyWh",
                    "energyDeliveredWh",
                ),
            )
            session_cost = _extract_float(
                _coalesce(
                    payloads,
                    (
                        "cost",
                        "total_cost",
                        "session_cost",
                        "totalCost",
                        "sessionCost",
                        "price",
                        "amount",
                    ),
                )
            )
            session_currency = _coalesce(payloads, ("currency", "currency_code", "currencyCode"))

            _LOGGER.debug(
                "Evaluating Tap Electric session %s for charger %s: imported_at=%s energy=%s cost=%s currency=%s keys=%s",
                session_id,
                charger_id,
                imported_at.isoformat(),
                session_energy_kwh,
                session_cost,
                session_currency,
                sorted(_flatten_payload_keys(session)),
            )

            if session_energy_kwh is None and session_cost is None:
                _LOGGER.debug(
                    "Skipping Tap Electric session %s for charger %s because no importable energy or cost fields were found",
                    session_id,
                    charger_id,
                )
                continue

            if session_energy_kwh is not None:
                running_energy = round(
                    float(energy_totals.get(charger_id, 0.0)) + max(session_energy_kwh, 0.0),
                    3,
                )
                energy_totals[charger_id] = running_energy
                statistic_start = _round_to_hour(imported_at)
                energy_statistics.setdefault(charger_id, {})[statistic_start] = StatisticData(
                    start=statistic_start,
                    state=running_energy,
                    sum=running_energy,
                )

            if session_cost is not None:
                running_cost = round(
                    float(cost_totals.get(charger_id, 0.0)) + max(session_cost, 0.0),
                    2,
                )
                cost_totals[charger_id] = running_cost
                currencies[charger_id] = str(session_currency or currencies.get(charger_id) or "EUR")
                statistic_start = _round_to_hour(imported_at)
                cost_statistics.setdefault(charger_id, {})[statistic_start] = StatisticData(
                    start=statistic_start,
                    state=running_cost,
                    sum=running_cost,
                )

            imported_session_ids.setdefault(charger_id, []).append(session_id)
            known_session_ids.add(session_id)
            imported_count += 1

            if latest_imported_at is None or imported_at > latest_imported_at:
                latest_imported_at = imported_at

        if not imported_count:
            _LOGGER.debug("Tap Electric historical sync imported no new completed sessions")
            return

        for charger_id, statistics in energy_statistics.items():
            async_add_external_statistics(
                self.hass,
                _build_energy_statistics_metadata(
                    entry_id=self._entry.entry_id,
                    charger_id=charger_id,
                    charger_name=charger_names.get(charger_id, charger_id),
                ),
                [statistics[key] for key in sorted(statistics)],
            )

        for charger_id, statistics in cost_statistics.items():
            async_add_external_statistics(
                self.hass,
                _build_cost_statistics_metadata(
                    entry_id=self._entry.entry_id,
                    charger_id=charger_id,
                    charger_name=charger_names.get(charger_id, charger_id),
                    currency=currencies.get(charger_id, "EUR"),
                ),
                [statistics[key] for key in sorted(statistics)],
            )

        if latest_imported_at is not None:
            history_state["last_history_sync"] = latest_imported_at.isoformat()

        for charger_id, session_ids in imported_session_ids.items():
            imported_session_ids[charger_id] = session_ids[-MAX_IMPORTED_SESSION_IDS:]

        await self._history_store.async_save(history_state)
        _LOGGER.debug(
            "Tap Electric historical sync imported %s completed session(s)",
            imported_count,
        )


def _build_charger_snapshot(
    *,
    charger_id: str,
    charger: Mapping[str, Any],
    status: Mapping[str, Any],
    active_session: Mapping[str, Any] | None,
    last_session: Mapping[str, Any] | None,
    history_state: Mapping[str, Any],
    entry_id: str,
) -> dict[str, Any]:
    """Merge raw payloads into one normalized charger snapshot."""
    active_session = active_session or {}
    last_session = last_session or {}

    charger_payloads = _candidate_payloads(charger)
    status_payloads = _candidate_payloads(status)
    session_payloads = _candidate_payloads(active_session, last_session)
    all_payloads = [*status_payloads, *charger_payloads, *session_payloads]

    connector = _extract_first_connector(status, charger)
    connector_payloads = _candidate_payloads(connector)

    session_start = _extract_datetime(
        _coalesce(
            session_payloads,
            (
                "start_time",
                "started_at",
                "session_start",
                "charging_started_at",
                "startTime",
                "startedAt",
                "sessionStart",
                "chargingStartedAt",
                "created_at",
                "createdAt",
            ),
        )
    )
    session_duration_seconds = _extract_session_duration_seconds(
        active_session,
        session_payloads,
        session_start,
    )
    current_power_kw = _extract_power_kw([*status_payloads, *connector_payloads, *charger_payloads])
    session_energy_kwh = _extract_energy_kwh(
        [*session_payloads, *status_payloads],
        (
            "session_energy_kwh",
            "current_session_energy_kwh",
            "energy_delivered_kwh",
            "charged_energy_kwh",
            "energy_kwh",
            "sessionEnergyKwh",
            "currentSessionEnergyKwh",
            "energyDeliveredKwh",
            "chargedEnergyKwh",
            "energyKwh",
            "session_energy_wh",
            "energy_delivered_wh",
            "sessionEnergyWh",
            "energyDeliveredWh",
        ),
    )
    total_energy_kwh = _extract_energy_kwh(
        [*charger_payloads, *status_payloads],
        (
            "total_energy_kwh",
            "meter_total_kwh",
            "historical_energy_kwh",
            "lifetime_energy_kwh",
            "energy_total_kwh",
            "total_energy_wh",
            "meter_total_wh",
            "lifetime_energy_wh",
        ),
    )
    historical_synced_energy_kwh = _extract_float(
        history_state.get("historical_energy_kwh", {}).get(charger_id)
    )
    if total_energy_kwh is None:
        total_energy_kwh = historical_synced_energy_kwh
    elif historical_synced_energy_kwh is not None:
        total_energy_kwh = max(total_energy_kwh, historical_synced_energy_kwh)

    session_cost = _extract_float(
        _coalesce(
            session_payloads,
            (
                "cost",
                "total_cost",
                "session_cost",
                "totalCost",
                "sessionCost",
                "price",
                "amount",
            ),
        )
    )
    currency = _coalesce(session_payloads, ("currency", "currency_code", "currencyCode"))
    historical_synced_cost = _extract_float(
        history_state.get("historical_cost", {}).get(charger_id)
    )
    historical_cost_currency = (
        history_state.get("historical_cost_currency", {}).get(charger_id)
        or currency
        or "EUR"
    )
    history_last_sync = _extract_datetime(history_state.get("last_history_sync"))

    load_status = _normalize_status_text(
        _coalesce(
            [*status_payloads, *session_payloads, *charger_payloads],
            ("load_status", "charging_status", "status", "state", "session_status"),
        )
    )
    connector_status = _normalize_status_text(
        _coalesce(
            [*connector_payloads, *status_payloads, *charger_payloads],
            ("connector_status", "status", "state"),
        )
    )

    online = _extract_bool(_coalesce(all_payloads, ("is_online", "online", "connected")))
    if online is None:
        online = _status_implies_online(load_status, connector_status)
    online_status = "online" if online is True else "offline" if online is False else "unknown"

    is_charging = _extract_bool(
        _coalesce([*status_payloads, *session_payloads], ("is_charging", "charging", "active"))
    )
    if is_charging is None:
        is_charging = _status_implies_charging(load_status, connector_status, bool(active_session))

    is_occupied = _extract_bool(
        _coalesce([*status_payloads, *connector_payloads], ("occupied", "is_occupied", "busy", "in_use"))
    )
    if is_occupied is None:
        is_occupied = _status_implies_occupied(load_status, connector_status, bool(active_session))

    return {
        "charger_id": charger_id,
        "name": _coalesce(charger_payloads, ("name", "display_name", "label", "title")) or f"Charger {charger_id}",
        "model": _coalesce(charger_payloads, ("model", "device_model", "charger_model")),
        "serial_number": _coalesce(charger_payloads, ("serial_number", "serial", "serialNumber")),
        "firmware_version": _coalesce(
            [*status_payloads, *charger_payloads],
            ("firmware_version", "firmware", "software_version"),
        ),
        "location_name": _coalesce(charger_payloads, ("location_name", "site_name", "address")),
        "manufacturer": MANUFACTURER,
        "load_status": load_status or "unknown",
        "current_power_kw": current_power_kw,
        "session_energy_kwh": session_energy_kwh,
        "total_energy_kwh": total_energy_kwh,
        "session_start": session_start,
        "session_duration_seconds": session_duration_seconds,
        "session_cost": session_cost,
        "currency": currency,
        "historical_synced_energy_kwh": historical_synced_energy_kwh,
        "historical_synced_cost": historical_synced_cost,
        "historical_cost_currency": historical_cost_currency,
        "history_last_sync": history_last_sync,
        "energy_statistic_id": _build_energy_statistic_id(entry_id, charger_id),
        "cost_statistic_id": _build_cost_statistic_id(entry_id, charger_id),
        "online_status": online_status,
        "connector_status": connector_status or "unknown",
        "is_charging": bool(is_charging),
        "is_occupied": bool(is_occupied),
        "connector_id": _coalesce(connector_payloads, ("id", "connector_id", "connectorId")),
        "active_session_id": _extract_session_id(active_session),
        "last_session_id": _extract_session_id(last_session),
        "raw": {
            "charger": dict(charger),
            "status": dict(status),
            "active_session": dict(active_session),
            "last_session": dict(last_session),
            "connector": dict(connector) if isinstance(connector, Mapping) else {},
        },
    }


def _candidate_payloads(*payloads: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """Return top-level and nested mappings for defensive parsing."""
    collected: list[Mapping[str, Any]] = []

    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue

        collected.append(payload)

        for key in (
            "data",
            "attributes",
            "metadata",
            "metrics",
            "status",
            "state",
            "session",
            "current_session",
            "totals",
            "energy",
            "pricing",
        ):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                collected.append(nested)

        for key in ("connectors", "connector_statuses"):
            nested_list = payload.get(key)
            if isinstance(nested_list, list):
                collected.extend(item for item in nested_list if isinstance(item, Mapping))

    return collected


def _flatten_payload_keys(payload: Mapping[str, Any]) -> set[str]:
    """Return the set of keys present in the top-level and nested payload mappings."""
    keys: set[str] = set()
    for item in _candidate_payloads(payload):
        keys.update(str(key) for key in item.keys())
    return keys


def _coalesce(payloads: Iterable[Mapping[str, Any]], keys: Iterable[str]) -> Any:
    """Return the first non-empty value for the given keys."""
    for key in keys:
        for payload in payloads:
            if key not in payload:
                continue
            value = payload[key]
            if value in (None, "", [], {}):
                continue
            return value
    return None


def _extract_charger_id(charger: Mapping[str, Any]) -> str | None:
    """Extract a stable charger identifier."""
    value = _coalesce(
        _candidate_payloads(charger),
        ("id", "charger_id", "charge_point_id", "evse_id", "uid"),
    )
    if value is None:
        return None
    return str(value)


def _extract_session_id(session: Mapping[str, Any] | None) -> str | None:
    """Extract a session identifier."""
    if not isinstance(session, Mapping):
        return None
    value = _coalesce(
        _candidate_payloads(session),
        ("id", "session_id", "transaction_id", "sessionId", "transactionId"),
    )
    return str(value) if value is not None else None


def _extract_session_charger_id(session: Mapping[str, Any]) -> str | None:
    """Extract the charger identifier from a session payload."""
    value = _coalesce(
        _candidate_payloads(session),
        (
            "charger_id",
            "charge_point_id",
            "evse_id",
            "station_id",
            "device_id",
            "chargerId",
            "chargePointId",
            "chargerUid",
        ),
    )
    if value is not None:
        return str(value)

    charger = session.get("charger")
    if isinstance(charger, Mapping):
        nested_value = _coalesce(
            (charger,),
            (
                "id",
                "charger_id",
                "charge_point_id",
                "uid",
                "chargerId",
                "chargePointId",
                "chargerUid",
            ),
        )
        if nested_value is not None:
            return str(nested_value)

    return None


def _match_active_session(
    charger_id: str,
    sessions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match the active session for a charger."""
    for session in sessions:
        if _extract_session_charger_id(session) == charger_id:
            return session
    return None


def _match_latest_session(
    charger_id: str,
    sessions: list[dict[str, Any]],
    *,
    exclude_session_id: str | None,
) -> dict[str, Any] | None:
    """Return the latest known session for a charger."""
    matches = [
        session
        for session in sessions
        if _extract_session_charger_id(session) == charger_id
        and _extract_session_id(session) != exclude_session_id
    ]

    if not matches:
        return None

    matches.sort(key=_session_sort_key, reverse=True)
    return matches[0]


def _session_sort_key(session: Mapping[str, Any]) -> datetime:
    """Sort sessions by their start time if possible."""
    value = _coalesce(
        _candidate_payloads(session),
        (
            "start_time",
            "started_at",
            "session_start",
            "startTime",
            "startedAt",
            "sessionStart",
            "created_at",
            "createdAt",
        ),
    )
    parsed = _extract_datetime(value)
    return parsed or datetime.min.replace(tzinfo=UTC)


def _extract_first_connector(
    status: Mapping[str, Any],
    charger: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the first connector payload if present."""
    for payload in (status, charger):
        connectors = payload.get("connectors")
        if isinstance(connectors, list):
            for connector in connectors:
                if isinstance(connector, Mapping):
                    return connector
    return {}


def _extract_energy_kwh(
    payloads: Iterable[Mapping[str, Any]],
    keys: Iterable[str],
) -> float | None:
    """Extract energy and normalize Wh to kWh when needed."""
    for key in keys:
        value = _coalesce(payloads, (key,))
        numeric = _extract_float(value)
        if numeric is None:
            continue
        if "wh" in key.lower() and "kwh" not in key.lower():
            return round(numeric / 1000, 3)
        return round(numeric, 3)
    return None


def _extract_power_kw(payloads: Iterable[Mapping[str, Any]]) -> float | None:
    """Extract power and normalize watts to kW when needed."""
    preferred_kw_keys = (
        "current_power_kw",
        "power_kw",
        "currentPowerKw",
        "powerKw",
    )
    preferred_w_keys = (
        "current_power_w",
        "power_w",
        "currentPowerW",
        "powerW",
        "watts",
    )

    value = _coalesce(payloads, preferred_kw_keys)
    numeric = _extract_float(value)
    if numeric is not None:
        return round(numeric, 3)

    value = _coalesce(payloads, preferred_w_keys)
    numeric = _extract_float(value)
    if numeric is not None:
        return round(numeric / 1000, 3)

    value = _coalesce(payloads, ("power", "current_power"))
    numeric = _extract_float(value)
    if numeric is None:
        return None

    # TODO: confirm exact Tap Electric unit conventions when the real API schema is known.
    return round(numeric / 1000, 3) if numeric > 100 else round(numeric, 3)


def _extract_session_duration_seconds(
    active_session: Mapping[str, Any],
    session_payloads: Iterable[Mapping[str, Any]],
    session_start: datetime | None,
) -> int | None:
    """Extract or derive the current session duration."""
    if active_session and session_start is not None:
        now = dt_util.utcnow()
        return max(0, int((now - session_start).total_seconds()))

    duration_seconds = _extract_float(
        _coalesce(
            session_payloads,
            ("duration_seconds", "session_duration_seconds", "duration", "durationSeconds"),
        )
    )
    if duration_seconds is not None:
        return int(duration_seconds)

    duration_minutes = _extract_float(
        _coalesce(session_payloads, ("duration_minutes", "session_duration_minutes", "durationMinutes"))
    )
    if duration_minutes is not None:
        return int(duration_minutes * 60)

    duration_hours = _extract_float(
        _coalesce(session_payloads, ("duration_hours", "session_duration_hours", "durationHours"))
    )
    if duration_hours is not None:
        return int(duration_hours * 3600)

    return None


def _extract_session_completed_at(session: Mapping[str, Any]) -> datetime | None:
    """Extract a suitable timestamp for historical session import."""
    return _extract_datetime(
        _coalesce(
            _candidate_payloads(session),
            (
                "end_time",
                "ended_at",
                "session_end",
                "completed_at",
                "stop_time",
                "updated_at",
                "endTime",
                "endedAt",
                "sessionEnd",
                "completedAt",
                "stopTime",
                "updatedAt",
                "start_time",
                "started_at",
                "startTime",
                "startedAt",
                "created_at",
                "createdAt",
            ),
        )
    )


def _round_to_hour(value: datetime) -> datetime:
    """Round a timestamp down to the top of the hour for statistics import."""
    return value.replace(minute=0, second=0, microsecond=0)


def _session_has_end_marker(session: Mapping[str, Any]) -> bool:
    """Return whether a session explicitly contains an end marker."""
    return _coalesce(
        _candidate_payloads(session),
        (
            "end_time",
            "ended_at",
            "session_end",
            "completed_at",
            "stop_time",
            "endTime",
            "endedAt",
            "sessionEnd",
            "completedAt",
            "stopTime",
        ),
    ) is not None


def _build_most_recent_open_session_ids(
    sessions: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Return the latest session without an explicit end marker for each charger."""
    latest_by_charger: dict[str, tuple[datetime, str]] = {}

    for session in sessions:
        session_id = _extract_session_id(session)
        charger_id = _extract_session_charger_id(session)
        if session_id is None or charger_id is None:
            continue
        if _session_has_end_marker(session):
            continue

        sort_key = _extract_session_completed_at(session) or _session_sort_key(session)
        current = latest_by_charger.get(charger_id)
        if current is None or sort_key > current[0]:
            latest_by_charger[charger_id] = (sort_key, session_id)

    return {
        charger_id: session_id
        for charger_id, (_, session_id) in latest_by_charger.items()
    }


def _extract_datetime(value: Any) -> datetime | None:
    """Extract a timezone-aware datetime from API data."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)

    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    return None


def _extract_float(value: Any) -> float | None:
    """Extract a float from loosely typed API values."""
    if value is None:
        return None

    if isinstance(value, bool):
        return float(value)

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        stripped = value.strip().replace(",", ".")
        try:
            return float(stripped)
        except ValueError:
            return None

    return None


def _extract_bool(value: Any) -> bool | None:
    """Extract a bool from loosely typed API values."""
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "online", "connected", "charging", "occupied"}:
            return True
        if lowered in {"false", "no", "0", "offline", "disconnected", "available", "idle"}:
            return False

    return None


def _normalize_status_text(value: Any) -> str | None:
    """Normalize status text for Home Assistant state display."""
    if value is None:
        return None

    if isinstance(value, str):
        return value.strip().lower().replace(" ", "_")

    return str(value).lower()


def _status_implies_online(load_status: str | None, connector_status: str | None) -> bool | None:
    """Infer online/offline state from status fields."""
    candidates = {load_status, connector_status}
    if any(value in {"faulted", "offline", "disconnected"} for value in candidates):
        return False
    if any(value in {"available", "occupied", "charging", "preparing", "finishing", "idle"} for value in candidates):
        return True
    return None


def _status_implies_charging(
    load_status: str | None,
    connector_status: str | None,
    has_active_session: bool,
) -> bool:
    """Infer charging state from status fields."""
    if has_active_session:
        return True
    candidates = {load_status, connector_status}
    return any(value in {"charging", "finishing", "starting"} for value in candidates)


def _status_implies_occupied(
    load_status: str | None,
    connector_status: str | None,
    has_active_session: bool,
) -> bool:
    """Infer occupied state from status fields."""
    if has_active_session:
        return True
    candidates = {load_status, connector_status}
    if any(value in {"available", "idle", "free"} for value in candidates):
        return False
    return any(value in {"occupied", "charging", "preparing", "reserved", "finishing"} for value in candidates)


def _session_is_active(session: Mapping[str, Any]) -> bool:
    """Infer whether a session is still active."""
    status = _normalize_status_text(
        _coalesce(
            _candidate_payloads(session),
            ("status", "state", "session_status", "sessionStatus"),
        )
    )
    if status in {"charging", "active", "in_progress", "started", "running"}:
        return True

    explicit = _extract_bool(
        _coalesce(
            _candidate_payloads(session),
            ("is_active", "active", "charging", "isActive"),
        )
    )
    return bool(explicit) if explicit is not None else False


def _should_treat_session_as_current(
    *,
    session: Mapping[str, Any],
    charger_status: Mapping[str, Any],
    most_recent_open_session_id: str | None,
) -> bool:
    """Infer whether an open session is the current live session for the charger."""
    if _session_has_end_marker(session):
        return False

    session_id = _extract_session_id(session)
    if session_id is None or session_id != most_recent_open_session_id:
        return False

    status_payloads = _candidate_payloads(charger_status)
    connector = _extract_first_connector(charger_status, {})
    connector_payloads = _candidate_payloads(connector)

    explicit = _extract_bool(
        _coalesce(
            [*status_payloads, *connector_payloads],
            ("is_charging", "charging", "active", "is_active"),
        )
    )
    if explicit is not None:
        return explicit

    load_status = _normalize_status_text(
        _coalesce(status_payloads, ("load_status", "charging_status", "status", "state"))
    )
    connector_status = _normalize_status_text(
        _coalesce([*connector_payloads, *status_payloads], ("connector_status", "status", "state"))
    )

    return _status_implies_charging(load_status, connector_status, False)


def _default_history_state() -> dict[str, Any]:
    """Return the default persisted history sync structure."""
    return {
        "imported_session_ids": {},
        "historical_energy_kwh": {},
        "historical_cost": {},
        "historical_cost_currency": {},
        "last_history_sync": None,
    }


def _normalize_history_state(stored_state: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the persisted history state loaded from storage."""
    state = _default_history_state()
    if not isinstance(stored_state, dict):
        return state

    for key in state:
        value = stored_state.get(key)
        if isinstance(value, dict):
            state[key] = value
        elif key == "last_history_sync":
            state[key] = value if isinstance(value, str) else None

    return state


def _build_energy_statistic_id(entry_id: str, charger_id: str) -> str:
    """Build the external statistic ID for historical energy."""
    return f"{DOMAIN}:{_statistics_object_id(entry_id, charger_id, 'historical_energy_kwh')}"


def _build_cost_statistic_id(entry_id: str, charger_id: str) -> str:
    """Build the external statistic ID for historical cost."""
    return f"{DOMAIN}:{_statistics_object_id(entry_id, charger_id, 'historical_cost')}"


def _statistics_object_id(entry_id: str, charger_id: str, suffix: str) -> str:
    """Build a Home Assistant compatible object id for external statistics."""
    return slugify(f"{entry_id}_{charger_id}_{suffix}")


def _build_energy_statistics_metadata(
    *,
    entry_id: str,
    charger_id: str,
    charger_name: str,
) -> StatisticMetaData:
    """Build statistics metadata for historical energy backfill."""
    return StatisticMetaData(
        statistic_id=_build_energy_statistic_id(entry_id, charger_id),
        source=DOMAIN,
        name=f"{charger_name} historical energy",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
    )


def _build_cost_statistics_metadata(
    *,
    entry_id: str,
    charger_id: str,
    charger_name: str,
    currency: str,
) -> StatisticMetaData:
    """Build statistics metadata for historical cost backfill."""
    return StatisticMetaData(
        statistic_id=_build_cost_statistic_id(entry_id, charger_id),
        source=DOMAIN,
        name=f"{charger_name} historical cost",
        unit_of_measurement=currency,
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
    )
