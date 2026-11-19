"""The DuckDB file: views over the Parquet datasets plus the derived, point-in-time tables.

Views (always current, read straight from Parquet):
  eq_daily_raw, idx_daily, delivery, fo_futures, fo_options, participant_oi, minute_bars, preopen,
  corp_actions, symbol_changes, results_dates, instruments

Tables rebuilt by `build()`:
  symbol_map          (symbol, valid_from, valid_until, sym_id): an old ticker's rows -> today's ticker
  eq_daily            every stock bar with sym_id, the bonus/split factor and adjusted OHLCV
                      (adjusted for bonuses, splits and consolidations only, not dividends)
  calendar            every NSE trading session
  fo_universe         (date, symbol): stocks with futures that day; exact point-in-time F&O membership
  liquidity_universe  (date, sym_id, turnover_60d, liq_rank): rank by 60-session average turnover
                      using only data up to that close. Includes stocks later delisted, so a
                      "top 500 by liquidity" universe has no survivorship bias.
  minute_scale        (date, symbol, source, scale): Upstox minute prices are back-adjusted to today's
                      share terms; minute price x scale = the price NSE actually traded (~ raw).
                      Intraday returns need no scaling.
"""
import datetime
import os

import duckdb
import pandas as pd

from . import config, store

VIEWS = ["eq_daily_raw:eq_daily", "idx_daily", "delivery", "fo_futures", "fo_options", "participant_oi",
         "minute_bars", "preopen", "corp_actions", "symbol_changes", "results_dates", "instruments", "industries", "etfs", "industry_pit"]
FAR = datetime.date(2100, 1, 1)
NEAR = datetime.date(1990, 1, 1)


def connect(read_only=False):
    os.makedirs(config.DATA, exist_ok=True)
    con = duckdb.connect(config.DB_PATH, read_only=read_only)
    return con


def symbol_map(changes):
    """changes: [(old, new, change_date)] -> rows (symbol, valid_from, valid_until, sym_id).
    Rows of `symbol` dated in [valid_from, valid_until) belong to `sym_id`, today's ticker."""
    by_old = {}
    for old, new, d in changes:
        if old != new:
            by_old.setdefault(old, []).append((d, new))
    for v in by_old.values():
        v.sort()

    def final(sym, on, depth=0):
        """The ticker that rows of `sym` dated `on` trade under today."""
        if depth > 20:
            return sym
        for d, new in by_old.get(sym, []):
            if d > on:
                return final(new, d, depth + 1)
        return sym

    out = []
    for sym, chg in by_old.items():
        start = NEAR
        for d, _new in chg:
            out.append((sym, start, d, final(sym, start)))
            start = d
    return [r for r in out if r[0] != r[3]]


def _views(con):
    for spec in VIEWS:
        view, _, ds = spec.partition(":")
        ds = ds or view
        files = store.month_files(ds)
        if files:
            con.execute("CREATE OR REPLACE VIEW %s AS SELECT * FROM read_parquet('%s', union_by_name=true)"
                        % (view, store.glob(ds)))
        else:
            con.execute("DROP VIEW IF EXISTS %s" % view)


def _has(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0


def build(log=print):
    con = connect()
    _views(con)
    if not _has(con, "eq_daily_raw"):
        log("db: no eq_daily data yet; run sync first")
        return con
    ch = con.execute("SELECT old_symbol, new_symbol, change_date FROM symbol_changes").fetchall() \
        if _has(con, "symbol_changes") else []
    sm = pd.DataFrame(symbol_map(ch), columns=["symbol", "valid_from", "valid_until", "sym_id"])
    con.register("sm_df", sm)
    con.execute("CREATE OR REPLACE TABLE symbol_map AS SELECT * FROM sm_df")
    con.unregister("sm_df")
    has_ca = _has(con, "corp_actions")
    con.execute("""
        CREATE OR REPLACE TEMP VIEW eq_ids AS
        SELECT r.*, coalesce(m.sym_id, r.symbol) AS sym_id
        FROM eq_daily_raw r
        LEFT JOIN symbol_map m ON r.symbol = m.symbol AND r.date >= m.valid_from AND r.date < m.valid_until
        QUALIFY row_number() OVER (PARTITION BY coalesce(m.sym_id, r.symbol), r.date
                                   ORDER BY (r.symbol = coalesce(m.sym_id, r.symbol)) DESC, r.turnover DESC) = 1
    """)
    if has_ca:
        con.execute("""
            CREATE OR REPLACE TEMP VIEW ca_ids AS
            SELECT DISTINCT coalesce(m.sym_id, c.symbol) AS sym_id, c.ex_date, c.factor
            FROM corp_actions c
            LEFT JOIN symbol_map m ON c.symbol = m.symbol AND c.ex_date >= m.valid_from AND c.ex_date < m.valid_until
            WHERE c.factor > 0
        """)
        fac = """(SELECT e.sym_id, e.date, exp(sum(ln(a.factor))) AS f
                  FROM eq_ids e JOIN ca_ids a ON a.sym_id = e.sym_id AND a.ex_date > e.date
                  GROUP BY e.sym_id, e.date)"""
    else:
        fac = "(SELECT NULL::VARCHAR AS sym_id, NULL::DATE AS date, NULL::DOUBLE AS f WHERE false)"
    con.execute("""
        CREATE OR REPLACE TABLE eq_daily AS
        SELECT e.date, e.sym_id, e.symbol, e.series,
               e.open, e.high, e.low, e.close, e.prev_close, e.volume, e.turnover,
               coalesce(f.f, 1.0) AS adj_factor,
               e.open * coalesce(f.f, 1.0)  AS adj_open,
               e.high * coalesce(f.f, 1.0)  AS adj_high,
               e.low * coalesce(f.f, 1.0)   AS adj_low,
               e.close * coalesce(f.f, 1.0) AS adj_close,
               e.volume / coalesce(f.f, 1.0) AS adj_volume
        FROM eq_ids e LEFT JOIN %s f ON f.sym_id = e.sym_id AND f.date = e.date
        ORDER BY e.sym_id, e.date
    """ % fac)
    con.execute("CREATE OR REPLACE TABLE calendar AS SELECT DISTINCT date FROM eq_daily_raw ORDER BY date")
    if _has(con, "fo_futures"):
        # stored under today's ticker (symbol_map), so renamed stocks keep their F&O history
        con.execute("""CREATE OR REPLACE TABLE fo_universe AS
                       SELECT DISTINCT f.date, coalesce(m.sym_id, f.symbol) AS symbol, f.symbol AS fo_symbol
                       FROM fo_futures f
                       LEFT JOIN symbol_map m ON m.symbol = f.symbol AND f.date >= m.valid_from AND f.date < m.valid_until
                       WHERE f.instrument = 'FUTSTK' ORDER BY 1, 2""")
    con.execute("""
        CREATE OR REPLACE TABLE liquidity_universe AS
        WITH t AS (
            SELECT date, sym_id, close,
                   avg(turnover) OVER w AS turnover_60d,
                   count(*) OVER w AS sessions_60d
            FROM eq_daily WHERE series = 'EQ' {etf}
            WINDOW w AS (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
        )
        SELECT date, sym_id, close, turnover_60d,
               rank() OVER (PARTITION BY date ORDER BY turnover_60d DESC) AS liq_rank
        FROM t WHERE sessions_60d >= 40
    """.format(etf=("AND sym_id NOT IN (SELECT symbol FROM etfs)" if _has(con, "etfs") else "")
               + " AND sym_id NOT LIKE '%BEES' AND sym_id NOT LIKE '%ETF%'"))
    if _has(con, "minute_bars"):
        # Upstox back-adjusts minute candles for later bonuses, splits and demergers; NSE's daily close
        # is the raw traded price. scale = NSE close / last minute close, so raw = minute price * scale.
        con.execute("""
            CREATE OR REPLACE TABLE minute_scale AS
            WITH m AS (SELECT date, symbol, arg_max(close, ts) AS last_close, any_value(source) AS source
                       FROM minute_bars GROUP BY date, symbol)
            SELECT m.date, m.symbol, m.source, e.close / m.last_close AS scale
            FROM m JOIN eq_daily e ON e.sym_id = m.symbol AND e.date = m.date
            WHERE m.last_close > 0
        """)
    for t in ("eq_daily", "calendar", "liquidity_universe", "symbol_map"):
        n = con.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
        log("db: %s %d rows" % (t, n))
    return con


def summary(con=None):
    """Rows, symbols and date range of every view and table."""
    con = con or connect(read_only=True)
    out = []
    names = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1").fetchall()]
    for n in names:
        cols = [r[0] for r in con.execute("DESCRIBE %s" % n).fetchall()]
        dc = next((c for c in ("date", "ex_date", "change_date", "announced_at") if c in cols), None)
        sc = next((c for c in ("sym_id", "symbol", "index_key", "participant") if c in cols), None)
        q = "SELECT count(*)%s%s FROM %s" % (
            ", min(%s), max(%s)" % (dc, dc) if dc else "", ", count(DISTINCT %s)" % sc if sc else "", n)
        r = con.execute(q).fetchone()
        out.append({"name": n, "rows": r[0], "first": str(r[1]) if dc else None, "last": str(r[2]) if dc else None,
                    "symbols": r[-1] if sc else None})
    return out
