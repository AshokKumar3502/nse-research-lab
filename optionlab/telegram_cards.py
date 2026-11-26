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
        self.d.line([48, H - 70, W - 48, H - 70], fill=LINE, width=2)
        self.d.text((48, H - 52), foot, font=font(24), fill=MUTED)
        b = io.BytesIO()
        self.im.save(b, "PNG", optimize=True)
        return b.getvalue()


def _sl(doc):
    if doc.get("sl_option"):
        return px(doc["sl_option"])
    return "%s %s %s" % (doc.get("index", "NIFTY"), "<" if doc["side"] == "CE" else ">", format(doc["sl_index"], ",.0f"))


def card(event, doc, foot="Paper trade · 1 lot · Not investment advice"):
    day = doc.get("day", "")
    name = {"NIFTY": "NIFTY 50"}.get(doc.get("index", "NIFTY"), doc.get("index", "NIFTY"))
    if event == "range":
        c = Card(BLUE, "%s · %s" % (name, day), "9:45 RANGE SET", "Waiting for a 1-minute close outside the range (until 13:00)")
        c.boxes([("Range HIGH  →  close above = BUY CALL", format(doc["range_high"], ",.2f"), LGREEN),
                 ("Range LOW  →  close below = BUY PUT", format(doc["range_low"], ",.2f"), LRED)], cols=1, h=120)
        return c.png(foot)
    if event == "no trade":
        c = Card(GREY, "%s · %s" % (name, day), "NO TRADE TODAY", None)
        c.line(doc.get("note") or "No breakout of the 9:15-9:45 range by 13:00", TEXT, 34, True)
        c.line("No position taken. Nothing to do.", MUTED, 30)
        return c.png(foot)
    call = doc["side"] == "CE"
    lot = doc.get("lot") or 0
    if event == "entry":
        c = Card(GREEN if call else RED, "SIGNAL · %s · %s" % (day, doc["entry_at"][11:]),
                 "BUY %s %s" % ("▲" if call else "▼", doc["symbol"]),
                 "%s breakout · expiry %s · 1 lot = %d qty" % ("Upside" if call else "Downside", doc["expiry"], lot))
        c.boxes([("Entry price", px(doc["entry"]), TEXT),
                 ("Stop-loss (sell below)", _sl(doc), LRED),
                 ("Capital (1 lot)", rs(doc["entry"] * lot), TEXT),
                 ("Exit", "15:15", AMBER)])
        if doc.get("sl_option"):
            c.line("Max loss at stop-loss ≈ %s" % rs((doc["sl_option"] - doc["entry"]) * lot, True), LRED, 30, True)
        return c.png(foot)
    if event == "status":
        g = doc["gross"]
        col = LGREEN if g > 0 else LRED if g < 0 else TEXT
        c = Card(BLUE, "LIVE · %s · %s" % (day, doc["last_at"][11:]), doc["symbol"], "Open position · updates every 10 minutes")
        c.big("P&L now (1 lot, before charges)", "%s   (%+.1f%%)" % (rs(g, True), (doc["last"] / doc["entry"] - 1) * 100), col)
        c.boxes([("Entry", px(doc["entry"]), TEXT), ("Now", px(doc["last"]), col),
                 ("Stop-loss", _sl(doc), LRED), ("Exit", "15:15", AMBER)], cols=4, h=120)
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
