"""Quarterly results filing times for EVERY stock that was ever in the research universe, including
stocks that are delisted or renamed today, so "has results history" can never stand in for "survived
to today".

Source: NSE's two results lists (integrated filings and the older corporates-financial-results). Both
lists are always read and merged (the earliest original filing per quarter wins), and every ticker a
company has traded under is tried. Self-contained (no skill pack), so it also runs on a GitHub runner.

  fetch_all       every universe stock (monthly, or the first time)
  update_recent   daily: the latest filings feed, plus stocks that are due to report for the last
                  quarter and have not yet (most overdue first, capped)

Cache: data/raw/results/<SYM>.json -> [{"q": "YYYY-MM-DD", "at": "YYYY-MM-DD HH:MM"}], one file per
sym_id. Resumable.
"""
import datetime
import gzip
import http.cookiejar
import json
import os
import threading
import time
import urllib.request

import pandas as pd

from .. import config, store

DIR = os.path.join(config.RAW, "results")
NSE_HOME = "https://www.nseindia.com/"
WARM = ["https://www.nseindia.com/companies-listing/corporate-filings-insider-trading", NSE_HOME]
INTEGRATED_URL = ("https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol=%s"
                  "&period_ended=all&type=Integrated%%20Filing-%%20Financials")
LEGACY_URL = "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=%s&period=Quarterly"
FEED_URL = ("https://www.nseindia.com/api/integrated-filing-results?index=equities&period=Quarterly"
            "&type=Integrated%%20Filing-%%20Financials&from_date=%s&to_date=%s")


class Session(object):
    """NSE JSON with a cookie jar, one request every `pause` seconds."""

    def __init__(self, pause=0.7):
        self.jar, self.pause, self.last = http.cookiejar.CookieJar(), pause, 0.0
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.h = {"User-Agent": config.UA, "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}

    def _open(self, url, accept):
        wait = self.last + self.pause - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        raw = self.op.open(urllib.request.Request(url, headers=dict(self.h, Accept=accept, Referer=NSE_HOME)),
                           timeout=60).read()
        return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

    def json(self, url):
        if not list(self.jar):
            for w in WARM:
                try:
                    self._open(w, "text/html,*/*")
                    break
                except Exception:     # noqa: BLE001
                    continue
        for attempt in (1, 2):
            try:
                raw = self._open(url, "application/json, text/plain, */*")
                if raw[:1] not in (b"[", b"{"):
                    raise ValueError("NSE sent a web page, not data")
                return json.loads(raw.decode("utf-8"))
            except Exception:      # noqa: BLE001
                if attempt == 2:
                    raise
                self.jar.clear()
                for w in WARM:
                    try:
                        self._open(w, "text/html,*/*")
                        break
                    except Exception:     # noqa: BLE001
                        continue


def _when(text, fmts):
    for f in fmts:
        try:
            return datetime.datetime.strptime((text or "").strip(), f)
        except ValueError:
            pass
    return None


def parse_filings(raw, kind):
    """NSE's results lists -> [(quarter_end date, filed datetime)], originals only."""
    rows = raw if isinstance(raw, list) else ((raw or {}).get("data") or [])
    out = []
    for r in rows:
        if kind == "legacy":
            if (r.get("period") or "Quarterly") != "Quarterly":
                continue
            q = _when(r.get("toDate"), ("%d-%b-%Y",))
            at = _when(r.get("broadCastDate") or r.get("filingDate"), ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"))
        else:
            if (r.get("type_Sub") or "Original") != "Original":
                continue
            q = _when((r.get("qe_Date") or "").title(), ("%d-%b-%Y",))
            at = _when(r.get("broadcast_Date") or r.get("creation_Date"), ("%d-%b-%Y %H:%M:%S",))
        if q and at and q <= at and (at - q).days <= 120:
            out.append((q.date(), at))
    return out


def _path(sym):
    return os.path.join(DIR, sym.replace("&", "_AND_").replace("/", "_") + ".json")


def fetch_one(sym, aliases, sess):
    """Every (quarter_end, filed_at) for one company across all its tickers and both lists."""
    rows, errors, asked = [], [], 0
    for s in [sym] + [x for x in aliases if x != sym]:
        code = s.replace("&", "%26")
        for kind, url in (("integrated", INTEGRATED_URL % code), ("legacy", LEGACY_URL % code)):
            asked += 1
            try:
                rows += parse_filings(sess.json(url), kind)
            except Exception as e:     # noqa: BLE001 - NSE errors vary; the count below decides
                errors.append("%s %s: %s" % (s, kind, str(e)[:60]))
    if not rows and len(errors) == asked:
        raise RuntimeError("; ".join(errors[:2]))
    best = {}
    for q, at in rows:
        if q not in best or at < best[q]:
            best[q] = at
    return sorted(best.items(), key=lambda x: x[1])


def _save(sym, got):
    with open(_path(sym) + ".tmp", "w", encoding="utf-8") as fh:
        json.dump([{"q": q.isoformat(), "at": at.strftime("%Y-%m-%d %H:%M")} for q, at in got], fh)
    os.replace(_path(sym) + ".tmp", _path(sym))


def _aliases(con):
    out = {}
    for old, new in con.execute("SELECT symbol, sym_id FROM symbol_map").fetchall():
        out.setdefault(new, []).append(old)
    return out


def _run(todo, aliases, workers, log):
    os.makedirs(DIR, exist_ok=True)
    t0, failed, done = time.time(), [], [0]
    lock = threading.Lock()

    def worker(chunk):
        sess = Session(pause=0.7)          # one cookie session per worker
        for s in chunk:
            try:
                got = fetch_one(s, aliases.get(s, []), sess)
            except Exception as e:     # noqa: BLE001
                with lock:
                    failed.append(s)
                    log("  %s: %s" % (s, str(e)[:100]))
                continue
            _save(s, got)
            with lock:
                done[0] += 1
                if done[0] % 50 == 0:
                    log("  results: %d of %d, %.0f min" % (done[0], len(todo), (time.time() - t0) / 60))

    threads = [threading.Thread(target=worker, args=(todo[i::workers],)) for i in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return failed


def fetch_all(con, top_n=500, refresh=False, workers=3, log=print):
    syms = [r[0] for r in con.execute(
        "SELECT DISTINCT sym_id FROM liquidity_universe WHERE liq_rank <= ? AND close >= 20 ORDER BY 1",
        [top_n]).fetchall()]
    todo = [s for s in syms if refresh or not os.path.exists(_path(s))]
    log("results: %d universe stocks, %d to fetch from NSE (about 2-4 requests each, %d sessions)" % (
        len(syms), len(todo), workers))
    failed = _run(todo, _aliases(con), workers, log)
    log("results: done, %d failed%s" % (len(failed), (": " + ", ".join(failed[:20])) if failed else ""))
    return failed


def last_quarter_end(today):
    q_ends = [datetime.date(today.year, m, d) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    past = [q for q in q_ends if q < today] or [datetime.date(today.year - 1, 12, 31)]
    return max(past)


def update_recent(con, top_n=500, cap=300, workers=3, log=print):
    """Daily: stocks in the latest filings feed, plus today's liquid stocks that have not yet filed for the
    last quarter (once 20 days have passed since it ended), most overdue first, at most `cap`."""
    today = config.today_ist()
    sess = Session(pause=0.7)
    fresh = set()
    try:
        frm = (today - datetime.timedelta(days=5)).strftime("%d-%m-%Y")
        feed = sess.json(FEED_URL % (frm, today.strftime("%d-%m-%Y")))
        rows = feed if isinstance(feed, list) else (feed or {}).get("data") or []
        fresh = {(r.get("symbol") or "").upper() for r in rows if r.get("symbol")}
    except Exception as e:     # noqa: BLE001
        log("results feed: %s" % str(e)[:80])
    q_last = last_quarter_end(today)
    due = []
    if (today - q_last).days >= 20:
        latest = con.execute("SELECT max(date) FROM liquidity_universe").fetchone()[0]
        uni = [r[0] for r in con.execute("SELECT sym_id FROM liquidity_universe WHERE date = ? AND liq_rank <= ?",
                                          [latest, top_n]).fetchall()]
        for s in uni:
            p = _path(s)
            have = []
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    have = json.load(fh)
            last_q = max((r["q"] for r in have), default="0000")
            checked = os.path.getmtime(p) if os.path.exists(p) else 0
            if last_q < q_last.isoformat() and time.time() - checked > 20 * 3600:
                due.append((checked, s))
    todo = sorted(fresh) + [s for _, s in sorted(due) if s not in fresh]
    todo = todo[:cap + len(fresh)]
    log("results: %d in the latest feed, %d due for the %s quarter; checking %d" % (
        len(fresh), len(due), q_last, len(todo)))
    failed = _run(todo, _aliases(con), workers, log) if todo else []
    return len(todo) - len(failed)


def table():
    """data/raw/results/*.json -> DataFrame (symbol = sym_id, quarter_end, announced_at)."""
    rows = []
    if os.path.isdir(DIR):
        for fn in os.listdir(DIR):
            if not fn.endswith(".json"):
                continue
            sym = fn[:-5].replace("_AND_", "&")
            with open(os.path.join(DIR, fn), encoding="utf-8") as fh:
                for r in json.load(fh):
                    rows.append((sym, datetime.date.fromisoformat(r["q"]),
                                 datetime.datetime.strptime(r["at"], "%Y-%m-%d %H:%M")))
    return pd.DataFrame(rows, columns=["symbol", "quarter_end", "announced_at"])


def write_table(log=print):
    df = table()
    if df.empty:
        return 0
    n = store.write_table("results_dates", df)
    log("results_dates: %d filings for %d stocks (all-universe fetch)" % (n, df.symbol.nunique()))
    return n
