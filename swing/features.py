"""Swing features at the close of day D, one row per (date, stock), for the point-in-time liquid
universe (top N by 60-session turnover that day, price >= 20). Everything is known at D's close;
trades are entered at D+1's open.

Groups
  trend      ret_5, ret_20, ret_60, ret_120, dist_ma20, dist_ma50, dist_ma200, ma50_over_ma200,
             dist_hi252, atr_pct, vol_20
  volume     turn_ratio_5_60, up_vol_share_20
  delivery   deliv_5_vs_60 (accumulation), deliv_z
  futures    oi_chg_5d, fut_buildup_5d (F&O stocks only, NaN otherwise)
  relative   rs_nifty_20, rs_industry_20, ind_rel_20, ind_rel_chg_5, ind_rank_20
  results    days_since_result, result_ret, result_turn_mult, result_close_loc
  candles    ret_1, close_loc_1, upper_wick_1, body_1
  market     nifty_ret_20, nifty_above_ma200, breadth_ma50, vix

Industry is point in time (swing/industry_pit.py: assigned monthly by return correlation to NSE's
industry baskets, for every stock including delisted ones), not today's lists.
"""

FEATURES = [
    "ret_5", "ret_20", "ret_60", "ret_120", "dist_ma20", "dist_ma50", "dist_ma200", "ma50_over_ma200",
    "dist_hi252", "atr_pct", "vol_20",
    "turn_ratio_5_60", "up_vol_share_20",
    "deliv_5_vs_60", "deliv_z",
    "oi_chg_5d", "fut_buildup_5d",
    "rs_nifty_20", "rs_industry_20", "ind_rel_20", "ind_rel_chg_5", "ind_rank_20",
    "days_since_result", "result_ret", "result_turn_mult", "result_close_loc",
    "ret_1", "close_loc_1", "upper_wick_1", "body_1",
    "nifty_ret_20", "nifty_above_ma200", "breadth_ma50", "vix",
]

SETS = ["delivery", "fo_futures", "results_dates", "industries", "industry_pit"]


def _has(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0


def ensure_views(con):
    """Temp views for datasets the DuckDB file has not registered yet (read straight from Parquet)."""
    from warehouse import store
    for ds in SETS:
        if not _has(con, ds) and store.month_files(ds):
            con.execute("CREATE OR REPLACE TEMP VIEW %s AS SELECT * FROM read_parquet('%s', union_by_name=true)"
                        % (ds, store.glob(ds)))


def build_sql(con, start=None, end=None, top_n=500):
    ensure_views(con)
    rng = ""
    if start:
        rng += " AND u.date >= DATE '%s'" % start
    if end:
        rng += " AND u.date <= DATE '%s'" % end
    has = {ds: _has(con, ds) for ds in SETS}
    deliv = """
      dl AS (SELECT date, symbol,
                    avg(deliv_pct) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
                  / nullif(avg(deliv_pct) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW), 0)
                  - 1 AS deliv_5_vs_60,
                    (deliv_pct - avg(deliv_pct) OVER w) / nullif(stddev_samp(deliv_pct) OVER w, 0) AS deliv_z
             FROM delivery WHERE series = 'EQ'
             WINDOW w AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)),""" if has["delivery"] else \
        "dl AS (SELECT NULL::DATE date, NULL::VARCHAR symbol, NULL::DOUBLE deliv_5_vs_60, NULL::DOUBLE deliv_z WHERE false),"
    fut = """
      nf AS (SELECT date, symbol, arg_min(oi, expiry) AS oi, arg_min(settle, expiry) AS settle
             FROM fo_futures WHERE instrument = 'FUTSTK' AND expiry >= date GROUP BY date, symbol),
      ff AS (SELECT date, symbol,
                    oi / nullif(lag(oi, 5) OVER s, 0) - 1 AS oi_chg_5d,
                    sign(settle / nullif(lag(settle, 5) OVER s, 0) - 1) * sign(oi / nullif(lag(oi, 5) OVER s, 0) - 1)
                        AS fut_buildup_5d
             FROM nf WINDOW s AS (PARTITION BY symbol ORDER BY date)),""" if has["fo_futures"] else \
        "ff AS (SELECT NULL::DATE date, NULL::VARCHAR symbol, NULL::DOUBLE oi_chg_5d, NULL::DOUBLE fut_buildup_5d WHERE false),"
    if has["industry_pit"]:      # point in time, every stock (swing/industry_pit.py)
        ind_join = "ASOF LEFT JOIN industry_pit i ON i.sym_id = b.sym_id AND b.date >= i.valid_from"
    elif has["industries"]:      # today's NSE lists only: leaks survival (see data/models/swing/REVIEW.md)
        ind_join = "LEFT JOIN industries i ON i.symbol = b.sym_id"
    else:
        ind_join = "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::VARCHAR industry WHERE false) i ON i.symbol = b.sym_id"
    res = """
      cal AS (SELECT date, row_number() OVER (ORDER BY date) AS n FROM calendar),
      ev0 AS (SELECT symbol AS sym_id,
                     CASE WHEN announced_at::TIME >= TIME '15:30' THEN announced_at::DATE + 1
                          ELSE announced_at::DATE END AS d0
              FROM results_dates),
      ev AS (SELECT e.sym_id, c.date AS event_day, c.n AS event_n
             FROM ev0 e ASOF JOIN (SELECT date, n FROM cal) c ON c.date >= e.d0),
      evx AS (SELECT ev.sym_id, ev.event_day, ev.event_n,
                     b.ret_1 AS result_ret, b.turnover / nullif(b.turn_60_prev, 0) AS result_turn_mult,
                     b.close_loc_1 AS result_close_loc
              FROM ev JOIN base b ON b.sym_id = ev.sym_id AND b.date = ev.event_day),""" if has["results_dates"] else ""
    return """
    WITH
    base AS (
      SELECT sym_id, date, series, adj_open, adj_high, adj_low, adj_close, turnover,
             adj_close / lag(adj_close, 1) OVER s - 1 AS ret_1,
             adj_close / lag(adj_close, 5) OVER s - 1 AS ret_5,
             adj_close / lag(adj_close, 20) OVER s - 1 AS ret_20,
             adj_close / lag(adj_close, 60) OVER s - 1 AS ret_60,
             adj_close / lag(adj_close, 120) OVER s - 1 AS ret_120,
             avg(adj_close) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
             avg(adj_close) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
             avg(adj_close) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma200,
             count(*) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n200,
             max(adj_high) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS hi252,
             greatest(adj_high, coalesce(lag(adj_close) OVER s, adj_high))
               - least(adj_low, coalesce(lag(adj_close) OVER s, adj_low)) AS tr,
             avg(turnover) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS turn_5,
             avg(turnover) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS turn_60,
             avg(turnover) OVER (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING) AS turn_60_prev,
             (adj_close - adj_low) / nullif(adj_high - adj_low, 0) AS close_loc_1,
             (adj_high - greatest(adj_open, adj_close)) / nullif(adj_high - adj_low, 0) AS upper_wick_1,
             (adj_close - adj_open) / nullif(adj_high - adj_low, 0) AS body_1
      FROM eq_daily WINDOW s AS (PARTITION BY sym_id ORDER BY date)),
    b2 AS (
      SELECT *, avg(tr) OVER w14 / nullif(adj_close, 0) AS atr_pct,
             stddev_samp(ret_1) OVER w20 AS vol_20,
             sum(CASE WHEN ret_1 > 0 THEN turnover ELSE 0 END) OVER w20 / nullif(sum(turnover) OVER w20, 0) AS up_vol_share_20
      FROM base
      WINDOW w14 AS (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW),
             w20 AS (PARTITION BY sym_id ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)),
    {deliv}
    {fut}
    {res}
    nifty AS (
      SELECT date, close / lag(close, 20) OVER (ORDER BY date) - 1 AS nifty_ret_20,
             CASE WHEN close > avg(close) OVER (ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)
                  THEN 1.0 ELSE 0.0 END AS nifty_above_ma200
      FROM idx_daily WHERE index_key = 'nifty 50'),
    vix AS (SELECT date, close AS vix FROM idx_daily WHERE index_key = 'india vix'),
    u AS (SELECT l.date, l.sym_id FROM liquidity_universe l WHERE l.liq_rank <= {top_n} AND l.close >= 20),
    f AS (
      SELECT b.*, coalesce(i.industry, 'UNKNOWN') AS industry
      FROM u JOIN b2 b ON b.sym_id = u.sym_id AND b.date = u.date
      {ind_join}
      WHERE true {rng}),
    breadth AS (SELECT date, avg(CASE WHEN adj_close > ma50 THEN 1.0 ELSE 0.0 END) AS breadth_ma50 FROM f GROUP BY date),
    indr AS (
      SELECT date, industry, median(ret_20) AS ind_ret_20, count(*) AS ind_n FROM f
      WHERE industry <> 'UNKNOWN' GROUP BY date, industry),
    indr2 AS (
      SELECT i.*, i.ind_ret_20 - n.nifty_ret_20 AS ind_rel_20,
             (i.ind_ret_20 - n.nifty_ret_20)
               - lag(i.ind_ret_20 - n.nifty_ret_20, 5) OVER (PARTITION BY i.industry ORDER BY i.date) AS ind_rel_chg_5,
             percent_rank() OVER (PARTITION BY i.date ORDER BY i.ind_ret_20) AS ind_rank_20
      FROM indr i JOIN nifty n USING (date) WHERE i.ind_n >= 3)
    SELECT f.date, f.sym_id, f.industry, f.adj_open, f.adj_high, f.adj_low, f.adj_close, f.turnover,
           f.ret_1, f.ret_5, f.ret_20, f.ret_60, f.ret_120,
           f.adj_close / nullif(f.ma20, 0) - 1 AS dist_ma20,
           f.adj_close / nullif(f.ma50, 0) - 1 AS dist_ma50,
           CASE WHEN f.n200 >= 200 THEN f.adj_close / nullif(f.ma200, 0) - 1 END AS dist_ma200,
           CASE WHEN f.n200 >= 200 THEN f.ma50 / nullif(f.ma200, 0) - 1 END AS ma50_over_ma200,
           f.adj_close / nullif(f.hi252, 0) - 1 AS dist_hi252, f.atr_pct, f.vol_20,
           f.turn_5 / nullif(f.turn_60, 0) AS turn_ratio_5_60, f.up_vol_share_20,
           dl.deliv_5_vs_60, dl.deliv_z, ff.oi_chg_5d, ff.fut_buildup_5d,
           f.ret_20 - n.nifty_ret_20 AS rs_nifty_20,
           f.ret_20 - ir.ind_ret_20 AS rs_industry_20, ir.ind_rel_20, ir.ind_rel_chg_5, ir.ind_rank_20,
           {resx}
           f.close_loc_1, f.upper_wick_1, f.body_1,
           n.nifty_ret_20, n.nifty_above_ma200, br.breadth_ma50, v.vix
    FROM f
    LEFT JOIN dl ON dl.symbol = f.sym_id AND dl.date = f.date
    LEFT JOIN ff ON ff.symbol = f.sym_id AND ff.date = f.date
    LEFT JOIN indr2 ir ON ir.date = f.date AND ir.industry = f.industry
    LEFT JOIN nifty n ON n.date = f.date
    LEFT JOIN vix v ON v.date = f.date
    LEFT JOIN breadth br ON br.date = f.date
    {resjoin}
    """.format(deliv=deliv, fut=fut, res=res, top_n=int(top_n), ind_join=ind_join, rng=rng,
               resx=("(SELECT c.n FROM cal c WHERE c.date = f.date) - e.event_n AS days_since_result, "
                     "e.result_ret, e.result_turn_mult, e.result_close_loc,") if has["results_dates"] else
               "NULL::DOUBLE AS days_since_result, NULL::DOUBLE AS result_ret, NULL::DOUBLE AS result_turn_mult, "
               "NULL::DOUBLE AS result_close_loc,",
               resjoin="ASOF LEFT JOIN evx e ON e.sym_id = f.sym_id AND f.date >= e.event_day"
               if has["results_dates"] else "")


def build(con, start=None, end=None, top_n=500):
    return con.execute(build_sql(con, start, end, top_n)).df()
