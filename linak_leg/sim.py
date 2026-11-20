"""Simulated CBD6S control box + DL6 column behind a USB2LIN06 cable.

It has the same methods as a `hid.device` (send_feature_report, get_feature_report,
close), so LinakLeg and the test tool run against it unchanged. It models what mattered
on the bench:

* ~38 mm/s top speed with a soft start, and a soft stop;
* dead-man: the box stops when commands stop arriving;
* handset codes 0x8000 / 0x7FFF, and the handset-style stop (one opposite code);
* position mode, where every *new* target restarts the soft start (the cause of the
  "barely moves" bug on the bench);
* end-of-travel limits (0 to 665 mm);
* optional failed USB answers (empty reads), as seen on Windows.
"""
import random
import threading
import time
from typing import Optional

from . import protocol as P


class SimulatedLeg:
    def __init__(self, stroke_mm: float = 665.0, speed_mm_s: float = 38.0, soft_start: float = 0.5,
                 soft_stop_mm: float = 3.0, deadman: float = 0.3, start_mm: float = 0.0,
                 fail_rate: float = 0.0, seed: Optional[int] = None, time_scale: float = 1.0):
        self.max_pos = stroke_mm * P.UNITS_PER_MM
        self.top_speed = speed_mm_s * P.UNITS_PER_MM          # units / s
        self.soft_start, self.soft_stop = soft_start, soft_stop_mm * P.UNITS_PER_MM
        self.deadman, self.fail_rate = deadman, fail_rate
        self.time_scale = time_scale
        self.rng = random.Random(seed)
        self.pos = start_mm * P.UNITS_PER_MM
        self.cmd: Optional[int] = None       # current command: MOVE_UP, MOVE_DOWN or a position
        self.cmd_t = 0.0                      # when the current command last arrived
        self.ramp_t = 0.0                     # when the soft start began
        self.dir = 0                          # +1 up, -1 down, 0 still
        self.going_down = False
        self.lockout_until = 0.0              # after a handset stop, ignore moves briefly
        self.initialised = False
        self.closed = False
        self.sent = []                        # every move value received, for tests
        self._lock = threading.Lock()
        self._t = self._now()

    # ---- hid.device interface ----------------------------------------------------
    def open(self, vid=None, pid=None):
        self.closed = False

    def close(self):
        self.closed = True

    def send_feature_report(self, buf):
        self._check_open()
        with self._lock:
            self._advance()
            if buf[0] == P.REPORT_INIT:
                if list(buf[1:4]) == [0x04, 0x00, 0xFB]:
                    self.initialised = True
                return len(buf)
            if buf[0] != P.REPORT_MOVE:
                raise IOError(f"unknown report {buf[0]}")
            value = buf[1] | (buf[2] << 8)
            if [buf[i] | (buf[i + 1] << 8) for i in (3, 5, 7)] != [value] * 3:
                raise IOError("move value not repeated 4 times")
            self.sent.append(value)
            if not self.initialised:
                return len(buf)
            self._command(value)
            return len(buf)

    def get_feature_report(self, report_id, length):
        self._check_open()
        if report_id != P.REPORT_STATUS:
            raise IOError(f"unknown report {report_id}")
        if self.fail_rate and self.rng.random() < self.fail_rate:
            return []                                   # a glitch, like hidapi on Windows
        with self._lock:
            self._advance()
            raw = [0] * length
            raw[0] = P.REPORT_STATUS
            p = int(round(self.pos))
            raw[4], raw[5] = p & 0xFF, (p >> 8) & 0xFF
            raw[6] = P.FLAG_DOWN if self.going_down else 0
            raw[7] = 1 if self.dir else 0
            tgt = self.cmd if (self.cmd is not None and self.dir) else P.STOPPED
            raw[20], raw[21] = tgt & 0xFF, (tgt >> 8) & 0xFF
            return raw

    # ---- helpers for tests -------------------------------------------------------
    @property
    def mm(self) -> float:
        return self.pos / P.UNITS_PER_MM

    @property
    def moving(self) -> bool:
        with self._lock:
            self._advance()
            return self.dir != 0

    def opener(self):
        """Use as LinakLeg(..., opener=sim.opener) so a reconnect reopens this same leg."""
        self.open()
        return self

    # ---- physics ----------------------------------------------------------------
    def _now(self):
        return time.monotonic() * self.time_scale

    def _check_open(self):
        if self.closed:
            raise IOError("device closed")

    def _command(self, value):
        now = self._t
        if value in (P.MOVE_UP, P.MOVE_DOWN):
            want = 1 if value == P.MOVE_UP else -1
            if self.dir and want != self.dir:           # handset-style stop
                self._halt()
                self.lockout_until = now + 0.3
                return
            if now < self.lockout_until:
                return
        if value != self.cmd or not self.dir:
            if value not in (P.MOVE_UP, P.MOVE_DOWN) and value == self.cmd and self._at_goal(value):
                self.cmd_t = now                         # still there: nothing to do
                return
            self.cmd, self.ramp_t = value, now           # a new target restarts the soft start
        self.cmd_t = now
        self._set_dir()

    def _at_goal(self, value):
        return abs(self.pos - min(value, self.max_pos)) < 1

    def _set_dir(self):
        if self.cmd == P.MOVE_UP:
            self.dir = 1
        elif self.cmd == P.MOVE_DOWN:
            self.dir = -1
        elif self.cmd is not None:
            goal = min(self.cmd, self.max_pos)
            self.dir = 0 if self._at_goal(goal) else (1 if goal > self.pos else -1)
        if self.dir:
            self.going_down = self.dir < 0

    def _halt(self):
        self.dir, self.cmd = 0, None

    def _speed(self, t):
        ramp = min(1.0, max(0.0, (t - self.ramp_t) / self.soft_start)) if self.soft_start else 1.0
        return self.top_speed * max(0.05, ramp)

    def _advance(self):
        """Move the simulated leg forward to now, in small steps."""
        now = self._now()
        t = self._t
        while t < now and self.dir:
            dt = min(0.01, now - t)
            t += dt
            if t - self.cmd_t > self.deadman:          # commands stopped arriving: dead-man stop
                self._halt()
                break
            v = self._speed(t)
            if self.cmd not in (P.MOVE_UP, P.MOVE_DOWN):
                goal = min(self.cmd, self.max_pos)
                left = abs(goal - self.pos)
                if left < self.soft_stop:                # ease off approaching the target
                    v = max(v * left / self.soft_stop, 0.2 * self.top_speed)
                step = min(left, v * dt)
                self.pos += step * self.dir
                if abs(goal - self.pos) < 1:
                    self.pos = goal
                    self._halt()
                    break
            else:
                self.pos += v * dt * self.dir
            if self.pos <= 0 or self.pos >= self.max_pos:  # end of travel
                self.pos = min(self.max_pos, max(0.0, self.pos))
                self._halt()
                break
        self._t = now
