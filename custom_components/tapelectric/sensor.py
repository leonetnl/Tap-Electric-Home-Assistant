"""Sensor platform for Tap Electric."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TapElectricDataUpdateCoordinator
from .device import TapElectricChargerEntity


@dataclass(frozen=True, kw_only=True)
class TapElectricSensorEntityDescription(SensorEntityDescription):
    """Describes a Tap Electric sensor entity."""

    value_fn: Callable[[Mapping[str, Any]], Any]
    attrs_fn: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None
    unit_fn: Callable[[Mapping[str, Any]], str | None] | None = None


SENSOR_DESCRIPTIONS: tuple[TapElectricSensorEntityDescription, ...] = (
    TapElectricSensorEntityDescription(
        key="load_status",
        translation_key="load_status",
        icon="mdi:ev-station",
        value_fn=lambda snapshot: snapshot.get("load_status"),
    ),
    TapElectricSensorEntityDescription(
        key="current_session_energy",
        translation_key="current_session_energy",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda snapshot: snapshot.get("session_energy_kwh"),
    ),
    TapElectricSensorEntityDescription(
        key="historical_synced_energy",
        translation_key="historical_synced_energy",
        icon="mdi:database-sync",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda snapshot: snapshot.get("historical_synced_energy_kwh"),
        attrs_fn=lambda snapshot: {
            "statistic_id": snapshot.get("energy_statistic_id"),
            "history_last_sync": snapshot.get("history_last_sync"),
        },
    ),
    TapElectricSensorEntityDescription(
        key="session_start",
        translation_key="session_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda snapshot: snapshot.get("session_start"),
    ),
    TapElectricSensorEntityDescription(
        key="session_duration",
        translation_key="session_duration",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda snapshot: snapshot.get("session_duration_seconds"),
        attrs_fn=lambda snapshot: {
            "duration_human": _format_duration(snapshot.get("session_duration_seconds"))
        },
    ),
    TapElectricSensorEntityDescription(
        key="connector_status",
        translation_key="connector_status",
        icon="mdi:power-plug",
        value_fn=lambda snapshot: snapshot.get("connector_status"),
    ),
    TapElectricSensorEntityDescription(
        key="charging_current",
        translation_key="charging_current",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.get("charging_current_amp"),
    ),
    TapElectricSensorEntityDescription(
        key="last_session_end",
        translation_key="last_session_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda snapshot: snapshot.get("last_session_end"),
    ),
    TapElectricSensorEntityDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        icon="mdi:lightning-bolt-circle",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda snapshot: snapshot.get("last_session_energy_kwh"),
    ),
    TapElectricSensorEntityDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        icon="mdi:timer-check-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda snapshot: snapshot.get("last_session_duration_seconds"),
        attrs_fn=lambda snapshot: {
            "duration_human": _format_duration(
                snapshot.get("last_session_duration_seconds")
            ),
            "idle_duration_seconds": snapshot.get("last_session_idle_seconds"),
        },
    ),
    TapElectricSensorEntityDescription(
        key="last_session_cost_ex_vat",
        translation_key="last_session_cost_ex_vat",
        icon="mdi:cash",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda snapshot: snapshot.get("last_session_cost_ex_vat"),
        unit_fn=lambda snapshot: snapshot.get("last_session_currency"),
    ),
    TapElectricSensorEntityDescription(
        key="last_session_energy_cost_ex_vat",
        translation_key="last_session_energy_cost_ex_vat",
        icon="mdi:lightning-bolt-circle",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda snapshot: snapshot.get("last_session_energy_cost_ex_vat"),
        unit_fn=lambda snapshot: snapshot.get("last_session_currency"),
    ),
    TapElectricSensorEntityDescription(
        key="last_session_idle_duration",
        translation_key="last_session_idle_duration",
        icon="mdi:parking",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda snapshot: snapshot.get("last_session_idle_seconds"),
        attrs_fn=lambda snapshot: {
            "duration_human": _format_duration(
                snapshot.get("last_session_idle_seconds")
            ),
        },
    ),
    TapElectricSensorEntityDescription(
        key="historical_cdr_energy",
        translation_key="historical_cdr_energy",
        icon="mdi:counter",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda snapshot: snapshot.get("historical_cdr_energy_kwh"),
    ),
    TapElectricSensorEntityDescription(
        key="historical_cdr_cost_ex_vat",
        translation_key="historical_cdr_cost_ex_vat",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda snapshot: snapshot.get("historical_cdr_cost_ex_vat"),
        unit_fn=lambda snapshot: snapshot.get("historical_cdr_currency"),
    ),
    TapElectricSensorEntityDescription(
        key="last_used_tariff_kwh",
        translation_key="last_used_tariff_kwh",
        icon="mdi:cash-fast",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.get("last_used_tariff_kwh"),
        unit_fn=lambda snapshot: _rate_unit(snapshot, UnitOfEnergy.KILO_WATT_HOUR),
        attrs_fn=lambda snapshot: {
            "tariff_type": snapshot.get("last_used_tariff_type"),
            "tariff_id": snapshot.get("last_used_tariff_id"),
        },
    ),
    TapElectricSensorEntityDescription(
        key="last_used_tariff_start",
        translation_key="last_used_tariff_start",
        icon="mdi:cash-plus",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.get("last_used_tariff_start"),
        unit_fn=lambda snapshot: snapshot.get("last_used_tariff_currency"),
    ),
    TapElectricSensorEntityDescription(
        key="last_used_tariff_hour",
        translation_key="last_used_tariff_hour",
        icon="mdi:timer-sand",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.get("last_used_tariff_hour"),
        unit_fn=lambda snapshot: _rate_unit(snapshot, UnitOfTime.HOURS),
    ),
    TapElectricSensorEntityDescription(
        key="last_used_tariff_idle_hour",
        translation_key="last_used_tariff_idle_hour",
        icon="mdi:parking",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.get("last_used_tariff_idle_hour"),
        unit_fn=lambda snapshot: _rate_unit(snapshot, UnitOfTime.HOURS),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tap Electric sensors from a config entry."""
    coordinator: TapElectricDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    known_entities: set[str] = set()

    @callback
    def async_add_missing_entities() -> None:
        entities: list[TapElectricSensor] = []
        chargers = coordinator.data.get("chargers", {})

        for charger_id in chargers:
            for description in SENSOR_DESCRIPTIONS:
                unique_id = f"{charger_id}_{description.key}"
                if unique_id in known_entities:
                    continue
                known_entities.add(unique_id)
                entities.append(TapElectricSensor(coordinator, charger_id, description))

        if entities:
            async_add_entities(entities)

    async_add_missing_entities()
    entry.async_on_unload(coordinator.async_add_listener(async_add_missing_entities))


class TapElectricSensor(TapElectricChargerEntity, SensorEntity):
    """Representation of a Tap Electric sensor."""

    entity_description: TapElectricSensorEntityDescription

    def __init__(
        self,
        coordinator: TapElectricDataUpdateCoordinator,
        charger_id: str,
        description: TapElectricSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, charger_id)
        self.entity_description = description
        self._attr_unique_id = f"{charger_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor state."""
        snapshot = self.charger_snapshot
        if snapshot is None:
            return None
        return self.entity_description.value_fn(snapshot)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the sensor unit, including dynamic units like currencies."""
        snapshot = self.charger_snapshot
        if snapshot is not None and self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(snapshot)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional sensor attributes."""
        snapshot = self.charger_snapshot
        if snapshot is None:
            return None

        attributes = self._base_debug_attributes()
        if self.entity_description.attrs_fn is not None:
            extra = self.entity_description.attrs_fn(snapshot)
            if extra:
                attributes.update(extra)
        return attributes


def _format_duration(value: Any) -> str | None:
    """Format a duration in seconds to a human readable string."""
    if not isinstance(value, int):
        return None
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _rate_unit(snapshot: Mapping[str, Any], denominator: str) -> str | None:
    """Build a dynamic monetary rate unit such as EUR/kWh."""
    currency = snapshot.get("last_used_tariff_currency")
    if not isinstance(currency, str) or not currency:
        return None
    return f"{currency}/{denominator}"

# To add new sensors later, append another TapElectricSensorEntityDescription above.
