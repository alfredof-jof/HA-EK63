"""Sensor binario de conectividad de la estufa con la nube."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartEK63ConfigEntry
from .const import KEY_ONLINE, KEY_SCHEDULING
from .coordinator import SmartEK63Coordinator
from .entity import SmartEK63Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartEK63ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for hid in coordinator.heaters:
        entities += [SmartEK63Online(coordinator, hid), SmartEK63Scheduling(coordinator, hid)]
    async_add_entities(entities)


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
