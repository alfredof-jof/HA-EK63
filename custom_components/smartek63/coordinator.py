"""DataUpdateCoordinator de SmartEK63: un GET por estufa por ciclo."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SmartEK63Api, SmartEK63AuthError, SmartEK63Error
from .const import (
    COMMAND_REFRESH_DELAY,
    CONF_HEATERS,
    CONF_MIN_TOGGLE_INTERVAL,
    DEFAULT_MIN_TOGGLE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KEY_STATUS,
    KEY_STATUS_DESCRIPTION,
    MAX_BACKOFF_INTERVAL,
    ON_STATUSES,
    PHASE_ALARM,
    PHASE_CLEANING,
    PHASE_HEATING,
    PHASE_IGNITION,
    PHASE_OFF,
    PHASE_SHUTDOWN,
    PHASE_STANDBY,
    PHASE_UNKNOWN,
)

# Palabras (italiano/inglés) de statusDescription -> fase. El orden importa:
# "spegnimento" debe evaluarse antes que "spento".
_PHASE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("allarm", PHASE_ALARM),
    ("alarm", PHASE_ALARM),
    ("errore", PHASE_ALARM),
    ("accensione", PHASE_IGNITION),
    ("warmup", PHASE_IGNITION),
    ("potenza", PHASE_HEATING),
    ("spegnimento", PHASE_SHUTDOWN),
    ("pulizia", PHASE_CLEANING),
    ("stand", PHASE_STANDBY),
    ("attesa", PHASE_STANDBY),
    ("spento", PHASE_OFF),
    ("off", PHASE_OFF),
)

if TYPE_CHECKING:
    from . import SmartEK63ConfigEntry

_LOGGER = logging.getLogger(__name__)


def status_code(data: dict[str, Any] | None) -> int | None:
    try:
        return int((data or {}).get(KEY_STATUS))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def phase(data: dict[str, Any] | None) -> str:
    """Fase normalizada a partir de statusDescription (y el código como respaldo)."""
    description = str((data or {}).get(KEY_STATUS_DESCRIPTION) or "").lower()
    for keyword, value in _PHASE_KEYWORDS:
        if keyword in description:
            return value
    code = status_code(data)
    if code == 0:
        return PHASE_OFF
    if code in ON_STATUSES:
        return PHASE_HEATING
    return PHASE_UNKNOWN


def is_on(data: dict[str, Any] | None) -> bool:
    """Encendida = arrancando, calentando o en standby esperando a reanudar."""
    current = phase(data)
    if current in (PHASE_IGNITION, PHASE_HEATING, PHASE_STANDBY):
        return True
    if current == PHASE_UNKNOWN:
        return status_code(data) in ON_STATUSES
    return False


class SmartEK63Coordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Lee el estado de todas las estufas de la cuenta."""

    config_entry: SmartEK63ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SmartEK63ConfigEntry, api: SmartEK63Api) -> None:
        self.base_interval = timedelta(
            seconds=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self.base_interval,
        )
        self.api = api
        self.heaters: dict[str, str] = dict(entry.data[CONF_HEATERS])
        self.options_snapshot = dict(entry.options)
        self._last_toggle: dict[str, float] = {}
        self._cancel_delayed_refresh: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            results = await asyncio.gather(*(self.api.get_heater(hid) for hid in self.heaters))
        except SmartEK63AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartEK63Error as err:
            # Backoff: duplicar el intervalo hasta MAX_BACKOFF_INTERVAL.
            current = self.update_interval or self.base_interval
            self.update_interval = min(current * 2, timedelta(seconds=MAX_BACKOFF_INTERVAL))
            raise UpdateFailed(f"Error leyendo la estufa: {err}") from err
        self.update_interval = self.base_interval
        return dict(zip(self.heaters, results, strict=True))

    # --- Comandos ---

    @property
    def min_toggle_seconds(self) -> float:
        return float(self.config_entry.options.get(CONF_MIN_TOGGLE_INTERVAL, DEFAULT_MIN_TOGGLE_INTERVAL)) * 60

    async def async_set_power(self, heater_id: str, on: bool) -> None:
        """Encender/apagar con protección anti-ciclos."""
        data = (self.data or {}).get(heater_id)
        if data is not None and is_on(data) == on:
            _LOGGER.debug("La estufa %s ya está %s; no se envía nada", heater_id, "on" if on else "off")
            return
        last = self._last_toggle.get(heater_id)
        wait = self.min_toggle_seconds
        if last is not None and time.monotonic() - last < wait:
            remaining = max(1, round((wait - (time.monotonic() - last)) / 60))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="toggle_too_soon",
                translation_placeholders={"minutes": str(remaining)},
            )
        await self._async_command(heater_id, self.api.set_power(heater_id, on))
        self._last_toggle[heater_id] = time.monotonic()

    async def async_command(
        self, heater_id: str, coro: Awaitable[None], optimistic: dict[str, Any] | None = None
    ) -> None:
        await self._async_command(heater_id, coro, optimistic)

    async def _async_command(
        self, heater_id: str, coro: Awaitable[None], optimistic: dict[str, Any] | None = None
    ) -> None:
        try:
            await coro
        except SmartEK63AuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
        except SmartEK63Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if optimistic and self.data and heater_id in self.data:
            self.data[heater_id] = {**self.data[heater_id], **optimistic}
            self.async_update_listeners()
        self._schedule_refresh_after_command()

    def _schedule_refresh_after_command(self) -> None:
        """La nube tarda unos segundos en reflejar el cambio: releer después."""
        if self._cancel_delayed_refresh:
            self._cancel_delayed_refresh()

        async def _refresh(_now: Any) -> None:
            self._cancel_delayed_refresh = None
            await self.async_request_refresh()

        self._cancel_delayed_refresh = async_call_later(self.hass, COMMAND_REFRESH_DELAY, _refresh)

    async def async_shutdown(self) -> None:
        if self._cancel_delayed_refresh:
            self._cancel_delayed_refresh()
            self._cancel_delayed_refresh = None
        await super().async_shutdown()
