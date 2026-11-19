"""Data quality checks. Each check returns {name, status: ok|warn|fail, detail, sample}.

Run after every sync; the report is saved to data/quality.json so research code can refuse to run
on a stale or broken warehouse.
"""
import datetime
import json
import os

from . import config


def _q(con, sql):
    return con.execute(sql).fetchall()


def _has(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0


def check(con):
    out = []

    def add(name, status, detail, sample=None):
        out.append({"name": name, "status": status, "detail": detail, "sample": sample or []})

    last = _q(con, "SELECT max(date) FROM calendar")[0][0]
    # freshness: every daily dataset should reach the last trading session
    for ds in ("eq_daily_raw", "idx_daily", "delivery", "fo_futures", "participant_oi", "minute_bars"):
        if not _has(con, ds):
            add("fresh:" + ds, "warn", "not loaded yet")
            continue
        m = _q(con, "SELECT max(date) FROM %s" % ds)[0][0]
        lag = len(_q(con, "SELECT date FROM calendar WHERE date > '%s'" % m)) if m else None
        add("fresh:" + ds, "ok" if lag == 0 else ("warn" if lag is not None and lag <= 3 else "fail"),
            "last %s, %s session(s) behind the calendar" % (m, lag))

    # coverage: sessions each dataset holds since it starts, against the calendar
    from . import store
    closed_kind = {"delivery": "delivery", "fo_futures": "fo", "participant_oi": "participant_oi"}
    for ds in ("idx_daily", "delivery", "fo_futures", "participant_oi"):
        if not _has(con, ds):
            continue
        rows = _q(con, """SELECT c.date FROM calendar c WHERE c.date >= (SELECT min(date) FROM %s)
                          AND c.date NOT IN (SELECT DISTINCT date FROM %s) ORDER BY 1 DESC""" % (ds, ds))
        unpublished = store.closed_days(closed_kind.get(ds, ds))
        miss = [str(x[0]) for x in rows if str(x[0]) not in unpublished]
        known = len(rows) - len(miss)
        add("coverage:" + ds, "ok" if not miss else ("warn" if len(miss) <= 10 else "fail"),
            "%d trading session(s) missing%s" % (len(miss), ", plus %d NSE never published" % known if known else ""),
            miss[:5])

    # OHLC sanity
    bad = _q(con, """SELECT date, symbol, open, high, low, close FROM eq_daily_raw
                     WHERE high < greatest(open, close) - 1e-6 OR low > least(open, close) + 1e-6
                        OR least(open, high, low, close) <= 0 LIMIT 5""")
    n = _q(con, """SELECT count(*) FROM eq_daily_raw WHERE high < greatest(open, close) - 1e-6
                   OR low > least(open, close) + 1e-6 OR least(open, high, low, close) <= 0""")[0][0]
    add("ohlc:eq_daily", "ok" if n == 0 else "warn", "%d bar(s) with impossible OHLC" % n, [list(map(str, b)) for b in bad])

    # duplicates
    for ds, keys in (("eq_daily_raw", "date, symbol"), ("delivery", "date, symbol, series"),
                     ("fo_futures", "date, symbol, instrument, expiry"), ("minute_bars", "date, symbol, ts")):
        if _has(con, ds):
            n = _q(con, "SELECT count(*) FROM (SELECT %s FROM %s GROUP BY ALL HAVING count(*) > 1)" % (keys, ds))[0][0]
            add("dupes:" + ds, "ok" if n == 0 else "fail", "%d duplicated key(s)" % n)

    # continuity: our previous stored close (any series, after renames) should equal NSE's own
    # prev_close on the next session unless a bonus/split happened that day. A mismatch means a
    # broken rename mapping or a missing day.
    cont = """
        WITH r AS (SELECT sym_id, date, close, prev_close, adj_factor,
                          lag(close) OVER w AS last_close, lag(date) OVER w AS last_date,
                          lag(adj_factor) OVER w AS last_factor
                   FROM eq_daily WINDOW w AS (PARTITION BY sym_id ORDER BY date)),
             c AS (SELECT date, lag(date) OVER (ORDER BY date) AS prev_session FROM calendar)
        SELECT r.sym_id, r.date, r.last_close, r.prev_close FROM r JOIN c USING (date)
        WHERE r.last_date = c.prev_session AND r.prev_close > 0 AND r.last_factor = r.adj_factor
          AND abs(r.last_close / r.prev_close - 1) > 0.02"""
    nb = _q(con, "SELECT count(*) FROM (%s)" % cont)[0][0]
    eg = _q(con, cont + " ORDER BY r.date DESC LIMIT 5")
    add("continuity:eq_daily", "ok" if nb < 100 else "warn",
        "%d session(s) where the stored previous close differs from NSE's prev_close by >2%% "
        "(rename mapping or a missing day)" % nb, ["%s %s %.2f vs %.2f" % x for x in eg])

    # big raw moves with no bonus/split on record: demergers, unrecorded actions, or real crashes
    big = """
        SELECT e.sym_id, e.date, round((e.close / e.prev_close - 1) * 100, 1) AS pct FROM eq_daily e
        WHERE e.prev_close > 0 AND abs(e.close / e.prev_close - 1) > 0.35 AND e.turnover > 5e7
          AND e.sym_id NOT LIKE '%%BEES' AND e.sym_id NOT LIKE '%%ETF%%'%s
          AND EXISTS (SELECT 1 FROM eq_daily y WHERE y.sym_id = e.sym_id AND y.date < e.date
                                                AND y.date >= e.date - INTERVAL 10 DAY)
          AND e.adj_factor = coalesce((SELECT max(x.adj_factor) FROM eq_daily x
                                       WHERE x.sym_id = e.sym_id AND x.date < e.date
                                         AND x.date >= e.date - INTERVAL 7 DAY), e.adj_factor)"""
    big = big % (" AND e.sym_id NOT IN (SELECT symbol FROM etfs)" if _has(con, "etfs") else "")
    nj = _q(con, "SELECT count(*) FROM (%s)" % big)[0][0]
    eg = _q(con, big + " ORDER BY abs(pct) DESC LIMIT 6")
    add("bigmoves:eq_daily", "ok" if nj < 60 else "warn",
        "%d liquid one-day moves over 35%% with no bonus/split on record (demerger, unrecorded action, "
        "or real)" % nj, ["%s %s %s%%" % x for x in eg])

    # minute bars: short sessions
    if _has(con, "minute_bars"):
        r = _q(con, """SELECT count(*), sum(CASE WHEN n < 300 THEN 1 ELSE 0 END) FROM
                       (SELECT date, symbol, count(*) n FROM minute_bars GROUP BY 1, 2)""")[0]
        add("minute:sessions", "ok" if not r[0] or (r[1] or 0) / r[0] < 0.05 else "warn",
            "%d stock-days, %d with fewer than 300 one-minute bars" % (r[0], r[1] or 0))

    status = "fail" if any(c["status"] == "fail" for c in out) else (
        "warn" if any(c["status"] == "warn" for c in out) else "ok")
    rep = {"checked_at": datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST"),
           "last_session": str(last), "status": status, "checks": out}
    with open(os.path.join(config.DATA, "quality.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    return rep
