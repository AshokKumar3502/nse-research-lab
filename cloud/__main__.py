"""Run the daily jobs on a GitHub runner (no laptop), and move state between the laptop and the cloud.

  python -m cloud morning    wait until 09:30:30 IST, intraday picks, paper trades, monitor, site
  python -m cloud evening    sync today's NSE data, swing picks, paper trades, monitor, site
  python -m cloud export     (laptop, monthly) copy models + reference tables into state/ for the cloud

The runner keeps a SLIM warehouse in GitHub's Actions cache (NSEWH_DATA): about 18 months of daily data
and 3 months of 1-minute candles, enough for every daily feature. If the cache is lost it is rebuilt from
NSE and Upstox on the next run (about 15 minutes). Durable state lives in the repo's state/ folder:
models trained monthly on the laptop, picks, the kill switch, results history and point-in-time
industries.
"""
import datetime
import glob
import gzip
import json
import os
import shutil
import sys
import time

from warehouse import config, db, quality, store

REPO = config.ROOT
STATE = os.path.join(REPO, "state")
MODEL_FILES = {"intraday": ["model-0930.txt", "report-0930.json", "daily-0930.csv"],
               "swing": ["model-h10.txt", "report-h10.json", "report-h20.json", "review-h10.json", "portfolio-h10.json",
                         "equity-h10.csv", "cohorts-h10.csv", "cohorts-h10-close.csv"]}
WINDOW = {"eq_daily": 560, "idx_daily": 560, "delivery": 420, "fo_futures": 90, "fo_options": 30,
          "participant_oi": 90, "minute_bars": 100, "preopen": 100}


def log(msg):
    print("[%s] %s" % (datetime.datetime.now(config.IST).strftime("%H:%M:%S"), msg), flush=True)


def _copy(src, dst):
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


# ---------------------------------------------------------------- state <-> data

def _results_to_state():
    res = {}
    for p in glob.glob(os.path.join(config.RAW, "results", "*.json")):
        with open(p, encoding="utf-8") as fh:
            res[os.path.basename(p)[:-5]] = json.load(fh)
    if res:
        os.makedirs(STATE, exist_ok=True)
        with gzip.open(os.path.join(STATE, "results.json.gz"), "wt", encoding="utf-8") as fh:
            json.dump(res, fh, sort_keys=True)
    return len(res)


def export():
    """Laptop: refresh state/ from the full warehouse (after the monthly retrain)."""
    for kind, files in MODEL_FILES.items():
        for f in files:
            _copy(os.path.join(config.DATA, "models", kind, f), os.path.join(STATE, "models", kind, f))
    for f in ("registry.json", "leaderboard.json"):
        _copy(os.path.join(config.DATA, "lab", f), os.path.join(STATE, "lab", f))
    _copy(os.path.join(config.SETS, "industry_pit", "all.parquet"), os.path.join(STATE, "industry_pit.parquet"))
    _copy(os.path.join(config.SOURCE, "nse", "corp-actions.json"), os.path.join(STATE, "corp-actions.json"))
    # the full 10-year warehouse, for the site's Data table (the runner only holds a slim window)
    with open(os.path.join(STATE, "warehouse_summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"built": datetime.datetime.now(config.IST).strftime("%Y-%m-%d"),
                   "tables": db.summary(db.connect(read_only=True))}, fh, indent=1, default=str)
    persist()
    log("export: state/ refreshed (%d results histories)" % _results_to_state())


def prepare():
    """Runner: state/ -> the slim warehouse."""
    for kind, files in MODEL_FILES.items():
        for f in files:
            _copy(os.path.join(STATE, "models", kind, f), os.path.join(config.DATA, "models", kind, f))
    for f in ("registry.json", "leaderboard.json"):
        _copy(os.path.join(STATE, "lab", f), os.path.join(config.DATA, "lab", f))
    for f in ("killswitch.json", "alerts-sent.json"):
        _copy(os.path.join(STATE, "monitor", f), os.path.join(config.DATA, "monitor", f))
    for p in glob.glob(os.path.join(STATE, "picks", "*.json")):
        _copy(p, os.path.join(config.DATA, "picks", os.path.basename(p)))
    _copy(os.path.join(STATE, "industry_pit.parquet"), os.path.join(config.SETS, "industry_pit", "all.parquet"))
    ca = os.path.join(config.SOURCE, "nse", "corp-actions.json")
    if not os.path.exists(ca):          # a fallback if NSE's site refuses the runner
        _copy(os.path.join(STATE, "corp-actions.json"), ca)
    rdir = os.path.join(config.RAW, "results")
    os.makedirs(rdir, exist_ok=True)
    p = os.path.join(STATE, "results.json.gz")
    if os.path.exists(p):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for name, rows in json.load(fh).items():
                q = os.path.join(rdir, name + ".json")
                if not os.path.exists(q):          # files fetched in the cloud are newer
                    with open(q, "w", encoding="utf-8") as out:
                        json.dump(rows, out)


def persist():
    """Runner or laptop: the durable bits back into state/ (picks, kill switch, alerts sent, results)."""
    for f in ("killswitch.json", "alerts-sent.json"):
        _copy(os.path.join(config.DATA, "monitor", f), os.path.join(STATE, "monitor", f))
    for p in glob.glob(os.path.join(config.DATA, "picks", "*.json")):
        _copy(p, os.path.join(STATE, "picks", os.path.basename(p)))
    _results_to_state()


def session_day(phase):
    """The trading day a job belongs to. GitHub can start the 18:45 evening job hours late (even after midnight),
    and a missed evening is caught up the next morning: an evening run before 15:30 IST settles the previous
    weekday."""
    now = datetime.datetime.now(config.IST)
    d = now.date()
    if phase == "evening" and now.time() < datetime.time(15, 30):
        d -= datetime.timedelta(days=1)
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)
    return d


def done_marker(phase):
    return os.path.join(STATE, "done", "%s-%s" % (phase, session_day(phase)))


# ---------------------------------------------------------------- the slim warehouse

def prune():
    """Drop month files and raw day files older than each dataset's window."""
    today = config.today_ist()
    for ds, days in WINDOW.items():
        cut = (today - datetime.timedelta(days=days)).strftime("%Y-%m")
        for f in store.month_files(ds):
            if os.path.basename(f)[:7] < cut:
                os.remove(f)
    cut = (today - datetime.timedelta(days=WINDOW["eq_daily"])).isoformat()
    for kind in ("eq", "idx"):
        for f in glob.glob(os.path.join(config.SOURCE, "nse", "days", kind, "*.csv.gz")):
            if os.path.basename(f)[:10] < cut:
                os.remove(f)


def sync():
    from warehouse.loaders import daily, fno, minute, nse_days, results
    t0 = time.time()
    today = config.today_ist()
    nse_days.sync_days(days_back=WINDOW["eq_daily"], log=log)
    nse_days.sync_reference(log=log)
    prune()
    daily.load_bars("eq", log=log)
    daily.load_bars("idx", log=log)
    daily.load_reference(log=log)
    for name, fn in (("industries", daily.load_industries), ("etfs", daily.load_etfs)):
        p = os.path.join(config.SETS, name, "all.parquet")
        if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 7 * 86400:
            try:
                fn(log=log)
            except Exception as e:     # noqa: BLE001
                log("%s: %s" % (name, e))

    def since(k):
        return today - datetime.timedelta(days=WINDOW[k])
    for name, fn in (("delivery", lambda: daily.load_delivery(since=since("delivery"), log=log)),
                     ("fo", lambda: fno.load_fo(since=since("fo_futures"), log=log)),
                     ("participant_oi", lambda: fno.load_participant_oi(since=since("participant_oi"), log=log))):
        try:
            fn()
        except Exception as e:     # noqa: BLE001
            log("ERROR %s: %s" % (name, e))
    db.build(log=log).close()
    con = db.connect(read_only=True)
    try:
        results.update_recent(con, log=log)
    except Exception as e:     # noqa: BLE001
        log("results update: %s" % e)
    results.write_table(log=log)
    first = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM fo_universe WHERE date >= ?",
                                      [today - datetime.timedelta(days=60)]).fetchall()]
    con.close()
    minute.load_upstox(list(minute.INDEX_KEYS) + syms, first, log=log)
    # Upstox's history ends yesterday; today's candles come from its intraday endpoint
    try:
        from intraday.live import fetch_today
        keys, _missing = minute.resolve_keys(list(minute.INDEX_KEYS) + syms, lambda m: None)
        today_bars = fetch_today(keys, today)
        if not today_bars.empty:
            store.upsert("minute_bars", today_bars, ["date", "symbol", "ts"])
        log("minute today: %d bars for %d symbols" % (len(today_bars), today_bars.symbol.nunique() if len(today_bars) else 0))
    except Exception as e:     # noqa: BLE001
        log("minute today: %s" % e)
    con = db.build(log=log)
    q = quality.check(con)
    log("sync done in %.0f min, data checks %s" % ((time.time() - t0) / 60, q["status"].upper()))
    con.close()
    return q


def wait_until(hh, mm, ss=0):
    now = datetime.datetime.now(config.IST)
    target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if now < target:
        log("waiting until %s IST (%.0f min)" % (target.strftime("%H:%M:%S"), (target - now).total_seconds() / 60))
        time.sleep((target - now).total_seconds())


def _step(name, fn):
    try:
        log("== " + name)
        return fn()
    except SystemExit as e:
        log("%s: %s" % (name, e))
    except Exception as e:     # noqa: BLE001 - one step failing must not stop the rest
        log("ERROR in %s: %s: %s" % (name, type(e).__name__, e))


def morning():
    from intraday import live
    from monitor.__main__ import run as monitor_run
    from papertrade.book import update as paper_update
    from site_build.build import build as site_build
    if os.path.exists(done_marker("morning")):
        log("morning already done today")
        return
    prepare()
    if not store.month_files("eq_daily"):
        log("no warehouse in the cache: rebuilding it first")
        sync()
    else:
        # a missed evening leaves the warehouse a day behind and the picks would find no rows: catch it up
        prev = config.today_ist() - datetime.timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= datetime.timedelta(days=1)
        last = db.connect(read_only=True).execute("SELECT max(date) FROM eq_daily").fetchone()[0]
        if last is None or last < prev:
            log("warehouse ends %s, before %s: syncing first" % (last, prev))
            sync()
    wait_until(9, 30, 30)
    con = db.connect(read_only=True)
    doc = _step("intraday picks", lambda: live.score(con, "09:30", 5, log=log))
    con.close()
    # the 9:30 candle (the paper-trade entry price) closes at 9:31:00: fetch it for the picked stocks
    if doc:
        wait_until(9, 31, 15)

        def refresh():
            from warehouse.loaders import minute
            syms = [p["symbol"] for side in ("long", "short") for p in doc.get(side, [])]
            keys, _m = minute.resolve_keys(syms, lambda m: None)
            bars = live.fetch_today(keys, config.today_ist())
            if not bars.empty:
                store.upsert("minute_bars", bars, ["date", "symbol", "ts"])
            log("9:30 candles: %d bars for %d stocks" % (len(bars), bars.symbol.nunique() if len(bars) else 0))
        _step("9:30 entry prices", refresh)
    _step("monitor", lambda: monitor_run(log=log))
    _step("paper trades", lambda: paper_update(db.connect(read_only=True), log=log))
    _step("site", site_build)
    persist()
    os.makedirs(os.path.dirname(done_marker("morning")), exist_ok=True)
    open(done_marker("morning"), "w").close()


def _git_push(paths, message):
    """Commit and push a few files from inside a GitHub job (no-op on the laptop)."""
    import subprocess
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    run = lambda *a: subprocess.run(["git", "-C", REPO] + list(a), capture_output=True, text=True)
    run("add", *paths)
    if run("diff", "--cached", "--quiet").returncode == 0:
        return
    run("commit", "-m", message)
    for _ in range(3):
        if run("pull", "--rebase", "-X", "theirs").returncode == 0 and run("push").returncode == 0:
            return
        time.sleep(5)
    log("git push failed (will retry next cycle)")


def live(every_min=10, until=(15, 20)):
    """Every 10 minutes in market hours: today's 1-minute prices for every open or pending paper trade ->
    paper book (SL checks, P&L) -> docs/data/live.json, which the app reads without a site rebuild."""
    from intraday.live import fetch_today
    from papertrade.book import update as paper_update
    from site_build.app import collect
    from warehouse.loaders import minute
    os.environ["PHASE"] = "live"
    prepare()
    if not store.month_files("eq_daily"):
        log("no warehouse in the cache: run the evening job first")
        return
    stop = datetime.time(*until)

    def cycle():
        today = config.today_ist()
        book = {}
        p = os.path.join(REPO, "docs", "paper.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                book = json.load(fh)
        syms = {t["symbol"] for t in book.get("trades", []) if t["status"] in ("open", "pending")}
        for f in glob.glob(os.path.join(config.DATA, "picks", "intraday-%s.json" % today)):
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            syms |= {x["symbol"] for side in ("long", "short") for x in d.get(side, [])}
        keys, _m = minute.resolve_keys(sorted(syms), lambda m: None)
        bars = fetch_today(keys, today)
        if not bars.empty:
            store.upsert("minute_bars", bars, ["date", "symbol", "ts"])
        from optionlab import nifty_orb, telegram_alerts
        for index in nifty_orb.INDEXES:
            try:
                telegram_alerts.notify(nifty_orb.run(log=log, index=index), log=log)
            except Exception as e:     # noqa: BLE001
                log("%s option: %s" % (index.lower(), e))
        paper_update(db.connect(read_only=True), log=log)
        data = collect()
        os.makedirs(os.path.join(REPO, "docs", "data"), exist_ok=True)
        with open(os.path.join(REPO, "docs", "data", "live.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str)
        log("live: %d stocks priced, %s" % (len(syms), data["updated"]["at"]))
        _git_push(["docs/data/live.json", "docs/paper.json", "state/picks"],
                  "Live P&L %s" % datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST"))

    if datetime.datetime.now(config.IST).time() > stop:
        log("after %s: no live updates today (the evening job settles the day)" % stop.strftime("%H:%M"))
        return
    while True:
        _step("live prices", cycle)
        now = datetime.datetime.now(config.IST)
        if now.time() >= stop:
            log("live updates done for today (the evening job settles the day)")
            break
        nxt = now + datetime.timedelta(minutes=every_min)
        nxt = nxt.replace(minute=nxt.minute - nxt.minute % every_min, second=5, microsecond=0)
        if nxt.time() > stop:
            nxt = now.replace(hour=stop.hour, minute=stop.minute, second=5, microsecond=0)
        time.sleep(max(1.0, (nxt - datetime.datetime.now(config.IST)).total_seconds()))


def evening():
    from monitor.__main__ import run as monitor_run
    from papertrade.book import update as paper_update
    from site_build.build import build as site_build
    from swing import scan
    if os.path.exists(done_marker("evening")):
        log("evening already done today")
        return
    prepare()
    q = sync()
    if q["status"] != "fail":
        _step("swing picks", lambda: scan.run(db.connect(read_only=True), log=log))
        _step("results-jump picks (paper test)",
              lambda: __import__("papertrade.results_jump", fromlist=["run"]).run(db.connect(read_only=True), log=log))
        _step("momentum picks (paper test)",
              lambda: __import__("papertrade.momentum", fromlist=["run"]).run(db.connect(read_only=True), log=log))
    else:
        log("data checks failed: no swing picks tonight (the monitor halts publishing)")
    _step("monitor", lambda: monitor_run(log=log))
    _step("nifty option (final)", lambda: __import__("optionlab.nifty_orb", fromlist=["run"]).run(log=log))
    _step("sensex option (final)", lambda: __import__("optionlab.nifty_orb", fromlist=["run"]).run(log=log, index="SENSEX"))
    _step("paper trades", lambda: paper_update(db.connect(read_only=True), log=log))
    _step("site", site_build)
    persist()
    os.makedirs(os.path.dirname(done_marker("evening")), exist_ok=True)
    open(done_marker("evening"), "w").close()


def main(argv):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[0] if argv else ""
    force = "--force" in argv
    if cmd in ("morning", "evening"):
        now = datetime.datetime.now(config.IST)
        if cmd == "morning" and now.time() >= datetime.time(15, 30):
            # GitHub can start the 08:50 schedule hours late; by now the useful job is the evening one
            # (its own done marker stops it running twice)
            log("morning trigger arrived at %s IST, after the close: running the evening job instead"
                % now.strftime("%H:%M"))
            cmd = "evening"
        if session_day(cmd).weekday() >= 5 and not force:
            log("weekend: nothing to do")
            return 0
        if force:
            m = done_marker(cmd)
            if os.path.exists(m):
                os.remove(m)
        (morning if cmd == "morning" else evening)()
        return 0
    if cmd == "live":
        if datetime.datetime.now(config.IST).weekday() >= 5 and not force:
            log("weekend: nothing to do")
            return 0
        live()
        return 0
    if cmd == "export":
        export()
        return 0
    if cmd == "sync":
        prepare()
        sync()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
