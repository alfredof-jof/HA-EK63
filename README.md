# SmartEK63 para Home Assistant

Integración personalizada (`custom_components/smartek63`) para controlar estufas de pellet **Edilkamin EK63** a través de la nube de la app **SmartEK63**.

> API no oficial, obtenida de [`smarthomesven/homey-smartek63`](https://github.com/smarthomesven/homey-smartek63) (GPL-3.0). Por eso este proyecto también es GPL-3.0. Edilkamin puede cambiar la API sin aviso.

## Qué crea

Por cada estufa, un dispositivo con:

| Entidad | Tipo | Notas |
|---|---|---|
| Estufa | `climate` | Apagado/calor, consigna (paso 1 °C), presets *auto* / *manual* |
| Relax | `switch` | |
| Standby | `switch` | |
| Potencia del ventilador | `number` | **Deshabilitada por defecto** hasta confirmar la escala |
| Fase | `sensor` (enum) | Apagada / Encendiendo / Calentando / En espera / Apagándose / Enfriando / Limpieza / Alarma, deducida de `statusDescription` |
| Estado de la estufa | `sensor` | Texto que manda la estufa, p. ej. `accensione: WARMUP`, `Potenza 3` |
| Código de estado | `sensor` | Diagnóstico: código `status` en bruto |
| Temperatura ambiente | `sensor` | Para históricos y automatizaciones |
| Nivel de potencia | `sensor` | Campo `power` (1-5) |
| Nivel ventilador N | `sensor` | `fan1Power`, `fan2Power`… según `fansNumber` |
| Última actualización en la nube | `sensor` | Diagnóstico (timestamp) |
| Conectividad | `binary_sensor` | Diagnóstico. Si la estufa está offline, el resto pasa a *no disponible* |
| Programación activa | `binary_sensor` | Cronotermostato de la estufa (`scheduling.active`) |
| Alarma | `binary_sensor` (problema) | Encendido si la estufa está en alarma. Atributos `code`, `description`, `alarm_code`, `alarm_text` y `unknown_status` |

## Panel de seguimiento

[`dashboards/estufa_ek63.yaml`](dashboards/estufa_ek63.yaml) es un panel listo para pegar, solo con tarjetas nativas: control, estado actual, gráficas de 24 h y 7 días, y registro de cambios. Las instrucciones están al principio del fichero.

## Códigos observados

Ciclos reales del 27 y 28/11/2026 con consigna 20 °C:

| `status` | `statusDescription` | Fase | Notas |
|---|---|---|---|
| 0 | Stufa spenta | Apagada | Apagado manual desde Stand-by (inmediato), o fin del enfriamiento sin *standby* |
| 2 | accensione: PULIZIA | Encendiendo | Limpieza del brasero al arrancar (~20-30 s) |
| 3 | accensione: CARICO PELLET | Encendiendo | ~2 min. Desde Stand-by a veces arranca directamente aquí |
| 4 | accensione: CONTROLLO INCREMENTO TEMPERATURA FUMI | Encendiendo | ~1-2 min |
| 5 | accensione: CONTROLLO SUPERAMENTO SOGLIA | Encendiendo | ~2 min |
| 10 | accensione: WARMUP | Encendiendo | ~5 min |
| 11-15 | Potenza 1-5 | Calentando | Arranca en 3-5 y modula hacia 1 al acercarse a la consigna |
| 40 | Spegnimento | Apagándose | Al superar la consigna o al apagar. ~7 min |
| 41 | Raffreddamento | Enfriando | ~12-19 min |
| 43 | Pulizia High | Limpieza | Limpieza periódica **en marcha** (~30 s) y vuelve a Potenza. Cuenta como encendida |
| 46 | Stand-by | En espera | Con *standby* activado. Vuelve a arrancar sola al bajar la temperatura |

### Alarmas

Aún no se ha visto ninguna por la API. El histórico de la app muestra estas:

| Código | Alarma |
|---|---|
| A02 | Fallo extractor de humos |
| A03 | Fin pellet / stop llama |
| A04 | Falta encendido |
| A11 | Intervención presostato de seguridad |

Se detectan por el patrón `A01`/`E12` en `statusDescription` y por palabras como *allarme*, *mancata accensione*, *pressostato*, *estrattore*, *fine pellet* o *sovratemperatura*. El sensor *Alarma* expone `alarm_code` (p. ej. `A03`) y `alarm_text` (texto de la tabla). Cualquier código nuevo:

- sale como fase *Desconocida*;
- se avisa una vez en el log de HA;
- lanza el evento `smartek63_status_changed`;
- en el panel del PC aparece un aviso, con notificación de escritorio si la activas.

Comportamiento en HA: durante *Apagándose* y *Enfriando* el termostato aparece apagado (no hay llama) y **no se permite encender** hasta que termine. En *Stand-by* aparece encendido y en reposo; en *Limpieza* (43), encendido y calentando. Un `online: false` aislado (pasa varias veces por hora, unos segundos) no deja las entidades como no disponibles: hacen falta 2 lecturas seguidas.

Características:

- Configuración por la interfaz (email + contraseña). **La contraseña no se guarda**, solo el *refresh token* de Cognito. Si caduca, HA pide volver a autenticar.
- Un GET por estufa cada 120 s (configurable, mínimo 60 s). Tras un error, el intervalo se duplica hasta 15 min y vuelve al normal cuando se recupera.
- Tras cada comando, el estado se actualiza de forma optimista y se relee a los 5 s.
- **Protección anti-ciclos:** no permite encender/apagar dos veces en menos de 10 min (configurable; 0 la desactiva). Tampoco envía "encender" si ya está encendida.
- Diagnósticos descargables con email, ids y tokens anonimizados.
- Nunca registra tokens ni contraseñas.

## Panel local en el PC (mientras no esté en HA)

`tools/monitor.py` lee la nube cada 30 s y sirve un panel en <http://127.0.0.1:8063>. El panel muestra:

- la fase, las temperaturas, la potencia y los ventiladores;
- gráficas de 1 h, 6 h, 24 h y 7 días;
- el registro de cambios y la tabla de códigos de estado vistos.

Solo lee: no envía comandos. Guarda cada lectura en `tools/dumps/` con el mismo formato que `explore.py --watch`, y al arrancar recupera el histórico de los últimos 7 días.

```powershell
python tools/monitor.py
```

Opciones: `--interval 60` (segundos entre lecturas) y `--port 8080`.

## Fase 0: exploración (recomendado antes de instalar)

Hay datos sin confirmar: códigos de `status`, escala del ventilador y nombre del campo de modo. Para descubrirlos, desde un PC con Python 3.12+:

```powershell
pip install -r tools/requirements.txt
```

```powershell
copy .env.example .env
```

Edita `.env` con tu email y contraseña y ejecuta:

```powershell
python tools/explore.py
```

```powershell
python tools/explore.py --watch 30
```

El primero vuelca el JSON de cada estufa en `tools/dumps/`. El segundo relee cada 30 s y muestra solo lo que cambia; déjalo corriendo mientras enciendes y apagas desde la app oficial. **El script no envía ningún comando.** Con esos datos se rellenan en `custom_components/smartek63/const.py`:

- `ON_STATUSES` y `STATUS_DESCRIPTIONS`
- `FAN_MIN` / `FAN_MAX`
- `KEY_MODE`
- `TEMP_MIN` / `TEMP_MAX`

## Instalación

Requiere Home Assistant 2025.1 o posterior.

**Con HACS (repo público):** HACS → menú ⋮ → *Repositorios personalizados* → `https://github.com/alfredof-jof/HA-EK63`, categoría *Integración* → instalar → reiniciar HA.

**Manual (repo privado):** copia la carpeta `custom_components/smartek63` en `/config/custom_components/` con el complemento **Samba share** o **Advanced SSH & Web Terminal** (ligeros para una Raspberry Pi 3; evita Studio Code Server) y reinicia HA.

Después: *Ajustes → Dispositivos y servicios → Añadir integración → SmartEK63*.

Para ver logs de depuración:

```yaml
logger:
  logs:
    custom_components.smartek63: debug
```

## Automatizaciones de ejemplo

Sustituye `climate.salon_ek63_c7f790`, `sensor.ek63_*`, `binary_sensor.ek63_*` y `notify.mobile_app_mi_movil` por tus entidades.

**Aviso de helada:**

```yaml
alias: Estufa - aviso temperatura baja
triggers:
  - trigger: numeric_state
    entity_id: sensor.salon_ek63_c7f790_temperatura_ambiente
    below: 5
actions:
  - action: notify.mobile_app_mi_movil
    data:
      message: "Casa a {{ states('sensor.salon_ek63_c7f790_temperatura_ambiente') }} °C"
```

**Encender antes de llegar** (con un `input_datetime.llegada_casa` y 2 h de antelación):

```yaml
alias: Estufa - precalentar antes de llegar
triggers:
  - trigger: template
    value_template: >
      {{ now() >= (states('input_datetime.llegada_casa') | as_datetime | as_local) - timedelta(hours=2) }}
conditions:
  - condition: state
    entity_id: climate.salon_ek63_c7f790
    state: "off"
actions:
  - action: climate.set_hvac_mode
    target:
      entity_id: climate.salon_ek63_c7f790
    data:
      hvac_mode: heat
```

**Estufa offline o en alarma:**

```yaml
alias: Estufa - offline o alarma
triggers:
  - trigger: state
    entity_id: binary_sensor.salon_ek63_c7f790_conectividad
    to: "off"
    for: "00:10:00"
  - trigger: state
    entity_id: binary_sensor.salon_ek63_c7f790_alarma
    to: "on"
actions:
  - action: notify.mobile_app_mi_movil
    data:
      title: Estufa
      message: >
        {% if trigger.entity_id.endswith('conectividad') %}Sin conexión desde hace 10 min
        {% else %}Alarma: {{ state_attr('binary_sensor.salon_ek63_c7f790_alarma', 'alarm_text') or state_attr('binary_sensor.salon_ek63_c7f790_alarma', 'description') }}
        (código {{ state_attr('binary_sensor.salon_ek63_c7f790_alarma', 'code') }}){% endif %}
```

**Estado nuevo sin clasificar** (para ir completando la integración). Cada cambio de código lanza el evento `smartek63_status_changed` con `old_status`, `status`, `description` y `phase`:

```yaml
alias: Estufa - estado desconocido
triggers:
  - trigger: event
    event_type: smartek63_status_changed
    event_data:
      phase: unknown
actions:
  - action: notify.mobile_app_mi_movil
    data:
      title: Estufa - estado nuevo
      message: "Código {{ trigger.event.data.status }}: {{ trigger.event.data.description }}"
```

## Limitaciones

- Depende de la nube: sin internet no hay control desde HA (la estufa sigue funcionando con su display o mando).
- Usa tu propia cuenta y una contraseña única para ella.

## Licencia

GPL-3.0. Ver [LICENSE](LICENSE).
