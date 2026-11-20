import datetime as dt
import json
import time

import pytest

from hat_desk.agent import DeskAgent, parse_schedules
from hat_desk.admin_client import DeskClient, DeskEntry, DeskError
from hat_desk.scheduler import Scheduler
from hat_desk.settings import ScheduleRule, Settings, SettingsStore


def wait_for(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def wait_still(desk, timeout=30.0):
    assert wait_for(lambda: desk.online, 5)
    time.sleep(0.4)
    assert wait_for(lambda: not desk.moving and desk.activity == "", timeout)


# ---- settings ---------------------------------------------------------------------
def test_settings_roundtrip(store):
    store.update(lambda s: setattr(s.presets[1], "height_mm", 1100.0))
    again = SettingsStore(store.path)
    assert again.settings.presets[1].height_mm == 1100.0
    assert again.settings.admin_key == store.settings.admin_key and len(store.settings.admin_key) > 10


def test_corrupt_settings_start_fresh(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json")
    s = SettingsStore(str(p))
    assert s.settings.presets[0].name == "Sit"
    assert (tmp_path / "settings.json.bad").exists()


def test_allowed_range_and_format():
    s = Settings(height_offset_mm=640, stroke_mm=665, low_limit_mm=700)
    assert s.allowed_range() == (700, 1305)
    assert s.fmt(725) == "72.5 cm"
    s.units = "in"
    assert s.fmt(762) == "30.0 in"


# ---- controller on the simulated leg ------------------------------------------------
def test_height_reads_desk_height(desk):
    assert wait_for(lambda: desk.height is not None, 5)
    assert desk.height == pytest.approx(640 + 80, abs=1)


def test_preset_save_and_go(desk, store):
    assert wait_for(lambda: desk.online, 5)
    assert desk.save_preset(0)
    assert store.settings.presets[0].height_mm == pytest.approx(720, abs=1)
    assert desk.go_preset(1) == "not set"
    store.update(lambda s: setattr(s.presets[1], "height_mm", 900.0))
    assert desk.go_preset(1) == "started"
    wait_still(desk)
    assert desk.last_result == "reached"
    assert desk.height == pytest.approx(900, abs=3)


def test_low_limit_clamps_go_to(desk, store):
    store.update(lambda s: setattr(s, "low_limit_mm", 700.0))
    assert desk.go_to(500) == "started"
    wait_still(desk)
    assert desk.height == pytest.approx(700, abs=3)


def test_low_limit_stops_hold(store):
    # Real-time simulation: at the 4x speed of the `desk` fixture the leg covers 15 mm per
    # 0.1 s status poll, which says more about the test clock than about the stop.
    from hat_desk.controller import DeskController
    desk = DeskController(store, simulate=True, sim_start_mm=80, sim_time_scale=1.0)
    try:
        _hold_down_to_limit(desk, store)
    finally:
        desk.close()


def _hold_down_to_limit(desk, store):
    store.update(lambda s: setattr(s, "low_limit_mm", 700.0))
    assert wait_for(lambda: desk.online, 5)
    end = time.monotonic() + 3
    while time.monotonic() < end:
        desk.hold(-1)
        time.sleep(0.1)
    desk.hold(0)
    wait_still(desk)
    # The leg stops limit_margin (4 mm) early to allow for the coast; allow 2 mm of timer jitter.
    assert desk.height >= 700 - 2


def test_max_then_restore(desk, store):
    assert wait_for(lambda: desk.online, 5)
    assert desk.run_action("max") == "started"
    assert store.settings.remembered_height_mm == pytest.approx(720, abs=1)
    wait_still(desk)
    assert desk.height == pytest.approx(640 + 665, abs=3)
    # a second "max" must not overwrite the real working height
    desk.run_action("max")
    wait_still(desk)
    assert store.settings.remembered_height_mm == pytest.approx(720, abs=1)
    assert desk.run_action("restore") == "started"
    wait_still(desk)
    assert desk.height == pytest.approx(720, abs=3)
    assert wait_for(lambda: store.settings.remembered_height_mm is None, 3)
    assert desk.run_action("restore") == "nothing to restore"


# ---- scheduler ----------------------------------------------------------------------
class FakeDesk:
    def __init__(self):
        self.actions, self.moving, self.reply = [], False, "started"

    def run_action(self, action, label=""):
        self.actions.append(action)
        return self.reply


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make_sched(store, now):
    store.update(lambda s: setattr(s, "schedules", [
        ScheduleRule(id="clean", name="Cleaning", time="17:30", days=[0, 1, 2, 3, 4], action="max"),
        ScheduleRule(id="morning", name="Morning", time="06:30", days=[0, 1, 2, 3, 4], action="restore"),
    ]))
    fake, clock = FakeDesk(), Clock(now)
    return Scheduler(store, fake, now=clock), fake, clock


FRI = dt.datetime(2026, 11, 20)   # a Friday


def test_schedule_fires_once_at_time(store):
    sched, fake, clock = make_sched(store, FRI.replace(hour=17, minute=29))
    warnings = []
    sched.on_warning = lambda r, s: warnings.append((r.id, s))
    sched.tick()
    assert fake.actions == [] and warnings == [("clean", 60)]
    clock.t = FRI.replace(hour=17, minute=30, second=5)
    sched.tick()
    sched.tick()
    assert fake.actions == ["max"]


def test_schedule_grace_window(store):
    sched, fake, clock = make_sched(store, FRI.replace(hour=17, minute=45))   # PC woke 15 min late
    sched.tick()
    assert fake.actions == ["max"]
    sched2, fake2, _ = make_sched(store, FRI.replace(hour=19, minute=0))      # far too late
    sched2.tick()
    assert fake2.actions == []


def test_schedule_respects_days_and_skip(store):
    sat = FRI + dt.timedelta(days=1)
    sched, fake, clock = make_sched(store, sat.replace(hour=17, minute=31))
    sched.tick()
    assert fake.actions == []
    clock.t = FRI.replace(hour=17, minute=29)
    sched.skip("clean")
    clock.t = FRI.replace(hour=17, minute=31)
    sched.tick()
    assert fake.actions == []


def test_schedule_waits_while_desk_in_use_and_retries_offline(store):
    sched, fake, clock = make_sched(store, FRI.replace(hour=17, minute=30))
    fake.moving = True
    sched.tick()
    assert fake.actions == []
    fake.moving, fake.reply = False, "offline"
    clock.t = FRI.replace(hour=17, minute=32)
    sched.tick()
    fake.reply = "started"
    clock.t = FRI.replace(hour=17, minute=33)
    sched.tick()
    sched.tick()
    assert fake.actions == ["max", "max"]


def test_next_runs(store):
    sched, _, _ = make_sched(store, FRI.replace(hour=18))
    runs = sched.next_runs()
    assert runs[0][1].id == "morning"
    assert runs[0][0] == dt.datetime(2026, 11, 23, 6, 30)   # Monday, not Saturday


# ---- network agent ------------------------------------------------------------------
@pytest.fixture
def agent(store, desk):
    a = DeskAgent(store, desk, host="127.0.0.1", port=0, discovery=False)
    a.start()
    yield a
    a.stop()


def test_agent_requires_key(agent):
    bad = DeskClient(DeskEntry("d", "127.0.0.1", agent.port), "wrong")
    with pytest.raises(DeskError, match="wrong admin key"):
        bad.status()


def test_agent_status_action_and_schedules(agent, store, desk):
    c = DeskClient(DeskEntry("d", "127.0.0.1", agent.port), store.settings.admin_key)
    assert wait_for(lambda: c.status()["online"], 5)
    assert c.status()["height_mm"] == pytest.approx(720, abs=1)
    assert c.action("height:800") == "started"
    wait_still(desk)
    assert desk.height == pytest.approx(800, abs=3)
    rules = [ScheduleRule(id="a", name="Cleaning", time="17:30", action="max")]
    assert c.put_schedules(rules, "Facilities")["count"] == 1
    assert store.settings.managed_by == "Facilities"
    assert c.get_schedules()["schedules"][0]["time"] == "17:30"
    with pytest.raises(DeskError, match="unknown action"):
        c.action("rm -rf")


def test_parse_schedules_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_schedules([{"id": "x", "time": "25:00", "action": "max"}])
    with pytest.raises(ValueError):
        parse_schedules([{"id": "x", "time": "17:30", "action": "explode"}])
    with pytest.raises(ValueError):
        parse_schedules("nope")
    r = parse_schedules([{"id": "x", "time": "7:5", "action": "preset:2"}])
    assert r[0].time == "07:05"


# ---- admin key from settings ----------------------------------------------------------
def test_change_admin_key(store):
    from hat_desk.settings import change_admin_key
    assert not store.settings.admin_key_set
    assert change_admin_key(store, "short", "short") == "Use at least 8 characters."
    assert change_admin_key(store, "office-key-1", "office-key-2") == "The two keys don't match."
    assert change_admin_key(store, "office-key-1", "office-key-1") == ""
    assert store.settings.admin_key == "office-key-1" and store.settings.admin_key_set
    # once set, changing it needs the current key
    assert change_admin_key(store, "office-key-2", "office-key-2") == "The current key is wrong."
    assert change_admin_key(store, "office-key-2", "office-key-2", current="guess") == "The current key is wrong."
    assert change_admin_key(store, "office-key-2", "office-key-2", current="office-key-1") == ""
    assert SettingsStore(store.path).settings.admin_key == "office-key-2"


def test_admin_key_at_install(store):
    from hat_desk.settings import set_admin_key
    assert set_admin_key(store, "short") == "The admin key needs at least 8 characters."
    assert not store.settings.admin_key_set
    assert set_admin_key(store, "  office-key-9 ") == ""
    s = SettingsStore(store.path).settings
    assert s.admin_key == "office-key-9" and s.admin_key_set
