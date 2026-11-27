"""Constantes de la integración SmartEK63.

Todo lo que depende de la API no oficial está aquí y en api.py, para que un
cambio de Edilkamin se arregle en un solo sitio.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartek63"
MANUFACTURER: Final = "Edilkamin"
MODEL: Final = "EK63"

# --- AWS Cognito (extraído de homey-smartek63, GPL-3.0) ---
COGNITO_REGION: Final = "eu-central-1"
COGNITO_USER_POOL_ID: Final = "eu-central-1_mqMTP332P"
COGNITO_CLIENT_ID: Final = "751nsgtv5okg7u05mneacrn480"

# --- API REST ---
API_BASE_URL: Final = "https://8wmnu4exgg.execute-api.eu-central-1.amazonaws.com/prod"
API_TIMEOUT: Final = 20  # segundos

# --- Config entry ---
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_HEATERS: Final = "heaters"  # dict {heater_id: nombre}
CONF_MIN_TOGGLE_INTERVAL: Final = "min_toggle_interval"  # minutos

DEFAULT_SCAN_INTERVAL: Final = 120  # segundos
MIN_SCAN_INTERVAL: Final = 60
MAX_SCAN_INTERVAL: Final = 3600
MAX_BACKOFF_INTERVAL: Final = 900  # tope del backoff tras errores
DEFAULT_MIN_TOGGLE_INTERVAL: Final = 10  # minutos entre on/off
COMMAND_REFRESH_DELAY: Final = 5  # segundos tras un comando antes de releer

# --- Campos del GET /heaters/{id} ---
KEY_ONLINE: Final = "online"
KEY_ROOM_TEMPERATURE: Final = "roomTemperature"
KEY_TARGET_TEMPERATURE: Final = "temperature"
KEY_STATUS: Final = "status"
KEY_RELAX: Final = "relaxMode"
KEY_STANDBY: Final = "standbyMode"
KEY_FAN_POWER: Final = "fan1Power"  # confirmado en fase 0 (P mayúscula)
KEY_FAN_POWER_TEMPLATE: Final = "fan{}Power"  # fan1Power, fan2Power, fan3Power
KEY_FANS_NUMBER: Final = "fansNumber"
KEY_MODE: Final = "mode"  # confirmado: "auto" / "manual"
KEY_STATUS_DESCRIPTION: Final = "statusDescription"  # texto de la estufa (italiano)
KEY_POWER: Final = "power"  # nivel de potencia configurado (1-5)
KEY_LAST_UPDATE: Final = "lastUpdate"  # ISO 8601 UTC
KEY_SCHEDULING: Final = "scheduling"  # {"active": bool, "slot1".."slot8"}

# --- Estado ---
# Observado (27/11/2026):
#   10 "accensione: WARMUP"   11-14 "Potenza 1".."Potenza 4"
# Homey usaba {1, 4, 5, 10, 11}; se añaden 12-14 (vistos) y 15 (Potenza 5,
# deducido). Además, la fase se
# calcula a partir de statusDescription (ver coordinator.phase), que es más fiable.
ON_STATUSES: Final = frozenset({1, 4, 5, 10, 11, 12, 13, 14, 15})
STATUS_DESCRIPTIONS: Final[dict[int, str]] = {
    10: "accensione: WARMUP",
    11: "Potenza 1",
    12: "Potenza 2",
    13: "Potenza 3",
    14: "Potenza 4",
}

# Fases normalizadas (sensor enum)
PHASE_OFF: Final = "off"
PHASE_IGNITION: Final = "ignition"
PHASE_HEATING: Final = "heating"
PHASE_SHUTDOWN: Final = "shutdown"
PHASE_CLEANING: Final = "cleaning"
PHASE_ALARM: Final = "alarm"
PHASE_STANDBY: Final = "standby"
PHASE_UNKNOWN: Final = "unknown"
PHASES: Final = [
    PHASE_OFF,
    PHASE_IGNITION,
    PHASE_HEATING,
    PHASE_STANDBY,
    PHASE_SHUTDOWN,
    PHASE_CLEANING,
    PHASE_ALARM,
    PHASE_UNKNOWN,
]

# --- Rangos ---
TEMP_MIN: Final = 5
TEMP_MAX: Final = 35
TEMP_STEP: Final = 1
# fan1Power = 2 observado: escala probable 1-5 como el resto de Edilkamin.
# Sigue sin confirmar qué acepta el PUT /fan; la entidad number va deshabilitada.
FAN_MIN: Final = 1
FAN_MAX: Final = 5
FAN_STEP: Final = 1

MODE_AUTO: Final = "auto"
MODE_MANUAL: Final = "manual"
MODES: Final = [MODE_AUTO, MODE_MANUAL]
