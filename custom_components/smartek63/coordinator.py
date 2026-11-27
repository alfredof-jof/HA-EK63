"""DataUpdateCoordinator de SmartEK63: un GET por estufa por ciclo."""

from __future__ import annotations

import asyncio
import logging
import re
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
    EVENT_STATUS_CHANGED,
    KEY_ONLINE,
    KEY_STATUS,
    KEY_STATUS_DESCRIPTION,
    MAX_BACKOFF_INTERVAL,
    OFFLINE_DEBOUNCE,
    ON_STATUSES,
    PHASE_ALARM,
    PHASE_CLEANING,
    PHASE_COOLING,
    PHASE_HEATING,
    PHASE_IGNITION,
    PHASE_OFF,
    PHASE_SHUTDOWN,
    PHASE_STANDBY,
    PHASE_UNKNOWN,
    STATUS_PHASES,
    TRANSITION_PHASES,
)

# Palabras (italiano/inglés) de statusDescription -> fase. El orden importa:
# primero las alarmas, y "spegnimento" antes que "spent" (Stufa spenta).
# Las alarmas aún no se han visto: se cubren los términos habituales de
# Edilkamin (mancata accensione, sovratemperatura, pellet esaurito, A01...).
_ALARM_CODE = re.compile(r"\b[ae]\d{2}\b")
_PHASE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("allarm", PHASE_ALARM),
    ("alarm", PHASE_ALARM),
    ("errore", PHASE_ALARM),
    ("error", PHASE_ALARM),
    ("anomalia", PHASE_ALARM),
    ("guasto", PHASE_ALARM),
    ("blocco", PHASE_ALARM),
    ("mancata", PHASE_ALARM),
    ("sovratemp", PHASE_ALARM),
    ("esaurit", PHASE_ALARM),
    ("mancanza", PHASE_ALARM),
    ("accensione", PHASE_IGNITION),
    ("warmup", PHASE_IGNITION),
    ("potenza", PHASE_HEATING),
    ("spegnimento", PHASE_SHUTDOWN),
    ("raffredd", PHASE_COOLING),
    ("pulizia", PHASE_CLEANING),
    ("stand", PHASE_STANDBY),
    ("attesa", PHASE_STANDBY),
    ("spent", PHASE_OFF),  # "Stufa spenta" (código 0)
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
    if _ALARM_CODE.search(description):
        return PHASE_ALARM
    for keyword, value in _PHASE_KEYWORDS:
        if keyword in description:
            return value
    code = status_code(data)
    if code in STATUS_PHASES:
        return STATUS_PHASES[code]
    if code in ON_STATUSES:
        return PHASE_HEATING
    return PHASE_UNKNOWN


def is_on(data: dict[str, Any] | None) -> bool:
    """Encendida = arrancando, calentando o en standby esperando a reanudar.

    Apagándose / enfriando cuentan como apagada (no hay llama), aunque sea un
    ciclo de standby que volverá a arrancar solo.
    """
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
        self._offline_streak: dict[str, int] = {}
        self._last_status: dict[str, tuple[int | None, str | None]] = {}
        self._reported_unknown: set[tuple[int | None, str | None]] = set()
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
        data = dict(zip(self.heaters, results, strict=True))
        for hid, heater in data.items():
            offline = heater.get(KEY_ONLINE) is False
            self._offline_streak[hid] = self._offline_streak.get(hid, 0) + 1 if offline else 0
            self._track_status(hid, heater)
        return data

    def _track_status(self, heater_id: str, heater: dict[str, Any]) -> None:
        """Evento en cada cambio de estado y aviso en el log de códigos nuevos."""
        current = (status_code(heater), heater.get(KEY_STATUS_DESCRIPTION))
        previous = self._last_status.get(heater_id)
        self._last_status[heater_id] = current
        current_phase = phase(heater)

        if current_phase in (PHASE_UNKNOWN, PHASE_ALARM) and current not in self._reported_unknown:
            self._reported_unknown.add(current)
            if current_phase == PHASE_ALARM:
                _LOGGER.warning("Alarma en la estufa: código %s, %r", *current)
            else:
                _LOGGER.warning(
                    "Código de estado no reconocido %s, %r. Añádelo a STATUS_PHASES en const.py", *current
                )

        if previous is not None and previous != current:
            self.hass.bus.async_fire(
                EVENT_STATUS_CHANGED,
                {
                    "heater_id": heater_id,
                    "name": self.heaters.get(heater_id),
                    "old_status": previous[0],
                    "old_description": previous[1],
                    "status": current[0],
                    "description": current[1],
                    "phase": current_phase,
                },
            )

    def is_offline(self, heater_id: str) -> bool:
        """Offline confirmado en varias lecturas seguidas (ignora falsos aislados)."""
        return self._offline_streak.get(heater_id, 0) >= OFFLINE_DEBOUNCE

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
        if on and data is not None and phase(data) in TRANSITION_PHASES:
            # No interrumpir apagado / enfriamiento / limpieza.
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="stove_busy")
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
