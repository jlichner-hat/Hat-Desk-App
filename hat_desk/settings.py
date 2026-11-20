"""Desk settings, saved as JSON in the user's app-data folder.

Heights are stored as *desk heights* in millimetres (floor to desktop), which is what people
see. The leg itself counts from 0 at its lowest point; `height_offset_mm` is the desk height
when the leg is fully down, so desk height = offset + leg position.
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from typing import List, Optional

APP_NAME = "HAT Desk"


def app_dir() -> str:
    """%APPDATA%\\HAT Desk on Windows, ~/.config/hat-desk elsewhere. HAT_DESK_HOME overrides."""
    if os.environ.get("HAT_DESK_HOME"):
        d = os.environ["HAT_DESK_HOME"]
    elif sys.platform == "win32":
        d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    else:
        d = os.path.join(os.path.expanduser("~"), ".config", "hat-desk")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class Preset:
    name: str
    height_mm: Optional[float] = None    # None = not programmed yet


@dataclass
class ScheduleRule:
    """One clock-based move, set by an admin. Runs on the desk's own clock."""
    id: str
    name: str
    time: str                            # "HH:MM", 24 h, local time of the desk PC
    days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])   # 0 = Monday
    action: str = "max"                  # max | min | restore | preset:1..3 | height:<mm>
    enabled: bool = True
    warn_seconds: int = 60               # desk shows a "moving in ..." notice first

    def describe_action(self) -> str:
        a = self.action
        if a == "max":
            return "Raise to highest (remember height)"
        if a == "min":
            return "Lower to lowest (remember height)"
        if a == "restore":
            return "Return to remembered height"
        if a.startswith("preset:"):
            return f"Go to preset {a.split(':', 1)[1]}"
        if a.startswith("height:"):
            return f"Go to {a.split(':', 1)[1]} mm"
        return a


ACTIONS = ["max", "restore", "min", "preset:1", "preset:2", "preset:3"]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Settings:
    desk_name: str = ""
    height_offset_mm: float = 640.0      # desk height with the leg fully down (calibrate in Settings)
    stroke_mm: float = 665.0             # DL6 stroke
    low_limit_mm: Optional[float] = None # user's lowest allowed desk height (None = leg's lowest)
    high_limit_mm: Optional[float] = None
    units: str = "cm"                    # cm | in
    presets: List[Preset] = field(default_factory=lambda: [Preset("Sit"), Preset("Stand"), Preset("3")])
    # --- admin-managed ---
    schedules: List[ScheduleRule] = field(default_factory=list)
    managed_by: str = ""                 # name of the admin console that last pushed schedules
    remembered_height_mm: Optional[float] = None   # height before a scheduled max/min move
    skip_until: Optional[str] = None     # ISO time; scheduled moves are skipped until then
    # --- network ---
    agent_enabled: bool = True
    agent_port: int = 47820
    admin_key: str = ""                  # shared secret; admin console must send it
    start_with_windows: bool = True

    # ---- derived ----------------------------------------------------------
    @property
    def min_height(self) -> float:
        return self.height_offset_mm

    @property
    def max_height(self) -> float:
        return self.height_offset_mm + self.stroke_mm

    def allowed_range(self):
        lo = self.min_height if self.low_limit_mm is None else max(self.min_height, self.low_limit_mm)
        hi = self.max_height if self.high_limit_mm is None else min(self.max_height, self.high_limit_mm)
        return lo, hi

    def to_leg_mm(self, desk_mm: float) -> float:
        return desk_mm - self.height_offset_mm

    def to_desk_mm(self, leg_mm: float) -> float:
        return leg_mm + self.height_offset_mm

    def fmt(self, desk_mm: Optional[float], unit: bool = True) -> str:
        if desk_mm is None:
            return "--"
        if self.units == "in":
            s = f"{desk_mm / 25.4:.1f}"
            return s + (" in" if unit else "")
        s = f"{desk_mm / 10:.1f}"
        return s + (" cm" if unit else "")

    # ---- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        d = dict(d)
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        if "presets" in d:
            d["presets"] = [Preset(**p) for p in d["presets"]][:3]
            while len(d["presets"]) < 3:
                d["presets"].append(Preset(str(len(d["presets"]) + 1)))
        if "schedules" in d:
            d["schedules"] = [ScheduleRule(**r) for r in d["schedules"]]
        return cls(**d)


class SettingsStore:
    """Thread-safe load/save. The UI, scheduler and network agent all share one store."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(app_dir(), "settings.json")
        self._lock = threading.RLock()
        self._listeners = []
        self.settings = self._load()

    def _load(self) -> Settings:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                s = Settings.from_dict(json.load(f))
        except FileNotFoundError:
            s = Settings()
        except Exception:
            # Corrupt file: keep a copy, start fresh rather than refusing to run.
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass
            s = Settings()
        changed = False
        if not s.desk_name:
            s.desk_name = os.environ.get("COMPUTERNAME") or platform.node() or "Desk"
            changed = True
        if not s.admin_key:
            s.admin_key = secrets.token_urlsafe(16)
            changed = True
        if changed:
            self._write(s)
        return s

    def _write(self, s: Settings):
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(s.to_dict(), f, indent=2)
        os.replace(tmp, self.path)

    def save(self):
        with self._lock:
            self._write(self.settings)
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def update(self, fn):
        """Change settings under the lock, then save and notify: store.update(lambda s: ...)."""
        with self._lock:
            fn(self.settings)
        self.save()

    def on_change(self, cb):
        self._listeners.append(cb)
