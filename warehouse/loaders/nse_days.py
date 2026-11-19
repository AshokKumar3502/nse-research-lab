"""NSE's daily stock and index files, symbol changes and bonus/split list, downloaded straight from NSE
into the same cache layout the india-day-trader-skill pack uses (<SOURCE>/nse/...). The laptop shares
the skill pack's cache; a GitHub runner has no skill pack, so it fills its own with this.

  <SOURCE>/nse/days/eq/YYYY-MM-DD.csv.gz    SYMBOL,series,open,high,low,close,prev_close,volume,turnover
  <SOURCE>/nse/days/idx/YYYY-MM-DD.csv.gz   key,name,open,high,low,close,volume
  <SOURCE>/nse/symbolchange.csv             NSE's file as published
  <SOURCE>/nse/corp-actions.json            {"years": {"2026": [[SYMBOL, ex_date, factor, subject]]}, "fetched": {}}

Days NSE never published (holidays) are remembered in state.json and not asked again.
"""
import csv
import datetime
import gzip
import http.cookiejar
import io
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .. import config, net, store

UDIFF_FROM = datetime.date(2024, 7, 8)
SERIES = ("EQ", "BE", "BZ")


def _dir(kind):
    return os.path.join(config.SOURCE, "nse", "days", kind)


def eq_url(d):
    if d >= UDIFF_FROM:
        return config.ARCH + "content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip" % d.strftime("%Y%m%d")
    mon = d.strftime("%b").upper()
    return config.ARCH + "content/historical/EQUITIES/%d/%s/cm%02d%s%dbhav.csv.zip" % (d.year, mon, d.day, mon, d.year)


def idx_url(d):
    return config.ARCH + "content/indices/ind_close_all_%s.csv" % d.strftime("%d%m%Y")


def _f(x):
    try:
        return float(str(x).strip().replace(",", ""))
    except ValueError:
        return None


def parse_eq(text):
    rd = csv.DictReader(io.StringIO(text))
    rd.fieldnames = [h.strip() for h in (rd.fieldnames or [])]
    if "TckrSymb" in rd.fieldnames:
        k = ("TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal")
    elif "SYMBOL" in rd.fieldnames:
        k = ("SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL")
    else:
        raise ValueError("not an NSE stock bhavcopy")
    out = {}
    for r in rd:
        sym, ser = (r.get(k[0]) or "").strip().upper(), (r.get(k[1]) or "").strip().upper()
        if not sym or ser not in SERIES:
            continue
        o, h, l, c, p, v, t = (_f(r.get(x)) for x in k[2:])
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or (sym in out and out[sym][0] == "EQ"):
            continue
        out[sym] = (ser, o, max(h, o, c), min(l, o, c), c, p or 0.0, v or 0.0, t or 0.0)
    return out


def parse_idx(text):
    rd = csv.DictReader(io.StringIO(text))
    rd.fieldnames = [h.strip() for h in (rd.fieldnames or [])]
    out = {}
    for r in rd:
        name, c = (r.get("Index Name") or "").strip(), _f(r.get("Closing Index Value"))
        if not name or not c or c <= 0:
            continue
        o = _f(r.get("Open Index Value")) or c
        o = o if o > 0 else c
        h = _f(r.get("High Index Value")) or max(o, c)
        l = _f(r.get("Low Index Value")) or min(o, c)
        out[" ".join(name.lower().split())] = (name, o, max(h, o, c), min(l if l > 0 else c, o, c), c,
                                                 _f(r.get("Volume")) or 0.0)
    return out


def _fetch(kind, d):
    try:
        text = net.text(net.get(eq_url(d) if kind == "eq" else idx_url(d)))
        rows = (parse_eq if kind == "eq" else parse_idx)(text)
    except (net.NotPublished, ValueError):
        return d, None
    return d, rows


def sync_days(days_back=550, workers=4, log=print):
    """Fetch every eq/idx day file of the last `days_back` calendar days that the cache lacks."""
    today = config.today_ist()
    start = today - datetime.timedelta(days=days_back)
    got = {}
    for kind in ("eq", "idx"):
        os.makedirs(_dir(kind), exist_ok=True)
        have = {f[:10] for f in os.listdir(_dir(kind)) if f.endswith(".csv.gz")}
        closed = store.closed_days("nse_" + kind)
        todo = [start + datetime.timedelta(days=i) for i in range((today - start).days + 1)]
        todo = [d for d in todo if d.isoformat() not in have and d.isoformat() not in closed]
        newly_closed, n = [], 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for d, rows in ex.map(lambda d: _fetch(kind, d), todo):
                if rows is None:
                    if (today - d).days > 4:
                        newly_closed.append(d)
                    continue
                p = os.path.join(_dir(kind), d.isoformat() + ".csv.gz")
                with gzip.open(p + ".tmp", "wt", encoding="utf-8", newline="") as fh:
                    w = csv.writer(fh)
                    for key in sorted(rows):
                        w.writerow((key,) + tuple(rows[key]))
                os.replace(p + ".tmp", p)
                n += 1
        store.mark_closed("nse_" + kind, newly_closed)
        got[kind] = n
        log("nse %s: %d new day file(s), %d checked" % (kind, n, len(todo)))
    return got


def _nse_json(url, jar):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    h = {"User-Agent": config.UA, "Accept-Language": "en-US,en;q=0.9"}
    if not list(jar):
        op.open(urllib.request.Request("https://www.nseindia.com/", headers=dict(h, Accept="text/html,*/*")),
                timeout=60).read()
    raw = op.open(urllib.request.Request(url, headers=dict(h, Accept="application/json",
                                                         Referer="https://www.nseindia.com/")), timeout=90).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


_RE_BONUS = re.compile(r"bonus\s*(\d+)\s*:\s*(\d+)", re.I)
_RE_FV = re.compile(r"(?:split|sub-division|consolidat)[^0-9]*?from\s*(?:rs\.?|re\.?|inr)?\s*([\d.]+)[^0-9]+?"
                    r"to\s*(?:rs\.?|re\.?|inr)?\s*([\d.]+)", re.I)


def ca_factor(subject):
    f = 1.0
    for a, b in _RE_BONUS.findall(subject or ""):
        a, b = float(a), float(b)
        if a > 0 and b > 0:
            f *= b / (a + b)
    for x, y in _RE_FV.findall(subject or ""):
        try:
            x, y = float(x.rstrip(".")), float(y.rstrip("."))
        except ValueError:
            continue
        if x > 0 and y > 0 and x != y:
            f *= y / x
    return None if f == 1.0 else f


def sync_reference(years=2, log=print):
    """symbolchange.csv (weekly) and the bonus/split list for the last `years` years (current year daily)."""
    root = os.path.join(config.SOURCE, "nse")
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, "symbolchange.csv")
    if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 7 * 86400:
        try:
            raw = net.get(config.ARCH + "content/equities/symbolchange.csv")
            with open(p, "wb") as fh:
                fh.write(raw)
        except (net.NotPublished, IOError) as e:
            log("symbol changes: kept the old file (%s)" % e)
    p = os.path.join(root, "corp-actions.json")
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        doc = {"years": {}, "fetched": {}}
    from .results import Session      # warms up on a page NSE allows scripts to open (the home page may 403)
    this, sess = config.today_ist().year, Session(pause=1.0)
    for y in range(this - years, this + 1):
        k = str(y)
        if k in doc["years"] and (y != this or time.time() - doc["fetched"].get(k, 0) < 86400 * 0.9):
            continue
        try:
            rows = sess.json("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
                             "&from_date=01-01-%d&to_date=31-12-%d" % (y, y))
        except Exception as e:     # noqa: BLE001 - NSE's site blocks some networks; keep what we have
            log("corporate actions %d: %s (kept what was there)" % (y, str(e)[:80]))
            continue
        keep = []
        for r in rows if isinstance(rows, list) else []:
            f = ca_factor(r.get("subject"))
            try:
                d = datetime.datetime.strptime(r.get("exDate", ""), "%d-%b-%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            if f is not None:
                keep.append([(r.get("symbol") or "").strip().upper(), d, f, r.get("subject", "")])
        doc["years"][k], doc["fetched"][k] = keep, time.time()
        time.sleep(1.0)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(p + ".tmp", p)
    log("reference: corporate actions for %s" % ", ".join(sorted(doc["years"])))
