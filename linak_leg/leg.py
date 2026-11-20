"""LinakLeg: drives a LINAK column through a CBD6S control box and a USB2LIN06 cable.

Extracted from JViz (arm_counter.py). Behaviour is the same as the bench-tested version:
a background thread owns the cable and works like the LINAK handset. While UP or DOWN is
held it sends "keep moving up/down" every 0.1 s, and on release it sends a stop (one
command the other way, retried 0.15 s later if the leg is still moving).

The control box only keeps moving while commands keep arriving, which is a dead-man switch.
On top of that this class adds:

* watchdog: the caller must confirm the direction with command() at least every
  `watchdog` seconds, or the leg stops (a frozen UI cannot keep the leg moving);
* hold limit: a single hold stops by itself after `max_hold` seconds;
* duty cycle: moves are refused once `duty_max` seconds of running are used in
  `duty_window` seconds (DL6: 2 min in 18 min), until the leg has rested;
* glitch tolerance: single bad USB answers are ignored (normal on Windows); only
  `max_fails` in a row count as a lost cable, which is then closed and reopened;
* optional lowest / highest allowed position for every move.
"""
import collections
import datetime
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from . import protocol as P


@dataclass
class LegConfig:
    watchdog: float = 0.5           # leg stops if command() hasn't confirmed the hold for this long
    max_hold: float = 20.0          # one hold stops by itself after this
    max_fails: int = 6              # bad USB answers in a row before the leg counts as offline
    duty_window: float = 18 * 60    # DL6 duty cycle: 2 min of running in 18 min
    duty_max: float = 120.0
    min_position: Optional[int] = None   # lowest allowed position, 0.1 mm units (None = box limit)
    max_position: Optional[int] = None   # highest allowed position, 0.1 mm units
    send_every: float = 0.1         # repeat interval while moving
    idle_poll: float = 0.3          # status poll interval while idle
    stop_recheck: float = 0.15      # after a stop, check again this much later
    reconnect_wait: float = 2.0     # wait before reopening a lost cable
    goto_tolerance: int = 20        # go_to() counts as arrived within this (0.1 mm units)
    limit_margin: int = 40          # a hold stops this far before min/max_position, for the coast
    log_path: Optional[str] = "leg_errors.log"   # every USB glitch, with time (None = off)
    max_log_lines: int = 2000


def list_cables() -> list:
    """Paths of every USB2LIN06 cable plugged in (one per leg set). Empty if hidapi is missing."""
    try:
        import hid
    except ImportError:
        return []
    return [d["path"] for d in hid.enumerate(P.VENDOR_ID, P.PRODUCT_ID)]


def open_hid_device(path=None):
    """Open a USB2LIN06 cable with the hidapi package: the first one found, or the one at
    `path` (from list_cables()). Raises on failure."""
    import hid  # pip install hidapi
    dev = hid.device()
    if path is None:
        dev.open(P.VENDOR_ID, P.PRODUCT_ID)
    else:
        dev.open_path(path)
    return dev


class LinakLeg:
    MOVE_UP, MOVE_DOWN, STOPPED = P.MOVE_UP, P.MOVE_DOWN, P.STOPPED

    def __init__(self, dev, config: Optional[LegConfig] = None,
                 opener: Callable[[], object] = open_hid_device):
        self.dev, self.cfg, self._opener = dev, config or LegConfig(), opener
        self.dir, self.dir_t = 0, 0.0
        self.pos: Optional[int] = None
        self.moving = self.going_down = False
        self.target: Optional[int] = None
        self.last_status: Optional[P.Status] = None
        self.online, self.message = True, ""
        self.fails, self.errors_logged = 0, 0
        self.runs = collections.deque()          # (start, end) of recent moves, for the duty cycle
        self._run_start: Optional[float] = None
        self._hold_dir, self._hold_start = 0, 0.0
        self._lock = threading.Lock()
        self._goal: Optional[int] = None
        self._goal_deadline = 0.0
        self._goal_done = threading.Event()
        self._goal_result = ""
        self._goal_last_pos, self._goal_still, self._goal_start = None, 0, 0.0
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="LinakLeg", daemon=True)
        self.thread.start()

    # ---- opening ---------------------------------------------------------------
    @classmethod
    def try_open(cls, config: Optional[LegConfig] = None,
                 opener: Callable[[], object] = open_hid_device) -> Tuple[Optional["LinakLeg"], Optional[str]]:
        """Returns (leg, None) or (None, reason). Never raises, so an app runs without a leg."""
        try:
            dev = opener()
        except ImportError:
            return None, "USB library not installed (pip install hidapi)"
        except Exception:
            return None, "USB2LIN06 cable not found"
        return cls(dev, config, opener), None

    # ---- public API ------------------------------------------------------------
    @property
    def mm(self) -> Optional[float]:
        return None if self.pos is None else self.pos / P.UNITS_PER_MM

    @property
    def resting(self) -> bool:
        return self.message.startswith("leg resting")

    def command(self, d: int):
        """Call every UI frame: +1 hold up, -1 hold down, 0 not held.
        A held direction cancels a running go_to()."""
        self.dir, self.dir_t = (1 if d > 0 else -1 if d < 0 else 0), time.monotonic()

    def stop(self):
        """Stop any move: releases a hold and cancels a go_to()."""
        self.command(0)
        with self._lock:
            if self._goal is not None:
                self._goal_result = "cancelled"
                self._goal = None
                self._goal_done.set()

    def go_to(self, position: int, timeout: Optional[float] = None, wait: bool = True) -> str:
        """Move to a position (0.1 mm units) in the box's position mode, sending the same target
        every 0.1 s (changing it would restart the box's soft start). The target is clamped to
        min_position / max_position. Returns "reached", "stopped short", "timeout", "cancelled",
        "resting", "offline" or, with wait=False, "started"."""
        if not self.online:
            return "offline"
        if self._duty_left() <= 0:
            return "resting"
        position = self._clamp(int(position))
        if timeout is None:   # full stroke at ~38 mm/s takes ~18 s; allow for distance + margin
            dist = abs(position - (self.pos or 0)) / P.UNITS_PER_MM
            timeout = 5.0 + dist / 20.0
        with self._lock:
            self.dir = 0
            self._goal_done.clear()
            self._goal_result = ""
            self._goal = position
            self._goal_start = time.monotonic()
            self._goal_deadline = self._goal_start + timeout
            self._goal_last_pos, self._goal_still = None, 0
        if not wait:
            return "started"
        return self.wait_goal(timeout + 2.0)

    def wait_goal(self, timeout: Optional[float] = None) -> str:
        if not self._goal_done.wait(timeout):
            self.stop()
            return "timeout"
        return self._goal_result

    def settle(self, timeout: float = 2.5):
        """Stop and wait until the leg is still (e.g. before taking a photo)."""
        self.stop()
        t0 = time.monotonic()
        time.sleep(0.3)
        while self.moving and time.monotonic() - t0 < timeout:
            time.sleep(0.1)

    def run_seconds(self) -> float:
        """Seconds of running in the last duty window."""
        now = time.monotonic()
        window = self.cfg.duty_window
        while self.runs and self.runs[0][1] < now - window:
            self.runs.popleft()
        total = sum(min(e, now) - max(s, now - window) for s, e in list(self.runs))
        if self._run_start is not None:
            total += now - self._run_start
        return total

    def close(self):
        """Stop the leg and end the thread."""
        self.stop()
        time.sleep(max(0.4, self.cfg.stop_recheck + 2 * self.cfg.send_every))   # let the loop send its stop
        self.running = False
        self.thread.join(timeout=2)
        try:
            self.dev.close()
        except Exception:
            pass

    # ---- cable I/O -------------------------------------------------------------
    def _init(self):
        self.dev.send_feature_report(P.init_report())
        time.sleep(P.INIT_WAIT)

    def _status(self) -> P.Status:
        st = P.parse_status(self.dev.get_feature_report(P.REPORT_STATUS, P.REPORT_LEN))
        self.pos, self.moving, self.going_down, self.target = st.position, st.moving, st.going_down, st.target
        self.last_status = st
        return st

    def _send(self, value: int):
        self.dev.send_feature_report(P.move_report(value))

    def _stop(self):
        """Handset-style stop: one command the other way, as LINAK's own cable driver does."""
        self._status()
        if self.target == P.STOPPED and not self.moving:
            return
        self._send(P.MOVE_UP if self.going_down else P.MOVE_DOWN)

    # ---- helpers ---------------------------------------------------------------
    def _clamp(self, position: int) -> int:
        lo = self.cfg.min_position if self.cfg.min_position is not None else 0
        hi = self.cfg.max_position if self.cfg.max_position is not None else P.MAX_POSITION
        return max(lo, min(hi, position))

    def _duty_left(self) -> float:
        return self.cfg.duty_max - self.run_seconds()

    def _at_limit(self, d: int) -> bool:
        if self.pos is None:
            return False
        if d < 0 and self.cfg.min_position is not None and self.pos <= self.cfg.min_position + self.cfg.limit_margin:
            return True
        if d > 0 and self.cfg.max_position is not None and self.pos >= self.cfg.max_position - self.cfg.limit_margin:
            return True
        return False

    def _finish_goal(self, result: str):
        with self._lock:
            if self._goal is not None:
                self._goal, self._goal_result = None, result
                self._goal_done.set()

    def _hold_direction(self, now: float) -> int:
        """The direction to move now, after the watchdog, hold limit, limits and duty cycle."""
        d = self.dir if now - self.dir_t < self.cfg.watchdog else 0       # UI stalled: stop
        if d != self._hold_dir:
            self._hold_dir, self._hold_start = d, now
        if d and now - self._hold_start > self.cfg.max_hold:                # one hold, too long
            d = 0
        if d and self._at_limit(d):
            d = 0
        if d and self._duty_left() <= 0:
            d, self.message = 0, "leg resting (duty limit)"
        return d

    def _log(self, text: str):
        """Keep a record of USB problems (a fast-growing log points to a marginal cable or port)."""
        if not self.cfg.log_path or self.errors_logged >= self.cfg.max_log_lines:
            return
        self.errors_logged += 1
        try:
            with open(self.cfg.log_path, "a") as fh:
                fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S.%f} {text}\n")
        except OSError:
            pass

    # ---- background thread ----------------------------------------------------
    def _loop(self):
        try:
            self._init()
        except Exception as e:
            self.online, self.message = False, "leg offline"
            self._log(f"init failed: {type(e).__name__}: {e}")
        last_active = False
        while self.running:
            active = False
            try:
                if not self.online:                    # cable lost: close it properly, then reconnect
                    time.sleep(self.cfg.reconnect_wait)
                    if not self.running:
                        break
                    try:
                        self.dev.close()
                    except Exception:
                        pass
                    self.dev = self._opener()
                    self._init()
                    self.online, self.message, self.fails = True, "", 0
                    self._log("reconnected")
                now = time.monotonic()
                d = self._hold_direction(now)
                if d and self._goal is not None:       # a held button overrides go_to()
                    self._finish_goal("cancelled")
                goal = self._goal
                if goal is not None and self._duty_left() <= 0:
                    self.message = "leg resting (duty limit)"
                    self._finish_goal("resting")
                    goal = None
                if goal is not None and now > self._goal_deadline:
                    self._finish_goal("timeout")
                    goal = None
                active = bool(d) or goal is not None
                if d:
                    self._send(P.MOVE_UP if d > 0 else P.MOVE_DOWN)   # keep moving, like the handset
                elif goal is not None:
                    self._send(goal)                                  # same target every time
                elif last_active:                                     # let go, watchdog or limit: stop
                    self._stop()
                    time.sleep(self.cfg.stop_recheck)
                    self._status()
                    if self.moving:                                   # still going: stop once more
                        self._stop()
                if active and self._run_start is None:
                    self._run_start = time.monotonic()
                last_active = active
                self._status()
                if goal is not None:
                    self._check_goal(goal)
                if not active and self._run_start is not None and not self.moving:
                    self.runs.append((self._run_start, time.monotonic()))
                    self._run_start = None
                if not active and self.resting and self._duty_left() > 10:
                    self.message = ""
                self.fails = 0
                time.sleep(self.cfg.send_every if active else self.cfg.idle_poll)
            except Exception as e:
                # A single bad USB answer is normal on Windows: skip it and keep going (a held
                # button keeps moving). Only several failures in a row mean the cable is lost.
                self.fails += 1
                self._log(f"{type(e).__name__}: {e} (failure {self.fails} in a row)")
                if self.fails >= self.cfg.max_fails:
                    self.online, self.message, self.pos = False, "leg offline", None
                    self._finish_goal("offline")
                    if self._run_start is not None:
                        self.runs.append((self._run_start, time.monotonic()))
                        self._run_start = None
                    last_active = False
                else:
                    last_active = (active or last_active) if self.online else False  # retry a missed stop
                time.sleep(0.05)
        # thread ending: one last stop if a move was running
        try:
            if last_active and self.online:
                self._stop()
        except Exception:
            pass

    def _check_goal(self, goal: int):
        if self.pos is None:
            return
        if abs(self.pos - goal) <= self.cfg.goto_tolerance and not self.moving:
            self._finish_goal("reached")
            return
        # Not moving any more, away from the target: end of travel, blocked, or hand control.
        if self.pos == self._goal_last_pos and not self.moving:
            self._goal_still += 1
        else:
            self._goal_still = 0
        self._goal_last_pos = self.pos
        if self._goal_still >= 5 and time.monotonic() - self._goal_start > 1.0:
            self._finish_goal("stopped short")
