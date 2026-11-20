"""DeskController: one desk (one USB2LIN06 cable, one leg set), in desk heights.

Wraps linak_leg.LinakLeg, which owns the cable and all the safety behaviour (hold-to-move,
watchdog, duty cycle, software limits). This class adds desk-level ideas on top: presets,
the user's low limit, "remember my height" for scheduled moves, and reconnecting when the
cable is unplugged or the app starts before it is plugged in.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from linak_leg import LegConfig, LinakLeg, SimulatedLeg, mm_to_units

from .settings import SettingsStore, app_dir

log = logging.getLogger("hat_desk")


class DeskController:
    RECONNECT_EVERY = 5.0

    def __init__(self, store: SettingsStore, simulate: bool = False,
                 sim_start_mm: float = 80.0, sim_time_scale: float = 1.0,
                 opener: Optional[Callable[[], object]] = None):
        self.store = store
        self.simulate = simulate
        self.sim: Optional[SimulatedLeg] = None
        if simulate:
            self.sim = SimulatedLeg(start_mm=sim_start_mm, stroke_mm=store.settings.stroke_mm,
                                    time_scale=sim_time_scale)
            opener = self.sim.opener
        self._opener = opener
        self.leg: Optional[LinakLeg] = None
        self.offline_reason = "connecting..."
        self.last_result = ""            # result of the last go-to ("reached", "cancelled", ...)
        self.activity = ""               # what the desk is doing, e.g. "Going to Stand"
        self._lock = threading.RLock()
        self._goal_seq = 0
        self._listeners = []
        self._running = True
        self._connect()
        store.on_change(self.apply_limits)
        self._watch = threading.Thread(target=self._watch_loop, name="DeskReconnect", daemon=True)
        self._watch.start()

    # ---- connection ---------------------------------------------------------
    def _leg_config(self) -> LegConfig:
        s = self.store.settings
        lo, hi = s.allowed_range()
        lo_leg, hi_leg = s.to_leg_mm(lo), s.to_leg_mm(hi)
        return LegConfig(
            min_position=mm_to_units(lo_leg) if lo_leg > 0.5 else None,
            max_position=mm_to_units(hi_leg) if hi_leg < s.stroke_mm - 0.5 else None,
            log_path=None if self.simulate else f"{app_dir()}/leg_errors.log",
        )

    def _connect(self):
        kwargs = {"opener": self._opener} if self._opener else {}
        leg, why = LinakLeg.try_open(self._leg_config(), **kwargs)
        with self._lock:
            self.leg = leg
            self.offline_reason = why or ""
        if leg:
            log.info("leg connected")
        self._notify()

    def _watch_loop(self):
        while self._running:
            time.sleep(self.RECONNECT_EVERY)
            if self.leg is None and self._running:
                self._connect()

    def apply_limits(self):
        """Push the current low/high limit into the running leg (called when settings change)."""
        leg = self.leg
        if leg is None:
            return
        cfg = self._leg_config()
        leg.cfg.min_position = cfg.min_position
        leg.cfg.max_position = cfg.max_position

    def close(self):
        self._running = False
        if self.leg:
            self.leg.close()

    # ---- listeners (UI refresh) -----------------------------------------------
    def on_change(self, cb):
        self._listeners.append(cb)

    def _notify(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                log.exception("listener failed")

    # ---- state ----------------------------------------------------------------
    @property
    def online(self) -> bool:
        return self.leg is not None and self.leg.online and self.leg.pos is not None

    @property
    def height(self) -> Optional[float]:
        """Desk height in mm, or None when there is no leg."""
        leg = self.leg
        if leg is None or leg.mm is None:
            return None
        return self.store.settings.to_desk_mm(leg.mm)

    @property
    def moving(self) -> bool:
        return bool(self.leg and self.leg.moving)

    @property
    def message(self) -> str:
        if self.leg is None:
            return self.offline_reason
        if not self.leg.online:
            return "Desk not responding, reconnecting..."
        if self.leg.resting:
            return "Resting motor (duty cycle), try again in a few minutes"
        return self.activity

    def status(self) -> dict:
        s = self.store.settings
        lo, hi = s.allowed_range()
        return {
            "name": s.desk_name,
            "id": s.desk_id,
            "online": self.online,
            "height_mm": None if self.height is None else round(self.height, 1),
            "moving": self.moving,
            "message": self.message,
            "last_result": self.last_result,
            "low_mm": lo, "high_mm": hi,
            "presets": [{"name": p.name, "height_mm": p.height_mm} for p in s.presets],
            "remembered_height_mm": s.remembered_height_mm,
            "simulated": self.simulate,
        }

    # ---- manual moves -----------------------------------------------------------
    def hold(self, direction: int):
        """Call every ~100 ms while UP (+1) or DOWN (-1) is held, and with 0 on release.
        The leg stops by itself if these calls stop arriving."""
        leg = self.leg
        if leg is None:
            return
        if direction:
            self._goal_seq += 1          # a held button cancels any go-to
            self.activity = ""
        leg.command(direction)

    def stop(self):
        self._goal_seq += 1
        self.activity = ""
        if self.leg:
            self.leg.stop()
        self._notify()

    # ---- go to a height ---------------------------------------------------------
    def go_to(self, desk_mm: float, label: str = "", on_done: Optional[Callable[[str], None]] = None) -> str:
        """Start moving to a desk height (clamped to the allowed range). Returns at once with
        "started", "offline" or "resting"; `on_done(result)` is called from a worker thread."""
        leg = self.leg
        if leg is None or not leg.online:
            return "offline"
        s = self.store.settings
        lo, hi = s.allowed_range()
        desk_mm = max(lo, min(hi, float(desk_mm)))
        r = leg.go_to(mm_to_units(s.to_leg_mm(desk_mm)), wait=False)
        if r != "started":
            self.last_result = r
            self._notify()
            return r
        self._goal_seq += 1
        seq = self._goal_seq
        self.activity = f"Going to {label}" if label else f"Going to {s.fmt(desk_mm)}"
        self._notify()

        def wait():
            result = leg.wait_goal()
            if seq == self._goal_seq:
                self.activity = ""
            self.last_result = result
            log.info("go to %.0f mm (%s): %s", desk_mm, label, result)
            self._notify()
            if on_done:
                on_done(result)

        threading.Thread(target=wait, name="DeskGoTo", daemon=True).start()
        return "started"

    def go_preset(self, index: int) -> str:
        p = self.store.settings.presets[index]
        if p.height_mm is None:
            return "not set"
        return self.go_to(p.height_mm, label=p.name)

    def save_preset(self, index: int, name: Optional[str] = None) -> bool:
        h = self.height
        if h is None:
            return False

        def upd(s):
            s.presets[index].height_mm = round(h, 1)
            if name:
                s.presets[index].name = name
        self.store.update(upd)
        return True

    def run_action(self, action: str, label: str = "") -> str:
        """Run a schedule / admin action: max, min, restore, preset:N, height:MM."""
        s = self.store.settings
        lo, hi = s.allowed_range()
        if action in ("max", "min"):
            h = self.height
            # Remember where the desk was, unless it is already parked from an earlier move
            # (so two "max" rules in a row don't overwrite the real working height).
            if h is not None and s.remembered_height_mm is None:
                self.store.update(lambda st: setattr(st, "remembered_height_mm", round(h, 1)))
            return self.go_to(hi if action == "max" else lo, label=label or ("highest" if action == "max" else "lowest"))
        if action == "restore":
            target = s.remembered_height_mm
            if target is None:
                return "nothing to restore"

            def done(result):
                if result == "reached":
                    self.store.update(lambda st: setattr(st, "remembered_height_mm", None))
            return self.go_to(target, label=label or "previous height", on_done=done)
        if action.startswith("preset:"):
            return self.go_preset(int(action.split(":", 1)[1]) - 1)
        if action.startswith("height:"):
            return self.go_to(float(action.split(":", 1)[1]), label=label)
        return "unknown action"
