"""Quarterly results filing times for EVERY stock that was ever in the research universe, including
stocks that are delisted or renamed today, so "has results history" can never stand in for "survived
to today".

Source: NSE's two results lists (integrated filings and the older corporates-financial-results), read
with the skill pack's fetcher (india-analyst) and parser (india-results-drift). Unlike the pack's own
`dates` command this always reads BOTH lists and merges them (the earliest filing per quarter wins),
and tries every ticker a company has traded under.

Cache: data/raw/results/<SYM>.json -> [{"q": "YYYY-MM-DD", "at": "YYYY-MM-DD HH:MM"}], one file per
sym_id; a symbol with a file is not asked again unless refresh=True. Resumable.
"""
import datetime
import json
import os
import sys
import threading
import time

import pandas as pd

from .. import config, store

DIR = os.path.join(config.RAW, "results")


def _skill_modules():
    for sub in ("india-results-drift", "india-analyst", "india-swing-desk"):
        p = os.path.join(config.SKILLS, sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import results_drift as rd
    return rd, rd.ra


def _path(sym):
    return os.path.join(DIR, sym.replace("&", "_AND_").replace("/", "_") + ".json")


def fetch_one(sym, aliases, fetcher, rd, ra):
    """Every (quarter_end, filed_at) for one company across all its tickers and both lists."""
    rows, errors, asked = [], [], 0
    for s in [sym] + [x for x in aliases if x != sym]:
        code = s.replace("&", "%26")
        for kind, url in (("integrated", ra.INTEGRATED_URL % code), ("legacy", ra.LEGACY_URL % code)):
            asked += 1
            try:
                rows += rd.parse_filings(fetcher.json(url, kind), kind)
            except Exception as e:     # noqa: BLE001 - NSE errors vary; the count below decides
                errors.append("%s %s: %s" % (s, kind, str(e)[:60]))
    if not rows and len(errors) == asked:
        raise RuntimeError("; ".join(errors[:2]))
    best = {}
    for q, at in rows:
        if q not in best or at < best[q]:
            best[q] = at
    return sorted(best.items(), key=lambda x: x[1])


def fetch_all(con, top_n=500, refresh=False, workers=3, log=print):
    rd, ra = _skill_modules()
    syms = [r[0] for r in con.execute(
        "SELECT DISTINCT sym_id FROM liquidity_universe WHERE liq_rank <= ? AND close >= 20 ORDER BY 1",
        [top_n]).fetchall()]
    aliases = {}
    for old, new in con.execute("SELECT symbol, sym_id FROM symbol_map").fetchall():
        aliases.setdefault(new, []).append(old)
    os.makedirs(DIR, exist_ok=True)
    todo = [s for s in syms if refresh or not os.path.exists(_path(s))]
    log("results: %d universe stocks, %d to fetch from NSE (about 2-4 requests each, %d sessions)" % (
        len(syms), len(todo), workers))
    t0, failed, done = time.time(), [], [0]
    lock = threading.Lock()

    def worker(chunk):
        f = ra.Fetcher(pause=0.7)          # one cookie session per worker
        for s in chunk:
            try:
                got = fetch_one(s, aliases.get(s, []), f, rd, ra)
            except Exception as e:     # noqa: BLE001
                with lock:
                    failed.append(s)
                    log("  %s: %s" % (s, str(e)[:100]))
                continue
            with open(_path(s) + ".tmp", "w", encoding="utf-8") as fh:
                json.dump([{"q": q.isoformat(), "at": at.strftime("%Y-%m-%d %H:%M")} for q, at in got], fh)
            os.replace(_path(s) + ".tmp", _path(s))
            with lock:
                done[0] += 1
                if done[0] % 50 == 0:
                    log("  results: %d of %d, %.0f min" % (done[0], len(todo), (time.time() - t0) / 60))

    threads = [threading.Thread(target=worker, args=(todo[i::workers],)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log("results: done, %d failed%s" % (len(failed), (": " + ", ".join(failed[:20])) if failed else ""))
    return failed


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
