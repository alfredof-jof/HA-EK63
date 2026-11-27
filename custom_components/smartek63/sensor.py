"""Sensores de la estufa."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SmartEK63ConfigEntry
from .const import (
    KEY_FAN_POWER_TEMPLATE,
    KEY_FANS_NUMBER,
    KEY_LAST_UPDATE,
    KEY_POWER,
    KEY_ROOM_TEMPERATURE,
    KEY_STATUS,
    KEY_STATUS_DESCRIPTION,
    PHASES,
)
from .coordinator import SmartEK63Coordinator, is_on, phase, status_code
from .entity import SmartEK63Entity, as_float


def _as_int(value: Any) -> int | None:
    number = as_float(value)
    return None if number is None else int(number)


def _as_datetime(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


@dataclass(frozen=True, kw_only=True)
class SmartEK63SensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[SmartEK63SensorDescription, ...] = (
    SmartEK63SensorDescription(
        key="phase",
        translation_key="phase",
        device_class=SensorDeviceClass.ENUM,
        options=PHASES,
        value_fn=phase,
        attrs_fn=lambda d: {"is_on": is_on(d)},
    ),
    SmartEK63SensorDescription(
        key=KEY_STATUS_DESCRIPTION,
        translation_key="status_description",
        value_fn=lambda d: d.get(KEY_STATUS_DESCRIPTION),
    ),
    SmartEK63SensorDescription(
        key=KEY_STATUS,
        translation_key="status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=status_code,
    ),
    SmartEK63SensorDescription(
        key=KEY_ROOM_TEMPERATURE,
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda d: as_float(d.get(KEY_ROOM_TEMPERATURE)),
    ),
    SmartEK63SensorDescription(
        key=KEY_POWER,
        translation_key="power_level",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _as_int(d.get(KEY_POWER)),
    ),
    SmartEK63SensorDescription(
        key=KEY_LAST_UPDATE,
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _as_datetime(d.get(KEY_LAST_UPDATE)),
    ),
)


def _fan_description(index: int) -> SmartEK63SensorDescription:
    key = KEY_FAN_POWER_TEMPLATE.format(index)
    return SmartEK63SensorDescription(
        key=key,
        translation_key="fan_level",
        translation_placeholders={"fan": str(index)},
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _as_int(d.get(key)),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartEK63ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SmartEK63Sensor] = []
    for hid in coordinator.heaters:
        data = (coordinator.data or {}).get(hid, {})
        descriptions = list(SENSORS)
        fans = _as_int(data.get(KEY_FANS_NUMBER)) or 1
        descriptions += [_fan_description(i) for i in range(1, min(fans, 3) + 1)]
        entities += [SmartEK63Sensor(coordinator, hid, d) for d in descriptions]
    async_add_entities(entities)


class SmartEK63Sensor(SmartEK63Entity, SensorEntity):
    entity_description: SmartEK63SensorDescription

    def __init__(
        self, coordinator: SmartEK63Coordinator, heater_id: str, description: SmartEK63SensorDescription
    ) -> None:
        super().__init__(coordinator, heater_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.heater)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.heater)
