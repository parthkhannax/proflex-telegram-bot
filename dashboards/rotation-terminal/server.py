#!/usr/bin/env python3
"""Proflex Rotation Terminal — http://localhost:11001

The pattern screens are fed by a store on disk that only grows when the engine
runs. Leaving that to whoever happens to open the page would mean a day nobody
looked at the terminal is a day permanently missing from the history — and once
it ages out of Yahoo's 60-day intraday window it cannot be recovered. So the
server harvests on its own schedule, and the page is just a reader.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import engine

PORT = 11001
HERE = Path(__file__).parent

ET = timezone(timedelta(hours=-4))
HARVEST_EVERY = 900          # seconds between harvests inside the window
WINDOW = (9, 18)             # ET hours the harvester is awake, inclusive-exclusive
IDLE_SLEEP = 600             # poll interval when outside the window


def _harvester():
    """Refresh the engine on a schedule so every session lands in the store.

    The window runs to 18:00 ET rather than the 16:00 close on purpose: the last
    hourly bar is only final after the bell, and a session captured while it is
    still in progress is partial. Because the store is last-write-wins over a
    60-day refetch, those post-close passes overwrite the partial capture with
    the complete one."""
    while True:
        now = datetime.now(ET)
        weekday = now.weekday() < 5
        in_window = WINDOW[0] <= now.hour < WINDOW[1]
        if weekday and in_window:
            try:
                engine.get(force=True)
                h = engine.harvest_state()
                print(f"[harvest] {now:%Y-%m-%d %H:%M ET} — store holds "
                      f"{h['sessions']} sessions ({h['wrote']} refreshed)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[harvest] {now:%Y-%m-%d %H:%M ET} — FAILED: {e}", flush=True)
            time.sleep(HARVEST_EVERY)
        else:
            time.sleep(IDLE_SLEEP)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/rotation"):
            try:
                data = engine.get(force="refresh=1" in self.path)
                self._send(200, json.dumps(data).encode(), "application/json")
            except Exception as e:  # noqa: BLE001
                self._send(500, json.dumps({"error": str(e)}).encode(),
                           "application/json")
            return
        if self.path.startswith("/api/harvest"):
            self._send(200, json.dumps(engine.harvest_state()).encode(),
                       "application/json")
            return
        html = (HERE / "index.html").read_bytes()
        self._send(200, html, "text/html; charset=utf-8")


if __name__ == "__main__":
    threading.Thread(target=_harvester, daemon=True).start()
    print(f"Proflex Rotation Terminal → http://localhost:{PORT}")
    print(f"[harvest] every {HARVEST_EVERY//60}min, weekdays "
          f"{WINDOW[0]:02d}:00–{WINDOW[1]:02d}:00 ET", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
