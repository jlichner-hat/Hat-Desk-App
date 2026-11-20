"""Clock-based moves, run on each desk's own clock.

Schedules are set by an admin (pushed over the network, see agent.py) and stored in the
desk's settings, so they keep working when the admin PC is off or the network is down.

Each rule fires once per day at its time. If the PC was asleep or the desk was busy at that
moment, it still fires up to GRACE minutes late, never later (a desk shouldn't suddenly rise
at 9 AM because the PC was off at 5:30 PM). Before moving, the desk shows a notice for
`warn_seconds` with a "Skip" button, so someone working late isn't surprised.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Callable, Dict, Optional, Tuple

from .settings import ScheduleRule, SettingsStore

log = logging.getLogger("hat_desk")

GRACE = dt.timedelta(minutes=20)


def _rule_time(rule: ScheduleRule, day: dt.date) -> dt.datetime:
    h, m = (int(x) for x in rule.time.split(":"))
    return dt.datetime.combine(day, dt.time(h, m))


class Scheduler:
    TICK = 5.0

    def __init__(self, store: SettingsStore, controller, now: Callable[[], dt.datetime] = dt.datetime.now):
        self.store, self.ctl, self.now = store, controller, now
        self.done: Dict[Tuple[str, dt.date], str] = {}       # (rule id, day) -> outcome
        self.skipped: set = set()                            # (rule id, day)
        self.on_warning: Optional[Callable[[ScheduleRule, int], None]] = None   # rule, seconds left
        self.on_fired: Optional[Callable[[ScheduleRule, str], None]] = None
        self._warned: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- control ---------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="DeskScheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.TICK):
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    def skip(self, rule_id: str, day: Optional[dt.date] = None):
        """Skip one rule for today (the "Skip" button on the notice)."""
        self.skipped.add((rule_id, day or self.now().date()))
        log.info("schedule %s skipped for %s", rule_id, day or self.now().date())

    # ---- the clock ---------------------------------------------------------------
    def next_runs(self, limit: int = 5):
        """Upcoming (datetime, rule) pairs, soonest first, for display."""
        now = self.now()
        out = []
        for rule in self.store.settings.schedules:
            if not rule.enabled or not rule.days:
                continue
            for k in range(8):
                day = now.date() + dt.timedelta(days=k)
                t = _rule_time(rule, day)
                if day.weekday() in rule.days and t > now and (rule.id, day) not in self.skipped:
                    out.append((t, rule))
                    break
        return sorted(out, key=lambda x: x[0])[:limit]

    def tick(self):
        now = self.now()
        s = self.store.settings
        for rule in list(s.schedules):
            if not rule.enabled:
                continue
            # today's run, and yesterday's in case the grace window crosses midnight
            for day in (now.date(), now.date() - dt.timedelta(days=1)):
                if day.weekday() not in rule.days:
                    continue
                key = (rule.id, day)
                if key in self.done or key in self.skipped:
                    continue
                t = _rule_time(rule, day)
                lead = dt.timedelta(seconds=max(0, rule.warn_seconds))
                if t - lead <= now < t and key not in self._warned:
                    self._warned.add(key)
                    if self.on_warning:
                        self.on_warning(rule, int((t - now).total_seconds()))
                if not (t <= now < t + GRACE):
                    continue
                if self.ctl.moving:
                    continue                      # someone is using the desk; try again next tick
                result = self.ctl.run_action(rule.action, label=rule.name)
                log.info("schedule %s (%s) at %s: %s", rule.name, rule.action, now, result)
                if result in ("offline", "resting"):
                    continue                      # retry until the grace window ends
                self.done[key] = result
                if self.on_fired:
                    self.on_fired(rule, result)
        # forget old days
        cutoff = now.date() - dt.timedelta(days=3)
        self.done = {k: v for k, v in self.done.items() if k[1] >= cutoff}
        self.skipped = {k for k in self.skipped if k[1] >= cutoff}
        self._warned = {k for k in self._warned if k[1] >= cutoff}
