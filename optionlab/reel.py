"""Daily reel: a vertical (9:16) video of the day for Instagram, made from the same cards as the Telegram alerts.

Order: title -> morning watchlist -> each entry (by time) and each target reached (by time) -> end-of-day board ->
closing slide with the day's total. Public wording only (no description of the rule). Needs ffmpeg.

    make(day, docs, out_path) -> out_path or None
"""
import datetime as dt
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw

from .telegram_cards import (AMBER, BG, BLUE, GREEN, LGREEN, LRED, LINE, MUTED, PANEL, TEXT, board_card, card, font, rs,
                             watchlist_card)

RW, RH = 1080, 1920
NAMES = {"NIFTY": "NIFTY 50", "SENSEX": "SENSEX", "BSE": "BSE Ltd", "ADANIENT": "Adani Ent", "DIXON": "Dixon", "PNB": "PNB",
         "LAURUSLABS": "Laurus Labs", "BAJFINANCE": "Bajaj Finance"}


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def _canvas():
    im = Image.new("RGB", (RW, RH), BG)
    d = ImageDraw.Draw(im)
    for yy in range(RH):                         # soft glow at the top
        k = max(0.0, 1 - yy / 900)
        c = tuple(int(BG[i] + (BLUE[i] - BG[i]) * 0.22 * k) for i in range(3))
        d.line([(0, yy), (RW, yy)], fill=c)
    return im, d


def _frame(png_bytes, label, step, total, colour=BLUE):
    """A card centred on a 9:16 canvas, with a label above and progress dots below."""
    import io
    im, d = _canvas()
    d.text((60, 120), "OPTION LAB", font=font(34, True), fill=MUTED)
    d.text((60, 172), label, font=font(54, True), fill=TEXT)
    c = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w = RW - 80
    h = int(c.height * w / c.width)
    if h > RH - 560:
        h = RH - 560
        w = int(c.width * h / c.height)
    c = c.resize((w, h), Image.LANCZOS)
    x, y = (RW - w) // 2, 300 + max(0, (RH - 560 - h) // 2)
    d.rounded_rectangle([x - 6, y - 6, x + w + 6, y + h + 6], 28, outline=colour, width=6)
    im.paste(c, (x, y))
    dots = min(total, 14)
    for i in range(dots):
        cx = RW // 2 + (i - (dots - 1) / 2) * 34
        on = i <= step * dots // max(1, total)
        d.ellipse([cx - 8, RH - 150, cx + 8, RH - 134], fill=colour if on else LINE)
    d.text((60, RH - 100), "Paper trade · 1 lot each · not investment advice", font=font(28), fill=MUTED)
    return im


def _title(day, n_trades):
    im, d = _canvas()
    d.text((60, 640), day.strftime("%A").upper(), font=font(44, True), fill=MUTED)
    d.text((60, 700), day.strftime("%d %b %Y"), font=font(110, True), fill=TEXT)
    d.text((60, 860), "Daily recap", font=font(70, True), fill=LGREEN)
    d.text((60, 990), "%d instruments · %d trade%s" % (len(NAMES), n_trades, "" if n_trades == 1 else "s"), font=font(44), fill=TEXT)
    d.text((60, 1060), "watchlist → entries → targets → result", font=font(36), fill=MUTED)
    d.text((60, RH - 100), "Paper trade · not investment advice", font=font(28), fill=MUTED)
    return im


def _closing(total, won, lost, none):
    im, d = _canvas()
    col = LGREEN if total > 0 else LRED if total < 0 else TEXT
    d.text((60, 620), "TODAY'S RESULT", font=font(44, True), fill=MUTED)
    d.text((60, 690), rs(total, True), font=font(150, True), fill=col)
    d.text((60, 880), "1 lot each, after charges", font=font(40), fill=MUTED)
    y = 1000
    for label, n, c in (("Won", won, GREEN), ("Lost", lost, (220, 38, 38)), ("No trade", none, LINE)):
        d.rounded_rectangle([60, y, 120, y + 60], 14, fill=c)
        d.text((150, y + 4), "%s  %d" % (label, n), font=font(46, True), fill=TEXT)
        y += 90
    d.text((60, RH - 100), "Paper trade · not investment advice", font=font(28), fill=MUTED)
    return im


def public(doc):
    """A copy of a trade document with public wording only."""
    d = dict(doc)
    d["public"] = True
    if d.get("status") == "no trade":
        d["note"] = "No trade today"
    return d


def make(day, docs, out_path, watch_rows=None, sec=2.6):
    """Render the reel to out_path (mp4). docs: today's trade documents. Returns out_path or None."""
    from .telegram_alerts import board_rows
    exe = _ffmpeg()
    if not exe:
        return None
    docs = [public(x) for x in docs if x.get("status") not in (None, "not started", "no market")]
    if not docs:
        return None
    trades = [x for x in docs if x.get("status") in ("open", "closed")]
    events = []                                   # (time, png, label, colour)
    for t in trades:
        events.append((t["entry_at"][11:], card("entry", t), "%s · ENTRY" % t["entry_at"][11:], GREEN if t["side"] == "CE" else (220, 38, 38)))
        for h in t.get("target_hits") or []:
            events.append((h["at"], card("t%d" % h["t"], t), "%s · TARGET %d" % (h["at"], h["t"]), GREEN))
    events.sort(key=lambda e: e[0])
    rows, _lines, total = board_rows(docs)
    frames = [(_title(day, len(trades)), 2.2)]
    if watch_rows:
        frames.append((_frame(watchlist_card(watch_rows, day.strftime("%a %d %b %Y"), sub="Today's instruments · paper trades on real option prices"), "MORNING WATCHLIST", 0, 1, BLUE), sec))
    n = len(events) + 1
    for i, (_t, png, label, colour) in enumerate(events):
        frames.append((_frame(png, label, i + 1, n, colour), sec))
    frames.append((_frame(board_card(rows, day.strftime("%d %b %Y") + " · close", total), "END OF DAY", n, n, AMBER), sec + 1.6))
    won = sum(1 for x in trades if x.get("status") == "closed" and (x.get("net") or 0) > 0)
    lost = sum(1 for x in trades if x.get("status") == "closed" and (x.get("net") or 0) <= 0)
    frames.append((_closing(total, won, lost, len(docs) - len(trades)), 2.6))
    tmp = tempfile.mkdtemp()
    args, filt = [exe, "-y"], []
    for i, (im, d) in enumerate(frames):
        p = os.path.join(tmp, "f%02d.png" % i)
        im.save(p)
        args += ["-loop", "1", "-t", "%.2f" % d, "-i", p]
        fo = max(0.0, d - 0.35)
        filt.append("[%d:v]scale=%d:%d,format=yuv420p,fade=t=in:st=0:d=0.35,fade=t=out:st=%.2f:d=0.35,setsar=1[v%d]" % (i, RW, RH, fo, i))
    filt.append("".join("[v%d]" % i for i in range(len(frames))) + "concat=n=%d:v=1:a=0[out]" % len(frames))
    args += ["-filter_complex", ";".join(filt), "-map", "[out]", "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    r = subprocess.run(args, capture_output=True, text=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg failed: %s" % r.stderr[-400:])
    return out_path


def watchlist_rows(day):
    """Morning watchlist rows for every instrument: name, the day's first price, lot, nearest expiry."""
    from . import nifty_orb
    rows = []
    for ix, c in nifty_orb.INDEXES.items():
        if c.get("start") and c["start"] > day:
            continue
        try:
            bars = nifty_orb.candles(c["key"], day)
            cs = [x for x in nifty_orb.contracts(day, ix) if x["expiry"] >= str(day)]
        except Exception:      # noqa: BLE001
            continue
        if not bars or not cs:
            continue
        e = min(x["expiry"] for x in cs)
        rows.append((NAMES.get(ix, ix), bars[0][1], [x["lot"] for x in cs if x["expiry"] == e][0], e, "WATCHING"))
    return rows
