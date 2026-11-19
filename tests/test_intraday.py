"""Offline tests of the intraday engine's labels, charges and verdict rules (synthetic data)."""
import datetime

import duckdb
import numpy as np
import pandas as pd
import pytest

from intraday import charges, labels, model


def _day(sym, day, path):
    """path: {HH:MM: (o, h, l, c)}; minutes not listed stay flat at the last close, 09:15..15:29."""
    rows, last = [], None
    t = datetime.datetime.combine(day, datetime.time(9, 15))
    while t.time() <= datetime.time(15, 29):
        k = t.strftime("%H:%M")
        if k in path:
            last = path[k]
        else:
            last = (last[3],) * 4
        rows.append((day, sym, t, *last, 1000.0, None, "test"))
        t += datetime.timedelta(minutes=1)
    return rows


def _con(rows):
    con = duckdb.connect()
    df = pd.DataFrame(rows, columns=["date", "symbol", "ts", "open", "high", "low", "close", "volume", "oi", "source"])
    con.register("df", df)
    con.execute("CREATE TABLE minute_bars AS SELECT * FROM df")
    return con


def test_long_stop_hits_window_low_and_short_runs_to_close():
    d = datetime.date(2025, 3, 3)
    path = {"09:15": (100, 101, 99, 100), "09:30": (100, 100.5, 99.8, 100.2),
            "10:00": (99.5, 99.6, 98.5, 98.8), "15:09": (98.0, 98.0, 98.0, 98.0)}
    r = labels.build(_con(_day("ABC", d, path))).iloc[0]
    assert r.entry == 100
    assert r.ret_long_stop == pytest.approx(-0.01)          # stopped at the window low, 99
    assert bool(r.long_stopped) and not bool(r.short_stopped)
    assert r.ret_close == pytest.approx(-0.02)              # 15:09 close 98
    assert r.ret_short_stop == pytest.approx(0.02)


def test_gap_through_stop_fills_at_the_open():
    d = datetime.date(2025, 3, 4)
    path = {"09:15": (100, 101, 99, 100), "09:30": (100, 100, 100, 100), "11:00": (97, 97.5, 96.5, 97)}
    r = labels.build(_con(_day("ABC", d, path))).iloc[0]
    assert r.ret_long_stop == pytest.approx(-0.03)          # gapped to 97, below the 99 stop


def test_round_trip_cost_is_realistic():
    c = charges.round_trip(50000, slippage=0)
    assert 0.0009 < c < 0.0012                             # about 0.10% before slippage
    assert charges.round_trip(50000) == pytest.approx(c + 0.001)


def test_verdict_rules():
    idx = pd.date_range("2023-01-02", periods=500, freq="B")
    rng = np.random.default_rng(1)
    good = pd.Series(0.002 + rng.normal(0, 0.01, len(idx)), index=idx)
    bad = pd.Series(-0.001 + rng.normal(0, 0.01, len(idx)), index=idx)
    assert model.verdict(model.stats(good)) == "EDGE"
    assert model.verdict(model.stats(bad)) == "NO EDGE"
    assert model.verdict(model.stats(good.head(50))) == "TOO LITTLE DATA"
    base = model.stats(good + 0.01)
    assert model.verdict(model.stats(good), base) == "UNPROVEN"   # positive but worse than the baseline
