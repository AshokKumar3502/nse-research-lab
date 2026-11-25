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


def _rs(x):
    return ("+" if x > 0 else "-" if x < 0 else "") + "Rs " + format(abs(round(x)), ",")


def messages(doc, sent):
    """The messages due for this trade document, given the event names already sent. Returns [(event, text)]."""
    out = []
    st = doc.get("status")
    if doc.get("range_high") and "range" not in sent and st in ("waiting", "open", "closed", "no trade"):
        out.append(("range", "NIFTY 9:15-9:45 range: high %s, low %s.\nWatching for a 1-minute close outside it until 13:00.\n%s"
                    % (format(doc["range_high"], ",.2f"), format(doc["range_low"], ",.2f"), FOOT)))
    if st == "no trade" and "no trade" not in sent:
        out.append(("no trade", "NIFTY option: no trade today. %s.\n%s" % (doc.get("note") or "No breakout by 13:00", FOOT)))
    if st not in ("open", "closed"):
        return out
    sym, lot = doc["symbol"], doc.get("lot") or 0
    sl = ("option below Rs %s" % doc["sl_option"]) if doc.get("sl_option") else \
        "NIFTY %s %s" % ("below" if doc["side"] == "CE" else "above", format(doc["sl_index"], ",.2f"))
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
    p = os.path.join(STATE, "telegram-nifty-%s.json" % doc["day"])
    sent = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            sent = json.load(fh).get("sent", [])
    n = 0
    for event, text in messages(doc, set(sent)):
        if send(text):
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
