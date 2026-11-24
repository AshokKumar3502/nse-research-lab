"""Paper trades of every published pick, Rs 50,000 a stock, settled on NSE's real prices.

Swing (data/picks/swing-<session>.json):
  entry   9:30 IST on the next session after the pick (the 9:30 one-minute bar's open, like intraday; the
          day's open when no 1-minute data exists), qty = floor(50,000 / entry)
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
ENTRY_TIME = "09:30"     # swing and intraday positions are both taken at 9:30 IST
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


def live_day(con, symbol, session, bars, hold):
    """Today's bar from the 1-minute candles while NSE's end-of-day file is not out yet (marked live):
    open = the 9:15 open, high/low so far, close = the latest price. [] when not needed or not available."""
    today = config.today_ist()
    if len(bars) >= hold or today <= session or (bars and bars[-1][0] >= today) or today.weekday() >= 5:
        return []
    try:
        r = con.execute("""SELECT arg_min(open, ts), max(high), min(low), arg_max(close, ts), count(*) FROM minute_bars
                           WHERE symbol = ? AND date = ? AND ts::TIME >= TIME '09:15'""", [symbol, today]).fetchone()
    except Exception:     # noqa: BLE001 - no minute data in this warehouse
        return []
    if not r or not r[4]:
        return []
    at = con.execute("SELECT max(ts) FROM minute_bars WHERE symbol = ? AND date = ?", [symbol, today]).fetchone()[0]
    # the last minute bar that started at HH:MM closes a minute later: that is the price's time
    at = (at + datetime.timedelta(minutes=1)).strftime("%H:%M") if at else "live"
    return [(today, r[0], r[1], r[2], r[3], bars[-1][5] if bars else 1.0, at)]


def entry_at(con, symbol, day):
    """The 9:30 entry on `day` from the 1-minute candles: (price, lowest low from 9:30 on) or None."""
    try:
        r = con.execute("""SELECT arg_min(open, ts), min(low), count(*) FROM minute_bars
                           WHERE symbol = ? AND date = ? AND ts::TIME >= CAST(? AS TIME)""",
                        [symbol, day, ENTRY_TIME]).fetchone()
    except Exception:     # noqa: BLE001
        return None
    return (float(r[0]), float(r[1])) if r and r[2] else None


def _previous():
    """Trades from the last published book, by id: a recorded 9:30 entry must never change on a rebuild."""
    try:
        return {t["id"]: t for t in _load(DOCS).get("trades", [])}
    except Exception:     # noqa: BLE001 - no book yet
        return {}


# The swing rule that passed validation sells at the 10th session's close with no stop (in the 10-year test the
# 2-ATR stop lowered results; a 24 Aug - 19 Nov 2026 replay: -Rs 1.32 lakh with it, -Rs 0.58 lakh without).
# Picks made from this session on follow the pick's use_stop flag (the scan writes False); earlier trades keep
# the stop they were opened with, so recorded results do not change.
SWING_NO_STOP_FROM = datetime.date(2026, 11, 23)


def _stop(p, kind, session):
    if p.get("stop") is None:
        return None
    if kind == "swing" and session >= SWING_NO_STOP_FROM and not p.get("use_stop", False):
        return None
    return float(p["stop"])


def swing_trades(con, pattern="swing-*.json", kind="swing", tag="S"):
    """Multi-session long trades from pick files: the swing model (S-) and the results-jump paper test (R-)."""
    trades, prev = [], _previous()
    for path in sorted(glob.glob(os.path.join(PICKS, pattern))):
        doc = _load(path)
        session = datetime.date.fromisoformat(doc["session"])
        for rank, p in enumerate(doc.get("picks", []), 1):
            hold = int(p.get("hold_sessions", 10))
            bars = con.execute("""SELECT date, open, high, low, close, adj_factor FROM eq_daily
                                  WHERE sym_id = ? AND date > ? ORDER BY date LIMIT ?""",
                               [p["symbol"], session, hold]).fetchall()
            bars = bars + live_day(con, p["symbol"], session, bars, hold)
            t = {"id": "%s-%s-%s" % (tag, session, p["symbol"]), "kind": kind, "side": "long", "symbol": p["symbol"],
                 "industry": p.get("industry"), "rank": rank, "signal_date": str(session), "hold": hold,
                 "sl": _stop(p, kind, session), "ref_close": float(p["ref_close"]), "status": "pending",
                 "entry_date": None, "entry": None, "qty": 0, "exit_date": None, "exit": None, "exit_reason": None,
                 "gross": 0.0, "charges": 0.0, "net": 0.0, "marks": [], "flag": None}
            if not bars:
                trades.append(t)
                continue
            d0 = bars[0][0]
            # enter at 9:30 on the first session (the same time as intraday); the 9:15 open if no 1-minute data
            e = entry_at(con, p["symbol"], d0)
            old = prev.get(t["id"]) or {}
            if not e and old.get("entry") and old.get("entry_date") == str(d0) \
                    and str(old.get("entry_at", "")).endswith(ENTRY_TIME):
                # the 1-minute bars are gone (the cloud keeps a slim window): keep the 9:30 entry already recorded
                o0, etime = float(old["entry"]), ENTRY_TIME
                low1 = old.get("entry_low")
                if low1 is None:     # books before entry_low: day 1 was checked on real 1-minute prices then
                    stopped_d1 = old.get("status") == "closed" and old.get("exit_date") == str(d0)
                    low1 = float(bars[0][3]) if stopped_d1 else max(float(bars[0][3]), (t["sl"] or 0.0) + 0.01)
            elif e:
                o0, low1, etime = e[0], e[1], ENTRY_TIME
            else:
                o0, low1, etime = float(bars[0][1]), float(bars[0][3]), "09:15"
            qty = int(STAKE // o0)
            t.update(entry_date=str(d0), entry=round(o0, 2), qty=qty, status="open", entry_at="%s %s" % (d0, etime),
                     entry_low=round(float(low1), 2))
            prev_val, sl = 0.0, (t["sl"] if t["sl"] is not None else float("-inf"))
            if bars[0][5] != bars[-1][5]:
                t["flag"] = "bonus/split during the hold: prices after it are on a new basis"
            for k, row in enumerate(bars, 1):
                d, o, h, l, c = row[0], float(row[1]), row[2], float(row[3]), float(row[4])
                at = row[6] if len(row) > 6 else "15:30"
                if k == 1:
                    l = low1                      # only prices from the entry time on count on day 1
                exit_px, reason = None, None
                if k == 1 and o0 <= sl:
                    exit_px, reason = o0, "entered below SL"
                elif l <= sl:
                    exit_px, reason = (min(o, sl) if k > 1 else sl), "SL hit"
                elif k == hold and at == "15:30":
                    exit_px, reason = c, "%d sessions done" % hold
                mark = exit_px if exit_px is not None else c
                total = (mark - o0) * qty
                t["marks"].append({"date": str(d), "session": k, "close": round(c, 2), "mark": round(mark, 2),
                                   "at": at, "day_pnl": round(total - prev_val, 2), "total_pnl": round(total, 2)})
                prev_val = total
                if exit_px is not None:
                    t.update(status="closed", exit_date=str(d), exit=round(exit_px, 2), exit_reason=reason)
                    break
            last = t["marks"][-1]["mark"]
            t["last"], t["last_at"] = last, "%s %s" % (t["marks"][-1]["date"], t["marks"][-1]["at"])
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
                last_at = at if done else bars[-1][0] + datetime.timedelta(minutes=1)
                t.update(entry=round(entry, 2), qty=qty, status="closed" if done else "open",
                         exit_date=str(day) if done else None, exit=round(exit_px, 2), exit_reason=reason,
                         exit_time=at.strftime("%H:%M") if at else None, gross=round(gross, 2),
                         entry_at="%s %s" % (day, t_dec), last=round(exit_px, 2),
                         last_at="%s %s" % (day, last_at.strftime("%H:%M")))
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
            r = rows.setdefault(m["date"], {"date": m["date"], "swing": 0.0, "intraday": 0.0, "option": 0.0, "results": 0.0, "momentum": 0.0, "charges": 0.0})
            r[t["kind"]] += m["day_pnl"]
        if t["status"] == "closed":
            r = rows.setdefault(t["exit_date"], {"date": t["exit_date"], "swing": 0.0, "intraday": 0.0, "option": 0.0, "results": 0.0, "momentum": 0.0, "charges": 0.0})
            r["charges"] += t["charges"]
    out = sorted(rows.values(), key=lambda r: r["date"])
    cum = 0.0
    for r in out:
        r["net"] = round(r["swing"] + r["intraday"] + r.get("option", 0.0) + r.get("results", 0.0) + r.get("momentum", 0.0) - r["charges"], 2)
        cum += r["net"]
        r["cumulative"] = round(cum, 2)
        for k in ("swing", "intraday", "option", "results", "momentum", "charges"):
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
            "option": block([t for t in trades if t["kind"] == "option"]),
            "results": block([t for t in trades if t["kind"] == "results"]),
            "momentum": block([t for t in trades if t["kind"] == "momentum"]),
            "all": block(trades)}


def update(con, log=print):
    trades = (swing_trades(con) + swing_trades(con, "rjump-*.json", "results", "R")
              + swing_trades(con, "mom-*.json", "momentum", "M") + intraday_trades(con))
    try:
        from optionlab import nifty_orb
        trades += nifty_orb.trades()
    except Exception as e:     # noqa: BLE001 - option rows are optional
        log("option trades: %s" % e)
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
