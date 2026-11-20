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
    desk_id: str = ""            # the desk's own id, from discovery or /status

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def loopback(self) -> bool:
        return self.host.startswith("127.") or self.host in ("localhost", "::1")


def merge_desks(existing: List[DeskEntry], found: List[DeskEntry]) -> List[DeskEntry]:
    """Add newly found desks to the list, one row per desk.

    A desk on the admin PC itself answers discovery twice (on its LAN address and on
    127.0.0.1), so desks are matched by their id, and a LAN address wins over 127.0.0.1
    because other admin PCs can use it too."""
    out = [DeskEntry(**vars(d)) for d in existing]
    for f in found:
        same = [d for d in out if (f.desk_id and d.desk_id == f.desk_id) or (d.host, d.port) == (f.host, f.port)]
        if not same:
            out.append(DeskEntry(**vars(f)))       # a copy: the merge below mustn't edit the input
            continue
        d = same[0]
        d.desk_id = d.desk_id or f.desk_id
        if d.loopback and not f.loopback:
            d.host, d.port = f.host, f.port
    return dedupe_desks(out)


def dedupe_desks(desks: List[DeskEntry]) -> List[DeskEntry]:
    """Drop repeat rows for the same desk id (keeping a LAN address over 127.0.0.1)."""
    by_id, out = {}, []
    for d in desks:
        if not d.desk_id:
            out.append(d)
            continue
        keep = by_id.get(d.desk_id)
        if keep is None:
            by_id[d.desk_id] = d
            out.append(d)
        elif keep.loopback and not d.loopback:
            keep.host, keep.port = d.host, d.port
            keep.selected = keep.selected or d.selected
        else:
            keep.selected = keep.selected or d.selected
    return out


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
                                                          port=int(d["port"]), desk_id=str(d.get("id") or ""))
    finally:
        s.close()
    return merge_desks([], list(found.values()))


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
