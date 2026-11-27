"""Potencia del ventilador.

La escala aún no está confirmada (fase 0), así que la entidad se crea
DESHABILITADA por defecto. Actívala en HA solo cuando FAN_MIN/FAN_MAX de
const.py coincidan con lo que acepta la estufa.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartEK63ConfigEntry
from .const import FAN_MAX, FAN_MIN, FAN_STEP, KEY_FAN_POWER
from .coordinator import SmartEK63Coordinator
from .entity import SmartEK63Entity, as_float


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartEK63ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SmartEK63FanPower(coordinator, hid) for hid in coordinator.heaters)


class SmartEK63FanPower(SmartEK63Entity, NumberEntity):
    _attr_translation_key = "fan_power"
    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = FAN_MIN
    _attr_native_max_value = FAN_MAX
    _attr_native_step = FAN_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: SmartEK63Coordinator, heater_id: str) -> None:
        super().__init__(coordinator, heater_id, KEY_FAN_POWER)

    @property
    def native_value(self) -> float | None:
        return as_float(self.heater.get(KEY_FAN_POWER))

    async def async_set_native_value(self, value: float) -> None:
        power = int(value)
        await self.coordinator.async_command(
            self.heater_id,
            self.coordinator.api.set_fan(self.heater_id, power),
            {KEY_FAN_POWER: power},
        )
