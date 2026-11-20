"""Talking to desks from the admin console: discovery, status, moves, schedules."""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .agent import DISCOVERY_PORT, DISCOVERY_PROBE
from .settings import ScheduleRule, app_dir


@dataclass
class DeskEntry:
    name: str
    host: str
    port: int = 47820
    selected: bool = True

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class AdminSettings:
    admin_key: str = ""
    console_name: str = "Facilities"
    desks: List[DeskEntry] = field(default_factory=list)
    schedules: List[ScheduleRule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AdminSettings":
        path = path or os.path.join(app_dir(), "admin.json")
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return cls(admin_key=d.get("admin_key", ""), console_name=d.get("console_name", "Facilities"),
                       desks=[DeskEntry(**x) for x in d.get("desks", [])],
                       schedules=[ScheduleRule(**x) for x in d.get("schedules", [])])
        except FileNotFoundError:
            return cls()

    def save(self, path: Optional[str] = None):
        path = path or os.path.join(app_dir(), "admin.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)


def discover(timeout: float = 1.5, port: int = DISCOVERY_PORT) -> List[DeskEntry]:
    """Broadcast on the LAN and collect the desks that answer."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
        for target in ("255.255.255.255", "127.0.0.1"):
            try:
                s.sendto(DISCOVERY_PROBE, (target, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            try:
                d = json.loads(data)
            except ValueError:
                continue
            if d.get("hatdesk"):
                host = addr[0]
                found[(host, int(d["port"]))] = DeskEntry(name=str(d.get("name") or host), host=host,
                                                          port=int(d["port"]))
    finally:
        s.close()
    return list(found.values())


class DeskError(Exception):
    pass


class DeskClient:
    def __init__(self, desk: DeskEntry, key: str, timeout: float = 3.0):
        self.desk, self.key, self.timeout = desk, key, timeout

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://{self.desk.address}{path}", data=data, method=method,
                                     headers={"X-HAT-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except Exception:
                msg = str(e)
            raise DeskError(msg)
        except (urllib.error.URLError, OSError) as e:
            raise DeskError("not reachable") from e

    def status(self) -> dict:
        return self._call("GET", "/status")

    def action(self, action: str, label: str = "") -> str:
        return self._call("POST", "/action", {"action": action, "label": label}).get("result", "")

    def stop(self) -> str:
        return self._call("POST", "/stop", {}).get("result", "")

    def get_schedules(self) -> dict:
        return self._call("GET", "/schedules")

    def put_schedules(self, rules: List[ScheduleRule], managed_by: str) -> dict:
        return self._call("PUT", "/schedules", {"schedules": [asdict(r) for r in rules],
                                                "managed_by": managed_by})
