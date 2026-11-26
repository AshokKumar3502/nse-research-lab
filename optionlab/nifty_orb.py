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
# the same rule on SENSEX (BSE weekly options), paper traded from 26 Nov 2026
INDEXES = {
    "NIFTY": {"key": INDEX_KEY, "step": 50, "segment": "NSE_FO", "name": "NIFTY", "sl_from": OPTION_SL_FROM, "file": "nifty",
              "list": minute.INSTRUMENTS_URL, "id": "O"},
    "SENSEX": {"key": "BSE_INDEX|SENSEX", "step": 100, "segment": "BSE_FO", "name": "SENSEX", "sl_from": dt.date(2026, 11, 26),
               "file": "sensex", "list": "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz", "id": "OX"},
    # BSE Ltd stock options (monthly expiry): the only F&O stock whose 2024-2026 backtest beat a random side
    "BSE": {"key": "NSE_EQ|INE118H01025", "step": 50, "segment": "NSE_FO", "name": "BSE LIMITED", "sl_from": dt.date(2026, 11, 27),
            "start": dt.date(2026, 11, 27), "file": "bse", "list": minute.INSTRUMENTS_URL, "id": "OB"},
    # the two strongest of the next 15 F&O stocks (2022-2026 backtest), monthly stock options
    "ADANIENT": {"key": "NSE_EQ|INE423A01024", "step": 50, "segment": "NSE_FO", "name": "ADANI ENTERPRISES LIMITED",
                 "sl_from": dt.date(2026, 11, 27), "start": dt.date(2026, 11, 27), "file": "adanient",
                 "list": minute.INSTRUMENTS_URL, "id": "OA"},
    "DIXON": {"key": "NSE_EQ|INE935N01020", "step": 100, "segment": "NSE_FO", "name": "DIXON TECHNO (INDIA) LTD",
              "sl_from": dt.date(2026, 11, 27), "start": dt.date(2026, 11, 27), "file": "dixon",
              "list": minute.INSTRUMENTS_URL, "id": "OD"},
    # the one stock whose rule beat a random side in both 2022-24 and 2025-26 (and random lost money in both)
    "PNB": {"key": "NSE_EQ|INE160A01022", "step": 1, "segment": "NSE_FO", "name": "PUNJAB NATIONAL BANK",
            "sl_from": dt.date(2026, 11, 27), "start": dt.date(2026, 11, 27), "file": "pnb",
            "list": minute.INSTRUMENTS_URL, "id": "OP"},
}


def _path(day, index="NIFTY"):
    return os.path.join(PICKS, "option-%s-%s.json" % (INDEXES[index]["file"], day))


def candles(key, day):
    minute._throttle()
    doc = net.get_json(CANDLES % urllib.parse.quote(key, safe=""))
    out = []
    for c in (doc.get("data") or {}).get("candles") or []:
        ts = dt.datetime.fromisoformat(c[0]).replace(tzinfo=None)
        if ts.date() == day:
            out.append((ts, float(c[1]), float(c[2]), float(c[3]), float(c[4])))
    return sorted(out)


def contracts(day, index="NIFTY"):
    """Today's NIFTY (or SENSEX) options from Upstox's instrument list (cached for the day)."""
    ix = INDEXES[index]
    p = os.path.join(config.DATA, "options", "%s-contracts-%s.json" % (ix["file"], day))
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    rows = json.loads(net.get(ix["list"], timeout=120).decode("utf-8"))
    keep = [{"key": x["instrument_key"], "strike": float(x["strike_price"]), "type": x["instrument_type"],
             "expiry": dt.datetime.fromtimestamp(x["expiry"] / 1000, config.IST).date().isoformat(),
             "lot": int(x.get("lot_size") or 0), "symbol": x.get("trading_symbol")}
            for x in rows if x.get("segment") == ix["segment"] and x.get("name") == ix["name"]
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


def run(day=None, log=print, index="NIFTY"):
    ix = INDEXES[index]
    day = day or config.today_ist()
    now = dt.datetime.now(config.IST).replace(tzinfo=None)
    p = _path(day, index)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            old = json.load(fh)
        if old.get("status") in ("closed", "no trade"):
            return old
    doc = {"day": str(day), "index": index, "status": "waiting", "updated": now.strftime("%H:%M"),
           "rule": "%s 9:15-9:45 range breakout, ATM option, SL = the option's own 9:15-9:45 low, exit 15:15" % index}
    if day.weekday() >= 5:
        doc["status"] = "no market"
        return _save(doc, p)
    if day < ix.get("start", day):
        doc.update(status="not started", note="%s paper trading starts on %s" % (index, ix["start"]))
        return doc                           # not saved: no trade before the start day
    idx = [b for b in candles(ix["key"], day) if b[0] + dt.timedelta(minutes=1) <= now]
    if not idx or idx[-1][0].time() < RANGE_END:
        doc["note"] = "The range builds from 9:15 to 9:45"
        return _save(doc, p)
    rng = [b for b in idx if b[0].time() < RANGE_END]
    hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
    doc.update(range_high=round(hi, 2), range_low=round(lo, 2), step=ix["step"],
               index_last=round(idx[-1][4], 2), index_last_at=(idx[-1][0] + dt.timedelta(minutes=1)).strftime("%H:%M"))
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
    strike = round(entry_bar[1] / ix["step"]) * ix["step"]
    cs = [c for c in contracts(day, index) if c["type"] == side and c["strike"] == strike and c["expiry"] >= str(day)]
    if not cs:                               # stock strikes are not always on the grid: the nearest listed strike
        live_ = [c for c in contracts(day, index) if c["type"] == side and c["expiry"] >= str(day)]
        if live_:
            e0 = min(c["expiry"] for c in live_)
            strike = min({c["strike"] for c in live_ if c["expiry"] == e0}, key=lambda k: abs(k - entry_bar[1]))
            cs = [c for c in live_ if c["expiry"] == e0 and c["strike"] == strike]
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
    opt_low = min(b[3] for b in pre) if (day >= ix["sl_from"] and pre) else None
    if opt_low is not None and opt_low >= entry:
        opt_low = None                       # no room below the entry: keep the NIFTY range stop
    doc.update(side=side, strike=strike, expiry=c["expiry"], symbol="%s %d %s" % (index, strike, side), lot=c["lot"],
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
                       why=("SL: option back to its 9:15-9:45 low" if opt_low is not None else "SL: %s back to the other side of the range" % index) if hit else "15:15 exit")
            break
    if doc["status"] == "open":
        last = opt[max(opt)]
        doc.update(last=last[4], last_at="%s %s" % (day, (last[0] + dt.timedelta(minutes=1)).strftime("%H:%M")))
    # T1/T2/T3 = entry + 1/2/3 x risk (information only: the paper trade still exits at the stop-loss or 15:15)
    risk = (entry - opt_low) if opt_low is not None else entry * 0.3
    tg = [round(entry + k * risk, 2) for k in (1, 2, 3)]
    end = doc.get("exit_at") or "%s 23:59" % day
    hits, best, best_at = [], entry, doc["entry_at"][11:]
    for t in sorted(opt):
        if t < ob[0] or "%s %s" % (day, t.strftime("%H:%M")) > end:
            continue
        b = opt[t]
        if b[2] > best:
            best, best_at = b[2], t.strftime("%H:%M")
        while len(hits) < 3 and b[2] >= tg[len(hits)]:
            hits.append({"t": len(hits) + 1, "at": t.strftime("%H:%M"), "price": tg[len(hits)]})
    doc.update(targets=tg, target_hits=hits, opt_high=best, opt_high_at=best_at)
    doc["gross"] = round((doc["last"] - entry) * c["lot"], 2)
    doc["charges"] = charges(entry, doc["last"], c["lot"]) if doc["status"] == "closed" else 0.0
    doc["net"] = round(doc["gross"] - doc["charges"], 2)
    log(index.lower() + " option: %s (%s) entry %s at %s, now %s, P&L Rs %s, %s" % (
        doc["symbol"], doc["expiry"], entry, doc["entry_at"][11:], doc["last"], doc["net"], doc["status"]))
    return _save(doc, p)


def trades():
    """Every option paper trade as paper-book rows (papertrade/book.py)."""
    out = []
    for index, ix in INDEXES.items():
      for f in sorted(glob.glob(os.path.join(PICKS, "option-%s-*.json" % ix["file"]))):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("status") not in ("open", "closed"):
            continue
        out.append({"id": "%s-%s" % (ix["id"], d["day"]), "kind": "option", "index": index, "side": "long", "symbol": d["symbol"],
                    "industry": "%s option · expiry %s" % (index, d["expiry"]), "signal_date": d["day"],
                    "entry_date": d["day"], "entry_at": d["entry_at"], "entry": d["entry"], "qty": d["lot"],
                    "sl": None, "sl_note": ("Option below Rs %s" % d["sl_option"]) if d.get("sl_option") else "%s %s %s" % (index, "below" if d["side"] == "CE" else "above",
                                                            format(d["sl_index"], ",.2f")),
                    "status": d["status"], "exit_date": d["day"] if d["status"] == "closed" else None,
                    "exit": d.get("exit"), "exit_reason": d.get("why"),
                    "exit_time": (d.get("exit_at") or "")[11:] or None,
                    "last": d["last"], "last_at": d["last_at"], "gross": d["gross"], "charges": d["charges"],
                    "net": d["net"], "flag": None, "targets": d.get("targets"), "target_hits": d.get("target_hits") or [],
                    "opt_high": d.get("opt_high"), "opt_high_at": d.get("opt_high_at"),
                    "marks": [{"date": d["day"], "session": 1, "close": d["last"], "mark": d["last"],
                               "at": d["last_at"][11:], "day_pnl": d["gross"], "total_pnl": d["gross"]}]})
    return out
