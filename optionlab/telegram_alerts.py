"""Telegram messages for the NIFTY option paper trade (optionlab/nifty_orb.py), sent from the 10-minute live job.

Messages (1 lot, paper trade, nothing here places an order):
  range    once after 9:45: today's 9:15-9:45 range, watching for a breakout
  entry    once, when the trade is taken: strike, entry price and time, stop-loss
  near     once per side, 9:45-13:00 while waiting: the index is within NEAR (0.1%) of its range high or low
           ("get ready": a 1-minute close beyond it takes the trade); checked every minute
  status   every live cycle (10 minutes) while the trade is open: last price, P&L for 1 lot
  exit     once, when it closes: exit price, reason, net P&L after charges
  no trade once, when there is no breakout by 13:00
Needs the TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables (GitHub secrets); without them it does
nothing. The chat id can be a channel (@name or -100...), with the bot added to the channel as an admin.
What was already sent is kept in state/picks/telegram-nifty-YYYY-MM-DD.json so a restarted job does not repeat it.
"""
import json
import os
import urllib.parse
import urllib.request
import uuid

from warehouse import config

STATE = os.path.join(config.ROOT, "state", "picks")
FOOT = "Paper trade, 1 lot. Not investment advice."
NEAR = 0.001          # "get ready" when the index is within 0.1% of the range edge (NIFTY ~26 pts, SENSEX ~85 pts)


def send(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok, data, timeout=20).read()
        return True
    except OSError:
        return False


def send_photo(png, caption):
    """Send a PNG card with a short caption (multipart upload). False if it could not be sent."""
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return False
    bnd = "----nifty%s" % uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption)):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (bnd, k, v)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"alert.png\"\r\n"
                  "Content-Type: image/png\r\n\r\n" % bnd).encode() + png + b"\r\n")
    parts.append(("--%s--\r\n" % bnd).encode())
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendPhoto" % tok, b"".join(parts),
                                 {"Content-Type": "multipart/form-data; boundary=%s" % bnd})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except OSError:
        return False


CAPTION = {"t1": "🎯 T1 HIT: {sym}", "t2": "🎯🎯 T2 HIT: {sym}", "t3": "🎯🎯🎯 T3 HIT: {sym}", "near_high": "🟡 GET READY: {ix} near its range HIGH (call side)", "near_low": "🟡 GET READY: {ix} near its range LOW (put side)",
           "range": "🔵 {ix} 9:45 range set: waiting for a breakout", "no trade": "⚪ {ix}: no trade today",
           "status": "🔵 Live update", "entry": "{dot} NEW SIGNAL: BUY {sym}", "exit": "{dot} CLOSED {sym}: {res}"}


def deliver(event, text, doc):
    """The colour card with a short caption; plain text if the card cannot be drawn or sent."""
    try:
        from .telegram_cards import card
        dot = "🟢" if (doc.get("side") == "CE" if event == "entry" else (doc.get("net") or 0) > 0) else "🔴"
        cap = CAPTION[event].format(ix=doc.get("index", "NIFTY"), dot=dot, sym=doc.get("symbol", ""), res="PROFIT" if (doc.get("net") or 0) > 0 else "LOSS")
        if event == "status":
            cap = "%s %s · P&L %s" % (cap, doc["symbol"], _rs(doc["gross"]))
        if send_photo(card(event, doc), cap + "\n" + FOOT):
            return True
    except Exception:      # noqa: BLE001  (no Pillow, bad data): fall back to text
        pass
    return send(text)


_RATES = {}


def hit_rates(index):
    """Share of past backtest trades that reached T1/T2/T3 before the stop (from docs/data/backtest*.json)."""
    if index in _RATES:
        return _RATES[index]
    name = {"NIFTY": "backtest.json"}.get(index, "backtest-%s.json" % index.lower())
    out = None
    try:
        with open(os.path.join(config.ROOT, "docs", "data", name), encoding="utf-8") as fh:
            days = [d for d in json.load(fh)["days"] if d.get("status") == "closed" and d.get("high") is not None
                    and d.get("sl_option") is not None and d["sl_option"] < d["entry"]]
        if days:
            out = [round(sum(1 for d in days if d["high"] >= d["entry"] + k * (d["entry"] - d["sl_option"])) / len(days) * 100)
                   for k in __import__("optionlab.nifty_orb", fromlist=["TARGET_R"]).TARGET_R]
    except (OSError, ValueError, KeyError):
        pass
    _RATES[index] = out
    return out


def _tline(doc):
    tg, got = doc.get("targets") or [], {h["t"] for h in doc.get("target_hits") or []}
    return " · ".join("T%d Rs %s%s" % (i + 1, format(v, ",.2f"), " ✓" if i + 1 in got else "") for i, v in enumerate(tg))


def _rs(x):
    return ("+" if x > 0 else "-" if x < 0 else "") + "Rs " + format(abs(round(x)), ",")


def messages(doc, sent):
    """The messages due for this trade document, given the event names already sent. Returns [(event, text)]."""
    out = []
    st = doc.get("status")
    ix = doc.get("index", "NIFTY")
    if doc.get("range_high") and "range" not in sent and st in ("waiting", "open", "closed", "no trade"):
        out.append(("range", ix + " 9:15-9:45 range: high %s, low %s.\nWatching for a 1-minute close outside it until 13:00.\n%s"
                    % (format(doc["range_high"], ",.2f"), format(doc["range_low"], ",.2f"), FOOT)))
    last, at = doc.get("index_last"), doc.get("index_last_at") or ""
    if st == "waiting" and last and doc.get("range_high") and "09:45" <= at < "13:00":
        step = doc.get("step") or 50
        for ev, edge, side, gap in (("near_high", doc["range_high"], "CE", doc["range_high"] - last),
                                    ("near_low", doc["range_low"], "PE", last - doc["range_low"])):
            if ev not in sent and 0 <= gap <= last * NEAR:
                out.append((ev, "GET READY: %s %s at %s, %s pts from the range %s %s.\nA 1-minute close %s it buys the %d %s (about).\n%s"
                            % (ix, format(last, ",.2f"), at, format(gap, ",.1f"), "high" if side == "CE" else "low",
                               format(edge, ",.2f"), "above" if side == "CE" else "below", round(edge / step) * step, side, FOOT)))
    if st == "no trade" and "no trade" not in sent:
        out.append(("no trade", "%s option: no trade today. %s.\n%s" % (ix, doc.get("note") or "No breakout by 13:00", FOOT)))
    if st not in ("open", "closed"):
        return out
    sym, lot = doc["symbol"], doc.get("lot") or 0
    sl = ("option below Rs %s" % doc["sl_option"]) if doc.get("sl_option") else \
        "%s %s %s" % (ix, "below" if doc["side"] == "CE" else "above", format(doc["sl_index"], ",.2f"))
    if "entry" not in sent:
        hr = hit_rates(ix)
        out.append(("entry", "BUY %s (expiry %s)\nEntry Rs %s at %s, 1 lot (%d qty)\nCapital about Rs %s\nStop-loss: %s\nTargets: %s%s\n"
                    "Exit: 15:15 if the stop-loss is not hit (targets are information only)\n%s"
                    % (sym, doc["expiry"], doc["entry"], doc["entry_at"][11:], lot, format(round(doc["entry"] * lot), ","), sl,
                       _tline(doc), ("\nReached in past trades: T1 %d%% · T2 %d%% · T3 %d%%" % tuple(hr)) if hr else "", FOOT)))
    for h in doc.get("target_hits") or []:
        if "t%d" % h["t"] not in sent:
            out.append(("t%d" % h["t"], "TARGET %d HIT: %s at %s\nTarget Rs %s (entry Rs %s): %s for 1 lot before charges\n%s\n"
                        "The paper trade keeps holding to 15:15 or the stop-loss (targets are information only).\n%s"
                        % (h["t"], sym, h["at"], format(h["price"], ",.2f"), doc["entry"], _rs((h["price"] - doc["entry"]) * lot),
                           _tline(doc), FOOT)))
    if st == "open":
        out.append(("status", "%s %s\nEntry Rs %s, now Rs %s\nP&L %s (1 lot, before charges)\nStop-loss: %s\nTargets: %s\n%s"
                    % (doc["last_at"][11:], sym, doc["entry"], doc["last"], _rs(doc["gross"]), sl, _tline(doc), FOOT)))
    elif "exit" not in sent:
        out.append(("exit", "%s %s\nEntry Rs %s at %s, exit Rs %s at %s\nReason: %s\nNet P&L %s (1 lot, after Rs %s charges)\n%s"
                    % ("PROFIT" if doc["net"] > 0 else "LOSS", sym, doc["entry"], doc["entry_at"][11:], doc["exit"],
                       doc["exit_at"][11:], doc.get("why"), _rs(doc["net"]), format(doc["charges"], ",.0f"), FOOT)))
    return out


def notify(doc, log=print, status=True):
    """Send what is due for today's trade document and remember it. Safe to call every cycle.
    status=False (the 1-minute watcher): everything except the 10-minute P&L status card."""
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")) or not doc or not doc.get("day"):
        return 0
    p = os.path.join(STATE, "telegram-%s-%s.json" % (doc.get("index", "NIFTY").lower(), doc["day"]))
    sent = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            sent = json.load(fh).get("sent", [])
    n = 0
    for event, text in messages(doc, set(sent)):
        if event == "status" and not status:
            continue
        if deliver(event, text, doc):
            n += 1
            if event != "status":
                sent.append(event)
        else:
            log("telegram: could not send the %s message" % event)
    os.makedirs(STATE, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"sent": sent}, fh)
    if n:
        log("telegram: %d message(s) sent" % n)
    return n


if __name__ == "__main__":       # python -m optionlab.telegram_alerts --test  (GitHub: the "Telegram test" workflow)
    import sys
    if "--test" in sys.argv:
        ok = send("Connected: NIFTY Option Lab bot is linked to this chat.\nYou will get the NIFTY option paper trade here "
                  "on trading days: the 9:45 range, the entry, a status every 10 minutes and the exit P&L.\n" + FOOT)
        print("sent" if ok else "NOT sent (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, and press Start in the bot first)")
        sys.exit(0 if ok else 1)
    if "--watchlist" in sys.argv:     # one card: every instrument the live job trades next session
        import datetime as _dt
        from warehouse import config as _cfg
        from . import nifty_orb
        from .telegram_cards import watchlist_card
        today = _cfg.today_ist()
        nxt = today + _dt.timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += _dt.timedelta(days=1)
        names = {"NIFTY": "NIFTY 50", "BSE": "BSE Ltd", "ADANIENT": "Adani Ent", "DIXON": "Dixon"}
        rows, lines = [], []
        for ix, c in nifty_orb.INDEXES.items():
            bars = nifty_orb.candles(c["key"], today)
            cs = [x for x in nifty_orb.contracts(today, ix) if x["expiry"] > str(today)]
            if not bars or not cs:
                continue
            e = min(x["expiry"] for x in cs)
            lot = [x["lot"] for x in cs if x["expiry"] == e][0]
            status = "FIRST DAY" if c.get("start") == nxt else "LIVE"
            rows.append((names.get(ix, ix), bars[-1][4], lot, e, status))
            lines.append("%s %s · lot %s · expiry %s · %s" % (names.get(ix, ix), format(bars[-1][4], ",.2f"), lot, e, status))
        cap = "🔵 Watchlist for %s: %d to watch, alerts from 9:45\n%s" % (nxt.strftime("%a %d %b"), len(rows), FOOT)
        ok = send_photo(watchlist_card(rows, nxt.strftime("%a %d %b %Y")), cap) or send(cap + "\n" + "\n".join(lines))
        print("\n".join(lines))
        print("sent" if ok else "NOT sent")
        sys.exit(0 if ok else 1)
    if "--sample-targets" in sys.argv:   # entry with T1/T2/T3, the three target-hit cards, a status card (made-up numbers)
        tg = [274.63, 307.25, 339.88]
        hits = [{"t": 1, "at": "10:48", "price": tg[0]}, {"t": 2, "at": "11:52", "price": tg[1]}, {"t": 3, "at": "13:40", "price": tg[2]}]
        b = {"day": "SAMPLE", "index": "NIFTY", "range_high": 25850.5, "range_low": 25760.2, "symbol": "NIFTY 25800 CE",
             "side": "CE", "strike": 25800, "expiry": "2026-12-01", "lot": 65, "entry": 242.0, "entry_at": "SAMPLE 10:19",
             "sl_option": 198.5, "sl_index": 25760.2, "status": "open", "targets": tg}
        docs = [("entry", dict(b, target_hits=[], last=242.0, last_at="SAMPLE 10:19", gross=0.0))]
        docs += [("t%d" % k, dict(b, target_hits=hits[:k], last=tg[k - 1], last_at="SAMPLE " + hits[k - 1]["at"],
                                  gross=(tg[k - 1] - 242) * 65)) for k in (1, 2, 3)]
        docs.append(("status", dict(b, target_hits=hits[:2], last=318.0, last_at="SAMPLE 12:30", gross=(318 - 242) * 65)))
        send("SAMPLE TARGET ALERTS (made-up numbers): entry with T1/T2/T3, a card when each target is reached, and the "
             "10-minute status with targets marked HIT. Targets are information only: the paper trade still exits at the "
             "stop-loss or 15:15.")
        ok = 0
        for ev, d in docs:
            ok += deliver(ev, dict(messages(d, set())).get(ev, ""), d)
        print("sent %d of %d sample target cards" % (ok, len(docs)))
        sys.exit(0 if ok == len(docs) else 1)
    if "--sample" in sys.argv:        # the five alert cards with made-up numbers, marked SAMPLE
        b = {"day": "SAMPLE", "range_high": 25850.5, "range_low": 25760.2, "symbol": "NIFTY 25800 CE", "side": "CE",
             "strike": 25800, "expiry": "2026-12-01", "lot": 65, "entry": 242.0, "entry_at": "SAMPLE 10:19",
             "sl_option": 198.5, "sl_index": 25760.2}
        docs = [("range", dict(b, status="waiting")), ("entry", dict(b, status="open", last=242.0, last_at="SAMPLE 10:19", gross=0.0)),
                ("status", dict(b, status="open", last=301.4, last_at="SAMPLE 12:30", gross=(301.4 - 242) * 65)),
                ("exit", dict(b, status="closed", exit=240.0, exit_at="SAMPLE 15:15", why="15:15 exit", net=-213.4,
                              charges=83.4, last=240.0, last_at="SAMPLE 15:15", gross=-130.0)),
                ("no trade", dict(b, status="no trade", note="No breakout of the 9:15-9:45 range by 13:00"))]
        send("SAMPLE ALERTS (made-up numbers): this is how each NIFTY option message will look.")
        ok = 0
        for ev, d in docs:
            msg = dict(messages(d, set())).get(ev, "")
            ok += deliver(ev, msg, d)
        print("sent %d of %d sample cards" % (ok, len(docs)))
        sys.exit(0 if ok == len(docs) else 1)
