"""Daily stock and index bars, delivery %, symbol changes and corporate actions.

Stock and index bars come from the day files india-backtest/nsedata.py keeps under
<SOURCE>/nse/days/{eq,idx}/YYYY-MM-DD.csv.gz (one per trading day, 10 years). `fetch=True` first asks
nsedata to download the days it lacks, so the skill pack and the warehouse share one cache.

Delivery: sec_bhavdata_full (from 2019-08-23) and NSE's MTO files before that, both from nsearchives.
Already-downloaded sec_bhavdata_full days in <SOURCE>/flows/hist are reused.
"""
import csv
import datetime
import gzip
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import config, net, store


def _d(s):
    return datetime.date.fromisoformat(s)


# ---------------------------------------------------------------- stock and index bars

def fetch_with_nsedata(years=10, quiet=False):
    """Let the skill pack's own downloader fill its day cache (shared with every skill)."""
    sys.path.insert(0, os.path.join(config.SKILLS, "india-backtest"))
    try:
        import nsedata
    finally:
        sys.path.pop(0)
    return nsedata.sync(nsedata.Store(os.path.join(config.SOURCE, "nse")), years=years, quiet=quiet)


def _cache_days(kind):
    d = os.path.join(config.SOURCE, "nse", "days", kind)
    if not os.path.isdir(d):
        return {}
    return {_d(f[:10]): os.path.join(d, f) for f in os.listdir(d) if f.endswith(".csv.gz")}


def _read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_bars(kind, rebuild=False, log=print):
    """Copy cached day files of `kind` ('eq' or 'idx') that the dataset lacks into Parquet."""
    name = "eq_daily" if kind == "eq" else "idx_daily"
    days = _cache_days(kind)
    have = set() if rebuild else store.present_dates(name)
    todo = sorted(d for d in days if d not in have)
    if not todo:
        log("%s: up to date (%d days)" % (name, len(have)))
        return 0
    log("%s: %d new day(s) from %s" % (name, len(todo), os.path.dirname(days[todo[0]])))
    rows = []
    for i, d in enumerate(todo):
        for r in _read_rows(days[d]):
            if kind == "eq" and len(r) >= 9:
                rows.append((d, r[0], r[1]) + tuple(_num(x) for x in r[2:9]))
            elif kind == "idx" and len(r) >= 7:
                rows.append((d, r[0], r[1]) + tuple(_num(x) for x in r[2:7]))
        if len(rows) > 400000 or i == len(todo) - 1:
            cols = (["date", "symbol", "series", "open", "high", "low", "close", "prev_close", "volume", "turnover"]
                    if kind == "eq" else ["date", "index_key", "index_name", "open", "high", "low", "close", "volume"])
            df = pd.DataFrame(rows, columns=cols)
            store.upsert(name, df, ["date", "symbol" if kind == "eq" else "index_key"])
            rows = []
            log("  %s: through %s" % (name, d))
    return len(todo)


# ---------------------------------------------------------------- delivery

SEC_URL = config.ARCH + "products/content/sec_bhavdata_full_%s.csv"
MTO_URL = config.ARCH + "archives/equities/mto/MTO_%s.DAT"


def parse_sec_bhavdata(text, day=None):
    """sec_bhavdata_full -> rows (date, symbol, series, avg_price, trades, deliv_qty, deliv_pct)."""
    rd = csv.reader(io.StringIO(text))
    hdr = [h.strip() for h in next(rd)]
    ix = {h: i for i, h in enumerate(hdr)}
    need = ("SYMBOL", "SERIES", "DATE1", "AVG_PRICE", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER")
    if any(k not in ix for k in need):
        raise ValueError("not a sec_bhavdata_full file")
    out = []
    for r in rd:
        if len(r) < len(hdr):
            continue
        r = [x.strip() for x in r]
        try:
            d = day or datetime.datetime.strptime(r[ix["DATE1"]], "%d-%b-%Y").date()
        except ValueError:
            continue
        out.append((d, r[ix["SYMBOL"]].upper(), r[ix["SERIES"]].upper(), _num(r[ix["AVG_PRICE"]]),
                    _num(r[ix["NO_OF_TRADES"]]), _num(r[ix["DELIV_QTY"]]), _num(r[ix["DELIV_PER"]])))
    return out


def parse_mto(text, day):
    """MTO_DDMMYYYY.DAT -> same rows; MTO has no average price or trade count."""
    out = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 7 and p[0] == "20":
            out.append((day, p[2].upper(), p[3].upper(), None, None, _num(p[5]), _num(p[6])))
    return out


def _local_sec(day):
    for folder in (os.path.join(config.SOURCE, "flows", "hist"), os.path.join(config.RAW, "delivery")):
        p = os.path.join(folder, day.isoformat() + ".csv.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return None


def _fetch_delivery(day):
    """-> (day, rows) or (day, None) when NSE has no file for it."""
    text = _local_sec(day)
    if text is not None:
        try:
            return day, (parse_sec_bhavdata(text, day) if "SYMBOL" in text[:200] else parse_mto(text, day))
        except ValueError:
            pass
    try:
        if day >= config.SEC_BHAVDATA_FROM:
            text = net.text(net.get(SEC_URL % day.strftime("%d%m%Y")))
            rows = parse_sec_bhavdata(text, day)
        else:
            text = net.text(net.get(MTO_URL % day.strftime("%d%m%Y")))
            rows = parse_mto(text, day)
    except net.NotPublished:
        return day, None
    os.makedirs(os.path.join(config.RAW, "delivery"), exist_ok=True)
    with gzip.open(os.path.join(config.RAW, "delivery", day.isoformat() + ".csv.gz"), "wt", encoding="utf-8") as fh:
        fh.write(text)
    return day, rows


def load_delivery(since=None, workers=4, log=print):
    """Delivery for every trading day in eq_daily (since `since`) that the dataset lacks."""
    trading = store.present_dates("eq_daily")
    have = store.present_dates("delivery") | {_d(x) for x in store.closed_days("delivery")}
    todo = sorted(d for d in trading if d not in have and (since is None or d >= since))
    if not todo:
        log("delivery: up to date")
        return 0
    log("delivery: %d day(s) to load" % len(todo))
    cols = ["date", "symbol", "series", "avg_price", "trades", "deliv_qty", "deliv_pct"]
    rows, missing, n = [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (day, got) in enumerate(ex.map(_fetch_delivery, todo)):
            if got is None:
                missing.append(day)
            else:
                rows += got
                n += 1
            if len(rows) > 300000 or i == len(todo) - 1:
                if rows:
                    store.upsert("delivery", pd.DataFrame(rows, columns=cols), ["date", "symbol", "series"])
                rows = []
                log("  delivery: %d of %d days" % (i + 1, len(todo)))
    old = [d for d in missing if (config.today_ist() - d).days > 5]
    store.mark_closed("delivery", old)
    if missing:
        log("  delivery: NSE had no file for %d day(s), e.g. %s" % (len(missing), missing[0]))
    return n


# ---------------------------------------------------------------- industries

INDUSTRY_LISTS = ["ind_nifty500list.csv", "ind_niftytotalmarket_list.csv", "ind_niftymicrocap250_list.csv"]


def load_industries(log=print):
    """NSE's industry for ~1,000 stocks from its index list files. Today's classification only:
    used for grouping (industry strength), never as a membership filter."""
    rows = {}
    for f in INDUSTRY_LISTS:
        try:
            text = net.text(net.get(config.ARCH + "content/indices/" + f))
        except (net.NotPublished, IOError) as e:
            log("industries: %s skipped (%s)" % (f, e))
            continue
        for r in csv.DictReader(io.StringIO(text)):
            sym = (r.get("Symbol") or "").strip().upper()
            ind = (r.get("Industry") or "").strip()
            if sym and ind and sym not in rows:
                rows[sym] = (sym, ind, (r.get("ISIN Code") or "").strip(), f)
    if not rows:
        return 0
    n = store.write_table("industries", pd.DataFrame(list(rows.values()),
                                                     columns=["symbol", "industry", "isin", "source_list"]))
    log("industries: %d stocks" % n)
    return n


def load_etfs(log=print):
    """NSE's ETF list, so ETFs (which trade in series EQ) stay out of the stock universes."""
    try:
        text = net.text(net.get(config.ARCH + "content/equities/eq_etfseclist.csv"))
    except (net.NotPublished, IOError) as e:
        log("etfs: skipped (%s)" % e)
        return 0
    syms = sorted({(r.get("Symbol") or "").strip().upper() for r in csv.DictReader(io.StringIO(text))} - {""})
    n = store.write_table("etfs", pd.DataFrame({"symbol": syms}))
    log("etfs: %d" % n)
    return n


# ---------------------------------------------------------------- reference tables

def load_reference(log=print):
    """Symbol changes, bonus/split factors and quarterly results dates from the skill cache."""
    nse = os.path.join(config.SOURCE, "nse")
    n = {}
    p = os.path.join(nse, "symbolchange.csv")
    rows = []
    if os.path.exists(p):
        with open(p, encoding="latin-1") as fh:
            for r in csv.reader(fh):
                if len(r) < 4 or r[1].strip().upper() == "OLD SYMBOL":
                    continue
                try:
                    d = datetime.datetime.strptime(r[3].strip(), "%d-%b-%Y").date()
                except ValueError:
                    continue
                rows.append((r[1].strip().upper(), r[2].strip().upper(), d))
    n["symbol_changes"] = store.write_table(
        "symbol_changes", pd.DataFrame(rows, columns=["old_symbol", "new_symbol", "change_date"]).drop_duplicates())

    p = os.path.join(nse, "corp-actions.json")
    rows, seen = [], set()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        for yr in doc.get("years", {}).values():
            for sym, d, f, subj in yr:
                k = (sym, d, subj.strip())
                if k not in seen:
                    seen.add(k)
                    rows.append((sym, _d(d), float(f), subj.strip()))
    n["corp_actions"] = store.write_table(
        "corp_actions", pd.DataFrame(rows, columns=["symbol", "ex_date", "factor", "subject"]))

    from . import results as all_results
    if all_results.table().shape[0]:
        # the all-universe fetch (python -m warehouse results) replaces the pack's partial list
        n["results_dates"] = all_results.write_table(log=lambda m: None)
        log("reference: " + ", ".join("%s %d" % kv for kv in n.items()))
        return n
    p = os.path.join(config.SOURCE, "results-dates", "dates.json")
    rows = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        for sym, qs in doc.items():
            for q in qs if isinstance(qs, list) else []:
                try:
                    at = datetime.datetime.strptime(q["at"], "%Y-%m-%d %H:%M")
                    rows.append((sym.upper(), _d(q["q"]), at))
                except (KeyError, ValueError, TypeError):
                    continue
    n["results_dates"] = store.write_table(
        "results_dates", pd.DataFrame(rows, columns=["symbol", "quarter_end", "announced_at"]))
    log("reference: " + ", ".join("%s %d" % kv for kv in n.items()))
    return n
