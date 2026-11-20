import time

from linak_leg import protocol as P
from linak_leg.sim import SimulatedLeg


def started(**kw):
    s = SimulatedLeg(**kw)
    s.send_feature_report(P.init_report())
    return s


def keep_sending(sim, value, seconds, every=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sim.send_feature_report(P.move_report(value))
        time.sleep(every)


def test_handset_up_reaches_near_full_speed():
    s = started(start_mm=100)
    keep_sending(s, P.MOVE_UP, 1.5)
    moved = s.mm - 100
    assert 40 < moved < 58          # 38 mm/s with a 0.5 s soft start


def test_ignores_moves_before_init():
    s = SimulatedLeg(start_mm=100)
    keep_sending(s, P.MOVE_UP, 0.3)
    assert s.mm == 100


def test_deadman_stops_when_commands_stop():
    s = started(start_mm=100)
    keep_sending(s, P.MOVE_UP, 0.5)
    time.sleep(0.6)
    assert not s.moving
    at = s.mm
    time.sleep(0.3)
    assert s.mm == at


def test_handset_stop_with_opposite_code():
    s = started(start_mm=100)
    keep_sending(s, P.MOVE_UP, 0.5)
    s.send_feature_report(P.move_report(P.MOVE_DOWN))
    st = P.parse_status(s.get_feature_report(4, 64))
    assert not st.moving and st.target == P.STOPPED


def test_changing_target_every_cycle_is_slow():
    """Reproduces the bench bug: a new position target every 0.2 s restarts the soft start."""
    s = started(start_mm=100)
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        s.send_feature_report(P.move_report(int(s.pos) + 300))
        time.sleep(0.2)
    slow = s.mm - 100
    s2 = started(start_mm=100)
    keep_sending(s2, 4000, 2.0)
    fast = s2.mm - 100
    assert fast > 50 and slow < 0.4 * fast


def test_position_mode_stops_at_target():
    s = started(start_mm=100)
    keep_sending(s, 1200, 1.2)
    assert s.mm == 120 and not s.moving


def test_end_of_travel():
    s = started(start_mm=663)
    keep_sending(s, P.MOVE_UP, 0.5)
    assert s.mm == 665 and not s.moving


def test_status_layout():
    s = started(start_mm=250)
    st = P.parse_status(s.get_feature_report(4, 64))
    assert st.position == 2500 and not st.moving and st.target == P.STOPPED
    s.send_feature_report(P.move_report(P.MOVE_DOWN))
    time.sleep(0.05)
    st = P.parse_status(s.get_feature_report(4, 64))
    assert st.moving and st.going_down and st.target == P.MOVE_DOWN


def test_glitches():
    s = started(fail_rate=1.0, seed=1)
    assert s.get_feature_report(4, 64) == []
