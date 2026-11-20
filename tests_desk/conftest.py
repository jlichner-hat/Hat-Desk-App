import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HAT_DESK_HOME", str(tmp_path))
    from hat_desk.settings import SettingsStore
    return SettingsStore(str(tmp_path / "settings.json"))


@pytest.fixture
def desk(store):
    """Desk controller on a simulated leg running 4x real time, starting 80 mm up."""
    from hat_desk.controller import DeskController
    ctl = DeskController(store, simulate=True, sim_start_mm=80, sim_time_scale=4.0)
    yield ctl
    ctl.close()
