"""Animated daily video (motion graphics) for Instagram, 1080x1920 at 30 fps, rendered frame by frame with Pillow
and encoded by ffmpeg. Public wording only (no description of the rule). No audio: add a sound in Instagram.

Scenes: intro -> watchlist -> one scene per trade (price chart draws itself, targets pop, P&L rolls) ->
scoreboard bars -> total -> outro.

    make(day, docs, out_path, watch_rows) -> out_path
"""
import math
import random
import subprocess

from PIL import Image, ImageDraw

from .reel import NAMES, _ffmpeg
from .telegram_cards import font

W, H, FPS = 1080, 1920, 30
BG0, BG1 = (6, 10, 22), (14, 21, 39)
TEXT, MUTED, LINE, PANEL = (236, 240, 248), (139, 151, 180), (40, 52, 82), (16, 23, 41)
UP, DN, AMB = (52, 211, 153), (251, 113, 133), (251, 191, 36)
IXC = {"NIFTY": (129, 140, 248), "SENSEX": (34, 211, 238), "BSE": (244, 114, 182), "ADANIENT": (251, 191, 36),
       "DIXON": (52, 211, 153), "PNB": (249, 115, 22), "LAURUSLABS": (96, 165, 250), "BAJFINANCE": (232, 121, 249),
       "TCS": (163, 230, 53)}
GRAD = [(99, 102, 241), (168, 85, 247), (34, 211, 238)]


# ---------------------------------------------------------------- helpers
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def eo(t):                      # ease out cubic
    t = clamp(t)
    return 1 - (1 - t) ** 3


def eb(t):                      # ease out back (small overshoot)
    t = clamp(t)
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def seg(t, a, b):               # progress of t inside [a, b]
    return clamp((t - a) / (b - a)) if b > a else 1.0


def mix(c1, c2, k):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * k) for i in range(3))


def rs(v, signed=True):
    s = format(abs(int(round(v))), ",")
    return ("+" if v > 0 else "−" if v < 0 else "") + "₹" + s if signed else "₹" + s


_F = {}


def F(size, bold=True):
    k = (size, bold)
    if k not in _F:
        _F[k] = font(size, bold)
    return _F[k]


def text_w(d, s, f):
    return d.textlength(s, font=f)


def alpha_text(im, xy, s, f, fill, a=1.0, anchor="la"):
    """Text with opacity a (0..1) on an RGB image."""
    if a <= 0:
        return
    if a >= 1:
        ImageDraw.Draw(im).text(xy, s, font=f, fill=fill, anchor=anchor)
        return
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, s, font=f, fill=fill + (int(255 * a),), anchor=anchor)
    im.paste(layer, (0, 0), layer)


def dashed(d, x1, y, x2, fill, width=3, dash=14, gap=10):
    x = x1
    while x < x2:
        d.line([(x, y), (min(x + dash, x2), y)], fill=fill, width=width)
        x += dash + gap


_BG = None


def background(frame):
    """Dark gradient with a slow diagonal grid and a drifting glow."""
    global _BG
    if _BG is None:
        _BG = Image.new("RGB", (W, H))
        px = _BG.load()
        for y in range(H):
            c = mix(BG1, BG0, y / H)
            for x in range(W):
                px[x, y] = c
    im = _BG.copy()
    d = ImageDraw.Draw(im, "RGBA")
    off = (frame * 1.2) % 120
    for i in range(-20, 40):
        x = i * 120 + off
        d.line([(x, 0), (x - H * 0.35, H)], fill=(255, 255, 255, 8), width=2)
    gx = W * 0.5 + math.sin(frame / 70) * 260
    gy = 420 + math.cos(frame / 90) * 120
    for r, a in ((520, 10), (380, 12), (240, 14)):
        d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=GRAD[0] + (a,))
    return im


def header(im, title, frame, t):
    d = ImageDraw.Draw(im)
    k = eo(seg(t, 0, 0.25))
    alpha_text(im, (70, 110), "OPTION LAB", F(34), MUTED, k)
    alpha_text(im, (70 - 40 * (1 - k), 158), title, F(64), TEXT, k)
    d.rectangle([70, 250, 70 + int(180 * k), 256], fill=GRAD[2])


def footer(im, a=1.0):
    alpha_text(im, (W // 2, H - 90), "Paper trade · 1 lot each · not investment advice", F(28, False), MUTED, a, "ma")


def gradient_text(im, xy, s, f, anchor="la"):
    """Text filled with the brand gradient."""
    d = ImageDraw.Draw(im)
    bbox = tuple(int(round(v)) for v in d.textbbox(xy, s, font=f, anchor=anchor))
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w <= 0 or h <= 0:
        return
    strip = Image.new("RGB", (256, 1))
    for x in range(256):
        k = x / 255
        strip.putpixel((x, 0), mix(GRAD[0], GRAD[1], k * 2) if k < .5 else mix(GRAD[1], GRAD[2], (k - .5) * 2))
    grad = strip.resize((w, h))
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).text(xy, s, font=f, fill=255, anchor=anchor)
    im.paste(grad, (bbox[0], bbox[1]), mask.crop(bbox).resize((w, h)))


# ---------------------------------------------------------------- scenes (each: draw(im, frame, t))
def scene_intro(day, n_trades):
    def draw(im, frame, t):
        d = ImageDraw.Draw(im)
        k1, k2, k3 = eb(seg(t, 0.0, 0.35)), eo(seg(t, 0.2, 0.55)), eo(seg(t, 0.45, 0.8))
        cx, cy = W // 2, 700
        r = 90 * k1
        d.rounded_rectangle([cx - r, cy - r, cx + r, cy + r], int(36 * k1 + 1), fill=GRAD[0])
        if k1 > 0.3:                             # logo: rising line
            pts = [(cx - 55, cy + 30), (cx - 15, cy - 5), (cx + 10, cy + 15), (cx + 55, cy - 40)]
            n = 1 + int(3 * clamp((k1 - .3) / .7))
            d.line(pts[:n + 1], fill=(255, 255, 255), width=12, joint="curve")
        alpha_text(im, (cx, 860 + 30 * (1 - k2)), day.strftime("%A").upper(), F(40), MUTED, k2, "ma")
        if k2 > 0:
            gradient_text(im, (cx, 920 + 30 * (1 - k2)), day.strftime("%d %b %Y"), F(118), "ma")
        alpha_text(im, (cx, 1080), "DAILY RECAP", F(58), TEXT, k3, "ma")
        alpha_text(im, (cx, 1170), "%d instruments · %d trade%s" % (len(NAMES), n_trades, "" if n_trades == 1 else "s"), F(40, False), MUTED, k3, "ma")
        footer(im, k3)
    return draw


def scene_watch(rows):
    def draw(im, frame, t):
        header(im, "Today's watchlist", frame, t)
        d = ImageDraw.Draw(im)
        for i, (ix, name, price, lot, exp) in enumerate(rows):
            k = eo(seg(t, 0.08 + i * 0.08, 0.38 + i * 0.08))
            if k <= 0:
                continue
            gap = min(190, int(1330 / max(1, len(rows))))
            y = 330 + i * gap
            x = 70 + int(260 * (1 - k))
            col = IXC.get(ix, GRAD[0])
            hh = gap - 30
            d.rounded_rectangle([x, y, x + W - 140, y + hh], 28, fill=PANEL, outline=LINE, width=2)
            d.rounded_rectangle([x, y, x + 12, y + hh], 6, fill=col)
            big, small = (50, 28) if hh >= 150 else (40, 24)
            y1, y2 = y + int(hh * 0.16), y + int(hh * 0.62)
            d.ellipse([x + 44, y1 + 10, x + 72, y1 + 38], fill=col)
            d.text((x + 92, y1), name, font=F(big), fill=TEXT)
            d.text((x + 92, y2), "Lot %s · expiry %s" % (lot, exp[5:] if exp else "–"), font=F(small, False), fill=MUTED)
            d.text((x + W - 190, y1 + 2), format(price * k, ",.2f"), font=F(big - 4), fill=TEXT, anchor="ra")
            d.text((x + W - 190, y2 + 2), "OPEN", font=F(small - 4), fill=AMB, anchor="ra")
        footer(im)
    return draw


def _mins(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def scene_trade(doc, idx, total):
    ix = doc.get("index", "NIFTY")
    col = IXC.get(ix, GRAD[0])
    call = doc.get("side") == "CE"
    path = [(_mins(a), v) for a, v in (doc.get("path") or [])]
    entry, qty = doc["entry"], doc.get("lot") or doc.get("qty") or 1
    sl = doc.get("sl_option")
    tg = doc.get("targets") or []
    hits = {h["t"]: h for h in doc.get("target_hits") or []}
    final = doc["net"] if doc.get("status") == "closed" else doc.get("gross", 0)
    shown_t = [v for i, v in enumerate(tg) if (i + 1) in hits] + ([tg[len(hits)]] if len(hits) < len(tg) else [])
    vals = [p[1] for p in path] + [entry] + ([sl] if sl else []) + shown_t
    lo, hi = (min(vals), max(vals)) if vals else (entry * .8, entry * 1.2)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    CX0, CX1, CY0, CY1 = 120, W - 190, 860, 1380

    def draw(im, frame, t):
        header(im, "%s · %s" % (doc["entry_at"][11:16], NAMES.get(ix, ix)), frame, t)
        d = ImageDraw.Draw(im)
        k = eo(seg(t, 0, 0.18))
        x0 = 70 + int(W * (1 - k))
        d.rounded_rectangle([x0, 320, x0 + W - 140, 1560], 36, fill=PANEL, outline=LINE, width=2)
        d.rounded_rectangle([x0, 320, x0 + W - 140, 334], 8, fill=col)
        sym = doc["symbol"]
        d.text((x0 + 50, 380), sym, font=F(62), fill=TEXT)
        pill = "CALL" if call else "PUT"
        pw = text_w(d, pill, F(30)) + 44
        d.rounded_rectangle([x0 + 50, 470, x0 + 50 + pw, 524], 27, fill=UP if call else DN)
        d.text((x0 + 72, 478), pill, font=F(30), fill=(10, 14, 24))
        d.text((x0 + 80 + pw, 480), "Entry %s at %s" % (format(entry, ",.2f"), doc["entry_at"][11:16]), font=F(34, False), fill=MUTED)
        nr = [n for n in doc.get("near") or [] if n["side"] == doc.get("side")]
        if nr:
            d.text((x0 + 50, 548), "Be ready %s · %s side · strike %s" % (nr[0]["at"], "call" if call else "put", format(nr[0]["strike"], "g")),
                   font=F(30, False), fill=AMB)
        dx = x0 - 70                                  # chart moves with the card
        yv = lambda v: CY1 - (v - lo) / (hi - lo) * (CY1 - CY0)
        # level lines
        a = eo(seg(t, 0.12, 0.3))
        if a > 0:
            dashed(d, CX0 + dx, yv(entry), CX1 + dx, mix(PANEL, MUTED, a), 3)
            d.text((CX1 + dx + 12, yv(entry) - 16), "Entry", font=F(24), fill=mix(PANEL, MUTED, a))
            if sl:
                dashed(d, CX0 + dx, yv(sl), CX1 + dx, mix(PANEL, DN, a), 3)
                d.text((CX1 + dx + 12, yv(sl) - 16), "SL", font=F(24), fill=mix(PANEL, DN, a))
            for i, v in enumerate(tg):
                if v in shown_t:
                    dashed(d, CX0 + dx, yv(v), CX1 + dx, mix(PANEL, UP, a * (1 if (i + 1) in hits else .55)), 3)
                    d.text((CX1 + dx + 12, yv(v) - 16), "T%d" % (i + 1), font=F(24), fill=mix(PANEL, UP, a))
        # the price line draws itself
        f = eo(seg(t, 0.22, 0.78))
        cur = entry
        if len(path) > 1 and f > 0:
            m0, m1 = path[0][0], path[-1][0]
            xm = lambda m: CX0 + dx + (m - m0) / max(1, m1 - m0) * (CX1 - CX0)
            n = max(2, int(len(path) * f))
            pts = [(xm(m), yv(v)) for m, v in path[:n]]
            cur = path[n - 1][1]
            lc = UP if cur >= entry else DN
            d.line(pts, fill=lc, width=6, joint="curve")
            d.ellipse([pts[-1][0] - 11, pts[-1][1] - 11, pts[-1][0] + 11, pts[-1][1] + 11], fill=lc)
            for tn, h in sorted(hits.items()):       # target badges pop as the line passes them
                if _mins(h["at"]) <= path[n - 1][0]:
                    hx, hy = xm(_mins(h["at"])), yv(h["price"])
                    pk = eb(clamp((path[n - 1][0] - _mins(h["at"])) / 12))
                    r = 16 * pk
                    d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=UP, outline=(255, 255, 255), width=3)
                    if pk > .5:
                        d.rounded_rectangle([hx - 52, hy - 74, hx + 52, hy - 30], 18, fill=UP)
                        d.text((hx, hy - 70), "T%d ✓" % tn if False else "T%d" % tn, font=F(28), fill=(10, 14, 24), anchor="ma")
        # P&L counter
        pl = final if t >= 0.8 else (cur - entry) * qty
        pc = UP if pl >= 0 else DN
        d.text((x0 + 50, 600), "P&L (1 lot)", font=F(30, False), fill=MUTED)
        d.text((x0 + 50, 640), rs(pl), font=F(104), fill=pc)
        # result stamp
        s = eb(seg(t, 0.8, 0.95))
        if s > 0:
            won = final > 0
            lab = "WON" if won else "LOST"
            sz = int(64 * s)
            if sz > 8:
                f2 = F(sz)
                tw = text_w(d, lab, f2)
                bx, by = x0 + W - 210 - tw, 400
                d.rounded_rectangle([bx - 24, by - 12, bx + tw + 24, by + sz + 22], 20, outline=UP if won else DN, width=6)
                d.text((bx, by), lab, font=f2, fill=UP if won else DN)
            why = doc.get("why") or ""
            alpha_text(im, (x0 + 50, 1460), ("Exit %s at %s · %s" % (format(doc.get("exit") or 0, ",.2f"), (doc.get("exit_at") or "")[11:16],
                                                                   "stop-loss" if str(why).startswith("SL") else "time exit")) if doc.get("status") == "closed" else "Open",
                       F(34, False), MUTED, s)
        dots = total
        for i in range(dots):
            cx = W // 2 + (i - (dots - 1) / 2) * 40
            d.ellipse([cx - 9, 1640, cx + 9, 1658], fill=col if i <= idx else LINE)
        footer(im)
    return draw


def scene_scores(items):
    """items: [(ix, name, pnl or None)]"""
    mx = max([abs(p) for _ix, _n, p in items if p is not None] + [1])

    def draw(im, frame, t):
        header(im, "Scoreboard", frame, t)
        d = ImageDraw.Draw(im)
        bx0, bx1 = 70, W - 70
        for i, (ix, name, p) in enumerate(items):
            gap = min(205, int(1340 / max(1, len(items))))
            y = 340 + i * gap
            k = eo(seg(t, 0.08 + i * 0.07, 0.55 + i * 0.07))
            a = eo(seg(t, 0.02 + i * 0.07, 0.25 + i * 0.07))
            if a <= 0:
                continue
            d.rounded_rectangle([bx0, y, bx1, y + gap - 30], 26, fill=PANEL, outline=LINE, width=2)
            hh = gap - 30
            by = y + hh - 36
            d.ellipse([bx0 + 34, y + 30, bx0 + 62, y + 58], fill=IXC.get(ix, GRAD[0]))
            d.text((bx0 + 80, y + 20), name, font=F(42 if hh >= 150 else 36), fill=TEXT)
            if p is None:
                d.text((bx1 - 34, y + 24), "No trade", font=F(34, False), fill=MUTED, anchor="ra")
                d.rounded_rectangle([bx0 + 34, by, bx1 - 34, by + 22], 11, fill=LINE)
                continue
            c = UP if p >= 0 else DN
            d.text((bx1 - 34, y + 16), rs(p * k), font=F(48 if hh >= 150 else 40), fill=c, anchor="ra")
            full = (bx1 - 34) - (bx0 + 34)
            L = max(28, full * abs(p) / mx * k)
            d.rounded_rectangle([bx0 + 34, by, bx1 - 34, by + 22], 11, fill=LINE)
            d.rounded_rectangle([bx0 + 34, by, bx0 + 34 + L, by + 22], 11, fill=c)
        footer(im)
    return draw


def scene_total(total, won, lost, none, seed):
    rnd = random.Random(seed)
    conf = [(rnd.uniform(0, W), rnd.uniform(-400, 0), rnd.uniform(-2, 2), rnd.uniform(6, 14), rnd.choice(GRAD + [UP, AMB]),
             rnd.uniform(0, 6.28)) for _ in range(90)]

    def draw(im, frame, t):
        d = ImageDraw.Draw(im)
        k = eo(seg(t, 0.05, 0.6))
        c = UP if total > 0 else DN if total < 0 else TEXT
        alpha_text(im, (W // 2, 560), "TODAY'S RESULT", F(46), MUTED, eo(seg(t, 0, .2)), "ma")
        d.text((W // 2, 640), rs(total * k), font=F(150), fill=c, anchor="ma")
        alpha_text(im, (W // 2, 840), "1 lot each, after charges", F(38, False), MUTED, k, "ma")
        for i, (lab, n, cc) in enumerate((("Won", won, UP), ("Lost", lost, DN), ("No trade", none, MUTED))):
            a = eb(seg(t, 0.35 + i * 0.1, 0.6 + i * 0.1))
            if a <= 0:
                continue
            x = 150 + i * 280
            r = int(110 * a)
            d.ellipse([x + 110 - r, 1050 - r, x + 110 + r, 1050 + r], outline=cc, width=10)
            d.text((x + 110, 1010), str(n), font=F(72), fill=TEXT, anchor="ma")
            d.text((x + 110, 1180), lab, font=F(36, False), fill=MUTED, anchor="ma")
        if total > 0 and t > 0.3:                   # confetti on a green day
            dt = (t - 0.3) * 60
            for x, y, vx, s, cc, ph in conf:
                yy = y + dt * 38
                if 0 < yy < H:
                    xx = x + vx * dt * 6 + math.sin(ph + dt / 6) * 20
                    d.rectangle([xx, yy, xx + s, yy + s * 0.6], fill=cc)
        footer(im)
    return draw


def scene_outro(day):
    def draw(im, frame, t):
        k = eo(seg(t, 0, 0.4))
        gradient_text(im, (W // 2, 820), "OPTION LAB", F(110), "ma") if k > 0.05 else None
        alpha_text(im, (W // 2, 970), "Daily paper-trading recap · " + day.strftime("%d %b"), F(40, False), TEXT, k, "ma")
        alpha_text(im, (W // 2, 1040), "Not investment advice", F(36, False), MUTED, k, "ma")
    return draw


def timeline_events(docs):
    """Every alert of the day in time order: (time, ix, tag, detail, colour)."""
    ev = []
    for x in docs:
        ix = x.get("index", "NIFTY")
        for n in x.get("near") or []:
            ev.append((n["at"], ix, "BE READY", "%s side · %s %s" % ("CALL" if n["side"] == "CE" else "PUT",
                                                                   format(n["strike"], "g"), n["side"]), AMB))
        if x.get("status") in ("open", "closed"):
            call = x["side"] == "CE"
            ev.append((x["entry_at"][11:16], ix, "ENTRY", "BUY %s %s @ %s" % (format(x["strike"], "g"), x["side"],
                                                                            format(x["entry"], ",.2f")), UP if call else DN))
            for h in x.get("target_hits") or []:
                ev.append((h["at"], ix, "TARGET %d" % h["t"], "%s %s hit %s" % (format(x["strike"], "g"), x["side"],
                                                                             format(h["price"], ",.2f")), UP))
            if x.get("status") == "closed":
                pl = x.get("net") or 0
                ev.append(((x.get("exit_at") or "")[11:16], ix, "EXIT", "%s %s out %s · %s" % (
                    format(x["strike"], "g"), x["side"], format(x.get("exit") or 0, ",.2f"), rs(pl)), UP if pl > 0 else DN))
    return sorted(ev, key=lambda e: (e[0], e[1]))


def scene_timeline(rows, page, pages):
    def draw(im, frame, t):
        header(im, "Alerts timeline" + ("  %d/%d" % (page + 1, pages) if pages > 1 else ""), frame, t)
        d = ImageDraw.Draw(im)
        d.line([(196, 330), (196, 330 + len(rows) * 172 - 40)], fill=LINE, width=4)
        for i, (hm, ix, tag, det, col) in enumerate(rows):
            k = eo(seg(t, 0.05 + i * 0.09, 0.3 + i * 0.09))
            if k <= 0:
                continue
            y = 330 + i * 172
            x = 70 + int(180 * (1 - k))
            d.text((70, y + 30), hm, font=F(36), fill=MUTED)
            r = int(16 * eb(seg(t, 0.05 + i * 0.09, 0.25 + i * 0.09)))
            d.ellipse([196 - r, y + 52 - r, 196 + r, y + 52 + r], fill=col)
            d.rounded_rectangle([x + 170, y, W - 70, y + 148], 24, fill=PANEL, outline=LINE, width=2)
            d.rounded_rectangle([x + 170, y, x + 182, y + 148], 6, fill=IXC.get(ix, GRAD[0]))
            d.text((x + 206, y + 20), NAMES.get(ix, ix), font=F(36), fill=TEXT)
            tw = text_w(d, tag, F(26)) + 30
            tx = W - 100 - tw
            d.rounded_rectangle([tx, y + 22, tx + tw, y + 64], 21, fill=col)
            d.text((tx + 15, y + 28), tag, font=F(26), fill=(10, 14, 24))
            d.text((x + 206, y + 84), det, font=F(32, False), fill=TEXT)
        footer(im)
    return draw


# ---------------------------------------------------------------- render
def make(day, docs, out_path, watch_rows=None, target=60.0):
    exe = _ffmpeg()
    if not exe:
        return None
    docs = [x for x in docs if x.get("status") not in (None, "not started", "no market")]
    trades = sorted([x for x in docs if x.get("status") in ("open", "closed")], key=lambda x: x["entry_at"])
    rev = {v: k for k, v in NAMES.items()}
    wl = [(rev.get(r[0], r[0]), r[0], r[1], r[2], r[3]) for r in (watch_rows or [])]
    items = []
    for x in docs:
        p = (x["net"] if x.get("status") == "closed" else x.get("gross")) if x.get("status") in ("open", "closed") else None
        items.append((x.get("index", "NIFTY"), NAMES.get(x.get("index", "NIFTY"), x.get("index")), p))
    total = sum(p for _i, _n, p in items if p is not None)
    won = sum(1 for x in trades if (x.get("net") or 0) > 0 and x.get("status") == "closed")
    lost = len(trades) - won
    scenes = [(scene_intro(day, len(trades)), 2.6)]
    if wl:
        scenes.append((scene_watch(wl), 3.4))
    ev = timeline_events(docs)
    page = 7
    pages = [ev[i:i + page] for i in range(0, len(ev), page)]
    scenes += [(scene_timeline(rows, i, len(pages)), 1.0 + 0.45 * len(rows)) for i, rows in enumerate(pages)]
    per = 3.6 if len(trades) <= 4 else 3.1
    scenes += [(scene_trade(x, i, len(trades)), per) for i, x in enumerate(trades)]
    scenes += [(scene_scores(items), 4.0), (scene_total(total, won, lost, len(docs) - len(trades), day.toordinal()), 3.6),
               (scene_outro(day), 2.2)]
    k = min(1.8, max(1.0, target / sum(sec for _d, sec in scenes)))   # slow every scene evenly to about a minute
    scenes = [(draw, sec * k) for draw, sec in scenes]
    p = subprocess.Popen([exe, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(FPS),
                          "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                          "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
    frame = 0
    for draw, sec in scenes:
        n = int(sec * FPS)
        for i in range(n):
            im = background(frame)
            draw(im, frame, i / max(1, n - 1))
            fade = min(1.0, i / 6, (n - 1 - i) / 6)     # quick fade between scenes
            if fade < 1:
                im = Image.blend(Image.new("RGB", (W, H), BG0), im, max(0.0, fade))
            p.stdin.write(im.tobytes())
            frame += 1
    p.stdin.close()
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed")
    return out_path
