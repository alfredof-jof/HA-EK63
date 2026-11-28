"""Sensor binario de conectividad de la estufa con la nube."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartEK63ConfigEntry
from .const import ALARM_CODES, KEY_ONLINE, KEY_SCHEDULING, KEY_STATUS_DESCRIPTION, PHASE_ALARM, PHASE_UNKNOWN
from .coordinator import SmartEK63Coordinator, alarm_code, phase, status_code
from .entity import SmartEK63Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartEK63ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for hid in coordinator.heaters:
        entities += [
            SmartEK63Online(coordinator, hid),
            SmartEK63Scheduling(coordinator, hid),
            SmartEK63Alarm(coordinator, hid),
        ]
    async_add_entities(entities)


class SmartEK63Alarm(SmartEK63Entity, BinarySensorEntity):
    """Problema: la estufa está en alarma o en un estado que no conocemos."""

    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SmartEK63Coordinator, heater_id: str) -> None:
        super().__init__(coordinator, heater_id, "alarm")

    @property
    def is_on(self) -> bool:
        return phase(self.heater) == PHASE_ALARM

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = phase(self.heater)
        code = alarm_code(self.heater)
        return {
            "code": status_code(self.heater),
            "alarm_code": code,
            "alarm_text": ALARM_CODES.get(code or ""),
            "description": self.heater.get(KEY_STATUS_DESCRIPTION),
            "unknown_status": current == PHASE_UNKNOWN,
        }


class SmartEK63Scheduling(SmartEK63Entity, BinarySensorEntity):
    """Cronotermostato de la estufa activo (scheduling.active)."""

    _attr_translation_key = "scheduling"

    def __init__(self, coordinator: SmartEK63Coordinator, heater_id: str) -> None:
        super().__init__(coordinator, heater_id, KEY_SCHEDULING)

    @property
    def is_on(self) -> bool | None:
        scheduling = self.heater.get(KEY_SCHEDULING)
        return bool(scheduling.get("active")) if isinstance(scheduling, dict) else None


class SmartEK63Online(SmartEK63Entity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmartEK63Coordinator, heater_id: str) -> None:
        super().__init__(coordinator, heater_id, KEY_ONLINE)

    @property
    def available(self) -> bool:
        # Sigue disponible aunque la estufa esté offline: es justo lo que informa.
        return self.coordinator.last_update_success and bool(self.heater)

    @property
    def is_on(self) -> bool:
        return bool(self.heater.get(KEY_ONLINE))
