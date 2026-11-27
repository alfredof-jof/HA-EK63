"""Entidad base de SmartEK63."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import SmartEK63Coordinator


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SmartEK63Entity(CoordinatorEntity[SmartEK63Coordinator]):
    """Una estufa = un dispositivo."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartEK63Coordinator, heater_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.heater_id = heater_id
        self._attr_unique_id = f"{heater_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, heater_id)},
            name=coordinator.heaters.get(heater_id, f"EK63 {heater_id}"),
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def heater(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self.heater_id, {})

    @property
    def available(self) -> bool:
        """No disponible si falla la nube o la estufa está offline."""
        return super().available and bool(self.heater) and not self.coordinator.is_offline(self.heater_id)
