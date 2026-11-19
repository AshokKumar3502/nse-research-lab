"""What each (date, symbol) would have earned after the decision time T. Never used as a feature.

Entry: the open of the first bar at or after T. Exits:
  close  : the close of the 15:09 bar (flat by 15:10, inside Zerodha's 15:12 / 15:25 square-offs)
  stop   : long  -> stop at the opening-window low;  short -> stop at the opening-window high.
           The first bar after entry that touches the stop exits at the stop, or at that bar's open
           when it gaps through. Otherwise the 15:09 close.
Returns are gross fractions; model.py subtracts charges.round_trip().
"""

EXIT_TIME = "15:10"


def build_sql(t="09:30", bars="minute_bars", start=None, end=None):
    rng = ""
    if start:
        rng += " AND date >= DATE '%s'" % start
    if end:
        rng += " AND date <= DATE '%s'" % end
    return """
    WITH
    b AS (SELECT date, symbol, ts, open, high, low, close FROM {bars} WHERE true {rng}),
    w AS (SELECT date, symbol, max(high) AS w_high, min(low) AS w_low FROM b
          WHERE ts::TIME >= TIME '09:15' AND ts::TIME < TIME '{t}' GROUP BY date, symbol),
    after AS (SELECT b.*, w.w_high, w.w_low FROM b JOIN w USING (date, symbol)
              WHERE b.ts::TIME >= TIME '{t}' AND b.ts::TIME < TIME '{x}'),
    ent AS (SELECT date, symbol, arg_min(open, ts) AS entry, min(ts) AS entry_ts,
                   arg_max(close, ts) AS exit_close, max(ts) AS last_ts, any_value(w_high) AS w_high,
                   any_value(w_low) AS w_low
            FROM after GROUP BY date, symbol),
    ls AS (SELECT a.date, a.symbol, arg_min(least(a.open, a.w_low), a.ts) AS long_stop_px
           FROM after a JOIN ent e USING (date, symbol)
           WHERE a.low <= a.w_low AND a.ts >= e.entry_ts GROUP BY a.date, a.symbol),
    ss AS (SELECT a.date, a.symbol, arg_min(greatest(a.open, a.w_high), a.ts) AS short_stop_px
           FROM after a JOIN ent e USING (date, symbol)
           WHERE a.high >= a.w_high AND a.ts >= e.entry_ts GROUP BY a.date, a.symbol)
    SELECT e.date, e.symbol, e.entry, e.exit_close,
           e.exit_close / e.entry - 1                                   AS ret_close,
           coalesce(ls.long_stop_px, e.exit_close) / e.entry - 1        AS ret_long_stop,
           1 - coalesce(ss.short_stop_px, e.exit_close) / e.entry       AS ret_short_stop,
           ls.long_stop_px IS NOT NULL AS long_stopped,
           ss.short_stop_px IS NOT NULL AS short_stopped,
           e.entry / nullif(e.w_low, 0) - 1                             AS long_risk,
           e.w_high / nullif(e.entry, 0) - 1                            AS short_risk
    FROM ent e LEFT JOIN ls USING (date, symbol) LEFT JOIN ss USING (date, symbol)
    WHERE e.entry > 0 AND e.last_ts::TIME >= TIME '15:00'
    """.format(bars=bars, t=t, x=EXIT_TIME, rng=rng)


def build(con, t="09:30", bars="minute_bars", start=None, end=None):
    return con.execute(build_sql(t, bars, start, end)).df()
