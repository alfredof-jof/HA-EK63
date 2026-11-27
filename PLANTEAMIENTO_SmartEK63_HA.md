# Integración SmartEK63 (Edilkamin EK63) para Home Assistant

Planteamiento para desarrollar con Claude Code una integración personalizada (`custom_component`) que controle la estufa de pellet EK63 desde Home Assistant, usando la misma nube que la app oficial **SmartEK63**.

---

## 1. Contexto

- Estufa: Edilkamin **EK63** con wifi integrado (E-Smart), ya funcionando en la app **SmartEK63** con mi cuenta.
- Home Assistant OS (64 bits) en una **Raspberry Pi 3 Model B+ (1 GB RAM)** → la integración debe ser ligera: sin dependencias pesadas y con poco *polling*.
- No existe integración para SmartEK63. Las integraciones "Edilkamin" de HACS son para la app *The Mind*, que usa otro backend, y **no sirven**.
- Referencia técnica: app de Homey `smarthomesven/homey-smartek63` (GitHub, licencia **GPL-3.0**, mantenida en noviembre de 2026). De ella se ha extraído la API que se describe abajo.
  - Para uso personal no hay problema. Si se publica la integración derivada, debe ser GPL-3.0 y citar el origen.

## 2. API (obtenida de la app de Homey, no oficial)

### Autenticación: AWS Cognito (USER_SRP_AUTH)

| Parámetro | Valor |
|---|---|
| Región | `eu-central-1` |
| UserPoolId | `eu-central-1_mqMTP332P` |
| ClientId | `751nsgtv5okg7u05mneacrn480` |
| Flujo | SRP con email + contraseña de la cuenta SmartEK63 → `accessToken`, `idToken`, `refreshToken` |
| Renovación | `REFRESH_TOKEN_AUTH` con el `refreshToken` |

En Python se usa **`pycognito`** (clase `Cognito` / `AWSSRP`). Home Assistant ya la usa para Nabu Casa, así que no añade peso. Se declara igualmente en `manifest.json`.

### Endpoints REST

Base: `https://8wmnu4exgg.execute-api.eu-central-1.amazonaws.com/prod`

Cabecera: `Authorization: <idToken>`. Va **el idToken a secas, sin "Bearer"**.

| Acción | Método y ruta | Cuerpo |
|---|---|---|
| Listar estufas | `GET /heaters` | → `[{ id, name, ... }]` |
| Estado | `GET /heaters/{id}` | → ver campos abajo |
| Encender/apagar | `PUT /heaters/{id}/status` | `{"status": "on" \| "off"}` |
| Temperatura objetivo | `PUT /heaters/{id}/temperature` | `{"temperature": 21}` |
| Modo | `PUT /heaters/{id}/mode` | `{"mode": "auto" \| "manual"}` |
| Relax | `PUT /heaters/{id}/relax` | `{"relaxMode": true \| false}` |
| Standby | `PUT /heaters/{id}/standby` | `{"standbyMode": true \| false}` |
| Ventilador | `PUT /heaters/{id}/fan` | `{"fan": 1, "power": <0-100?>}` (**hay que verificar la escala**) |

Campos conocidos del `GET /heaters/{id}`:

- `online` (bool)
- `roomTemperature`
- `temperature` (consigna)
- `status` (numérico)
- `relaxMode`
- `standbyMode`
- `fan1power`

Homey considera "encendida" la estufa cuando `status ∈ {1, 4, 5, 10, 11}`. Es una interpretación suya: los demás códigos (arranque, limpieza, apagado, alarma…) hay que **descubrirlos observando**.

Cuando el token caduca, la API devuelve **401** → renovar con el refresh token y reintentar una vez.

## 3. Alcance de la integración

**Dominio:** `smartek63`. **iot_class:** `cloud_polling`.

| Entidad | Tipo | Detalle |
|---|---|---|
| Estufa | `climate` | `hvac_modes: off / heat`; `current_temperature` = `roomTemperature`; `target_temperature` = `temperature` (paso de 1 °C y rango a confirmar); preset `auto` / `manual` |
| Relax | `switch` | |
| Standby | `switch` | |
| Modo | `select` | `auto` / `manual` (si no va como preset del climate) |
| Ventilador | `number` o `fan` | Según la escala que se confirme |
| Estado | `sensor` | Código `status` en bruto + texto descriptivo cuando se conozca el mapeo |
| Online | `binary_sensor` | `connectivity` |
| Resto | `sensor` | Lo que aparezca en el JSON (alarmas, potencia, nivel de pellet…), tras la fase 0 |

Requisitos:

- **Config flow** por la interfaz: email + contraseña → login → lista de estufas → elegir una (o todas).
- Guardar el **refresh token** en la config entry y **no** guardar la contraseña si se puede evitar (o, si hace falta, solo para reautenticar).
- **Reauth flow** si el refresh token deja de valer.
- **DataUpdateCoordinator**: un solo GET por estufa cada **120 s** por defecto, configurable en opciones (mínimo 60 s). Tras cada comando, refresco inmediato.
- Si `online == false` o hay errores de red → entidades `unavailable`, sin inundar el log.
- **Nunca loguear tokens ni la contraseña.**
- Todo asíncrono con `aiohttp` (la sesión de HA). Las llamadas de `pycognito` son síncronas, así que van en `hass.async_add_executor_job`.
- Protección: no enviar on/off repetidos en menos de X minutos. Una estufa de pellet tiene ciclos de arranque y limpieza que no hay que interrumpir.
- Traducciones `es` y `en`.

## 4. Estructura del repositorio

```
smartek63-ha/
├── custom_components/smartek63/
│   ├── __init__.py          # setup/unload de la config entry
│   ├── manifest.json        # requirements: pycognito
│   ├── const.py             # pool, client id, URL base, códigos de estado
│   ├── api.py               # cliente: login, refresh, get/put, gestión de 401
│   ├── coordinator.py       # DataUpdateCoordinator
│   ├── config_flow.py       # login + selección de estufa + opciones + reauth
│   ├── entity.py            # entidad base (device_info, available)
│   ├── climate.py
│   ├── switch.py
│   ├── select.py
│   ├── number.py
│   ├── sensor.py
│   ├── binary_sensor.py
│   ├── strings.json
│   └── translations/{es,en}.json
├── tools/explore.py         # script de exploración (fase 0)
├── tests/
├── hacs.json
├── README.md
└── LICENSE                  # GPL-3.0
```

## 5. Fases de desarrollo

**Fase 0: exploración (antes de tocar Home Assistant).**
Un script `tools/explore.py` independiente que:

1. Hace login con email y contraseña, pasados por variables de entorno.
2. Lista las estufas.
3. Vuelca el JSON completo de `GET /heaters/{id}` a un fichero con fecha.
4. Opcionalmente, ejecuta en bucle cada 30 s para ver cómo cambia `status` durante un encendido y un apagado hechos desde la app oficial.

Con eso se completan los códigos de estado, la escala del ventilador y los campos extra. **Solo lecturas**, salvo una prueba controlada: activar y desactivar *relax*.

**Fase 1: cliente API + config flow + coordinator.** La integración carga, autentica, renueva el token y muestra los datos en un `sensor` de estado.

**Fase 2: entidades de control.** `climate`, los `switch`, el `select` y el `number`/`fan`. Se prueba cada comando desde HA y se confirma en la app oficial.

**Fase 3: robustez.**
- Reauth flow.
- Manejo de 401, 5xx y timeouts con *backoff*.
- Estado `unavailable` cuando toca.
- Protección anti-ciclos de on/off.
- Opciones (intervalo de *polling*).
- Diagnósticos (`diagnostics.py`) con los datos sensibles anonimizados.

**Fase 4: empaquetado.** `hacs.json`, README e instalación:
- **Repo público:** HACS → *repositorios personalizados*.
- **Repo privado:** copiar `custom_components/smartek63` a `/config/custom_components/` con el complemento **Samba** o **SSH**. Son ligeros y adecuados para la Pi 3. Evitar Studio Code Server, que consume demasiada RAM.

**Fase 5: automatizaciones de ejemplo** (en el README).
- Aviso al móvil si `roomTemperature < 5 °C`.
- Encender la estufa N horas antes de llegar, con un botón o un calendario.
- Notificación si la estufa entra en un `status` de alarma o pasa a `offline`.

## 6. Riesgos

- **API no oficial:** Edilkamin puede cambiarla sin aviso. Hay que aislar todo en `api.py` y `const.py` para que los arreglos sean de un solo sitio.
- **Dependencia de la nube:** sin internet en el pueblo, no hay control desde HA. La estufa sigue funcionando con su display o su mando.
- **Seguridad:** usar las credenciales propias (nunca tokens de terceros, como hacía `ilozano/edilkamin-pellet`). La contraseña debe ser única para esta cuenta.

---

## 7. Prompt para arrancar en Claude Code

> Quiero desarrollar una integración personalizada de Home Assistant (`custom_components/smartek63`) para controlar mi estufa de pellet Edilkamin EK63 a través de la nube de la app SmartEK63. Lee `PLANTEAMIENTO_SmartEK63_HA.md` en la raíz del repo: contiene la API (Cognito + endpoints REST), el alcance, la estructura y las fases.
>
> Empieza **solo por la Fase 0**: crea `tools/explore.py` (Python 3.12, `pycognito` + `aiohttp` o `requests`) que lea `EK63_EMAIL` y `EK63_PASSWORD` de variables de entorno, haga login SRP, liste las estufas y guarde el JSON completo del estado de cada una en `tools/dumps/` con fecha y hora. Añade un modo `--watch 30` que repita la lectura cada 30 s y muestre solo los campos que cambian. No envíes ningún comando a la estufa. No imprimas tokens. Añade `tools/dumps/` y `.env` al `.gitignore`.
>
> Cuando tenga los volcados te los paso y seguimos con la Fase 1, siguiendo las convenciones actuales de Home Assistant (config flow, `DataUpdateCoordinator`, `aiohttp` con la sesión de HA, `pycognito` en el executor, entidades con `has_entity_name`).
