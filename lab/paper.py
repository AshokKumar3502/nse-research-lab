"""The paper ledger: every signal of every strategy in `paper` or `live_small`, recorded the evening it
fires and settled on real NSE prices (adjusted daily bars) as they arrive. Nothing is traded.

data/lab/paper.parquet, one row per signal:
  spec_id, signal_date, sym_id, status (pending | open | closed | void), entry_date, entry, stop,
  exit_date, exit, sessions, net (after delivery charges + slippage)
"""
import datetime
import os

import numpy as np
import pandas as pd

from swing import charges, features as swing_features, labels as swing_labels, model as swing_model
from warehouse import config

from . import backtest

PATH = os.path.join(config.DATA, "lab", "paper.parquet")
COLS = ["spec_id", "signal_date", "sym_id", "status", "entry_date", "entry", "stop", "exit_date", "exit",
        "sessions", "net", "atr_pct", "close"]


def load():
    if os.path.exists(PATH):
        return pd.read_parquet(PATH)
    return pd.DataFrame(columns=COLS)


def save(df):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    df.to_parquet(PATH + ".tmp", index=False)
    os.replace(PATH + ".tmp", PATH)


def today_rows(con):
    last = con.execute("SELECT max(date) FROM calendar").fetchone()[0]
    f = swing_features.build(con, start=last - datetime.timedelta(days=15), end=last)
    f["date"] = pd.to_datetime(f["date"])
    f = f[f.date == pd.Timestamp(last)].copy()
    f["avoid"] = swing_model.avoid(f)
    return last, f


def record_signals(con, specs, ledger, log=print):
    last, day = today_rows(con)
    sessions = [d for (d,) in con.execute("SELECT date FROM calendar ORDER BY date").fetchall()]
    pos = {d: i for i, d in enumerate(sessions)}
    new = []
    for spec in specs:
        mine = ledger[ledger.spec_id == spec["id"]]
        if len(mine) and (pd.Timestamp(last) <= pd.to_datetime(mine.signal_date).max()):
            continue
        if spec["mode"] == "cohort" and len(mine):
            prev = pd.to_datetime(mine.signal_date).max().date()
            if pos.get(last, 0) - pos.get(prev, 0) < int(spec["hold"]):
                continue
        hits = backtest.select(day, spec)
        if spec["mode"] == "cohort":
            key = spec["rank"].lstrip("-")
            hits = hits.sort_values(key, ascending=not spec["rank"].startswith("-")).head(int(spec["k"]))
        for r in hits.itertuples():
            new.append({"spec_id": spec["id"], "signal_date": pd.Timestamp(last), "sym_id": r.sym_id,
                        "status": "pending", "atr_pct": float(r.atr_pct), "close": float(r.adj_close)})
        log("paper %s: %d signal(s) on %s" % (spec["id"], len(hits), last))
    if new:
        ledger = pd.concat([ledger, pd.DataFrame(new)], ignore_index=True)
    return ledger


def settle(con, ledger, specs_by_id):
    """Fill entries at the next open and exits at the stop or the hold-th close."""
    if ledger.empty:
        return ledger
    todo = ledger[ledger.status.isin(["pending", "open"])]
    if todo.empty:
        return ledger
    syms = sorted(todo.sym_id.unique())
    start = pd.to_datetime(todo.signal_date).min().date()
    px = con.execute("""SELECT sym_id, date, adj_open, adj_low, adj_close FROM eq_daily
                        WHERE date > ? AND sym_id IN (SELECT unnest(?)) ORDER BY sym_id, date""",
                     [start, syms]).df()
    px["date"] = pd.to_datetime(px.date)
    by_sym = {s: g.reset_index(drop=True) for s, g in px.groupby("sym_id")}
    cost = charges.cnc_round_trip()
    for i in todo.index:
        r = ledger.loc[i]
        spec = specs_by_id.get(r.spec_id)
        bars = by_sym.get(r.sym_id)
        if spec is None or bars is None:
            continue
        after = bars[bars.date > pd.Timestamp(r.signal_date)]
        if after.empty:
            continue
        e = after.iloc[0]
        entry = float(e.adj_open)
        stop = entry - swing_labels.STOP_ATR * r.atr_pct * r.close if spec.get("stop_atr") else -np.inf
        ledger.loc[i, ["status", "entry_date", "entry", "stop"]] = ["open", e.date, entry, stop]
        hold = int(spec["hold"])
        for k, b in enumerate(after.head(hold).itertuples(), start=1):
            if b.adj_low <= stop:
                px_out = min(b.adj_open, stop)
            elif k == hold:
                px_out = b.adj_close
            else:
                continue
            ledger.loc[i, ["status", "exit_date", "exit", "sessions", "net"]] = [
                "closed", b.date, px_out, k, px_out / entry - 1 - cost]
            break
    return ledger


def stats(ledger):
    out = {}
    closed = ledger[ledger.status == "closed"]
    for sid, g in closed.groupby("spec_id"):
        g = g.sort_values("exit_date")
        eq = g.net.cumsum()
        out[sid] = {"n": int(len(g)), "mean": float(g.net.mean()), "sd": float(g.net.std(ddof=1)) if len(g) > 1 else 0.0,
                    "win_pct": round(float((g.net > 0).mean()) * 100, 1),
                    "max_dd": float((eq - eq.cummax()).min()) if len(g) else 0.0,
                    "open": int(((ledger.spec_id == sid) & ledger.status.isin(["pending", "open"])).sum())}
    return out
