"""Offline tests of the live monitor's drift checks and kill switch."""
import numpy as np
import pytest

from monitor import drift, killswitch


def test_green_when_live_matches_backtest():
    rng = np.random.default_rng(3)
    live = list(0.005 + rng.normal(0, 0.02, 60))
    a = drift.assess(live, 0.005, 0.02, bt_dd=-0.3, bt_win=0.55)
    assert a["status"] == "GREEN", a


def test_red_on_sustained_shortfall():
    rng = np.random.default_rng(4)
    live = list(-0.01 + rng.normal(0, 0.02, 40))         # backtest promised +0.5% a period
    a = drift.assess(live, 0.005, 0.02, bt_dd=-0.3, bt_win=0.55)
    assert a["status"] == "RED" and a["z"] < -2.5 and a["cusum_low"] <= -drift.CUSUM_H


def test_warming_up_below_min_n():
    assert drift.assess([-0.05] * 5, 0.005, 0.02)["status"] == "WARMING UP"


def test_streak_limit_and_drawdown():
    assert drift.longest_losing_run([1, -1, -1, 0, 1, -1]) == 3
    assert drift.streak_limit(0.5, 100) >= 10
    assert drift.max_drawdown([0.1, -0.05, -0.1, 0.02]) == pytest.approx(-0.15)


def test_killswitch_rules(tmp_path):
    p = str(tmp_path / "ks.json")
    ks = killswitch.load(p)
    assert killswitch.blocked("swing_model", ks) is None
    killswitch.kill(ks, "swing_model", "drift", auto=True)
    assert killswitch.blocked("swing_model", ks).startswith("KILLED")
    killswitch.revive(ks, "swing_model", "reviewed")
    assert killswitch.blocked("swing_model", ks) is None
    killswitch.halt(ks, True, "bad data")
    assert killswitch.blocked("intraday_long", ks).startswith("HALTED")
    killswitch.save(ks, p)
    assert killswitch.load(p)["halt"]["on"] is True
