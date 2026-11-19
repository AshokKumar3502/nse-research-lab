"""Offline tests of the research lab: spec safety, the Skeptic's maths, cohort selection, lifecycle."""
import pandas as pd
import pytest

from lab import backtest, registry, skeptic, spec


def _spec(**kw):
    s = {"id": "test_idea", "name": "t", "hypothesis": "h", "filter": "rs_nifty_20 > {m}", "rank": "-rs_nifty_20",
         "k": 2, "hold": 5, "params": {"m": 0.0}}
    s.update(kw)
    return spec.validate(s)


def test_spec_rejects_code_and_unknown_names():
    for bad in ("__import__('os')", "rs_nifty_20.real > 0", "x[0] > 1", "nonexistent > 0", "'a' == 'a'"):
        with pytest.raises(spec.SpecError):
            spec.check_expr(bad)
    with pytest.raises(spec.SpecError):
        _spec(hold=7)
    with pytest.raises(spec.SpecError):
        _spec(filter="rs_nifty_20 > {missing}")
    assert spec.render("a > {m}", {"m": 0.05}) == "a > 0.05"


def test_hurdle_grows_with_trials():
    assert skeptic.t_hurdle(1) == pytest.approx(1.96, abs=0.01)
    assert skeptic.t_hurdle(20) > 2.9
    assert skeptic.t_hurdle(100) > skeptic.t_hurdle(20)
    v = skeptic.variants({"a": 1.0, "b": 2.0})
    assert len(v) == 8 and {"a": 1.0, "b": 2.0} not in v


def test_cohort_takes_top_k_every_hold_sessions():
    dates = pd.bdate_range("2024-01-01", periods=10)
    rows = []
    for d in dates:
        for i, s in enumerate("ABCD"):
            rows.append({"date": d, "sym_id": s, "industry": "X", "rs_nifty_20": i * 0.01, "fwd_5": 0.01 * i,
                         "turnover": 1e9})
    df = pd.DataFrame(rows)
    series, trades = backtest.evaluate(df, _spec(), cost=0.0)
    assert list(series.index) == [dates[0], dates[5]]               # every 5th session
    assert set(trades.sym_id) == {"C", "D"}                          # top 2 by rs_nifty_20
    assert series.iloc[0] == pytest.approx(0.025)


def test_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "PATH", str(tmp_path / "registry.json"))
    reg = registry.load()
    s = _spec()
    registry.count_trial(reg, s)
    e = registry.record_review(reg, s, {"verdict": "PASS", "t": 3.5, "hurdle": 2.0, "mean_pct": 1.0,
                                        "failed": [], "bt_max_dd": -0.1})
    assert e["stage"] == "paper"
    moves = registry.promote(reg, {"test_idea": {"n": 35, "mean": 0.009, "sd": 0.03, "max_dd": -0.05}})
    assert moves == [("test_idea", "live_small")]
    moves = registry.promote(reg, {"test_idea": {"n": 40, "mean": -0.02, "sd": 0.02, "max_dd": -0.05}})
    assert moves == [("test_idea", "retired")]
    s2 = _spec(params={"m": 0.01})
    assert registry.needs_test(reg, s2) and registry.count_trial(reg, s2) == 2
