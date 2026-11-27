"""Cliente de la nube SmartEK63 (Cognito + API REST).

No depende de Home Assistant. Las llamadas a Cognito (boto3/pycognito) son
síncronas y se ejecutan con ``run_sync`` (en HA: ``hass.async_add_executor_job``).
Nunca se registran tokens ni contraseñas.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    API_BASE_URL,
    API_TIMEOUT,
    COGNITO_CLIENT_ID,
    COGNITO_REGION,
    COGNITO_USER_POOL_ID,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_MARGIN = 300  # renovar el idToken 5 min antes de que caduque

RunSync = Callable[..., Awaitable[Any]]


class SmartEK63Error(Exception):
    """Error genérico de la API."""


class SmartEK63AuthError(SmartEK63Error):
    """Credenciales o refresh token no válidos."""


class SmartEK63ConnectionError(SmartEK63Error):
    """Error de red o timeout."""


# ---------------------------------------------------------------------------
# Cognito (síncrono, ejecutar en executor)
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_cognito_client: Any = None


def _get_cognito_client() -> Any:
    """Cliente boto3 sin firmar (InitiateAuth es público). Se crea una vez."""
    global _cognito_client  # noqa: PLW0603
    with _client_lock:
        if _cognito_client is None:
            import boto3  # noqa: PLC0415  (import pesado: solo en el executor)
            from botocore import UNSIGNED  # noqa: PLC0415
            from botocore.config import Config  # noqa: PLC0415

            _cognito_client = boto3.client(
                "cognito-idp",
                region_name=COGNITO_REGION,
                config=Config(signature_version=UNSIGNED, retries={"max_attempts": 2}),
            )
        return _cognito_client


def _tokens_from_result(result: dict[str, Any]) -> dict[str, str]:
    auth = result["AuthenticationResult"]
    tokens = {"id_token": auth["IdToken"], "access_token": auth["AccessToken"]}
    if auth.get("RefreshToken"):
        tokens["refresh_token"] = auth["RefreshToken"]
    return tokens


def _translate_boto_error(err: Exception) -> SmartEK63Error:
    from botocore.exceptions import ClientError  # noqa: PLC0415

    if isinstance(err, ClientError):
        code = err.response.get("Error", {}).get("Code", "")
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            return SmartEK63AuthError(code)
        return SmartEK63Error(code or "ClientError")
    return SmartEK63ConnectionError(type(err).__name__)


def cognito_login(email: str, password: str) -> dict[str, str]:
    """Login SRP con email y contraseña. Devuelve id/access/refresh token."""
    from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415
    from pycognito.aws_srp import AWSSRP  # noqa: PLC0415

    try:
        srp = AWSSRP(
            username=email,
            password=password,
            pool_id=COGNITO_USER_POOL_ID,
            client_id=COGNITO_CLIENT_ID,
            client=_get_cognito_client(),
        )
        return _tokens_from_result(srp.authenticate_user())
    except (ClientError, BotoCoreError) as err:
        raise _translate_boto_error(err) from None
    except Exception as err:  # retos MFA, cambio de contraseña obligatorio…
        raise SmartEK63AuthError(type(err).__name__) from None


def cognito_refresh(refresh_token: str) -> dict[str, str]:
    """REFRESH_TOKEN_AUTH. Devuelve id/access token (y refresh si rota)."""
    from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415

    try:
        result = _get_cognito_client().initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )
    except (ClientError, BotoCoreError) as err:
        raise _translate_boto_error(err) from None
    return _tokens_from_result(result)


def jwt_claims(token: str) -> dict[str, Any]:
    """Lee los claims de un JWT sin verificarlo (solo para exp/sub)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


async def _default_run_sync(func: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


# ---------------------------------------------------------------------------
# Cliente asíncrono
# ---------------------------------------------------------------------------


class SmartEK63Api:
    """Cliente REST asíncrono con renovación automática del token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        *,
        run_sync: RunSync | None = None,
        on_refresh_token: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._refresh_token = refresh_token
        self._run_sync = run_sync or _default_run_sync
        self._on_refresh_token = on_refresh_token
        self._id_token: str | None = None
        self._id_token_exp = 0.0
        self._lock = asyncio.Lock()

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    def set_tokens(self, tokens: dict[str, str]) -> None:
        """Usa tokens ya obtenidos (p. ej. tras el login del config flow)."""
        self._id_token = tokens["id_token"]
        self._id_token_exp = float(jwt_claims(self._id_token).get("exp", time.time() + 3000))
        new_refresh = tokens.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_refresh_token:
                self._on_refresh_token(new_refresh)

    async def _get_id_token(self, force: bool = False) -> str:
        async with self._lock:
            if force or not self._id_token or time.time() > self._id_token_exp - TOKEN_MARGIN:
                _LOGGER.debug("Renovando idToken")
                self.set_tokens(await self._run_sync(cognito_refresh, self._refresh_token))
            assert self._id_token is not None
            return self._id_token

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE_URL}{path}"
        for attempt in range(2):
            token = await self._get_id_token(force=attempt > 0)
            try:
                async with self._session.request(
                    method,
                    url,
                    json=payload,
                    headers={"Authorization": token},  # idToken sin "Bearer"
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        _LOGGER.debug("401 en %s %s: renovando token", method, path)
                        continue
                    if resp.status >= 400:
                        raise SmartEK63Error(f"HTTP {resp.status} en {method} {path}")
                    text = await resp.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise SmartEK63ConnectionError(f"{type(err).__name__} en {method} {path}") from err
            if not text:
                return None
            try:
                return json.loads(text)
            except ValueError:
                return text
        raise SmartEK63Error(f"401 persistente en {method} {path}")

    # --- Lecturas ---

    async def get_heaters(self) -> dict[str, str]:
        """Devuelve {heater_id: nombre}."""
        data = await self._request("GET", "/heaters")
        if isinstance(data, dict):
            data = next((data[k] for k in ("heaters", "items", "data") if isinstance(data.get(k), list)), [data])
        heaters: dict[str, str] = {}
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            hid = next((item[k] for k in ("id", "heaterId", "serial", "serialNumber") if item.get(k) is not None), None)
            if hid is not None:
                heaters[str(hid)] = str(item.get("name") or f"EK63 {hid}")
        return heaters

    async def get_heater(self, heater_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/heaters/{quote(heater_id, safe='')}")
        if not isinstance(data, dict):
            raise SmartEK63Error("Respuesta inesperada del estado de la estufa")
        return data

    # --- Comandos ---

    async def _put(self, heater_id: str, action: str, payload: dict[str, Any]) -> None:
        await self._request("PUT", f"/heaters/{quote(heater_id, safe='')}/{action}", payload)

    async def set_power(self, heater_id: str, on: bool) -> None:
        await self._put(heater_id, "status", {"status": "on" if on else "off"})

    async def set_temperature(self, heater_id: str, temperature: float) -> None:
        value: float | int = int(temperature) if float(temperature).is_integer() else temperature
        await self._put(heater_id, "temperature", {"temperature": value})

    async def set_mode(self, heater_id: str, mode: str) -> None:
        await self._put(heater_id, "mode", {"mode": mode})

    async def set_relax(self, heater_id: str, enabled: bool) -> None:
        await self._put(heater_id, "relax", {"relaxMode": enabled})

    async def set_standby(self, heater_id: str, enabled: bool) -> None:
        await self._put(heater_id, "standby", {"standbyMode": enabled})

    async def set_fan(self, heater_id: str, power: int, fan: int = 1) -> None:
        await self._put(heater_id, "fan", {"fan": fan, "power": int(power)})
