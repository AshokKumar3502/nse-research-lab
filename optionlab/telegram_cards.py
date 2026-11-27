"""Colour picture cards (PNG) for the NIFTY option Telegram alerts (optionlab/telegram_alerts.py).

card(event, doc) -> PNG bytes, event in: range, entry, status, exit, no trade.
Green = call / profit, red = put / loss, blue = status, grey = no trade. Needs Pillow; the caller falls back to text.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 760
BG, PANEL, LINE = (17, 24, 39), (31, 41, 55), (55, 65, 81)
TEXT, MUTED = (243, 244, 246), (156, 163, 175)
GREEN, RED, BLUE, AMBER, GREY = (22, 163, 74), (220, 38, 38), (37, 99, 235), (217, 119, 6), (75, 85, 99)
LGREEN, LRED = (74, 222, 128), (248, 113, 113)

_FONTS = {
    True: ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    False: ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
}


def font(size, bold=False):
    for p in _FONTS[bold]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def rs(x, sign=False):
    s = format(abs(round(x)), ",")
    return ("+" if x > 0 else "-" if x < 0 else "") + "₹" + s if sign else "₹" + s


def px(x):
    return "₹" + format(x, ",.2f")


class Card:
    def __init__(self, colour, tag, title, sub):
        self.im = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.im)
        self.d.rectangle([0, 0, W, 170], fill=colour)
        self.d.text((48, 30), tag, font=font(30, True), fill=(255, 255, 255))
        self.d.text((48, 72), title, font=font(60, True), fill=(255, 255, 255))
        self.y = 200
        if sub:
            self.d.text((48, self.y), sub, font=font(30), fill=MUTED)
            self.y += 56

    def boxes(self, items, cols=2, h=130):
        gap, x0 = 24, 48
        w = (W - 2 * x0 - gap * (cols - 1)) // cols
        for i, (label, value, colour) in enumerate(items):
            r, c = divmod(i, cols)
            x, y = x0 + c * (w + gap), self.y + r * (h + gap)
            self.d.rounded_rectangle([x, y, x + w, y + h], 18, fill=PANEL, outline=LINE, width=2)
            self.d.text((x + 22, y + 18), label, font=font(26 if cols < 4 else 24), fill=MUTED)
            self.d.text((x + 22, y + 58), value, font=font(44 if cols < 4 else 34, True), fill=colour or TEXT)
        rows = (len(items) + cols - 1) // cols
        self.y += rows * (h + gap)

    def big(self, label, value, colour):
        self.d.rounded_rectangle([48, self.y, W - 48, self.y + 150], 18, fill=PANEL, outline=colour, width=4)
        self.d.text((78, self.y + 20), label, font=font(28), fill=MUTED)
        self.d.text((78, self.y + 62), value, font=font(64, True), fill=colour)
        self.y += 174

    def line(self, text, colour=MUTED, size=28, bold=False):
        self.d.text((48, self.y), text, font=font(size, bold), fill=colour)
        self.y += size + 18

    def png(self, foot):
        if getattr(self, "doc", None) is not None:
            _badge(self, self.doc)
        self.d.line([48, H - 70, W - 48, H - 70], fill=LINE, width=2)
        self.d.text((48, H - 52), foot, font=font(24), fill=MUTED)
        b = io.BytesIO()
        self.im.save(b, "PNG", optimize=True)
        return b.getvalue()


def _sl(doc):
    if doc.get("sl_option"):
        return px(doc["sl_option"])
    return "%s %s %s" % (doc.get("index", "NIFTY"), "<" if doc["side"] == "CE" else ">", format(doc["sl_index"], ",.0f"))


GOLD = (245, 179, 1)


def _star(d, cx, cy, r, fill):
    """A filled 5-point star (the card font has no star glyph)."""
    import math
    pts = [(cx + (r if i % 2 == 0 else r * 0.42) * math.sin(i * math.pi / 5), cy - (r if i % 2 == 0 else r * 0.42) * math.cos(i * math.pi / 5))
           for i in range(10)]
    d.polygon(pts, fill=fill)


def pill(d, x, y, h, size):
    """Gold PRIORITY pill with a drawn star at (x, y); returns its width."""
    f = font(size, True)
    w = h * 0.55 + d.textlength("PRIORITY", font=f) + h * 0.9
    d.rounded_rectangle([x, y, x + w, y + h], h // 2, fill=GOLD)
    _star(d, x + h * 0.55, y + h / 2, h * 0.3, (40, 28, 0))
    d.text((x + h * 0.95, y + (h - size) / 2 - 2), "PRIORITY", font=f, fill=(40, 28, 0))
    return w


def _badge(c, doc):
    """Gold ★ PRIORITY badge in the top-right of the card header."""
    if not doc.get("priority"):
        return
    w = 46 * 0.55 + c.d.textlength("PRIORITY", font=font(26, True)) + 46 * 0.9
    pill(c.d, W - 48 - w, 26, 46, 26)


def card(event, doc, foot="Paper trade · 1 lot · Not investment advice"):
    png = _card(event, doc, foot)
    return png


def _card(event, doc, foot):
    day = doc.get("day", "")
    name = {"NIFTY": "NIFTY 50"}.get(doc.get("index", "NIFTY"), doc.get("index", "NIFTY"))
    if event == "range":
        c = Card(BLUE, "%s · %s" % (name, day), "9:45 RANGE SET", "Waiting for a 1-minute close outside the range (until 13:00)")
        c.doc = doc
        c.boxes([("Range HIGH  →  close above = BUY CALL", format(doc["range_high"], ",.2f"), LGREEN),
                 ("Range LOW  →  close below = BUY PUT", format(doc["range_low"], ",.2f"), LRED)], cols=1, h=120)
        return c.png(foot)
    if event in ("near_high", "near_low"):
        up = event == "near_high"
        edge = doc["range_high"] if up else doc["range_low"]
        last, step = doc["index_last"], doc.get("step") or 50
        c = Card(AMBER, "%s · %s · NEAR RANGE %s" % (name, doc.get("index_last_at", ""), "HIGH" if up else "LOW"),
                 "BE READY %s %s SIDE" % ("▲" if up else "▼", "CALL" if up else "PUT"),
                 "Not entered yet: a 1-minute close %s %s takes the trade" % ("above" if up else "below", format(edge, ",.2f")))
        c.doc = doc
        c.boxes([("%s now" % name, format(last, ",.2f"), TEXT),
                 ("Range %s" % ("high" if up else "low"), format(edge, ",.2f"), LGREEN if up else LRED),
                 ("Distance", "%s pts" % format(abs(edge - last), ",.2f"), AMBER),
                 ("If it breaks", "BUY %d %s" % (round(edge / step) * step, "CE" if up else "PE"), LGREEN if up else LRED)])
        return c.png(foot)
    if event == "no trade":
        c = Card(GREY, "%s · %s" % (name, day), "NO TRADE TODAY", None)
        c.line(doc.get("note") or "No breakout of the 9:15-9:45 range by 13:00", TEXT, 34, True)
        c.line("No position taken. Nothing to do.", MUTED, 30)
        return c.png(foot)
    call = doc["side"] == "CE"
    lot = doc.get("lot") or 0
    if event == "entry":
        c = Card(GREEN if call else RED, "ENTRY TAKEN · %s · %s" % (day, doc["entry_at"][11:]),
                 "BUY %s %s" % ("▲" if call else "▼", doc["symbol"]),
                 ("%s option · expiry %s · 1 lot = %d qty" % ("Call" if call else "Put", doc["expiry"], lot)) if doc.get("public") else
                 "%s breakout · expiry %s · 1 lot = %d qty" % ("Upside" if call else "Downside", doc["expiry"], lot))
        c.doc = doc
        c.boxes([("Entry price", px(doc["entry"]), TEXT),
                 ("Stop-loss (sell below)", _sl(doc), LRED),
                 ("Capital (1 lot)", rs(doc["entry"] * lot), TEXT),
                 ("Lot", "%d qty" % lot, AMBER) if doc.get("public") else ("Exit", "15:15", AMBER)])
        if doc.get("sl_option"):
            c.line("Max loss at stop-loss ≈ %s" % rs((doc["sl_option"] - doc["entry"]) * lot, True), LRED, 30, True)
        if doc.get("targets"):
            c.line("Targets  T1 %s · T2 %s · T3 %s" % tuple(px(v) for v in doc["targets"]), LGREEN, 30, True)
        return c.png(foot)
    if event == "status":
        g = doc["gross"]
        col = LGREEN if g > 0 else LRED if g < 0 else TEXT
        c = Card(BLUE, "LIVE · %s · %s" % (day, doc["last_at"][11:]), doc["symbol"], "Open position · updates every 10 minutes")
        c.big("P&L now (1 lot, before charges)", "%s   (%+.1f%%)" % (rs(g, True), (doc["last"] / doc["entry"] - 1) * 100), col)
        c.boxes([("Entry", px(doc["entry"]), TEXT), ("Now", px(doc["last"]), col),
                 ("Stop-loss", _sl(doc), LRED), ("Exit", "15:15", AMBER)], cols=4, h=120)
        if doc.get("targets"):
            got = {h["t"] for h in doc.get("target_hits") or []}
            c.line("  ".join("T%d %s%s" % (i + 1, px(v), " HIT" if i + 1 in got else "") for i, v in enumerate(doc["targets"])), LGREEN, 28, True)
        return c.png(foot)
    if event in ("t1", "t2", "t3"):
        k = int(event[1])
        h = [x for x in doc.get("target_hits") or [] if x["t"] == k][0]
        got = {x["t"] for x in doc.get("target_hits") or []}
        c = Card(GREEN, "TARGET HIT · %s · %s" % (day, h["at"]), "T%d REACHED  %s" % (k, doc["symbol"]),
                 "Information only" if doc.get("public") else "Information only: the paper trade holds to 15:15 or the stop-loss")
        c.big("P&L at T%d (1 lot, before charges)" % k, rs((h["price"] - doc["entry"]) * lot, True), LGREEN)
        c.boxes([("Entry", px(doc["entry"]), TEXT)] + [("T%d%s" % (i + 1, " HIT" if i + 1 in got else ""), px(v), LGREEN if i + 1 in got else MUTED)
                                                      for i, v in enumerate(doc["targets"])], cols=4, h=120)
        return c.png(foot)
    if event == "exit":
        n = doc["net"]
        win = n > 0
        c = Card(GREEN if win else RED, "CLOSED · %s · %s" % (day, doc["exit_at"][11:]),
                 "%s  %s" % ("PROFIT" if win else "LOSS", doc["symbol"]), doc.get("why"))
        c.big("Net P&L (1 lot, after %s charges)" % rs(doc["charges"]), rs(n, True), LGREEN if win else LRED)
        c.boxes([("Entry · %s" % doc["entry_at"][11:], px(doc["entry"]), TEXT),
                 ("Exit · %s" % doc["exit_at"][11:], px(doc["exit"]), LGREEN if win else LRED)])
        return c.png(foot)
    raise ValueError(event)


def watchlist_card(rows, day, foot="Paper trade · 1 lot each · Not investment advice",
                   sub="Alerts from 9:45: range set → GET READY → entry → status → exit"):
    """One card listing every instrument: name, last close, lot, next expiry, ATM capital estimate, status.
    rows: [(name, close, lot, expiry, status)]."""
    h = 230 + 112 * len(rows) + 90
    c = Card(BLUE, "WATCHLIST · %s" % day, "NEXT SESSION · %d TRADES" % len(rows),
             "Alerts from 9:45: range set → GET READY → entry → status → exit")
    c.im = c.im.resize((W, max(H, h)))
    c.d = ImageDraw.Draw(c.im)
    c.d.rectangle([0, 0, W, 170], fill=BLUE)
    c.d.text((48, 30), "WATCHLIST · %s" % day, font=font(30, True), fill=(255, 255, 255))
    c.d.text((48, 72), "NEXT SESSION · %d TO WATCH" % len(rows), font=font(56, True), fill=(255, 255, 255))
    c.d.rectangle([0, 171, W, max(H, h)], fill=BG)
    c.d.text((48, 200), sub, font=font(28), fill=MUTED)
    y = 256
    for name, close, lot, expiry, status in rows:
        c.d.rounded_rectangle([48, y, W - 48, y + 96], 16, fill=PANEL, outline=LINE, width=2)
        c.d.text((72, y + 14), name, font=font(36, True), fill=TEXT)
        c.d.text((72, y + 58), "Lot %s · expiry %s" % (lot, expiry), font=font(24), fill=MUTED)
        c.d.text((560, y + 14), format(close, ",.2f"), font=font(36, True), fill=TEXT)
        c.d.text((560, y + 58), status, font=font(24, True), fill=LGREEN if status.startswith("FIRST") else AMBER)
        y += 112
    hh = max(H, h)
    c.d.line([48, hh - 70, W - 48, hh - 70], fill=LINE, width=2)
    c.d.text((48, hh - 52), foot, font=font(24), fill=MUTED)
    b = io.BytesIO()
    c.im.save(b, "PNG", optimize=True)
    return b.getvalue()


def board_card(rows, stamp, total, foot="Paper trade · 1 lot each · targets are information only · Not investment advice"):
    """One card for every instrument. rows: dicts with name, tag, tag_colour, line, targets [(label, hit)], pnl (or None)."""
    rh = 132
    hh = 250 + rh * len(rows) + 90
    im = Image.new("RGB", (W, hh), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 150], fill=BLUE)
    d.text((48, 26), "LIVE BOARD · %s" % stamp, font=font(30, True), fill=(255, 255, 255))
    d.text((48, 68), "%d INSTRUMENTS" % len(rows), font=font(52, True), fill=(255, 255, 255))
    col = LGREEN if total > 0 else LRED if total < 0 else TEXT
    d.rounded_rectangle([48, 172, W - 48, 232], 14, fill=PANEL, outline=col, width=3)
    d.text((72, 186), "Total P&L today (1 lot each)", font=font(26), fill=MUTED)
    tt = rs(total, True)
    d.text((W - 72 - d.textlength(tt, font=font(34, True)), 182), tt, font=font(34, True), fill=col)
    y = 250
    for r in rows:
        d.rounded_rectangle([48, y, W - 48, y + rh - 12], 16, fill=PANEL, outline=LINE, width=2)
        d.text((72, y + 14), r["name"], font=font(32, True), fill=TEXT)
        tx = 72 + d.textlength(r["name"], font=font(32, True)) + 16
        tw = d.textlength(r["tag"], font=font(20, True)) + 24
        d.rounded_rectangle([tx, y + 18, tx + tw, y + 50], 14, fill=r["tag_colour"])
        d.text((tx + 12, y + 22), r["tag"], font=font(20, True), fill=(255, 255, 255))
        if r.get("priority"):
            px_ = tx + tw + 10
            pill(d, px_, y + 18, 32, 20)
        d.text((72, y + 58), r["line"], font=font(23), fill=MUTED)
        x = 72
        for label, hit in r.get("targets") or []:
            d.text((x, y + 88), label, font=font(21, True), fill=LGREEN if hit else MUTED)
            x += d.textlength(label, font=font(21, True)) + 22
        if r.get("pnl") is not None:
            p = rs(r["pnl"], True)
            c = LGREEN if r["pnl"] > 0 else LRED if r["pnl"] < 0 else TEXT
            d.text((W - 72 - d.textlength(p, font=font(34, True)), y + 14), p, font=font(34, True), fill=c)
        y += rh
    d.line([48, hh - 70, W - 48, hh - 70], fill=LINE, width=2)
    d.text((48, hh - 52), foot, font=font(22), fill=MUTED)
    b = io.BytesIO()
    im.save(b, "PNG", optimize=True)
    return b.getvalue()
