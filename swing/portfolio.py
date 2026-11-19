"""A day-by-day portfolio simulation of the swing ranker's out-of-sample predictions, with the rules
a real account would follow:

  capital           Rs 10,00,000 (configurable), marked to market at every close
  entries           after D's close, the highest-ranked stocks with a positive predicted excess
                    return, bought at D+1's open
  size              risk RISK_PER_TRADE of equity between entry and the 2-ATR stop, capped at
                    MAX_WEIGHT of equity
  limits            MAX_POSITIONS open at once, MAX_PER_INDUSTRY in one industry
  regime exposure   full when NIFTY 50 is above its 200-day average and over half the universe is
                    above its 50-day; half when only one holds; no new entries when neither
  exits             the stop (at the stop, or the open when it gaps through), else the close of the
                    HOLD-th session
  costs             Zerodha delivery charges + slippage on every round trip

Compared with NIFTY 50 bought and held over the same days.
"""
import numpy as np
import pandas as pd

from . import charges

CAPITAL = 1_000_000.0
RISK_PER_TRADE = 0.01
MAX_WEIGHT = 0.15
MAX_POSITIONS = 10
MAX_PER_INDUSTRY = 3
HOLD = 10
STOP_ATR = 2.0


def exposure(row):
    a, b = row.nifty_above_ma200 == 1, (row.breadth_ma50 or 0) > 0.5
    return 1.0 if a and b else (0.5 if a or b else 0.0)


def simulate(con, oos, hold=HOLD, capital=CAPITAL, max_positions=MAX_POSITIONS, slippage=0.001):
    oos = oos.copy()
    oos["date"] = pd.to_datetime(oos.date)
    start, end = oos.date.min(), oos.date.max()
    syms = sorted(oos.sym_id.unique())
    px = con.execute("""SELECT sym_id, date, adj_open, adj_high, adj_low, adj_close FROM eq_daily
                        WHERE date >= ? AND date <= ? + INTERVAL 40 DAY AND sym_id IN (SELECT unnest(?))""",
                     [start.date(), end.date(), syms]).df()
    px["date"] = pd.to_datetime(px.date)
    bars = {(r.sym_id, r.date): r for r in px.itertuples(index=False)}
    days = sorted(px.date.unique())
    nifty = con.execute("SELECT date, close FROM idx_daily WHERE index_key = 'nifty 50' AND date BETWEEN ? AND ?",
                        [start.date(), end.date()]).df()
    nifty["date"] = pd.to_datetime(nifty.date)
    by_day = {d: g for d, g in oos.groupby("date")}
    cash, pos, pending, trades, curve = capital, {}, [], [], []
    for d in days:
        if d > end and not pos:
            break
        # 1. fill yesterday's orders at today's open
        for o in pending:
            b = bars.get((o["sym"], d))
            if b is None or b.adj_open <= 0:
                continue
            entry = b.adj_open * (1 + slippage)
            stop = entry - STOP_ATR * o["atr"] * o["close"]
            equity = cash + sum(p["qty"] * p["last"] for p in pos.values())
            risk = max(entry - stop, entry * 0.01)
            qty = int(min(RISK_PER_TRADE * equity / risk, MAX_WEIGHT * equity / entry, cash / entry))
            if qty <= 0:
                continue
            cash -= qty * entry
            pos[o["sym"]] = {"qty": qty, "entry": entry, "stop": stop, "day0": d, "n": 0, "last": entry,
                             "industry": o["industry"]}
        pending = []
        # 2. stops and time exits on today's bar
        for s in list(pos):
            p, b = pos[s], bars.get((s, d))
            if b is None:
                continue
            p["n"] += 1
            exit_px = None
            if b.adj_low <= p["stop"]:
                exit_px = min(b.adj_open, p["stop"]) * (1 - slippage)
            elif p["n"] >= hold:
                exit_px = b.adj_close * (1 - slippage)
            p["last"] = b.adj_close
            if exit_px is not None:
                notional = p["qty"] * p["entry"]
                fee = (charges.cnc_round_trip(notional, 0)) * notional
                cash += p["qty"] * exit_px - fee
                trades.append({"sym": s, "entry_day": p["day0"], "exit_day": d, "days": p["n"],
                               "ret": exit_px / p["entry"] - 1 - fee / notional, "stopped": exit_px < p["entry"] and b.adj_low <= p["stop"]})
                del pos[s]
        equity = cash + sum(p["qty"] * p["last"] for p in pos.values())
        curve.append((d, equity, len(pos)))
        # 3. tonight's orders
        day = by_day.get(d)
        if day is None or d >= end:
            continue
        slots = int(round(max_positions * exposure(day.iloc[0]))) - len(pos)
        if slots <= 0:
            continue
        held_ind = pd.Series([p["industry"] for p in pos.values()]).value_counts().to_dict()
        for r in day[day.pred > 0].sort_values("pred", ascending=False).itertuples():
            if slots <= 0:
                break
            if r.sym_id in pos or held_ind.get(r.industry, 0) >= MAX_PER_INDUSTRY and r.industry != "UNKNOWN":
                continue
            pending.append({"sym": r.sym_id, "atr": r.atr_pct, "close": r.adj_close, "industry": r.industry})
            held_ind[r.industry] = held_ind.get(r.industry, 0) + 1
            slots -= 1
    eq = pd.DataFrame(curve, columns=["date", "equity", "positions"]).set_index("date")
    return eq, pd.DataFrame(trades), nifty.set_index("date").close


def summary(eq, trades, nifty):
    def perf(s):
        s = s.dropna()
        if len(s) < 2:
            return {}
        r = s.pct_change().dropna()
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        return {"cagr_pct": round(((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100, 2) if yrs > 0 else None,
                "max_drawdown_pct": round((s / s.cummax() - 1).min() * 100, 2),
                "sharpe": round(r.mean() / r.std() * np.sqrt(250), 2) if r.std() > 0 else None,
                "total_pct": round((s.iloc[-1] / s.iloc[0] - 1) * 100, 2),
                "by_year_pct": {str(y): round((g.iloc[-1] / g.iloc[0] - 1) * 100, 2)
                                for y, g in s.groupby(s.index.year) if len(g) > 1}}
    out = {"portfolio": perf(eq.equity), "nifty50_buy_hold": perf(nifty.reindex(eq.index).ffill()),
           "avg_positions": round(float(eq.positions.mean()), 2)}
    if len(trades):
        out["trades"] = {"n": int(len(trades)), "win_pct": round((trades.ret > 0).mean() * 100, 1),
                         "avg_net_pct": round(trades.ret.mean() * 100, 3),
                         "stopped_pct": round(trades.stopped.mean() * 100, 1),
                         "avg_days": round(float(trades.days.mean()), 1)}
    return out
