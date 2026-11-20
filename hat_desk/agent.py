"""The desk's network agent: lets an admin console see and control this desk over the office LAN.

- HTTP on `agent_port` (default 47820). Every request must carry the office admin key in the
  `X-HAT-Key` header. The key is set once per desk at install time (`--admin-key`), and the
  desk's own user never sees it in the app.
- UDP discovery on port 47821: the admin console broadcasts `HATDESK?` and each desk answers
  with its name and port, so desks don't need to be typed in by IP address.

Endpoints:
    GET  /status                 height, online, presets, limits ...
    POST /action  {"action": "max" | "min" | "restore" | "preset:N" | "height:MM"}
    POST /stop
    GET  /schedules
    PUT  /schedules {"schedules": [...], "managed_by": "Facilities"}
"""
from __future__ import annotations

import hmac
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .settings import ScheduleRule, SettingsStore

log = logging.getLogger("hat_desk")

DISCOVERY_PORT = 47821
DISCOVERY_PROBE = b"HATDESK?"
VALID_ACTIONS = ("max", "min", "restore")


def valid_action(a: str) -> bool:
    if a in VALID_ACTIONS:
        return True
    if a.startswith("preset:"):
        return a.split(":", 1)[1] in ("1", "2", "3")
    if a.startswith("height:"):
        try:
            float(a.split(":", 1)[1])
            return True
        except ValueError:
            return False
    return False


def parse_schedules(items) -> list:
    """Validate schedules sent by an admin. Raises ValueError with a readable reason."""
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("schedules must be a list of at most 50 rules")
    out = []
    for i, r in enumerate(items):
        try:
            rule = ScheduleRule(id=str(r["id"]), name=str(r.get("name", ""))[:60], time=str(r["time"]),
                                days=[int(d) for d in r.get("days", [0, 1, 2, 3, 4])],
                                action=str(r["action"]), enabled=bool(r.get("enabled", True)),
                                warn_seconds=int(r.get("warn_seconds", 60)))
            h, m = (int(x) for x in rule.time.split(":"))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"rule {i + 1}: needs id, time (HH:MM) and action")
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"rule {i + 1}: bad time {rule.time}")
        rule.time = f"{h:02d}:{m:02d}"
        if any(d < 0 or d > 6 for d in rule.days):
            raise ValueError(f"rule {i + 1}: days are 0 (Mon) to 6 (Sun)")
        if not valid_action(rule.action):
            raise ValueError(f"rule {i + 1}: unknown action {rule.action}")
        rule.warn_seconds = max(0, min(600, rule.warn_seconds))
        out.append(rule)
    return out


class _Handler(BaseHTTPRequestHandler):
    server_version = "HATDesk/1"
    agent: "DeskAgent"

    def log_message(self, fmt, *args):
        log.debug("agent %s - " + fmt, self.address_string(), *args)

    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        key = self.headers.get("X-HAT-Key", "")
        s = self.agent.store.settings
        if not s.admin_key_set:
            # The desk only has its random placeholder key: nobody can know it, so say so
            # instead of "wrong key", which sends people hunting for a typo.
            self._send(401, {"error": "admin key not set on this desk"})
            return False
        if hmac.compare_digest(key.encode(), s.admin_key.encode()):
            return True
        self._send(401, {"error": "wrong admin key"})
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._authorised():
            return
        a = self.agent
        if self.path == "/status":
            self._send(200, a.ctl.status())
        elif self.path == "/schedules":
            s = a.store.settings
            self._send(200, {"schedules": [r.__dict__ for r in s.schedules], "managed_by": s.managed_by})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorised():
            return
        a = self.agent
        try:
            body = self._body()
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        if self.path == "/action":
            action = str(body.get("action", ""))
            if not valid_action(action):
                return self._send(400, {"error": f"unknown action {action}"})
            result = a.ctl.run_action(action, label=body.get("label") or "")
            log.info("admin %s: %s -> %s", self.client_address[0], action, result)
            self._send(200, {"result": result})
        elif self.path == "/stop":
            a.ctl.stop()
            self._send(200, {"result": "stopped"})
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self):
        if not self._authorised():
            return
        if self.path != "/schedules":
            return self._send(404, {"error": "not found"})
        try:
            body = self._body()
            rules = parse_schedules(body.get("schedules"))
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        managed_by = str(body.get("managed_by", "Admin"))[:60]

        def upd(s):
            s.schedules = rules
            s.managed_by = managed_by
        self.agent.store.update(upd)
        log.info("admin %s pushed %d schedules", self.client_address[0], len(rules))
        self._send(200, {"result": "saved", "count": len(rules)})


class DeskAgent:
    def __init__(self, store: SettingsStore, controller, host: str = "0.0.0.0",
                 port: Optional[int] = None, discovery: bool = True):
        self.store, self.ctl = store, controller
        handler = type("Handler", (_Handler,), {"agent": self})
        self.httpd = ThreadingHTTPServer((host, store.settings.agent_port if port is None else port), handler)
        self.port = self.httpd.server_address[1]
        self._udp: Optional[socket.socket] = None
        self._discovery = discovery

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, name="DeskAgentHTTP", daemon=True).start()
        if self._discovery:
            try:
                self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._udp.bind(("", DISCOVERY_PORT))
                threading.Thread(target=self._discovery_loop, name="DeskAgentUDP", daemon=True).start()
            except OSError as e:
                log.warning("discovery not available: %s", e)
                self._udp = None
        log.info("agent listening on port %d", self.port)

    def _discovery_loop(self):
        while self._udp:
            try:
                data, addr = self._udp.recvfrom(256)
            except OSError:
                return
            if data.strip() == DISCOVERY_PROBE:
                reply = json.dumps({"hatdesk": 1, "name": self.store.settings.desk_name,
                                    "id": self.store.settings.desk_id, "port": self.port}).encode()
                try:
                    self._udp.sendto(reply, addr)
                except OSError:
                    pass

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._udp:
            u, self._udp = self._udp, None
            u.close()
