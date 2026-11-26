"""Replay the app's NIFTY option rule (optionlab/nifty_orb.py) day by day over the last N months, for the app's
Backtest tab.

  python -m optionlab.replay --months 6                    -> docs/data/backtest.json
  python -m optionlab.replay --months 60 --index sensex    -> docs/data/backtest-sensex.json (from Jun 2023)
  python -m optionlab.replay --months 60 --index bse       -> docs/data/backtest-bse.json (BSE Ltd stock options, from Dec 2024)

SENSEX: 1-minute SENSEX candles from Upstox (cached in data/options/sensex-1m.parquet). No BSE option history in
the warehouse, so the volatility is NIFTY's previous-day ATM IV; weekly expiry Friday to 2024, Tuesday Jan-Aug 2025,
Thursday from Sep 2025 (a holiday moves it to the trading day before); 1 lot = 20 (today's lot size); strike step 100.

Signal, entry and exit times and the stop-loss come from REAL NIFTY 1-minute prices. Option prices: Black-Scholes
with the previous day's real ATM implied volatility from NSE's bhavcopy, because 1-minute prices of expired
contracts are not available for free. Checked against real option prices (Oct-Nov 2026) that model flatters
buyers by about Rs 160-330 a trade, so the file also carries a corrected total. Days the app paper-traded on real
option prices use those real trades. Also given: the same trade in NIFTY futures (real prices) as a check.
1 lot = 65 (today's lot size) for every day; charges as nifty_orb.charges. Capital = premium paid for 1 lot.
"""
import argparse
import datetime as dt
import glob
import json
import math
import os

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from warehouse import config, db

from .nifty_orb import EXIT, LAST_ENTRY, RANGE_END, STEP, charges

LOT, R = 65, 0.065
BIAS = (160, 330)
OUT = os.path.join(config.ROOT, "docs", "data", "backtest.json")
IX = {"NIFTY": {"lot": LOT, "step": STEP, "out": OUT, "file": "nifty", "first": dt.date(2022, 1, 3)},
      "SENSEX": {"lot": 20, "step": 100, "out": os.path.join(config.ROOT, "docs", "data", "backtest-sensex.json"),
                 "file": "sensex", "first": dt.date(2023, 6, 1)},
      "BSE": {"lot": 200, "step": 50, "out": os.path.join(config.ROOT, "docs", "data", "backtest-bse.json"),
              "file": "bse", "first": dt.date(2024, 12, 2), "symbol": "BSE", "name": "BSE Ltd"},
      "ADANIENT": {"lot": 309, "step": 50, "out": os.path.join(config.ROOT, "docs", "data", "backtest-adanient.json"),
                   "file": "adanient", "first": dt.date(2022, 1, 3), "symbol": "ADANIENT", "name": "Adani Enterprises"},
      "DIXON": {"lot": 50, "step": 100, "out": os.path.join(config.ROOT, "docs", "data", "backtest-dixon.json"),
                "file": "dixon", "first": dt.date(2022, 1, 3), "symbol": "DIXON", "name": "Dixon Technologies"},
      "PNB": {"lot": 8000, "step": 1, "out": os.path.join(config.ROOT, "docs", "data", "backtest-pnb.json"),
              "file": "pnb", "first": dt.date(2022, 1, 3), "symbol": "PNB", "name": "Punjab National Bank"}}
SNAP = [1, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10]


def replay_stock(con, start, end, index):
    """The rule on a stock's own options (BSE Ltd). Minute bars are split/bonus adjusted and strikes are not, so each
    day's bars are scaled back to the traded price (raw close / adjusted close snapped to 1, 1/2, 1/3 ...); the
    quantity is today's lot in today's shares. Strike = the listed strike nearest the price; nearest monthly expiry;
    volatility = the stock's own previous-day ATM IV. Same fields as replay()."""
    sym, lot_now = IX[index]["symbol"], IX[index]["lot"]
    m = con.execute("SELECT date, ts, open, high, low, close FROM minute_bars WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY ts",
                    [sym, start, end]).df()
    m["ts"] = pd.to_datetime(m.ts)
    m["date"] = m.ts.dt.date
    raw = con.execute("SELECT date, close FROM eq_daily_raw WHERE symbol = ? AND series = 'EQ' AND date >= ? AND date <= ? ORDER BY date",
                      [sym, start - dt.timedelta(days=10), end]).df()
    raw["date"] = pd.to_datetime(raw.date).dt.date
    rclose, days = dict(zip(raw.date, raw.close)), list(raw.date)
    opts = con.execute("SELECT date, expiry, strike, option_type, close FROM fo_options WHERE symbol = ? AND instrument = 'OPTSTK' "
                       "AND date >= ? AND date <= ? AND close > 0", [sym, start - dt.timedelta(days=10), end]).df()
    opts["date"] = pd.to_datetime(opts.date).dt.date
    opts["expiry"] = pd.to_datetime(opts.expiry).dt.date
    og = {d: g for d, g in opts.groupby("date")}
    byday = {d: g.reset_index(drop=True) for d, g in m.groupby("date")}
    rows = []
    for i in range(1, len(days)):
        d, prev = days[i], days[i - 1]
        g, po = byday.get(d), og.get(prev)
        if d < start or g is None or po is None or len(g) < 300:
            continue
        r = float(g.close.iloc[-1]) / rclose[d]
        f = min(SNAP, key=lambda x: abs(x - r))
        if abs(r / f - 1) > 0.03:
            continue
        sc = 1 / f
        tt = g.ts.dt.time.to_numpy()
        ts = [x.to_pydatetime() for x in g.ts]
        hi, lo, op, cl = (g.high.to_numpy() * sc, g.low.to_numpy() * sc, g.open.to_numpy() * sc, g.close.to_numpy() * sc)
        j0, k_end = int(np.searchsorted(tt, RANGE_END)), int(np.searchsorted(tt, EXIT))
        if k_end >= len(tt) or j0 < 20 or tt[0] > dt.time(9, 16):
            continue
        rh, rl = float(hi[:j0].max()), float(lo[:j0].min())
        row = {"date": str(d), "range_low": round(rl, 1), "range_high": round(rh, 1)}
        sig = None
        for k in range(j0, len(tt) - 1):
            if tt[k] >= LAST_ENTRY:
                break
            if cl[k] > rh or cl[k] < rl:
                sig = (k + 1, bool(cl[k] > rh))
                break
        if sig is None:
            rows.append(dict(row, status="no trade", why="No breakout of the 9:15-9:45 range by 13:00"))
            continue
        j, call = sig
        exps = sorted(e for e in po.expiry.unique() if e >= d)
        if not exps:
            continue
        exp = exps[0]
        pe = po[po.expiry == exp]
        strikes = np.array(sorted(pe.strike.unique()))
        if len(strikes) < 3:
            continue
        K0 = float(strikes[np.abs(strikes - rclose[prev]).argmin()])
        ivs = [x for x in (_iv(rr.close, rclose[prev], K0, _years(dt.datetime.combine(prev, dt.time(15, 30)), exp),
                               rr.option_type == "CE") for rr in pe[pe.strike == K0].itertuples()) if x]
        if not ivs:
            continue
        sg = float(np.mean(ivs))
        K = float(strikes[np.abs(strikes - op[j]).argmin()])
        qty = int(round(lot_now * f))
        entry = _bs(float(op[j]), K, _years(ts[j], exp), sg, call)
        if entry < 0.5:
            continue
        opt_low = min(_bs(lo[k] if call else hi[k], K, _years(ts[k], exp), sg, call) for k in range(0, j0))
        hit, kx, exitp, s_exit = False, k_end, None, float(op[k_end])
        for k in range(j, k_end + 1):
            if opt_low < entry and _bs(lo[k] if call else hi[k], K, _years(ts[k], exp), sg, call) <= opt_low:
                hit, kx, exitp, s_exit = True, k, opt_low, float(lo[k] if call else hi[k])
                break
        if exitp is None:
            exitp = _bs(s_exit, K, _years(ts[kx], exp), sg, call)
        bests = [_bs(hi[k] if call else lo[k], K, _years(ts[k], exp), sg, call) for k in range(j, kx + 1)]
        kb = int(np.argmax(bests))
        peak = max(bests[kb], entry)
        row.update(high=round(peak, 2), high_time=ts[j + kb].strftime("%H:%M"),
                   high_pnl=round((peak - entry) * qty - charges(entry, peak, qty)))
        rows.append(dict(row, status="closed", side="CE" if call else "PE", strike=int(K), expiry=str(exp),
                         expiry_day=bool(d == exp), entry_time=ts[j].strftime("%H:%M"), exit_time=ts[kx].strftime("%H:%M"),
                         why="SL: option back to its 9:15-9:45 low" if hit else "15:15 exit", sl_option=round(opt_low, 2),
                         nifty_entry=round(float(op[j]), 1), nifty_exit=round(s_exit, 1),
                         nifty_pts=round((s_exit - float(op[j])) * (1 if call else -1), 1),
                         entry=round(entry, 2), exit=round(exitp, 2), qty=qty,
                         pnl=round((exitp - entry) * qty - charges(entry, exitp, qty)),
                         capital=round(entry * qty), source="model"))
    return rows
SENSEX_CACHE = os.path.join(config.DATA, "options", "sensex-1m.parquet")


def sensex_bars(start, end, log=print):
    """SENSEX 1-minute candles [start, end] from Upstox, cached; only missing months are fetched."""
    from warehouse.loaders import minute
    cols = ["date", "ts", "open", "high", "low", "close"]
    have = pd.read_parquet(SENSEX_CACHE) if os.path.exists(SENSEX_CACHE) else pd.DataFrame(columns=cols)
    got = set(pd.to_datetime(have.ts).dt.strftime("%Y-%m")) if len(have) else set()
    new = []
    for a, b in minute._months(start, end):
        if a.strftime("%Y-%m") in got and b < end:
            continue
        new += [r[:6] for r in minute.fetch_upstox("BSE_INDEX|SENSEX", a, b)]
    if new:
        have = pd.concat([have, pd.DataFrame(new, columns=cols)])
        have["ts"] = pd.to_datetime(have.ts)
        have = have.drop_duplicates("ts").sort_values("ts")
        have["date"] = have.ts.dt.date
        os.makedirs(os.path.dirname(SENSEX_CACHE), exist_ok=True)
        have.to_parquet(SENSEX_CACHE)
        log("sensex candles: +%d minutes" % len(new))
    m = have.copy()
    m["ts"] = pd.to_datetime(m.ts)
    m["date"] = m.ts.dt.date
    return m[(m.date >= start) & (m.date <= end)][cols]


def sensex_expiry(d, trading):
    wd = 4 if d < dt.date(2025, 1, 1) else 1 if d < dt.date(2025, 9, 1) else 3
    e = d + dt.timedelta(days=(wd - d.weekday()) % 7)
    while e > d and e not in trading:
        e -= dt.timedelta(days=1)
    return e


def _bs(S, K, T, s, call):
    if T <= 1e-6:
        return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (math.log(S / K) + (R + 0.5 * s * s) * T) / (s * math.sqrt(T))
    d2 = d1 - s * math.sqrt(T)
    if call:
        return S * norm.cdf(d1) - K * math.exp(-R * T) * norm.cdf(d2)
    return K * math.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def _iv(price, S, K, T, call):
    try:
        return brentq(lambda s: _bs(S, K, T, s, call) - price, 0.01, 3.0)
    except Exception:     # noqa: BLE001
        return None


def _years(t, e):
    return max((dt.datetime.combine(e, dt.time(15, 30)) - t).total_seconds() / (365 * 86400), 0.0)


def _fut_cost(a, b):
    return 40 + 0.0002 * max(a, b) * LOT + 0.0000173 * (a + b) * LOT * 1.18 + 2 * LOT


def replay(con, start, end, index="NIFTY"):
    lot_, step_ = IX[index]["lot"], IX[index]["step"]
    if index == "SENSEX":
        m = sensex_bars(start, end)
    else:
        m = con.execute("SELECT date, ts, open, high, low, close FROM minute_bars WHERE symbol = 'NIFTY 50' "
                        "AND date >= ? AND date <= ? ORDER BY ts", [start, end]).df()
    m["ts"] = pd.to_datetime(m.ts)
    m["date"] = m.ts.dt.date
    idx = con.execute("SELECT date, close FROM idx_daily WHERE index_key = 'nifty 50' AND date >= ? AND date <= ? ORDER BY date",
                      [start - dt.timedelta(days=10), end]).df()
    idx["date"] = pd.to_datetime(idx.date).dt.date
    close, days = dict(zip(idx.date, idx.close)), list(idx.date)
    trading = set(days)
    opts = con.execute("SELECT date, expiry, strike, option_type, close FROM fo_options WHERE symbol = 'NIFTY' "
                       "AND instrument = 'OPTIDX' AND date >= ? AND date <= ? AND close > 0",
                       [start - dt.timedelta(days=10), end]).df()
    opts["date"] = pd.to_datetime(opts.date).dt.date
    opts["expiry"] = pd.to_datetime(opts.expiry).dt.date
    og = {d: g for d, g in opts.groupby("date")}
    byday = {d: g.reset_index(drop=True) for d, g in m.groupby("date")}
    rows = []
    for i in range(1, len(days)):
        d, prev = days[i], days[i - 1]
        g, po = byday.get(d), og.get(prev)
        if d < start or g is None or po is None or len(g) < 300:
            continue
        tt = g.ts.dt.time.to_numpy()
        ts = [x.to_pydatetime() for x in g.ts]
        hi, lo, op, cl = g.high.to_numpy(), g.low.to_numpy(), g.open.to_numpy(), g.close.to_numpy()
        j0, k_end = int(np.searchsorted(tt, RANGE_END)), int(np.searchsorted(tt, EXIT))
        if k_end >= len(tt):
            continue
        rh, rl = float(hi[:j0].max()), float(lo[:j0].min())
        row = {"date": str(d), "range_low": round(rl, 1), "range_high": round(rh, 1)}
        sig = None
        for k in range(j0, len(tt) - 1):
            if tt[k] >= LAST_ENTRY:
                break
            if cl[k] > rh or cl[k] < rl:
                sig = (k + 1, bool(cl[k] > rh))
                break
        if sig is None:
            rows.append(dict(row, status="no trade", why="No breakout of the 9:15-9:45 range by 13:00"))
            continue
        j, call = sig
        exps = sorted(e for e in po.expiry.unique() if e >= d)
        if not exps:
            continue
        exp = exps[0]
        K0 = round(close[prev] / STEP) * STEP
        p_ = po[(po.expiry == exp) & (po.strike == K0)]
        ivs = [x for x in (_iv(r.close, close[prev], K0, _years(dt.datetime.combine(prev, dt.time(15, 30)), exp),
                               r.option_type == "CE") for r in p_.itertuples()) if x]
        if not ivs:
            continue
        sg = float(np.mean(ivs))          # NIFTY's ATM IV (also used for SENSEX: no BSE option history here)
        if index == "SENSEX":
            exp = sensex_expiry(d, trading)
        K = round(op[j] / step_) * step_
        entry = _bs(float(op[j]), K, _years(ts[j], exp), sg, call)
        # stop-loss: the option's own 9:15-9:45 low (as the live rule from 26 Nov 2026)
        opt_low = min(_bs(lo[k] if call else hi[k], K, _years(ts[k], exp), sg, call) for k in range(0, j0))
        hit, kx, exitp, s_exit = [], k_end, None, float(op[k_end])
        for k in range(j, k_end + 1):
            if opt_low < entry and _bs(lo[k] if call else hi[k], K, _years(ts[k], exp), sg, call) <= opt_low:
                hit, kx, exitp, s_exit = [k], k, opt_low, float(lo[k] if call else hi[k])
                break
        if exitp is None:
            exitp = _bs(s_exit, K, _years(ts[kx], exp), sg, call)
        pts = (s_exit - float(op[j])) * (1 if call else -1)
        # the best price reached while the trade was open (hindsight: what selling at the high would have made)
        bests = [_bs(hi[k] if call else lo[k], K, _years(ts[k], exp), sg, call) for k in range(j, kx + 1)]
        kb = int(np.argmax(bests))
        peak = max(bests[kb], entry)
        row.update(high=round(peak, 2), high_time=ts[j + kb].strftime("%H:%M"),
                   high_pnl=round((peak - entry) * lot_ - charges(entry, peak, lot_)))
        rows.append(dict(row, status="closed", side="CE" if call else "PE", strike=int(K), expiry=str(exp),
                         expiry_day=bool(d == exp), entry_time=ts[j].strftime("%H:%M"), exit_time=ts[kx].strftime("%H:%M"),
                         why="SL: option back to its 9:15-9:45 low" if len(hit) else "15:15 exit", sl_option=round(opt_low, 2),
                         nifty_entry=round(float(op[j]), 1), nifty_exit=round(s_exit, 1), nifty_pts=round(pts, 1),
                         entry=round(entry, 2), exit=round(exitp, 2),
                         pnl=round((exitp - entry) * lot_ - charges(entry, exitp, lot_)),
                         capital=round(entry * lot_), source="model",
                         **({"futures_pnl": round(pts * LOT - _fut_cost(float(op[j]), s_exit))} if index == "NIFTY" else {})))
    return rows


def real_trades(after, index="NIFTY"):
    out = []
    for f in sorted(glob.glob(os.path.join(config.ROOT, "state", "picks", "option-%s-*.json" % IX[index]["file"]))):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if d["day"] <= str(after):
            continue
        base = {"date": d["day"], "range_low": d.get("range_low"), "range_high": d.get("range_high")}
        if d.get("status") == "closed":
            out.append(dict(base, status="closed", side=d["side"], strike=int(d["strike"]), expiry=d["expiry"],
                            expiry_day=d["expiry"] == d["day"], entry_time=d["entry_at"][11:], exit_time=d["exit_at"][11:],
                            why=d["why"].replace("back to the other side of the range", "back through the range"),
                            entry=d["entry"], exit=d["exit"], pnl=round(d["net"]), capital=round(d["entry"] * d["lot"]),
                            source="real"))
        elif d.get("status") == "no trade":
            out.append(dict(base, status="no trade", why=d.get("note", "No breakout")))
        elif d.get("status") == "no market":
            out.append(dict(base, status="holiday", why="Market holiday"))
    return out


APP_DROP = ("range_low", "range_high", "nifty_entry", "nifty_exit", "nifty_pts", "futures_pnl")


def sanitize(doc):
    """App-facing copy of a backtest document: results only, no description of the rule."""
    for d in doc.get("days", []):
        for k in APP_DROP:
            d.pop(k, None)
        w = str(d.get("why") or "")
        d["why"] = "Stop-loss" if w.startswith("SL") else "Time exit" if "15:15" in w else "No signal" if d.get("status") == "no trade" else w
    m = doc.get("method") or {}
    doc["method"] = {"lot": m.get("lot"), "note": "Option prices before the paper trade are modelled; real paper-trade days use real prices."}
    doc.get("summary", {}).pop("futures_net", None)
    return doc


def real_vs_model(log=print):
    """Each real paper-trade day next to what the backtest model gives for the same day -> docs/data/realcheck.json."""
    from .nifty_orb import INDEXES, PICKS
    con = db.connect(read_only=True)
    out = []
    for index, ix in IX.items():
        files = sorted(glob.glob(os.path.join(PICKS, "option-%s-*.json" % ix["file"])))
        for f in files:
            with open(f, encoding="utf-8") as fh:
                r = json.load(fh)
            if r.get("status") != "closed":
                continue
            d = dt.date.fromisoformat(r["day"])
            row = {"index": index, "date": r["day"],
                   "real": {"side": r["side"], "strike": r["strike"], "entry": r["entry"], "exit": r["exit"],
                            "entry_time": r["entry_at"][11:], "exit_time": r["exit_at"][11:], "pnl": round(r["net"]),
                            "why": "Stop-loss" if str(r.get("why", "")).startswith("SL") else "Time exit"}, "model": None}
            try:
                m = (replay_stock(con, d, d, index) if "symbol" in ix else replay(con, d, d, index))
                m = [x for x in m if x["date"] == r["day"] and x["status"] == "closed"]
                if m:
                    x = m[0]
                    row["model"] = {"side": x["side"], "strike": x["strike"], "entry": x["entry"], "exit": x["exit"],
                                    "entry_time": x["entry_time"], "exit_time": x["exit_time"], "pnl": x["pnl"],
                                    "why": "Stop-loss" if x["why"].startswith("SL") else "Time exit"}
            except Exception as e:     # noqa: BLE001
                log("real vs model %s %s: %s" % (index, r["day"], e))
            out.append(row)
    doc = {"built": dt.datetime.now(config.IST).strftime("%d %b %Y, %H:%M IST"), "rows": out}
    p = os.path.join(config.ROOT, "docs", "data", "realcheck.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    log("real vs model: %d real trades, %d with a model result" % (len(out), sum(1 for x in out if x["model"])))
    return doc


def build(months=6, log=print, index="NIFTY"):
    con = db.connect(read_only=True)
    end = con.execute("SELECT max(date) FROM minute_bars WHERE symbol = 'NIFTY 50'").fetchone()[0]
    start = max((pd.Timestamp(end) - pd.DateOffset(months=months)).date(), IX[index]["first"])
    rows = (replay_stock(con, start, end, index) if "symbol" in IX[index] else replay(con, start, end, index)) + real_trades(end, index)
    rows.sort(key=lambda r: r["date"])
    cum = 0
    for r in rows:
        cum += r.get("pnl", 0) or 0
        r["cum"] = cum
    tr = [r for r in rows if r["status"] == "closed"]
    pn = [r["pnl"] for r in tr]
    wins = [x for x in pn if x > 0]
    peak, dd = 0, 0
    for r in rows:
        peak = max(peak, r["cum"])
        dd = min(dd, r["cum"] - peak)
    model_n = sum(1 for r in tr if r["source"] == "model")
    months_out = []
    for mo in sorted({r["date"][:7] for r in rows}):
        t = [r for r in tr if r["date"].startswith(mo)]
        months_out.append({"month": mo, "days": sum(1 for r in rows if r["date"].startswith(mo) and r["status"] != "holiday"),
                           "trades": len(t), "won": sum(1 for r in t if r["pnl"] > 0), "net": sum(r["pnl"] for r in t),
                           "capital_max": max((r["capital"] for r in t), default=0)})
    doc = {"built": dt.datetime.now(config.IST).strftime("%d %b %Y, %H:%M IST"),
           "from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None,
           "index": index,
           "method": {"rule": index + " 9:15-9:45 range breakout; first 1-minute close outside it by 13:00 buys the ATM call (up) "
                              "or put (down) at the next minute; otherwise exit at 15:15. 1 lot, nearest weekly expiry. "
                              "Stop-loss: the bought option's own 9:15-9:45 low (live from 26 Nov 2026; earlier real NIFTY "
                              "days used NIFTY crossing back through the range).",
                      "prices": ("Times and levels: real %s 1-minute prices. Option prices: a model set each day from "
                                 "NSE's real NIFTY option prices%s; 'real' days are the app's own paper trades on real option "
                                 "prices." % (index, " (no BSE option history, so SENSEX uses NIFTY's volatility and the "
                                              "published weekly expiry day)" if index == "SENSEX" else "")) if "symbol" not in IX[index] else
                                ("Times and levels: real %s 1-minute share prices (adjusted back to the traded price "
                                 "around any bonus or split). Option prices: a model set each day from the stock's own real "
                                 "option prices (NSE bhavcopy), nearest monthly expiry and listed strike; 1 lot = %d shares "
                                 "today (fewer shares before a bonus or split, same exposure). 'Real' days are the app's own "
                                 "paper trades." % (IX[index]["name"], IX[index]["lot"])),
                      "bias": "The model flatters option buyers by about Rs %d-%d a trade (checked on real prices)." % BIAS,
                      "lot": IX[index]["lot"]},
           "summary": {"trading_days": sum(1 for r in rows if r["status"] != "holiday"), "trades": len(tr),
                       "no_trade_days": sum(1 for r in rows if r["status"] == "no trade"),
                       "won": len(wins), "win_pct": round(len(wins) / len(tr) * 100) if tr else None,
                       "net": sum(pn), "avg": round(sum(pn) / len(pn)) if pn else None,
                       "avg_win": round(sum(wins) / len(wins)) if wins else None,
                       "avg_loss": round(sum(x for x in pn if x <= 0) / max(1, len(pn) - len(wins))) if pn else None,
                       "best": max(pn, default=None), "worst": min(pn, default=None), "max_drawdown": dd,
                       "capital_avg": round(sum(r["capital"] for r in tr) / len(tr)) if tr else None,
                       "capital_max": max((r["capital"] for r in tr), default=None),
                       "corrected_net": [sum(pn) - BIAS[1] * model_n, sum(pn) - BIAS[0] * model_n],
                       "futures_net": sum(r.get("futures_pnl", 0) for r in tr if "futures_pnl" in r),
                       "high_net": sum(r["high_pnl"] for r in tr if "high_pnl" in r),
                       "high_vs_actual_trades": sum(1 for r in tr if "high_pnl" in r)},
           "months": months_out, "days": rows}
    out = IX[index]["out"]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(sanitize(doc), fh, separators=(",", ":"))
    s = doc["summary"]
    log("backtest %s to %s: %d trades, win %s%%, net Rs %s, worst fall Rs %s -> %s" % (
        doc["from"], doc["to"], s["trades"], s["win_pct"], s["net"], s["max_drawdown"], out))
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--index", default="nifty", choices=["nifty", "sensex", "bse", "adanient", "dixon", "pnb"])
    a = ap.parse_args()
    build(a.months, index=a.index.upper())
