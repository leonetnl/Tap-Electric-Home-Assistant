"""Binary sensor platform for Tap Electric."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TapElectricDataUpdateCoordinator
from .device import TapElectricChargerEntity


@dataclass(frozen=True, kw_only=True)
class TapElectricBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Tap Electric binary sensor entity."""

    value_fn: Callable[[Mapping[str, Any]], bool]
    attrs_fn: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None


BINARY_SENSOR_DESCRIPTIONS: tuple[TapElectricBinarySensorEntityDescription, ...] = (
    TapElectricBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        icon="mdi:ev-plug-type2",
        value_fn=lambda snapshot: bool(snapshot.get("is_charging")),
    ),
    TapElectricBinarySensorEntityDescription(
        key="occupied",
        translation_key="occupied",
        icon="mdi:car-electric",
        value_fn=lambda snapshot: bool(snapshot.get("is_occupied")),
        attrs_fn=lambda snapshot: {
            "availability_state": "occupied" if snapshot.get("is_occupied") else "available"
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tap Electric binary sensors from a config entry."""
    coordinator: TapElectricDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    known_entities: set[str] = set()

    @callback
    def async_add_missing_entities() -> None:
        entities: list[TapElectricBinarySensor] = []
        chargers = coordinator.data.get("chargers", {})

        for charger_id in chargers:
            for description in BINARY_SENSOR_DESCRIPTIONS:
                unique_id = f"{charger_id}_{description.key}"
                if unique_id in known_entities:
                    continue
                known_entities.add(unique_id)
                entities.append(TapElectricBinarySensor(coordinator, charger_id, description))

        if entities:
            async_add_entities(entities)

    async_add_missing_entities()
    entry.async_on_unload(coordinator.async_add_listener(async_add_missing_entities))


class TapElectricBinarySensor(TapElectricChargerEntity, BinarySensorEntity):
    """Representation of a Tap Electric binary sensor."""

    entity_description: TapElectricBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: TapElectricDataUpdateCoordinator,
        charger_id: str,
        description: TapElectricBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, charger_id)
        self.entity_description = description
        self._attr_unique_id = f"{charger_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        snapshot = self.charger_snapshot
        if snapshot is None:
            return None
        return self.entity_description.value_fn(snapshot)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional binary sensor attributes."""
        snapshot = self.charger_snapshot
        if snapshot is None:
            return None

        attributes = self._base_debug_attributes()
        if self.entity_description.attrs_fn is not None:
            extra = self.entity_description.attrs_fn(snapshot)
            if extra:
                attributes.update(extra)
        return attributes

