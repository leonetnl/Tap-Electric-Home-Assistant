"""Data coordinator for Tap Electric."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TapElectricApiClient
from .const import DEFAULT_SCAN_INTERVAL, MANUFACTURER, MAX_PARALLEL_STATUS_REQUESTS
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and normalize Tap Electric data."""
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


def _build_charger_snapshot(
    *,
    charger_id: str,
    charger: Mapping[str, Any],
    status: Mapping[str, Any],
    active_session: Mapping[str, Any] | None,
    last_session: Mapping[str, Any] | None,
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
                "created_at",
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
            "session_energy_wh",
            "energy_delivered_wh",
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
    session_cost = _extract_float(
        _coalesce(
            session_payloads,
            (
                "cost",
                "total_cost",
                "session_cost",
                "price",
                "amount",
            ),
        )
    )
    currency = _coalesce(session_payloads, ("currency", "currency_code", "currencyCode"))

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
    value = _coalesce(_candidate_payloads(session), ("id", "session_id", "transaction_id"))
    return str(value) if value is not None else None


def _extract_session_charger_id(session: Mapping[str, Any]) -> str | None:
    """Extract the charger identifier from a session payload."""
    value = _coalesce(
        _candidate_payloads(session),
        ("charger_id", "charge_point_id", "evse_id", "station_id", "device_id"),
    )
    if value is None:
        return None
    return str(value)


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
        ("start_time", "started_at", "session_start", "created_at"),
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
            ("duration_seconds", "session_duration_seconds", "duration"),
        )
    )
    if duration_seconds is not None:
        return int(duration_seconds)

    duration_minutes = _extract_float(
        _coalesce(session_payloads, ("duration_minutes", "session_duration_minutes"))
    )
    if duration_minutes is not None:
        return int(duration_minutes * 60)

    duration_hours = _extract_float(
        _coalesce(session_payloads, ("duration_hours", "session_duration_hours"))
    )
    if duration_hours is not None:
        return int(duration_hours * 3600)

    return None


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
