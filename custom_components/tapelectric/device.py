"""Shared entity helpers for Tap Electric."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import TapElectricDataUpdateCoordinator


class TapElectricChargerEntity(CoordinatorEntity[TapElectricDataUpdateCoordinator]):
    """Base entity for Tap Electric charger entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TapElectricDataUpdateCoordinator, charger_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._charger_id = charger_id

    @property
    def charger_snapshot(self) -> Mapping[str, Any] | None:
        """Return the normalized snapshot for this charger."""
        return self.coordinator.data.get("chargers", {}).get(self._charger_id)

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        return super().available and self.charger_snapshot is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the charger."""
        snapshot = self.charger_snapshot or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._charger_id)},
            manufacturer=MANUFACTURER,
            name=snapshot.get("name") or f"Charger {self._charger_id}",
            model=snapshot.get("model"),
            serial_number=snapshot.get("serial_number"),
            sw_version=snapshot.get("firmware_version"),
            suggested_area=snapshot.get("location_name"),
        )

    def _base_debug_attributes(self) -> dict[str, Any]:
        """Return common debug attributes."""
        snapshot = self.charger_snapshot or {}
        raw = snapshot.get("raw", {})

        attributes: dict[str, Any] = {
            "charger_id": self._charger_id,
        }

        if snapshot.get("connector_id") is not None:
            attributes["connector_id"] = snapshot["connector_id"]
        if snapshot.get("active_session_id") is not None:
            attributes["active_session_id"] = snapshot["active_session_id"]
        if snapshot.get("last_session_id") is not None:
            attributes["last_session_id"] = snapshot["last_session_id"]

        if raw:
            attributes["raw_api_data"] = raw

        return attributes

