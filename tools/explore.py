#!/usr/bin/env python3
"""Fase 0: exploración de la API de SmartEK63 (SOLO LECTURA).

Uso:
    set EK63_EMAIL / EK63_PASSWORD (o crea un fichero .env en la raíz del repo)
    python tools/explore.py              # una lectura y volcado a tools/dumps/
    python tools/explore.py --watch 30   # lectura cada 30 s mostrando lo que cambia

Este script NO envía ningún comando a la estufa y NUNCA imprime tokens.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import requests
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pycognito.aws_srp import AWSSRP

REGION = "eu-central-1"
USER_POOL_ID = "eu-central-1_mqMTP332P"
CLIENT_ID = "751nsgtv5okg7u05mneacrn480"
API_BASE_URL = "https://8wmnu4exgg.execute-api.eu-central-1.amazonaws.com/prod"

# Interpretación de la app de Homey (pendiente de confirmar).
ON_STATUSES = {1, 4, 5, 10, 11}

ROOT = Path(__file__).resolve().parent.parent
DUMPS = Path(__file__).resolve().parent / "dumps"


def load_dotenv(path: Path) -> None:
    """Carga KEY=VALUE de un .env sin pisar variables ya definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


class Client:
    """Cliente mínimo de solo lectura."""

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._cognito = boto3.client(
            "cognito-idp",
            region_name=REGION,
            config=Config(signature_version=UNSIGNED),
        )
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._http = requests.Session()

    def login(self) -> None:
        srp = AWSSRP(
            username=self._email,
            password=self._password,
            pool_id=USER_POOL_ID,
            client_id=CLIENT_ID,
            client=self._cognito,
        )
        result = srp.authenticate_user()["AuthenticationResult"]
        self._id_token = result["IdToken"]
        self._refresh_token = result["RefreshToken"]

    def refresh(self) -> None:
        try:
            resp = self._cognito.initiate_auth(
                ClientId=CLIENT_ID,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={"REFRESH_TOKEN": self._refresh_token or ""},
            )
            self._id_token = resp["AuthenticationResult"]["IdToken"]
        except ClientError:
            # El refresh token ha caducado: login completo.
            self.login()

    def get(self, path: str) -> Any:
        for attempt in range(2):
            resp = self._http.get(
                f"{API_BASE_URL}{path}",
                headers={"Authorization": self._id_token or ""},
                timeout=20,
            )
            if resp.status_code == 401 and attempt == 0:
                print("  (token caducado, renovando)")
                self.refresh()
                continue
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return resp.text
        raise RuntimeError("401 persistente tras renovar el token")


def heater_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("heaters", "items", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return payload if isinstance(payload, list) else []


def heater_id(item: dict[str, Any]) -> str:
    for key in ("id", "heaterId", "serial", "serialNumber"):
        if item.get(key) is not None:
            return str(item[key])
    raise KeyError(f"No encuentro el id en {sorted(item)}")


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}{key}."))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            out.update(flatten(value, f"{prefix}{idx}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out


def save_json(name: str, data: Any) -> Path:
    DUMPS.mkdir(parents=True, exist_ok=True)
    path = DUMPS / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def describe_status(status: Any) -> str:
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "?"
    return "encendida (según Homey)" if code in ON_STATUSES else "otro estado"


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploración SmartEK63 (solo lectura)")
    parser.add_argument(
        "--watch",
        type=int,
        metavar="SEG",
        help="Repetir la lectura cada SEG segundos (mínimo 10) y mostrar los cambios",
    )
    parser.add_argument("--env", type=Path, default=ROOT / ".env", help="Fichero .env")
    args = parser.parse_args()

    load_dotenv(args.env)
    email = os.environ.get("EK63_EMAIL")
    password = os.environ.get("EK63_PASSWORD")
    if not email or not password:
        print("Faltan EK63_EMAIL y/o EK63_PASSWORD (entorno o .env).", file=sys.stderr)
        return 2

    client = Client(email, password)
    print("Login en Cognito…")
    try:
        client.login()
    except (ClientError, BotoCoreError, Exception) as err:  # noqa: BLE001
        # No imprimimos el objeto de error completo por si incluyera datos sensibles.
        print(f"Error de login: {type(err).__name__}", file=sys.stderr)
        return 1
    print("Login correcto.")

    stamp = now_stamp()
    raw_list = client.get("/heaters")
    print(f"Lista guardada en {save_json(f'heaters_{stamp}.json', raw_list)}")
    heaters = heater_list(raw_list)
    if not heaters:
        print("La cuenta no tiene estufas.")
        return 0

    ids: list[str] = []
    for item in heaters:
        hid = heater_id(item)
        ids.append(hid)
        print(f"\n== Estufa {item.get('name', '(sin nombre)')} [{hid}]")
        detail = client.get(f"/heaters/{hid}")
        path = save_json(f"heater_{safe_name(hid)}_{stamp}.json", detail)
        print(f"Estado guardado en {path}")
        if isinstance(detail, dict):
            for key, value in sorted(flatten(detail).items()):
                print(f"  {key} = {value!r}")
            print(f"  -> status {detail.get('status')!r}: {describe_status(detail.get('status'))}")

    if not args.watch:
        return 0

    interval = max(10, args.watch)
    print(f"\nModo watch cada {interval} s. Ctrl+C para salir.")
    previous: dict[str, dict[str, Any]] = {}
    try:
        while True:
            for hid in ids:
                try:
                    detail = client.get(f"/heaters/{hid}")
                except requests.RequestException as err:
                    print(f"[{dt.datetime.now():%H:%M:%S}] {hid}: error de red ({type(err).__name__})")
                    continue
                flat = flatten(detail)
                old = previous.get(hid)
                ts = dt.datetime.now()
                if old is None:
                    print(f"[{ts:%H:%M:%S}] {hid}: lectura inicial (status={flat.get('status')!r})")
                    changes = {k: (None, v) for k, v in flat.items()}
                else:
                    keys = set(old) | set(flat)
                    changes = {
                        k: (old.get(k), flat.get(k))
                        for k in sorted(keys)
                        if old.get(k) != flat.get(k)
                    }
                    for key, (before, after) in changes.items():
                        extra = f"  ({describe_status(after)})" if key == "status" else ""
                        print(f"[{ts:%H:%M:%S}] {hid}: {key}: {before!r} -> {after!r}{extra}")
                if changes:
                    DUMPS.mkdir(parents=True, exist_ok=True)
                    log = DUMPS / f"watch_{safe_name(hid)}_{ts:%Y%m%d}.jsonl"
                    with log.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"ts": ts.isoformat(), "data": detail}, ensure_ascii=False) + "\n")
                previous[hid] = flat
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nFin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
