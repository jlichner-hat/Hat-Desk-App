import time

from conftest import hold, wait_for

from linak_leg import LegConfig, LinakLeg, SimulatedLeg
from linak_leg import protocol as P


def stays_still(leg, seconds=0.4):
    at = leg.pos
    time.sleep(seconds)
    return leg.pos == at and not leg.moving


def test_try_open_reasons():
    def missing():
        raise ImportError("no hid")

    def absent():
        raise OSError("open failed")

    assert LinakLeg.try_open(opener=missing) == (None, "USB library not installed (pip install hidapi)")
    assert LinakLeg.try_open(opener=absent) == (None, "USB2LIN06 cable not found")


def test_sends_init_first(make_leg):
    leg, sim = make_leg()
    assert sim.initialised


def test_hold_up_moves_and_release_stops(make_leg):
    leg, sim = make_leg()
    start = leg.pos
    hold(leg, 1, 1.5)
    assert leg.pos - start > 300                 # > 30 mm in 1.5 s
    leg.command(0)
    assert wait_for(lambda: not sim.moving, 0.5)  # active stop, not just the dead-man
    assert P.MOVE_DOWN in sim.sent[-3:]           # handset-style stop: one code the other way
    assert stays_still(leg)


def test_hold_down(make_leg):
    leg, sim = make_leg()
    start = sim.mm
    hold(leg, -1, 1.0)
    leg.command(0)
    assert wait_for(lambda: not sim.moving, 0.5)
    assert sim.mm < start - 15


def test_watchdog_stops_a_stalled_ui(make_leg):
    leg, sim = make_leg()
    leg.command(1)                    # UI confirms once, then freezes
    time.sleep(0.3)
    assert sim.moving
    assert wait_for(lambda: not sim.moving, 1.0)


def test_max_hold(make_leg):
    leg, sim = make_leg(max_hold=0.6)
    end = time.monotonic() + 1.5
    stopped_at = None
    while time.monotonic() < end:
        leg.command(1)
        if stopped_at is None and time.monotonic() - end > -0.6 and not sim.moving:
            stopped_at = time.monotonic()
        time.sleep(0.05)
    assert stopped_at is not None
    leg.command(0)
    time.sleep(0.4)
    start = sim.mm
    hold(leg, 1, 0.5)                 # a new hold works again
    assert sim.mm > start


def test_duty_cycle_blocks_moves(make_leg):
    leg, sim = make_leg(duty_max=0.6)
    hold(leg, 1, 1.2)
    assert leg.resting
    assert not sim.moving
    assert leg.go_to(leg.pos + 100) == "resting"


def test_min_position(make_leg):
    leg, sim = make_leg(SimulatedLeg(start_mm=105), min_position=1000)
    hold(leg, -1, 1.0)
    leg.command(0)
    assert wait_for(lambda: not sim.moving, 0.5)
    assert 98 <= sim.mm <= 105                    # stops at the lowest allowed height, never far below


def test_glitches_are_ignored(make_leg):
    leg, sim = make_leg(SimulatedLeg(start_mm=300, fail_rate=0.15, seed=7))
    start = sim.mm
    hold(leg, 1, 1.5)
    leg.command(0)
    assert leg.online
    assert sim.mm - start > 30
    assert wait_for(lambda: not sim.moving, 1.0)


def test_offline_after_repeated_failures_then_reconnects(make_leg):
    sim = SimulatedLeg(start_mm=300)
    leg, _ = make_leg(sim, reconnect_wait=0.3)
    sim.fail_rate = 1.0
    assert wait_for(lambda: not leg.online, 4)
    assert leg.pos is None and leg.message == "leg offline"
    sim.fail_rate = 0.0
    assert wait_for(lambda: leg.online and leg.pos is not None, 4)


def test_glitch_log(make_leg, tmp_path):
    leg, sim = make_leg(SimulatedLeg(start_mm=300, fail_rate=0.5, seed=3))
    time.sleep(1.5)
    log = (tmp_path / "leg_errors.log").read_text()
    assert "IOError" in log or "OSError" in log


def test_go_to_reaches_target(make_leg):
    leg, sim = make_leg()
    assert leg.go_to(3400) == "reached"
    assert abs(sim.mm - 340) <= 2
    assert stays_still(leg)


def test_go_to_down(make_leg):
    leg, sim = make_leg()
    assert leg.go_to(2700) == "reached"
    assert abs(sim.mm - 270) <= 2


def test_go_to_sends_same_target(make_leg):
    leg, sim = make_leg()
    leg.go_to(3300)
    targets = {v for v in sim.sent if v not in (P.MOVE_UP, P.MOVE_DOWN)}
    assert targets == {3300}


def test_go_to_is_clamped(make_leg):
    leg, sim = make_leg(SimulatedLeg(start_mm=130), min_position=1200)
    assert leg.go_to(0) == "reached"
    assert abs(sim.mm - 120) <= 2


def test_hold_cancels_go_to(make_leg):
    leg, sim = make_leg()
    leg.go_to(6000, wait=False)
    time.sleep(0.5)
    hold(leg, -1, 0.3)
    assert leg.wait_goal(1) == "cancelled"
    leg.command(0)
    assert wait_for(lambda: not sim.moving, 0.6)


def test_stop_cancels_go_to(make_leg):
    leg, sim = make_leg()
    leg.go_to(6000, wait=False)
    time.sleep(0.6)
    leg.stop()
    assert leg.wait_goal(1) == "cancelled"
    assert wait_for(lambda: not sim.moving, 0.6)
    assert stays_still(leg)


def test_go_to_stopped_short_at_end_of_travel(make_leg):
    leg, sim = make_leg(SimulatedLeg(start_mm=300, stroke_mm=310))
    assert leg.go_to(4000) == "stopped short"
    assert sim.mm == 310


def test_settle(make_leg):
    leg, sim = make_leg()
    hold(leg, 1, 0.6)
    leg.settle()
    assert not sim.moving and not leg.moving


def test_close_stops(make_leg):
    sim = SimulatedLeg(start_mm=300)
    leg, why = LinakLeg.try_open(LegConfig(log_path=None), sim.opener)
    hold(leg, 1, 0.5)
    leg.close()
    assert not sim.moving
    assert not leg.thread.is_alive()
