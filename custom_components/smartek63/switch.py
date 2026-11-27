"""Interruptores Relax y Standby."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartEK63ConfigEntry
from .api import SmartEK63Api
from .const import KEY_RELAX, KEY_STANDBY
from .coordinator import SmartEK63Coordinator
from .entity import SmartEK63Entity


@dataclass(frozen=True, kw_only=True)
class SmartEK63SwitchDescription(SwitchEntityDescription):
    set_fn: Callable[[SmartEK63Api, str, bool], Coroutine[Any, Any, None]]


SWITCHES: tuple[SmartEK63SwitchDescription, ...] = (
    SmartEK63SwitchDescription(
        key=KEY_RELAX,
        translation_key="relax",
        set_fn=lambda api, hid, value: api.set_relax(hid, value),
    ),
    SmartEK63SwitchDescription(
        key=KEY_STANDBY,
        translation_key="standby",
        set_fn=lambda api, hid, value: api.set_standby(hid, value),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartEK63ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SmartEK63Switch(coordinator, hid, description) for hid in coordinator.heaters for description in SWITCHES
    )


class SmartEK63Switch(SmartEK63Entity, SwitchEntity):
    entity_description: SmartEK63SwitchDescription

    def __init__(
        self, coordinator: SmartEK63Coordinator, heater_id: str, description: SmartEK63SwitchDescription
    ) -> None:
        super().__init__(coordinator, heater_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        value = self.heater.get(self.entity_description.key)
        return None if value is None else bool(value)

    async def _async_set(self, value: bool) -> None:
        await self.coordinator.async_command(
            self.heater_id,
            self.entity_description.set_fn(self.coordinator.api, self.heater_id, value),
            {self.entity_description.key: value},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
