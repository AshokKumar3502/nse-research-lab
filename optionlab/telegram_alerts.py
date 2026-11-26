"""Telegram messages for the NIFTY option paper trade (optionlab/nifty_orb.py), sent from the 10-minute live job.

Messages (1 lot, paper trade, nothing here places an order):
  range    once after 9:45: today's 9:15-9:45 range, watching for a breakout
  entry    once, when the trade is taken: strike, entry price and time, stop-loss
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


CAPTION = {"range": "🔵 {ix} 9:45 range set: waiting for a breakout", "no trade": "⚪ {ix}: no trade today",
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
    if st == "no trade" and "no trade" not in sent:
        out.append(("no trade", "%s option: no trade today. %s.\n%s" % (ix, doc.get("note") or "No breakout by 13:00", FOOT)))
    if st not in ("open", "closed"):
        return out
    sym, lot = doc["symbol"], doc.get("lot") or 0
    sl = ("option below Rs %s" % doc["sl_option"]) if doc.get("sl_option") else \
        "%s %s %s" % (ix, "below" if doc["side"] == "CE" else "above", format(doc["sl_index"], ",.2f"))
    if "entry" not in sent:
        out.append(("entry", "BUY %s (expiry %s)\nEntry Rs %s at %s, 1 lot (%d qty)\nCapital about Rs %s\nStop-loss: %s\nExit: 15:15 if the stop-loss is not hit\n%s"
                    % (sym, doc["expiry"], doc["entry"], doc["entry_at"][11:], lot, format(round(doc["entry"] * lot), ","), sl, FOOT)))
    if st == "open":
        out.append(("status", "%s %s\nEntry Rs %s, now Rs %s\nP&L %s (1 lot, before charges)\nStop-loss: %s\n%s"
                    % (doc["last_at"][11:], sym, doc["entry"], doc["last"], _rs(doc["gross"]), sl, FOOT)))
    elif "exit" not in sent:
        out.append(("exit", "%s %s\nEntry Rs %s at %s, exit Rs %s at %s\nReason: %s\nNet P&L %s (1 lot, after Rs %s charges)\n%s"
                    % ("PROFIT" if doc["net"] > 0 else "LOSS", sym, doc["entry"], doc["entry_at"][11:], doc["exit"],
                       doc["exit_at"][11:], doc.get("why"), _rs(doc["net"]), format(doc["charges"], ",.0f"), FOOT)))
    return out


def notify(doc, log=print):
    """Send what is due for today's trade document and remember it. Safe to call every cycle."""
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")) or not doc or not doc.get("day"):
        return 0
    p = os.path.join(STATE, "telegram-%s-%s.json" % (doc.get("index", "NIFTY").lower(), doc["day"]))
    sent = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            sent = json.load(fh).get("sent", [])
    n = 0
    for event, text in messages(doc, set(sent)):
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
