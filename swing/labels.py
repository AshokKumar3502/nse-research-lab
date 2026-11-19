"""What a swing trade opened at D+1's open would have earned (adjusted daily prices). Never a feature.

For each horizon h in HORIZONS:
  fwd_h       exit at the close of the h-th session after entry
  fwd_stop_h  stop at entry - STOP_ATR x ATR(D); the first session whose low touches it exits at the
              stop, or at that session's open when it gaps through; otherwise the h-th close
Gross fractions; charges.cnc_round_trip() is subtracted later.
"""

HORIZONS = (5, 10, 20)
STOP_ATR = 2.0


def build(con, feats):
    """feats: DataFrame with date, sym_id, atr_pct, adj_close (from features.build)."""
    con.register("lab_in", feats[["date", "sym_id", "atr_pct", "adj_close"]])
    hmax = max(HORIZONS)
    cols = []
    for h in HORIZONS:
        cols.append("max(CASE WHEN p.k = {h} THEN p.adj_close END) / any_value(e.entry) - 1 AS fwd_{h}".format(h=h))
        cols.append("""coalesce(arg_min(CASE WHEN p.k <= {h} AND p.adj_low <= e.stop THEN least(p.adj_open, e.stop) END,
                                         CASE WHEN p.k <= {h} AND p.adj_low <= e.stop THEN p.k END),
                                max(CASE WHEN p.k = {h} THEN p.adj_close END)) / any_value(e.entry) - 1 AS fwd_stop_{h}"""
                    .format(h=h))
    sql = """
    WITH r AS (SELECT sym_id, date, adj_open, adj_low, adj_close,
                      row_number() OVER (PARTITION BY sym_id ORDER BY date) AS rn FROM eq_daily),
         e AS (SELECT i.date, i.sym_id, r.rn, nx.adj_open AS entry,
                      nx.adj_open - {atr} * i.atr_pct * i.adj_close AS stop
               FROM lab_in i JOIN r ON r.sym_id = i.sym_id AND r.date = i.date
               JOIN r nx ON nx.sym_id = i.sym_id AND nx.rn = r.rn + 1
               WHERE nx.adj_open > 0),
         p AS (SELECT e.date AS d0, e.sym_id, r.rn - e.rn AS k, r.adj_open, r.adj_low, r.adj_close
               FROM e JOIN r ON r.sym_id = e.sym_id AND r.rn BETWEEN e.rn + 1 AND e.rn + {hmax})
    SELECT e.date, e.sym_id, any_value(e.entry) AS entry, any_value(e.stop) AS stop, {cols}
    FROM e JOIN p ON p.d0 = e.date AND p.sym_id = e.sym_id
    GROUP BY e.date, e.sym_id
    """.format(atr=STOP_ATR, hmax=hmax, cols=",\n           ".join(cols))
    out = con.execute(sql).df()
    con.unregister("lab_in")
    return out
