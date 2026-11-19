"""Offline tests of the swing engine's labels, charges, regime exposure and avoid list."""
import datetime

import duckdb
import pandas as pd
import pytest

from swing import charges, labels, model, portfolio


def _con(closes, lows=None, opens=None):
    days = [datetime.date(2024, 1, 1) + datetime.timedelta(days=i) for i in range(len(closes))]
    lows = lows or closes
    opens = opens or closes
    df = pd.DataFrame({"sym_id": "ABC", "date": days, "adj_open": opens, "adj_low": lows, "adj_close": closes})
    con = duckdb.connect()
    con.register("df", df)
    con.execute("CREATE TABLE eq_daily AS SELECT * FROM df")
    return con, days


def test_entry_next_open_and_time_exit():
    closes = [100.0] + [100.0 + i for i in range(1, 26)]
    con, days = _con(closes)
    feats = pd.DataFrame({"date": [days[0]], "sym_id": ["ABC"], "atr_pct": [0.02], "adj_close": [100.0]})
    r = labels.build(con, feats).iloc[0]
    assert r.entry == 101.0                                    # D+1 open
    assert r.fwd_5 == pytest.approx(105 / 101 - 1)             # close of the 5th session after entry
    assert r.fwd_stop_10 == pytest.approx(r.fwd_10)            # never stopped


def test_stop_gap_fills_at_open():
    closes = [100.0, 100.0, 100.0, 90.0] + [90.0] * 22
    opens = [100.0, 100.0, 100.0, 92.0] + [90.0] * 22
    con, days = _con(closes, lows=closes, opens=opens)
    feats = pd.DataFrame({"date": [days[0]], "sym_id": ["ABC"], "atr_pct": [0.02], "adj_close": [100.0]})
    r = labels.build(con, feats).iloc[0]
    assert r.stop == pytest.approx(96.0)                       # 100 - 2 x 2% x 100
    assert r.fwd_stop_5 == pytest.approx(-0.08)                # gapped to 92, under the 96 stop
    assert r.fwd_5 == pytest.approx(-0.10)


def test_cnc_cost():
    c = charges.cnc_round_trip(100000, slippage=0)
    assert 0.0022 < c < 0.0026                                 # ~0.24% on Rs 1 lakh
    assert charges.cnc_round_trip(100000) == pytest.approx(c + 0.002)


def test_exposure_and_avoid():
    assert portfolio.exposure(pd.Series({"nifty_above_ma200": 1, "breadth_ma50": 0.6})) == 1.0
    assert portfolio.exposure(pd.Series({"nifty_above_ma200": 1, "breadth_ma50": 0.4})) == 0.5
    assert portfolio.exposure(pd.Series({"nifty_above_ma200": 0, "breadth_ma50": 0.3})) == 0.0
    d = pd.DataFrame({"ret_1": [-0.04, 0.01, 0.0, 0.01], "body_1": [0, 0, -0.9, 0.2],
                      "upper_wick_1": [0, 0, 0, 0.1], "ret_5": [0, 0.3, 0, 0.02]})
    assert list(model.avoid(d)) == [True, True, True, False]
