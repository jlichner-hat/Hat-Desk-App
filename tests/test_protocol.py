import pytest

from linak_leg import protocol as P


def test_init_report():
    r = P.init_report()
    assert len(r) == 64 and r[:4] == [3, 0x04, 0x00, 0xFB] and not any(r[4:])


@pytest.mark.parametrize("value,lo,hi", [(0x8000, 0x00, 0x80), (0x7FFF, 0xFF, 0x7F), (2500, 0xC4, 0x09)])
def test_move_report_repeats_value_four_times(value, lo, hi):
    r = P.move_report(value)
    assert r[0] == 5 and r[1:9] == [lo, hi] * 4 and not any(r[9:])


def test_move_report_range():
    with pytest.raises(ValueError):
        P.move_report(0x10000)


def test_parse_status():
    raw = [0] * 64
    raw[0], raw[4], raw[5], raw[6], raw[7] = 4, 0xC4, 0x09, 0x80, 1
    raw[20], raw[21] = 0x01, 0x80
    st = P.parse_status(raw)
    assert st.position == 2500 and st.mm == 250.0
    assert st.moving and st.going_down and not st.blocked
    assert st.target == P.STOPPED


def test_direction_flag_is_not_moving_flag():
    raw = [0] * 64
    raw[6] = 0x80          # direction bit set, byte 7 zero: NOT moving (the bench bug)
    st = P.parse_status(raw)
    assert st.going_down and not st.moving


@pytest.mark.parametrize("raw", [None, [], [4, 0, 0]])
def test_short_status_raises(raw):
    with pytest.raises(IOError):
        P.parse_status(raw)


def test_mm_to_units():
    assert P.mm_to_units(250) == 2500 and P.mm_to_units(0.06) == 1
