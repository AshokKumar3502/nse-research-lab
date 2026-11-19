"""1-minute candles and NSE's pre-open book.

Sources, best first:
  1. Upstox's public historical-candle API (v3), no login: 1-minute bars from January 2022, one
     calendar month per request. https://api.upstox.com/v3/historical-candle/<key>/minutes/1/<to>/<from>
     Instrument keys are ISIN-based (NSE_EQ|INE002A01018) and come from Upstox's public instrument
     file, so a stock that has since been delisted or renamed may not resolve: `minute` reports them.
  2. The skill pack's recorder (<SOURCE>/recorder/YYYY-MM-DD/bars1m.json.gz, Yahoo 1-minute),
     imported only for days Upstox has not filled.

The pre-open book (<SOURCE>/recorder/YYYY-MM-DD/preopen.json.gz) exists only from the day the
recorder started: NSE publishes only the latest session, so it cannot be backfilled.
"""
import calendar
import datetime
import gzip
import json
import os
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import config, net, store

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
CANDLE_URL = "https://api.upstox.com/v3/historical-candle/%s/minutes/1/%s/%s"
INDEX_KEYS = {"NIFTY 50": "NSE_INDEX|Nifty 50", "NIFTY BANK": "NSE_INDEX|Nifty Bank",
              "INDIA VIX": "NSE_INDEX|India VIX", "NIFTY FIN SERVICE": "NSE_INDEX|Nifty Fin Service"}
RECORDER_INDEX = {"^NSEI": "NIFTY 50", "^NSEBANK": "NIFTY BANK", "^INDIAVIX": "INDIA VIX"}
COLS = ["date", "symbol", "ts", "open", "high", "low", "close", "volume", "oi", "source"]

_rate_lock = threading.Lock()
_last_call = [0.0]
MIN_GAP = 0.12   # seconds between Upstox calls across all threads (about 8 a second)


def load_instruments(log=print):
    """Upstox's NSE instrument list -> instruments table (equities: symbol, ISIN, key, tick, lot, CAS)."""
    raw = net.get(INSTRUMENTS_URL, timeout=120)
    rows = []
    for x in json.loads(raw.decode("utf-8")):
        if x.get("segment") == "NSE_EQ" and x.get("instrument_type") in ("EQ", "BE", "BZ", "SM", "ST"):
            rows.append((x.get("trading_symbol", "").upper(), x.get("isin"), x.get("instrument_key"),
                         x.get("instrument_type"), x.get("name"), (x.get("tick_size") or 0) / 100.0,
                         x.get("lot_size"), bool(x.get("cas_eligible"))))
    df = pd.DataFrame(rows, columns=["symbol", "isin", "instrument_key", "series", "name", "tick_size",
                                     "lot_size", "cas_eligible"]).drop_duplicates("symbol")
    store.write_table("instruments", df)
    log("instruments: %d NSE equities (Upstox list, %s)" % (len(df), config.today_ist()))
    return df


def _throttle():
    with _rate_lock:
        wait = _last_call[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def fetch_upstox(key, start, end):
    """1-minute candles for [start, end] (dates, at most a month apart) -> list of tuples."""
    _throttle()
    url = CANDLE_URL % (urllib.parse.quote(key, safe=""), end.isoformat(), start.isoformat())
    doc = net.get_json(url)
    out = []
    for c in (doc.get("data") or {}).get("candles") or []:
        ts = datetime.datetime.fromisoformat(c[0]).replace(tzinfo=None)   # IST wall clock
        out.append((ts.date(), ts, c[1], c[2], c[3], c[4], c[5], c[6] if len(c) > 6 else None))
    return out


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = datetime.date(y, m, 1)
        last = datetime.date(y, m, calendar.monthrange(y, m)[1])
        yield max(first, start), min(last, end)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def resolve_keys(symbols, log=print):
    """symbols -> {symbol: upstox key}; indices by name, stocks through the instrument list."""
    ins = None
    p = os.path.join(store.set_dir("instruments"), "all.parquet")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
        ins = pd.read_parquet(p)
    if ins is None:
        ins = load_instruments(log)
    by = dict(zip(ins.symbol, ins.instrument_key))
    keys, missing = {}, []
    for s in symbols:
        k = INDEX_KEYS.get(s) or by.get(s)
        if k:
            keys[s] = k
        else:
            missing.append(s)
    return keys, missing


def load_upstox(symbols, start, end=None, workers=4, log=print):
    """Fill minute_bars for `symbols` from `start` to `end`, a month at a time. Months that are over
    and fully fetched are remembered in state.json and never asked again."""
    end = end or config.today_ist()
    start = max(start, config.UPSTOX_MINUTE_FROM)
    keys, missing = resolve_keys(symbols, log)
    if missing:
        log("minute: no Upstox key for %d symbol(s) (delisted or renamed?): %s" % (
            len(missing), ", ".join(sorted(missing)[:15]) + (" ..." if len(missing) > 15 else "")))
    st = store.load_state()
    done = st.setdefault("minute_done", {})
    total = 0
    for a, b in _months(start, end):
        mkey = a.strftime("%Y-%m")
        over = b < config.today_ist() and b == datetime.date(a.year, a.month, calendar.monthrange(a.year, a.month)[1])
        todo = [s for s in keys if s not in set(done.get(mkey, []))]
        if not todo:
            continue
        t0 = time.time()
        rows, ok, failed = [], [], []

        def job(sym):
            try:
                return sym, fetch_upstox(keys[sym], a, b), None
            except (IOError, ValueError, net.NotPublished) as e:
                return sym, None, str(e)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for sym, got, err in ex.map(job, todo):
                if got is None:
                    failed.append((sym, err))
                    continue
                ok.append(sym)
                rows += [(r[0], sym, r[1], r[2], r[3], r[4], r[5], r[6], r[7], "upstox") for r in got]
        if rows:
            store.upsert("minute_bars", pd.DataFrame(rows, columns=COLS), ["date", "symbol", "ts"], date_col="date")
        total += len(rows)
        if over:
            st = store.load_state()
            md = st.setdefault("minute_done", {})
            md[mkey] = sorted(set(md.get(mkey, [])) | set(ok))
            store.save_state(st)
            done = st["minute_done"]
        log("  minute %s: %d symbols, %d bars, %d failed, %.0fs" % (mkey, len(ok), len(rows), len(failed),
                                                                   time.time() - t0))
        if failed:
            log("    e.g. %s: %s" % failed[0])
    return total


def import_recorder(log=print):
    """Recorder days (Yahoo 1-minute) for (day, symbol) pairs minute_bars does not hold yet, and
    every recorded pre-open book."""
    root = os.path.join(config.SOURCE, "recorder")
    if not os.path.isdir(root):
        log("recorder: nothing at %s" % root)
        return 0
    have = {}
    for f in store.month_files("minute_bars"):
        t = pd.read_parquet(f, columns=["date", "symbol"]).drop_duplicates()
        for d, s in zip(t.date, t.symbol):
            have.setdefault(d, set()).add(s)
    bars, pre = [], []
    for day in sorted(os.listdir(root)):
        try:
            d = datetime.date.fromisoformat(day)
        except ValueError:
            continue
        p = os.path.join(root, day, "bars1m.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
            for sym, rows in (doc.get("bars") or {}).items():
                name = RECORDER_INDEX.get(sym, sym.upper())
                if name in have.get(d, ()):
                    continue
                for r in rows:
                    hh, mm = (int(x) for x in r[0].split(":"))
                    ts = datetime.datetime(d.year, d.month, d.day, hh, mm)
                    bars.append((d, name, ts, r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else None, None, "yahoo"))
        p = os.path.join(root, day, "preopen.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
            for sym, x in (doc.get("stocks") or {}).items():
                pre.append((d, sym.upper(), x.get("prev_close"), x.get("iep"), x.get("change_pct"),
                            x.get("final_qty"), x.get("buy_qty"), x.get("sell_qty"), x.get("ato_buy"),
                            x.get("ato_sell"), x.get("updated"), json.dumps(x.get("book") or [])))
    if bars:
        store.upsert("minute_bars", pd.DataFrame(bars, columns=COLS), ["date", "symbol", "ts"])
    if pre:
        store.upsert("preopen", pd.DataFrame(pre, columns=[
            "date", "symbol", "prev_close", "iep", "change_pct", "final_qty", "buy_qty", "sell_qty",
            "ato_buy", "ato_sell", "updated", "book_json"]), ["date", "symbol"])
    log("recorder: %d Yahoo minute bars (days Upstox lacks), %d pre-open rows" % (len(bars), len(pre)))
    return len(bars)
