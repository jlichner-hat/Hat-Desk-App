import time

import pytest

from linak_leg import LegConfig, LinakLeg, SimulatedLeg


def wait_for(cond, timeout=5.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def hold(leg, d, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        leg.command(d)
        time.sleep(0.05)


@pytest.fixture
def make_leg(tmp_path):
    legs = []

    def make(sim=None, **cfg):
        sim = sim or SimulatedLeg(start_mm=300)
        cfg.setdefault("log_path", str(tmp_path / "leg_errors.log"))
        leg, why = LinakLeg.try_open(LegConfig(**cfg), sim.opener)
        assert leg is not None, why
        assert wait_for(lambda: leg.pos is not None, 3)
        legs.append(leg)
        return leg, sim

    yield make
    for leg in legs:
        leg.close()
