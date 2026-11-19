"""Paper trades: Rs 50,000 a stock, entry at the open (swing) / 9:30 (intraday), suggested SL, exits."""
import datetime
import json

import duckdb
import pandas as pd
import pytest

from papertrade import book

D = datetime.date


def _con(daily, minute):
    con = duckdb.connect()
    con.register("d", pd.DataFrame(daily, columns=["sym_id", "date", "open", "high", "low", "close", "adj_factor"]))
    con.execute("CREATE TABLE eq_daily AS SELECT * FROM d")
    con.register("m", pd.DataFrame(minute, columns=["symbol", "date", "ts", "open", "high", "low", "close"]))
    con.execute("CREATE TABLE minute_bars AS SELECT * FROM m")
    return con


def _pick(tmp_path, name, doc, monkeypatch):
    monkeypatch.setattr(book, "PICKS", str(tmp_path))
    (tmp_path / name).write_text(json.dumps(doc))


def test_swing_holds_ten_sessions_then_sells(tmp_path, monkeypatch):
    days = [D(2026, 1, 1) + datetime.timedelta(days=i) for i in range(12)]
    rows = [("ABC", d, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1.0) for i, d in enumerate(days)]
    _pick(tmp_path, "swing-2026-01-01.json", {"session": "2026-01-01", "picks": [
        {"symbol": "ABC", "industry": "X", "ref_close": 100.5, "stop": 90.0, "hold_sessions": 10}]}, monkeypatch)
    t = book.swing_trades(_con(rows, []))[0]
    assert t["entry"] == 101.0 and t["qty"] == int(50000 // 101.0)        # next session's open
    assert t["status"] == "closed" and t["exit_reason"] == "10 sessions done"
    assert t["exit"] == 110.5 and len(t["marks"]) == 10                   # close of the 10th session
    assert t["marks"][-1]["total_pnl"] == pytest.approx((110.5 - 101.0) * t["qty"])
    assert t["net"] < t["gross"]                                           # charges taken


def test_swing_gap_below_sl_exits_at_open(tmp_path, monkeypatch):
    rows = [("ABC", D(2026, 1, 1), 100, 101, 99, 100, 1.0), ("ABC", D(2026, 1, 2), 100, 101, 99, 100, 1.0),
            ("ABC", D(2026, 1, 3), 85, 86, 84, 85, 1.0)]
    _pick(tmp_path, "swing-2026-01-01.json", {"session": "2026-01-01", "picks": [
        {"symbol": "ABC", "ref_close": 100, "stop": 90.0, "hold_sessions": 10}]}, monkeypatch)
    t = book.swing_trades(_con(rows, []))[0]
    assert t["exit"] == 85 and t["exit_reason"] == "SL hit" and t["exit_date"] == "2026-01-03"


def test_intraday_short_stopped_at_sl(tmp_path, monkeypatch):
    day = D(2026, 1, 5)

    def ts(h, m):
        return datetime.datetime(2026, 1, 5, h, m)
    minute = [("XYZ", day, ts(9, 30), 200.0, 200.5, 199.5, 200.0), ("XYZ", day, ts(10, 0), 200.5, 202.5, 200.0, 202.0),
              ("XYZ", day, ts(15, 9), 195.0, 195.0, 195.0, 195.0)]
    _pick(tmp_path, "intraday-2026-01-05.json", {"day": "2026-01-05", "decision_time": "09:30", "long": [],
                                                  "short": [{"symbol": "XYZ", "stop": 202.0}]}, monkeypatch)
    t = book.intraday_trades(_con([], minute))[0]
    assert t["entry"] == 200.0 and t["qty"] == 250
    assert t["exit"] == 202.0 and t["exit_reason"] == "SL hit" and t["exit_time"] == "10:00"
    assert t["gross"] == pytest.approx(-500.0)
