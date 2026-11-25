"""NIFTY 9:45 range breakout, paper-traded on REAL NIFTY option prices (Upstox public 1-minute candles).

UNPROVEN (2026-11-23): the 2022-2026 modelled backtest (t 3.74) was matched by buying a random call or put at
9:45, and real Upstox option prices show the Black-Scholes model flatters buyers by Rs 160-330 a trade. This
paper trade on real prices is the test. The rule:
  range   NIFTY's high and low from 9:15 to 9:45
  entry   from 9:45 to 13:00, the first 1-minute close above the high -> buy the ATM CALL, below the low ->
          buy the ATM PUT, at the next minute's open; nearest expiry; one trade a day, one lot
  SL      NIFTY back to the other side of the 9:15-9:45 range (the low for a call, the high for a put)
  exit    15:15 if the SL has not been hit; no target
P&L is per lot after Zerodha charges, on the option's real traded prices. Nothing here places an order.

-> data/picks/option-nifty-YYYY-MM-DD.json
"""
import datetime as dt
import glob
import json
import os
import urllib.parse

from warehouse import config, net
from warehouse.loaders import minute

STEP = 50
RANGE_END, LAST_ENTRY, EXIT = dt.time(9, 45), dt.time(13, 0), dt.time(15, 15)
# from this day the stop-loss is the bought option's own 9:15-9:45 low (real 1-minute prices) instead of NIFTY
# crossing back through its range (6-month replay: +Rs 66,114 vs +Rs 62,653, slightly smaller losses)
OPTION_SL_FROM = dt.date(2026, 11, 26)
INDEX_KEY = "NSE_INDEX|Nifty 50"
CANDLES = "https://api.upstox.com/v3/historical-candle/intraday/%s/minutes/1"
PICKS = os.path.join(config.DATA, "picks")


def _path(day):
    return os.path.join(PICKS, "option-nifty-%s.json" % day)


def candles(key, day):
    minute._throttle()
    doc = net.get_json(CANDLES % urllib.parse.quote(key, safe=""))
    out = []
    for c in (doc.get("data") or {}).get("candles") or []:
        ts = dt.datetime.fromisoformat(c[0]).replace(tzinfo=None)
        if ts.date() == day:
            out.append((ts, float(c[1]), float(c[2]), float(c[3]), float(c[4])))
    return sorted(out)


def contracts(day):
    """Today's NIFTY options from Upstox's instrument list (cached for the day)."""
    p = os.path.join(config.DATA, "options", "nifty-contracts-%s.json" % day)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    rows = json.loads(net.get(minute.INSTRUMENTS_URL, timeout=120).decode("utf-8"))
    keep = [{"key": x["instrument_key"], "strike": float(x["strike_price"]), "type": x["instrument_type"],
             "expiry": dt.datetime.fromtimestamp(x["expiry"] / 1000, config.IST).date().isoformat(),
             "lot": int(x.get("lot_size") or 0), "symbol": x.get("trading_symbol")}
            for x in rows if x.get("segment") == "NSE_FO" and x.get("name") == "NIFTY"
            and x.get("instrument_type") in ("CE", "PE")]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(keep, fh)
    return keep


def charges(buy, sell, lot):
    """Zerodha option charges per lot: Rs 20 an order, STT 0.1% of the sell premium, NSE 0.03503%, stamp, GST."""
    brk, exch = 40.0, 0.0003503 * (buy + sell) * lot
    return round(brk + 0.001 * sell * lot + exch + 0.00003 * buy * lot + 0.18 * (brk + exch), 2)


def _save(doc, p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    return doc


def run(day=None, log=print):
    day = day or config.today_ist()
    now = dt.datetime.now(config.IST).replace(tzinfo=None)
    p = _path(day)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            old = json.load(fh)
        if old.get("status") in ("closed", "no trade"):
            return old
    doc = {"day": str(day), "status": "waiting", "updated": now.strftime("%H:%M"),
           "rule": "NIFTY 9:15-9:45 range breakout, ATM option, SL = the option's own 9:15-9:45 low, exit 15:15"}
    if day.weekday() >= 5:
        doc["status"] = "no market"
        return _save(doc, p)
    idx = [b for b in candles(INDEX_KEY, day) if b[0] + dt.timedelta(minutes=1) <= now]
    if not idx or idx[-1][0].time() < RANGE_END:
        doc["note"] = "The range builds from 9:15 to 9:45"
        return _save(doc, p)
    rng = [b for b in idx if b[0].time() < RANGE_END]
    hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
    doc.update(range_high=round(hi, 2), range_low=round(lo, 2))
    sig = None
    for i, b in enumerate(idx):
        if b[0].time() < RANGE_END:
            continue
        if b[0].time() >= LAST_ENTRY or i + 1 >= len(idx):
            break
        if b[4] > hi or b[4] < lo:
            sig = (i + 1, "CE" if b[4] > hi else "PE", b[0])
            break
    if sig is None:
        done = now.time() >= dt.time(13, 1)
        doc.update(status="no trade" if done else "waiting",
                   note="No breakout of the 9:15-9:45 range by 13:00" if done else "Watching for a breakout until 13:00")
        return _save(doc, p)
    j, side, sig_ts = sig
    entry_bar = idx[j]
    strike = round(entry_bar[1] / STEP) * STEP
    cs = [c for c in contracts(day) if c["type"] == side and c["strike"] == strike and c["expiry"] >= str(day)]
    if not cs:
        doc.update(status="error", note="No %s %d contract in the instrument list" % (side, strike))
        return _save(doc, p)
    c = min(cs, key=lambda x: x["expiry"])
    opt = {b[0]: b for b in candles(c["key"], day)}

    def at_or_after(t):
        return opt.get(t) or next((opt[x] for x in sorted(opt) if x >= t), None)
    ob = at_or_after(entry_bar[0])
    if ob is None:
        doc.update(status="waiting", note="No option trade yet after the signal")
        return _save(doc, p)
    entry = ob[1]
    sl_level = lo if side == "CE" else hi
    pre = [b for t, b in opt.items() if t.time() < RANGE_END]
    opt_low = min(b[3] for b in pre) if (day >= OPTION_SL_FROM and pre) else None
    if opt_low is not None and opt_low >= entry:
        opt_low = None                       # no room below the entry: keep the NIFTY range stop
    doc.update(side=side, strike=strike, expiry=c["expiry"], symbol="NIFTY %d %s" % (strike, side), lot=c["lot"],
               key=c["key"], signal_at="%s %s" % (day, sig_ts.strftime("%H:%M")),
               entry_at="%s %s" % (day, ob[0].strftime("%H:%M")), entry=entry, sl_index=round(sl_level, 2), sl_option=opt_low,
               status="open")
    for b in (idx[j:] if opt_low is None else [opt[t] for t in sorted(opt) if t >= ob[0]]):
        if opt_low is None:
            hit = (side == "CE" and b[3] <= sl_level) or (side == "PE" and b[2] >= sl_level)
        else:
            hit = b[3] <= opt_low
        if hit or b[0].time() >= EXIT:
            o = at_or_after(b[0])
            if o is None:
                break
            px = (min(o[1], opt_low) if opt_low is not None else o[4]) if hit else o[1]
            stamp = "%s %s" % (day, b[0].strftime("%H:%M"))
            doc.update(status="closed", exit=px, exit_at=stamp, last=px, last_at=stamp,
                       why=("SL: option back to its 9:15-9:45 low" if opt_low is not None else "SL: NIFTY back to the other side of the range") if hit else "15:15 exit")
            break
    if doc["status"] == "open":
        last = opt[max(opt)]
        doc.update(last=last[4], last_at="%s %s" % (day, (last[0] + dt.timedelta(minutes=1)).strftime("%H:%M")))
    doc["gross"] = round((doc["last"] - entry) * c["lot"], 2)
    doc["charges"] = charges(entry, doc["last"], c["lot"]) if doc["status"] == "closed" else 0.0
    doc["net"] = round(doc["gross"] - doc["charges"], 2)
    log("nifty option: %s (%s) entry %s at %s, now %s, P&L Rs %s, %s" % (
        doc["symbol"], doc["expiry"], entry, doc["entry_at"][11:], doc["last"], doc["net"], doc["status"]))
    return _save(doc, p)


def trades():
    """Every option paper trade as paper-book rows (papertrade/book.py)."""
    out = []
    for f in sorted(glob.glob(os.path.join(PICKS, "option-nifty-*.json"))):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("status") not in ("open", "closed"):
            continue
        out.append({"id": "O-%s" % d["day"], "kind": "option", "side": "long", "symbol": d["symbol"],
                    "industry": "NIFTY option · expiry %s" % d["expiry"], "signal_date": d["day"],
                    "entry_date": d["day"], "entry_at": d["entry_at"], "entry": d["entry"], "qty": d["lot"],
                    "sl": None, "sl_note": ("Option below Rs %s" % d["sl_option"]) if d.get("sl_option") else "NIFTY %s %s" % ("below" if d["side"] == "CE" else "above",
                                                            format(d["sl_index"], ",.2f")),
                    "status": d["status"], "exit_date": d["day"] if d["status"] == "closed" else None,
                    "exit": d.get("exit"), "exit_reason": d.get("why"),
                    "exit_time": (d.get("exit_at") or "")[11:] or None,
                    "last": d["last"], "last_at": d["last_at"], "gross": d["gross"], "charges": d["charges"],
                    "net": d["net"], "flag": None,
                    "marks": [{"date": d["day"], "session": 1, "close": d["last"], "mark": d["last"],
                               "at": d["last_at"][11:], "day_pnl": d["gross"], "total_pnl": d["gross"]}]})
    return out
