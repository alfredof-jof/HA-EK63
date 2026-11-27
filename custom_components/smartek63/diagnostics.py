"""Diagnósticos con datos sensibles anonimizados."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import SmartEK63ConfigEntry
from .const import CONF_HEATERS, CONF_REFRESH_TOKEN

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_HEATERS,
    "id",
    "heaterId",
    "serial",
    "serialNumber",
    "mac",
    "macAddress",
    "userId",
    "owner",
    "ownerId",
    "email",
    "name",
    "ssid",
    "ip",
    "latitude",
    "longitude",
    "address",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SmartEK63ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "heaters": [async_redact_data(data, TO_REDACT) for data in (coordinator.data or {}).values()],
    }
