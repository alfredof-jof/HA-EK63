#!/usr/bin/env python3
"""Panel local de seguimiento de la EK63 (SOLO LECTURA).

Lee la nube cada N segundos, guarda cada lectura en tools/dumps/ (mismo
formato que ``explore.py --watch``) y sirve un panel en http://127.0.0.1:8063

    python tools/monitor.py              # cada 30 s, puerto 8063
    python tools/monitor.py --interval 60 --port 8080

No envía ningún comando a la estufa y no expone tokens: el servidor solo
escucha en 127.0.0.1 y únicamente sirve los datos de estado.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from explore import DUMPS, ROOT, Client, heater_id, heater_list, load_dotenv, safe_name  # noqa: E402

HTML_FILE = Path(__file__).resolve().parent / "monitor.html"
HISTORY_DAYS = 7
# Campos que cambian en cada lectura y no aportan al registro de cambios.
IGNORE_CHANGES = {"lastUpdate", "date"}
# Campos que se mandan al navegador en el histórico (para no enviar JSON enorme).
HISTORY_FIELDS = (
    "online",
    "status",
    "statusDescription",
    "temperature",
    "roomTemperature",
    "power",
    "mode",
    "relaxMode",
    "standbyMode",
    "fan1Power",
    "fan2Power",
    "fan3Power",
)

def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}{key}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out

class HeaterState:
    def __init__(self, hid: str, name: str) -> None:
        self.id = hid
        self.name = name
        self.latest: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []  # {"ts", **HISTORY_FIELDS}
        self.changes: list[dict[str, Any]] = []  # {"ts", "key", "old", "new"}
        self.codes: dict[str, dict[str, Any]] = {}
        self._prev_flat: dict[str, Any] | None = None

    def add(self, ts: str, data: dict[str, Any]) -> None:
        self.latest = data
        self.history.append({"ts": ts, **{k: data.get(k) for k in HISTORY_FIELDS}})
        flat = flatten(data)
        if self._prev_flat is not None:
            for key in sorted(set(flat) | set(self._prev_flat)):
                if key in IGNORE_CHANGES:
                    continue
                old, new = self._prev_flat.get(key), flat.get(key)
                if old != new:
                    self.changes.append({"ts": ts, "key": key, "old": old, "new": new})
        self._prev_flat = flat
        code = data.get("status")
        if code is not None:
            entry = self.codes.setdefault(
                str(code), {"description": data.get("statusDescription"), "first": ts, "count": 0}
            )
            entry["description"] = data.get("statusDescription") or entry["description"]
            entry["last"] = ts
            entry["count"] += 1

    def trim(self) -> None:
        cutoff = (dt.datetime.now() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
        self.history = [h for h in self.history if h["ts"] >= cutoff]
        self.changes = [c for c in self.changes if c["ts"] >= cutoff][-2000:]

class Monitor:
    def __init__(self, client: Client, interval: int) -> None:
        self.client = client
        self.interval = interval
        self.lock = threading.Lock()
        self.heaters: dict[str, HeaterState] = {}
        self.last_poll: str | None = None
        self.error: str | None = None

    def discover(self) -> None:
        for item in heater_list(self.client.get("/heaters")):
            hid = heater_id(item)
            self.heaters[hid] = HeaterState(hid, str(item.get("name") or hid))

    def load_history(self) -> None:
        cutoff = (dt.datetime.now() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
        for hid, state in self.heaters.items():
            rows: list[tuple[str, dict[str, Any]]] = []
            for path in sorted(DUMPS.glob(f"watch_{safe_name(hid)}_*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("ts", "") >= cutoff and isinstance(row.get("data"), dict):
                        rows.append((row["ts"], row["data"]))
            for ts, data in sorted(rows, key=lambda r: r[0]):
                state.add(ts, data)
            print(f"  {state.name}: {len(rows)} lecturas previas cargadas")

    def poll_once(self) -> None:
        for hid, state in self.heaters.items():
            data = self.client.get(f"/heaters/{hid}")
            if not isinstance(data, dict):
                raise RuntimeError("respuesta inesperada")
            ts = dt.datetime.now().isoformat()
            DUMPS.mkdir(parents=True, exist_ok=True)
            log = DUMPS / f"watch_{safe_name(hid)}_{dt.datetime.now():%Y%m%d}.jsonl"
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": ts, "data": data}, ensure_ascii=False) + "\n")
            with self.lock:
                state.add(ts, data)
                state.trim()

    def run(self) -> None:
        while True:
            try:
                self.poll_once()
                with self.lock:
                    self.last_poll = dt.datetime.now().isoformat()
                    self.error = None
            except Exception as err:  # noqa: BLE001  (el hilo no debe morir nunca)
                with self.lock:
                    self.error = f"{type(err).__name__} ({dt.datetime.now():%H:%M:%S})"
                print(f"Error de lectura: {type(err).__name__}")
            time.sleep(self.interval)

    def snapshot(self, since: str) -> dict[str, Any]:
        with self.lock:
            return {
                "poll": {"last": self.last_poll, "error": self.error, "interval": self.interval},
                "heaters": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "latest": s.latest,
                        "history": [h for h in s.history if h["ts"] > since],
                        "changes": [c for c in s.changes if c["ts"] > since],
                        "codes": s.codes,
                    }
                    for s in self.heaters.values()
                ],
            }

def make_handler(monitor: Monitor) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, HTML_FILE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                since = parse_qs(url.query).get("since", [""])[0]
                body = json.dumps(monitor.snapshot(since), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args: Any) -> None:
            pass  # silencio: no llenar la consola

    return Handler

def main() -> int:
    parser = argparse.ArgumentParser(description="Panel local EK63 (solo lectura)")
    parser.add_argument("--interval", type=int, default=30, help="Segundos entre lecturas (mín. 15)")
    parser.add_argument("--port", type=int, default=8063)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    load_dotenv(args.env)
    email, password = os.environ.get("EK63_EMAIL"), os.environ.get("EK63_PASSWORD")
    if not email or not password:
        print("Faltan EK63_EMAIL y/o EK63_PASSWORD (entorno o .env).", file=sys.stderr)
        return 2

    client = Client(email, password)
    print("Login en Cognito…")
    try:
        client.login()
    except Exception as err:  # noqa: BLE001
        print(f"Error de login: {type(err).__name__}", file=sys.stderr)
        return 1

    monitor = Monitor(client, max(15, args.interval))
    monitor.discover()
    monitor.load_history()
    threading.Thread(target=monitor.run, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(monitor))
    print(f"Panel en http://127.0.0.1:{args.port}  (Ctrl+C para salir)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFin.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
