"""Integración SmartEK63 (Edilkamin EK63) para Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartEK63Api
from .const import CONF_REFRESH_TOKEN
from .coordinator import SmartEK63Coordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SmartEK63ConfigEntry = ConfigEntry[SmartEK63Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SmartEK63ConfigEntry) -> bool:
    """Configura la integración a partir de la config entry."""

    @callback
    def _store_refresh_token(token: str) -> None:
        # Si Cognito rota el refresh token, guardarlo (no dispara recarga: ver listener).
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: token})

    api = SmartEK63Api(
        async_get_clientsession(hass),
        entry.data[CONF_REFRESH_TOKEN],
        run_sync=hass.async_add_executor_job,
        on_refresh_token=_store_refresh_token,
    )
    coordinator = SmartEK63Coordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: SmartEK63ConfigEntry) -> None:
    """Recargar solo si cambian las opciones (no al guardar un token nuevo)."""
    if dict(entry.options) != entry.runtime_data.options_snapshot:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmartEK63ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
