"""Paper trades of every published pick, Rs 50,000 a stock, settled on NSE's real prices.

Swing (data/picks/swing-<session>.json):
  entry   the open of the next session after the pick, qty = floor(50,000 / open)
  SL      the suggested stop price; a session whose low touches it exits at the SL, or at that
          session's open when it gaps below
  exit    otherwise the close of the 10th session (the plan's hold_sessions)
  marks   every session in between is marked to its close: day P&L and total P&L per stock

Intraday (data/picks/intraday-<day>.json), longs and shorts:
  entry   the 9:30 one-minute bar's open (the first price after the pick is made), qty = floor(50,000 / entry)
  SL      the suggested stop (the other side of the 9:15-9:30 range); hit -> exit at the SL, or at the
          bar's open when it gaps through
  exit    otherwise the 15:09 bar's close (flat by 15:10)

P&L is after Zerodha charges (delivery for swing, MIS for intraday); no extra slippage is added, so
these are paper fills at the quoted prices. Prices are NSE's raw traded prices (daily bars) and
Upstox 1-minute candles scaled to them. A bonus or split inside a swing hold is flagged.

-> data/paper/book.json and docs/paper.json
"""
import datetime
import glob
import json
import os

from intraday import charges as mis
from swing import charges as cnc
from warehouse import config

STAKE = 50000.0
PICKS = os.path.join(config.DATA, "picks")
OUT = os.path.join(config.DATA, "paper", "book.json")
DOCS = os.path.join(config.ROOT, "docs", "paper.json")


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fee(kind, notional):
    if notional <= 0:
        return 0.0
    f = cnc.cnc_round_trip(notional, slippage=0) if kind == "swing" else mis.round_trip(notional, slippage=0)
    return round(f * notional, 2)


def swing_trades(con):
    trades = []
    for path in sorted(glob.glob(os.path.join(PICKS, "swing-*.json"))):
        doc = _load(path)
        session = datetime.date.fromisoformat(doc["session"])
        for rank, p in enumerate(doc.get("picks", []), 1):
            hold = int(p.get("hold_sessions", 10))
            bars = con.execute("""SELECT date, open, high, low, close, adj_factor FROM eq_daily
                                  WHERE sym_id = ? AND date > ? ORDER BY date LIMIT ?""",
                               [p["symbol"], session, hold]).fetchall()
            t = {"id": "S-%s-%s" % (session, p["symbol"]), "kind": "swing", "side": "long", "symbol": p["symbol"],
                 "industry": p.get("industry"), "rank": rank, "signal_date": str(session), "hold": hold,
                 "sl": float(p["stop"]), "ref_close": float(p["ref_close"]), "status": "pending",
                 "entry_date": None, "entry": None, "qty": 0, "exit_date": None, "exit": None, "exit_reason": None,
                 "gross": 0.0, "charges": 0.0, "net": 0.0, "marks": [], "flag": None}
            if not bars:
                trades.append(t)
                continue
            d0, o0 = bars[0][0], float(bars[0][1])
            qty = int(STAKE // o0)
            t.update(entry_date=str(d0), entry=round(o0, 2), qty=qty, status="open")
            prev_val, sl = 0.0, t["sl"]
            if bars[0][5] != bars[-1][5]:
                t["flag"] = "bonus/split during the hold: prices after it are on a new basis"
            for k, (d, o, h, l, c, _f) in enumerate(bars, 1):
                o, l, c = float(o), float(l), float(c)
                exit_px, reason = None, None
                if k == 1 and o0 <= sl:
                    exit_px, reason = o0, "opened below SL"
                elif l <= sl:
                    exit_px, reason = (min(o, sl) if k > 1 else sl), "SL hit"
                elif k == hold:
                    exit_px, reason = c, "%d sessions done" % hold
                mark = exit_px if exit_px is not None else c
                total = (mark - o0) * qty
                t["marks"].append({"date": str(d), "session": k, "close": round(c, 2), "mark": round(mark, 2),
                                   "day_pnl": round(total - prev_val, 2), "total_pnl": round(total, 2)})
                prev_val = total
                if exit_px is not None:
                    t.update(status="closed", exit_date=str(d), exit=round(exit_px, 2), exit_reason=reason)
                    break
            last = t["marks"][-1]["mark"]
            t["gross"] = round((last - o0) * qty, 2)
            # an open position has paid its buy-side charges; the rest come off when it closes
            full = _fee("swing", o0 * qty)
            t["charges"] = full if t["status"] == "closed" else round(full / 2, 2)
            t["net"] = round(t["gross"] - t["charges"], 2)
            trades.append(t)
    return trades


def intraday_trades(con):
    trades = []
    for path in sorted(glob.glob(os.path.join(PICKS, "intraday-*.json"))):
        doc = _load(path)
        day = datetime.date.fromisoformat(doc["day"])
        t_dec = doc.get("decision_time", "09:30")
        for side in ("long", "short"):
            for rank, p in enumerate(doc.get(side, []), 1):
                t = {"id": "I-%s-%s-%s" % (day, side[0].upper(), p["symbol"]), "kind": "intraday", "side": side,
                     "symbol": p["symbol"], "rank": rank, "signal_date": str(day), "sl": float(p["stop"]),
                     "status": "pending", "entry_date": str(day), "entry": None, "qty": 0, "exit_date": None,
                     "exit": None, "exit_reason": None, "exit_time": None, "gross": 0.0, "charges": 0.0,
                     "net": 0.0, "marks": [], "flag": None}
                bars = con.execute("""SELECT ts, open, high, low, close FROM minute_bars
                                      WHERE symbol = ? AND date = ? AND ts::TIME >= CAST(? AS TIME)
                                        AND ts::TIME < TIME '15:10' ORDER BY ts""",
                                   [p["symbol"], day, t_dec]).fetchall()
                if not bars:
                    trades.append(t)
                    continue
                entry = float(bars[0][1])          # the plan's prices are on this same 1-minute scale
                qty = int(STAKE // entry)
                sl = t["sl"]
                exit_px, reason, at = None, None, None
                for ts, o, h, l, c in bars:
                    o, h, l = float(o), float(h), float(l)
                    if side == "long" and l <= sl:
                        exit_px, reason, at = min(o, sl), "SL hit", ts
                        break
                    if side == "short" and h >= sl:
                        exit_px, reason, at = max(o, sl), "SL hit", ts
                        break
                last_ts = bars[-1][0]
                if exit_px is None:
                    exit_px, at = float(bars[-1][4]), last_ts
                    reason = "squared off 15:10" if last_ts.time() >= datetime.time(15, 9) else None
                sign = 1 if side == "long" else -1
                gross = sign * (exit_px - entry) * qty
                done = reason is not None
                t.update(entry=round(entry, 2), qty=qty, status="closed" if done else "open",
                         exit_date=str(day) if done else None, exit=round(exit_px, 2), exit_reason=reason,
                         exit_time=at.strftime("%H:%M") if at else None, gross=round(gross, 2))
                t["charges"] = _fee("intraday", entry * qty) if done else 0.0
                t["net"] = round(gross - t["charges"], 2)
                t["marks"] = [{"date": str(day), "session": 1, "close": round(exit_px, 2), "mark": round(exit_px, 2),
                               "day_pnl": round(gross, 2), "total_pnl": round(gross, 2)}]
                trades.append(t)
    return trades


def daily(trades):
    """P&L per day: swing mark-to-market moves and intraday results (gross); charges on the exit day."""
    rows = {}
    for t in trades:
        for m in t["marks"]:
            r = rows.setdefault(m["date"], {"date": m["date"], "swing": 0.0, "intraday": 0.0, "charges": 0.0})
            r[t["kind"]] += m["day_pnl"]
        if t["status"] == "closed":
            r = rows.setdefault(t["exit_date"], {"date": t["exit_date"], "swing": 0.0, "intraday": 0.0, "charges": 0.0})
            r["charges"] += t["charges"]
    out = sorted(rows.values(), key=lambda r: r["date"])
    cum = 0.0
    for r in out:
        r["net"] = round(r["swing"] + r["intraday"] - r["charges"], 2)
        cum += r["net"]
        r["cumulative"] = round(cum, 2)
        for k in ("swing", "intraday", "charges"):
            r[k] = round(r[k], 2)
    return out


def summary(trades):
    def block(ts):
        closed = [t for t in ts if t["status"] == "closed"]
        opened = [t for t in ts if t["status"] == "open"]
        wins = [t for t in closed if t["net"] > 0]
        return {"trades": len(ts), "closed": len(closed), "open": len(opened),
                "pending": len([t for t in ts if t["status"] == "pending"]),
                "realised": round(sum(t["net"] for t in closed), 2),
                "unrealised": round(sum(t["gross"] for t in opened), 2),
                "invested_open": round(sum((t["entry"] or 0) * t["qty"] for t in opened), 2),
                "win_pct": round(len(wins) / len(closed) * 100, 1) if closed else None}
    return {"swing": block([t for t in trades if t["kind"] == "swing"]),
            "intraday": block([t for t in trades if t["kind"] == "intraday"]),
            "all": block(trades)}


def update(con, log=print):
    trades = swing_trades(con) + intraday_trades(con)
    doc = {"built": datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST"), "stake": STAKE,
           "last_session": str(con.execute("SELECT max(date) FROM calendar").fetchone()[0]),
           "summary": summary(trades), "daily": daily(trades), "trades": trades}
    for p in (OUT, DOCS):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, default=str)
        os.replace(p + ".tmp", p)
    s = doc["summary"]
    log("paper: %d swing (%d open, %d pending), %d intraday; realised Rs %s, unrealised Rs %s" % (
        s["swing"]["trades"], s["swing"]["open"], s["swing"]["pending"], s["intraday"]["trades"],
        format(int(s["all"]["realised"]), ","), format(int(s["all"]["unrealised"]), ",")))
    return doc
