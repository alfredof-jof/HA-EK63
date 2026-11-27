"""Config flow de SmartEK63: login, selección de estufas, opciones y reauth."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    SmartEK63Api,
    SmartEK63AuthError,
    SmartEK63ConnectionError,
    SmartEK63Error,
    cognito_login,
    jwt_claims,
)
from .const import (
    CONF_HEATERS,
    CONF_MIN_TOGGLE_INTERVAL,
    CONF_REFRESH_TOKEN,
    DEFAULT_MIN_TOGGLE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class SmartEK63ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flujo de configuración."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._refresh_token = ""
        self._heaters: dict[str, str] = {}

    async def _async_login(self, email: str, password: str) -> tuple[str, str, dict[str, str]]:
        """Login y lista de estufas. Devuelve (sub, refresh_token, heaters)."""
        tokens = await self.hass.async_add_executor_job(cognito_login, email, password)
        api = SmartEK63Api(
            async_get_clientsession(self.hass),
            tokens["refresh_token"],
            run_sync=self.hass.async_add_executor_job,
        )
        api.set_tokens(tokens)
        heaters = await api.get_heaters()
        sub = str(jwt_claims(tokens["id_token"]).get("sub") or email.lower())
        return sub, api.refresh_token, heaters

    async def _async_try_login(
        self, email: str, password: str, errors: dict[str, str]
    ) -> tuple[str, str, dict[str, str]] | None:
        try:
            return await self._async_login(email, password)
        except SmartEK63AuthError:
            errors["base"] = "invalid_auth"
        except SmartEK63ConnectionError:
            errors["base"] = "cannot_connect"
        except SmartEK63Error:
            errors["base"] = "unknown"
        except Exception:
            _LOGGER.exception("Error inesperado en el login")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            result = await self._async_try_login(email, user_input[CONF_PASSWORD], errors)
            if result is not None:
                sub, refresh_token, heaters = result
                await self.async_set_unique_id(sub)
                self._abort_if_unique_id_configured()
                if not heaters:
                    return self.async_abort(reason="no_heaters")
                self._email, self._refresh_token, self._heaters = email, refresh_token, heaters
                if len(heaters) == 1:
                    return self._async_create()
                return await self.async_step_heaters()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL, "")}
            ),
            errors=errors,
        )

    async def async_step_heaters(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input[CONF_HEATERS]
            if selected:
                self._heaters = {hid: name for hid, name in self._heaters.items() if hid in selected}
                return self._async_create()
            errors["base"] = "no_heater_selected"

        schema = vol.Schema(
            {
                vol.Required(CONF_HEATERS, default=list(self._heaters)): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=hid, label=name) for hid, name in self._heaters.items()],
                        multiple=True,
                    )
                )
            }
        )
        return self.async_show_form(step_id="heaters", data_schema=schema, errors=errors)

    @callback
    def _async_create(self) -> ConfigFlowResult:
        # La contraseña NO se guarda: solo el refresh token.
        return self.async_create_entry(
            title=self._email,
            data={
                CONF_EMAIL: self._email,
                CONF_REFRESH_TOKEN: self._refresh_token,
                CONF_HEATERS: self._heaters,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_try_login(email, user_input[CONF_PASSWORD], errors)
            if result is not None:
                sub, refresh_token, _heaters = result
                await self.async_set_unique_id(sub)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_REFRESH_TOKEN: refresh_token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> SmartEK63OptionsFlow:
        return SmartEK63OptionsFlow()


class SmartEK63OptionsFlow(OptionsFlow):
    """Opciones: intervalo de sondeo y protección anti-ciclos."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_MIN_TOGGLE_INTERVAL: int(user_input[CONF_MIN_TOGGLE_INTERVAL]),
                }
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MIN_TOGGLE_INTERVAL,
                    default=options.get(CONF_MIN_TOGGLE_INTERVAL, DEFAULT_MIN_TOGGLE_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
