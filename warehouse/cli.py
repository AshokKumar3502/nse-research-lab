"""NSE research warehouse.

  python -m warehouse sync [--fetch] [--since 2019-01-01] [--skip fo,delivery,...]
        daily data: stock and index bars, delivery, F&O bhavcopy, participant OI, reference tables,
        then rebuild the DuckDB tables and run the quality checks
  python -m warehouse minute [--universe fo|nifty|SYM,SYM] [--since 2022-01-01] [--workers 4]
        1-minute candles from Upstox's public API (from January 2022) plus the recorder's days
  python -m warehouse results [--refresh]
        quarterly results filing times for every stock ever in the universe (incl. delisted)
  python -m warehouse build        rebuild the DuckDB tables only
  python -m warehouse check        run the quality checks
  python -m warehouse status       rows, symbols and date range of everything
  python -m warehouse sql "SELECT ..."
"""
import argparse
import datetime
import json
import sys
import time

from . import config, db, quality
from .loaders import daily, fno, minute


def _log(msg):
    print(msg, flush=True)


def _date(s):
    return datetime.date.fromisoformat(s)


def cmd_sync(a):
    t0 = time.time()
    skip = set((a.skip or "").split(","))
    if a.fetch:
        _log("fetching new stock and index days with india-backtest/nsedata.py ...")
        rep = daily.fetch_with_nsedata(years=a.years, quiet=False)
        _log("  nsedata: %s" % json.dumps(rep))
    daily.load_bars("eq", log=_log)
    daily.load_bars("idx", log=_log)
    steps = [("reference", lambda: daily.load_reference(log=_log)),
             ("industries", lambda: daily.load_industries(log=_log)),
             ("etfs", lambda: daily.load_etfs(log=_log))]
    if "delivery" not in skip:
        steps.append(("delivery", lambda: daily.load_delivery(since=_date(a.since), log=_log)))
    if "fo" not in skip:
        steps.append(("fo", lambda: fno.load_fo(since=_date(a.fo_since or a.since), log=_log)))
    if "poi" not in skip:
        steps.append(("participant_oi", lambda: fno.load_participant_oi(since=_date(a.since), log=_log)))
    if "recorder" not in skip:
        steps.append(("recorder", lambda: minute.import_recorder(log=_log)))
    failed = []
    for name, fn in steps:
        try:   # one dataset failing must not stop the rest, or the rebuild below
            fn()
        except Exception as e:
            failed.append(name)
            _log("ERROR in %s: %s: %s (the other datasets carry on)" % (name, type(e).__name__, e))
    con = db.build(log=_log)
    rep = quality.check(con)
    _print_quality(rep)
    if failed:
        _log("sync: these steps failed and need a look: %s" % ", ".join(failed))
    _log("sync done in %.0fs" % (time.time() - t0))
    return 0 if rep["status"] != "fail" and not failed else 1


def universe(con, spec, since):
    if spec == "fo":
        rows = con.execute("SELECT DISTINCT symbol FROM fo_universe WHERE date >= ?", [since]).fetchall()
        syms = sorted(r[0] for r in rows)
    elif spec.startswith("top"):
        n = int(spec[3:])
        rows = con.execute("""SELECT DISTINCT sym_id FROM liquidity_universe WHERE date >= ? AND liq_rank <= ?""",
                           [since, n]).fetchall()
        syms = sorted(r[0] for r in rows)
    else:
        syms = [s.strip().upper() for s in spec.split(",") if s.strip()]
    return list(minute.INDEX_KEYS) + syms if a_with_indices(spec) else syms


def a_with_indices(spec):
    return spec == "fo" or spec.startswith("top")


def cmd_minute(a):
    since = _date(a.since)
    con = db.connect(read_only=True)
    syms = universe(con, a.universe, since)
    con.close()
    _log("minute: %d symbols from %s (Upstox public API)" % (len(syms), since))
    minute.load_upstox(syms, since, workers=a.workers, log=_log)
    minute.import_recorder(log=_log)
    db.build(log=_log)
    return 0


def _print_quality(rep):
    _log("quality: %s (last session %s)" % (rep["status"].upper(), rep["last_session"]))
    for c in rep["checks"]:
        mark = {"ok": "  ok  ", "warn": " WARN ", "fail": " FAIL "}[c["status"]]
        _log("[%s] %-24s %s%s" % (mark, c["name"], c["detail"], ("  e.g. " + "; ".join(c["sample"][:3])) if c["sample"] and c["status"] != "ok" else ""))


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="warehouse", description="NSE research warehouse")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("sync")
    p.add_argument("--fetch", action="store_true", help="download new stock/index days first (nsedata)")
    p.add_argument("--years", type=float, default=10)
    p.add_argument("--since", default="2016-11-01", help="first day for delivery and participant OI")
    p.add_argument("--fo-since", default=None, help="first day for the F&O bhavcopy (default: --since)")
    p.add_argument("--skip", default="", help="comma list: delivery,fo,poi,recorder")
    p = sub.add_parser("minute")
    p.add_argument("--universe", default="fo", help="fo, top200, or SYM,SYM")
    p.add_argument("--since", default="2022-01-01")
    p.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("results")
    p.add_argument("--refresh", action="store_true")
    sub.add_parser("build")
    sub.add_parser("check")
    sub.add_parser("status")
    p = sub.add_parser("sql")
    p.add_argument("query")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        return cmd_sync(a)
    if a.cmd == "minute":
        return cmd_minute(a)
    if a.cmd == "results":
        from .loaders import results as res
        res.fetch_all(db.connect(read_only=True), refresh=a.refresh, log=_log)
        res.write_table(log=_log)
        db.build(log=_log)
        return 0
    if a.cmd == "build":
        db.build(log=_log)
        return 0
    if a.cmd == "check":
        rep = quality.check(db.build(log=lambda m: None))
        _print_quality(rep)
        return 0 if rep["status"] != "fail" else 1
    if a.cmd == "status":
        for r in db.summary():
            _log("%-20s %12s rows  %6s symbols  %s .. %s" % (r["name"], "{:,}".format(r["rows"]),
                                                           r["symbols"] if r["symbols"] is not None else "-",
                                                           r["first"], r["last"]))
        return 0
    if a.cmd == "sql":
        con = db.connect(read_only=True)
        print(con.execute(a.query).df().to_string(max_rows=60))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
