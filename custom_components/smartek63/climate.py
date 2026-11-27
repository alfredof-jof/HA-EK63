"""Entidad climate de la estufa."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartEK63ConfigEntry
from .const import (
    KEY_MODE,
    KEY_ROOM_TEMPERATURE,
    KEY_TARGET_TEMPERATURE,
    MODES,
    PHASE_CLEANING,
    PHASE_COOLING,
    PHASE_HEATING,
    PHASE_IGNITION,
    PHASE_SHUTDOWN,
    PHASE_STANDBY,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP,
)
from .coordinator import is_on, phase
from .entity import SmartEK63Entity, as_float


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartEK63ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SmartEK63Climate(coordinator, hid) for hid in coordinator.heaters)


class SmartEK63Climate(SmartEK63Entity, ClimateEntity):
    """Estufa de pellet como termostato."""

    _attr_name = None  # usa el nombre del dispositivo
    _attr_translation_key = "stove"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMP_STEP
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX
    _attr_preset_modes = MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator, heater_id: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(coordinator, heater_id, "climate")

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if is_on(self.heater) else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        current = phase(self.heater)
        if current == PHASE_IGNITION:
            return HVACAction.PREHEATING
        if current == PHASE_HEATING:
            return HVACAction.HEATING
        if current in (PHASE_STANDBY, PHASE_SHUTDOWN, PHASE_COOLING, PHASE_CLEANING):
            return HVACAction.IDLE
        return HVACAction.HEATING if is_on(self.heater) else HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        return as_float(self.heater.get(KEY_ROOM_TEMPERATURE))

    @property
    def target_temperature(self) -> float | None:
        return as_float(self.heater.get(KEY_TARGET_TEMPERATURE))

    @property
    def preset_mode(self) -> str | None:
        mode = self.heater.get(KEY_MODE)
        return mode if mode in MODES else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_set_power(self.heater_id, hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_set_power(self.heater_id, True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_power(self.heater_id, False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            temperature = round(float(temperature))
            await self.coordinator.async_command(
                self.heater_id,
                self.coordinator.api.set_temperature(self.heater_id, temperature),
                {KEY_TARGET_TEMPERATURE: temperature},
            )
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.async_command(
            self.heater_id,
            self.coordinator.api.set_mode(self.heater_id, preset_mode),
            {KEY_MODE: preset_mode},
        )
