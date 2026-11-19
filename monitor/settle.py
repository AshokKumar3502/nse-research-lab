"""Turns every published pick into a settled result on real prices, one ledger for all strategies.

  intraday   data/picks/intraday-DAY.json -> the same labels as the backtest (intraday/labels.py) on
             that day's 1-minute bars: entry at the 09:30 open, opening-window stop, flat by 15:10
  swing      data/picks/swing-SESSION.json -> entry at the next open (adjusted daily bars), the plan's
             stop distance, exit at the stop or the hold-th close (as swing/labels.py)
  lab        data/lab/paper.parquet, already settled by lab/paper.py

-> data/monitor/live.parquet: strategy, period, date, sym, net (after charges), source.
`period` is what the backtest's statistics are per: the day (intraday), the rebalance (swing), the
trade (lab).
"""
import glob
import json
import os

import pandas as pd

from intraday import charges as ic, labels as il
from swing import charges as sc
from warehouse import config

LIVE = os.path.join(config.DATA, "monitor", "live.parquet")
PICKS = os.path.join(config.DATA, "picks")
COLS = ["strategy", "period", "date", "sym", "net", "source"]


def intraday(con):
    rows = []
    last_min = con.execute("SELECT max(date) FROM minute_bars").fetchone()[0]
    cost = ic.round_trip()
    for path in sorted(glob.glob(os.path.join(PICKS, "intraday-*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        day = pd.Timestamp(doc["day"]).date()
        if last_min is None or day > last_min or not (doc.get("long") or doc.get("short")):
            continue
        t = doc.get("decision_time", "09:30")
        y = il.build(con, t, start=day, end=day).set_index("symbol")
        for side, col in (("long", "ret_long_stop"), ("short", "ret_short_stop")):
            for p in doc.get(side, []):
                if p["symbol"] in y.index:
                    rows.append(("intraday_" + side, str(day), str(day), p["symbol"],
                                 float(y.loc[p["symbol"], col]) - cost, "picks"))
    return rows


def swing(con):
    rows = []
    cost = sc.cnc_round_trip()
    for path in sorted(glob.glob(os.path.join(PICKS, "swing-*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        session = pd.Timestamp(doc["session"]).date()
        for p in doc.get("picks", []):
            hold = int(p["hold_sessions"])
            bars = con.execute("""SELECT date, adj_open, adj_low, adj_close FROM eq_daily
                                  WHERE sym_id = ? AND date > ? ORDER BY date LIMIT ?""",
                               [p["symbol"], session, hold]).fetchall()
            if not bars:
                continue
            entry = bars[0][1]
            stop = entry * (1 + p["stop_pct"] / 100.0)     # the plan's stop distance from the entry
            out = next((min(b[1], stop) for b in bars if b[2] <= stop), None)
            if out is None:
                if len(bars) < hold:
                    continue                                # still open
                out = bars[-1][3]
            rows.append(("swing_model", str(session), str(session), p["symbol"], out / entry - 1 - cost, "picks"))
    return rows


def lab():
    p = os.path.join(config.DATA, "lab", "paper.parquet")
    if not os.path.exists(p):
        return []
    led = pd.read_parquet(p)
    led = led[led.status == "closed"]
    return [("lab:" + r.spec_id, str(pd.Timestamp(r.exit_date).date()), str(pd.Timestamp(r.signal_date).date()),
             r.sym_id, float(r.net), "lab paper") for r in led.itertuples()]


def build(con):
    rows = []
    for fn in (lambda: intraday(con), lambda: swing(con), lab):
        try:
            rows += fn()
        except Exception as e:   # one broken source must not stop the monitor; it is reported
            rows.append(("_errors", "", "", type(e).__name__, float("nan"), str(e)[:200]))
    df = pd.DataFrame(rows, columns=COLS)
    os.makedirs(os.path.dirname(LIVE), exist_ok=True)
    df.to_parquet(LIVE, index=False)
    return df
