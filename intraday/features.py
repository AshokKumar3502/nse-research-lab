"""Features for one decision time T (default 09:30), one row per (date, symbol).

Everything is known at T: the opening window is the 1-minute bars from 09:15 up to (not including)
T; daily features come from the previous session's close; nothing from after T is used. Prices are
used only as ratios, so Upstox's back-adjusted minute prices and NSE's raw daily prices never mix.

Groups:
  window   ret_window, range_atr, close_loc, vwap_dist, up_share, rvol, gap, ret_window_vs_nifty
  prior    ret_1d, ret_5d, ret_20d, atr_pct, dist_hi20, dist_lo20, above_ma50, turnover_rank
  flows    deliv_pct, deliv_z, oi_chg_pct, fut_ret_1d, buildup (+1 long build / short cover, -1 other)
  market   nifty_ret_1d, nifty_above_ma200, vix, vix_chg, nifty_window_ret
  preopen  preopen_imb, preopen_gap (only from the day the recorder started; NaN before)
"""

FEATURES = [
    "ret_window", "range_atr", "close_loc", "vwap_dist", "up_share", "rvol", "gap", "ret_window_vs_nifty",
    "ret_1d", "ret_5d", "ret_20d", "atr_pct", "dist_hi20", "dist_lo20", "above_ma50", "turnover_rank",
    "deliv_pct", "deliv_z", "oi_chg_pct", "fut_ret_1d", "buildup",
    "nifty_ret_1d", "nifty_above_ma200", "vix", "vix_chg", "nifty_window_ret",
    "preopen_imb", "preopen_gap",
]


def _has(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0


def build_sql(con, t="09:30", bars="minute_bars", start=None, end=None):
    """The feature query. `bars` is any table/view shaped like minute_bars (live scoring passes a
    view that adds today's candles)."""
    rng = ""
    if start:
        rng += " AND w.date >= DATE '%s'" % start
    if end:
        rng += " AND w.date <= DATE '%s'" % end
    lookback = (" AND b.date >= DATE '%s' - INTERVAL 45 DAY" % start) if start else ""
    has_del = _has(con, "delivery")
    has_fo = _has(con, "fo_futures")
    has_pre = _has(con, "preopen")
    uni = ("SELECT date, symbol FROM fo_universe" if _has(con, "fo_universe")
           else "SELECT date, sym_id AS symbol FROM liquidity_universe WHERE liq_rank <= 200")
    deliv = """
        dl AS (SELECT date, symbol, deliv_pct,
                      (deliv_pct - avg(deliv_pct) OVER w) / nullif(stddev_samp(deliv_pct) OVER w, 0) AS deliv_z
               FROM delivery WHERE series = 'EQ'
               WINDOW w AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)),
    """ if has_del else "dl AS (SELECT NULL::DATE date, NULL::VARCHAR symbol, NULL::DOUBLE deliv_pct, NULL::DOUBLE deliv_z WHERE false),"
    fut = """
        nf AS (SELECT date, symbol, arg_min(oi, expiry) AS oi, arg_min(chg_oi, expiry) AS chg_oi,
                      arg_min(settle, expiry) AS settle
               FROM fo_futures WHERE instrument = 'FUTSTK' AND expiry >= date GROUP BY date, symbol),
        ff AS (SELECT date, symbol, chg_oi / nullif(oi - chg_oi, 0) AS oi_chg_pct,
                      settle / nullif(lag(settle) OVER (PARTITION BY symbol ORDER BY date), 0) - 1 AS fut_ret_1d
               FROM nf),
    """ if has_fo else "ff AS (SELECT NULL::DATE date, NULL::VARCHAR symbol, NULL::DOUBLE oi_chg_pct, NULL::DOUBLE fut_ret_1d WHERE false),"
    pre = """
        po AS (SELECT date, symbol, (buy_qty - sell_qty) / nullif(buy_qty + sell_qty, 0) AS preopen_imb,
                      iep / nullif(prev_close, 0) - 1 AS preopen_gap FROM preopen),
    """ if has_pre else "po AS (SELECT NULL::DATE date, NULL::VARCHAR symbol, NULL::DOUBLE preopen_imb, NULL::DOUBLE preopen_gap WHERE false),"
    return """
    WITH
    b AS (SELECT * FROM {bars} b WHERE true {lookback} {rng_end}),
    win AS (
        SELECT date, symbol,
               first(open ORDER BY ts) AS w_open, max(high) AS w_high, min(low) AS w_low,
               last(close ORDER BY ts) AS w_close, sum(volume) AS w_vol,
               sum(close * volume) / nullif(sum(volume), 0) AS w_vwap,
               avg(CASE WHEN close > open THEN 1.0 ELSE 0.0 END) AS up_share, count(*) AS w_bars
        FROM b WHERE ts::TIME >= TIME '09:15' AND ts::TIME < TIME '{t}'
        GROUP BY date, symbol),
    dayc AS (SELECT date, symbol, last(close ORDER BY ts) AS d_close FROM b
             WHERE ts::TIME <= TIME '15:30' GROUP BY date, symbol),
    w2 AS (
        SELECT w.*, lag(d.d_close) OVER s AS prev_mclose,
               w_vol / nullif(avg(w_vol) OVER (PARTITION BY w.symbol ORDER BY w.date
                                               ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), 0) AS rvol
        FROM win w JOIN dayc d USING (date, symbol)
        WINDOW s AS (PARTITION BY w.symbol ORDER BY w.date)),
    cal AS (SELECT date, lag(date) OVER (ORDER BY date) AS prev_date
            FROM (SELECT date FROM calendar UNION SELECT DISTINCT date FROM b)),
    tr AS (
        SELECT sym_id, date, adj_close, adj_high, adj_low,
               greatest(adj_high, coalesce(lag(adj_close) OVER (PARTITION BY sym_id ORDER BY date), adj_high))
             - least(adj_low, coalesce(lag(adj_close) OVER (PARTITION BY sym_id ORDER BY date), adj_low)) AS true_range
        FROM eq_daily),
    dly AS (
        SELECT sym_id AS symbol, date,
               adj_close / lag(adj_close, 1) OVER s - 1 AS ret_1d,
               adj_close / lag(adj_close, 5) OVER s - 1 AS ret_5d,
               adj_close / lag(adj_close, 20) OVER s - 1 AS ret_20d,
               avg(true_range) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr_abs,
               adj_close, max(adj_high) OVER s20 AS hi20, min(adj_low) OVER s20 AS lo20,
               avg(adj_close) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50
        FROM tr
        WINDOW s AS (PARTITION BY sym_id ORDER BY date),
               s20 AS (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)),
    {deliv}
    {fut}
    {pre}
    nifty AS (
        SELECT date, close / lag(close) OVER (ORDER BY date) - 1 AS nifty_ret_1d,
               CASE WHEN close > avg(close) OVER (ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)
                    THEN 1.0 ELSE 0.0 END AS nifty_above_ma200
        FROM idx_daily WHERE index_key = 'nifty 50'),
    vix AS (SELECT date, close AS vix, close / lag(close) OVER (ORDER BY date) - 1 AS vix_chg
            FROM idx_daily WHERE index_key = 'india vix'),
    nw AS (SELECT date, w_close / nullif(w_open, 0) - 1 AS nifty_window_ret FROM win WHERE symbol = 'NIFTY 50'),
    liq AS (SELECT date, sym_id AS symbol, liq_rank FROM liquidity_universe)
    SELECT w.date, w.symbol,
           w.w_close / nullif(w.w_open, 0) - 1                          AS ret_window,
           (w.w_high - w.w_low) / nullif(d.atr_abs / d.adj_close * w.w_close, 0) AS range_atr,
           (w.w_close - w.w_low) / nullif(w.w_high - w.w_low, 0)         AS close_loc,
           w.w_close / nullif(w.w_vwap, 0) - 1                           AS vwap_dist,
           w.up_share, w.rvol,
           w.w_open / nullif(w.prev_mclose, 0) - 1                       AS gap,
           (w.w_close / nullif(w.w_open, 0) - 1) - nw.nifty_window_ret   AS ret_window_vs_nifty,
           d.ret_1d, d.ret_5d, d.ret_20d, d.atr_abs / nullif(d.adj_close, 0) AS atr_pct,
           d.adj_close / nullif(d.hi20, 0) - 1 AS dist_hi20, d.adj_close / nullif(d.lo20, 0) - 1 AS dist_lo20,
           CASE WHEN d.adj_close > d.ma50 THEN 1.0 ELSE 0.0 END AS above_ma50,
           liq.liq_rank::DOUBLE AS turnover_rank,
           dl.deliv_pct, dl.deliv_z, ff.oi_chg_pct, ff.fut_ret_1d,
           CASE WHEN ff.oi_chg_pct IS NULL THEN NULL
                WHEN (ff.fut_ret_1d > 0 AND ff.oi_chg_pct > 0) OR (ff.fut_ret_1d > 0 AND ff.oi_chg_pct < 0) THEN 1.0
                ELSE -1.0 END AS buildup,
           n.nifty_ret_1d, n.nifty_above_ma200, v.vix, v.vix_chg, nw.nifty_window_ret,
           po.preopen_imb, po.preopen_gap,
           w.w_high, w.w_low, w.w_close, w.w_bars
    FROM w2 w
    JOIN cal ON cal.date = w.date
    JOIN ({uni}) fu ON fu.symbol = w.symbol AND fu.date = cal.prev_date
    LEFT JOIN dly d ON d.symbol = w.symbol AND d.date = cal.prev_date
    LEFT JOIN dl ON dl.symbol = w.symbol AND dl.date = cal.prev_date
    LEFT JOIN ff ON ff.symbol = w.symbol AND ff.date = cal.prev_date
    LEFT JOIN po ON po.symbol = w.symbol AND po.date = w.date
    LEFT JOIN nifty n ON n.date = cal.prev_date
    LEFT JOIN vix v ON v.date = cal.prev_date
    LEFT JOIN nw ON nw.date = w.date
    LEFT JOIN liq ON liq.symbol = w.symbol AND liq.date = cal.prev_date
    WHERE w.w_bars >= 10 {rng}
    """.format(bars=bars, t=t, lookback=lookback, rng_end=(" AND b.date <= DATE '%s'" % end) if end else "",
               deliv=deliv, fut=fut, pre=pre, rng=rng, uni=uni)


def build(con, t="09:30", bars="minute_bars", start=None, end=None):
    """-> DataFrame of features (plus w_high, w_low, w_close for the labels)."""
    return con.execute(build_sql(con, t, bars, start, end)).df()
