"""The swing model's skeptic review, as code, so every re-validation repeats it.

Reads the walk-forward predictions (data/models/swing/oos-h10.parquet) and checks what the
statistics in model.py cannot see:

  survivorship   universe stock-days whose next 10 sessions are missing (suspension, delisting)
  fills          picks whose entry day was locked (high == low) or opened 5%+ above the last close
  k              the edge across K = 3 .. 50: a real ranking signal fades smoothly
  offsets        all 10 possible rebalance days (not just one lucky alignment)
  years          every calendar year, pooled over offsets
  leak           does MISSING data predict returns? If stocks without industry or results history
                 underperform and the model avoids them, missingness is leaking survival
-> data/models/swing/review-h10.json, plus a verdict: KEEP, DOUBT or KILL.
"""
import json
import os

import numpy as np
import pandas as pd

from warehouse import config

from . import charges


def run(con, hold=10, path=None):
    path = path or os.path.join(config.DATA, "models", "swing", "oos-h%d.parquet" % hold)
    o = pd.read_parquet(path)
    o["date"] = pd.to_datetime(o.date)
    col, ex = "fwd_%d" % hold, "excess_%d" % hold
    o = o[o[col].notna()]
    cost = charges.cnc_round_trip()
    days = np.array(sorted(o.date.unique()))
    out, problems = {}, []

    def picks(off, k=10):
        return o[o.date.isin(set(days[off::hold]))].sort_values(["date", "pred"], ascending=[True, False]) \
            .groupby("date").head(k)

    def series(off, k=10):
        return picks(off, k).groupby("date")[col].mean() - cost

    r = con.execute("""
        WITH n AS (SELECT sym_id, date, count(*) OVER (PARTITION BY sym_id ORDER BY date
                                                       ROWS BETWEEN 1 FOLLOWING AND %d FOLLOWING) fwd FROM eq_daily)
        SELECT count(*), sum(CASE WHEN n.fwd < %d THEN 1 ELSE 0 END)
        FROM liquidity_universe u JOIN n USING (sym_id, date)
        WHERE u.liq_rank <= 500 AND u.close >= 20 AND u.date >= ? AND u.date <= (SELECT max(date) - INTERVAL 30 DAY FROM calendar)
    """ % (hold, hold), [pd.Timestamp(days[0]).date()]).fetchone()
    out["survivorship_pct"] = round(r[1] / max(r[0], 1) * 100, 3)
    if out["survivorship_pct"] > 0.5:
        problems.append("survivorship")

    t = picks(0)
    nx = con.execute("""SELECT sym_id, lag(date) OVER w AS date, adj_open / lag(adj_close) OVER w - 1 AS gap,
                               adj_high = adj_low AS locked FROM eq_daily WINDOW w AS (PARTITION BY sym_id ORDER BY date)""").df()
    nx["date"] = pd.to_datetime(nx.date)
    t2 = t.merge(nx, on=["sym_id", "date"], how="left")
    clean = t2[(t2.gap.fillna(0) <= 0.05) & ~t2.locked.fillna(False).astype(bool)]
    out["fills"] = {"gap5_pct": round(float((t2.gap > 0.05).mean()) * 100, 2), "locked": int(t2.locked.fillna(False).sum()),
                    "edge_without_pct": round(float(clean[col].mean() - cost) * 100, 3)}

    out["k"] = {k: round(float(pd.concat([series(i, k) for i in range(hold)]).mean()) * 100, 3) for k in (3, 5, 10, 20, 50)}
    offs = [float(series(i).mean()) for i in range(hold)]
    out["offsets_pct"] = [round(x * 100, 3) for x in offs]
    if min(offs) <= 0:
        problems.append("offsets")
    pooled = pd.concat([series(i) for i in range(hold)])
    s0 = series(0)
    out["mean_pct"] = round(float(pooled.mean()) * 100, 3)
    out["t_offset0"] = round(float(s0.mean() / (s0.std() / np.sqrt(len(s0)))), 2)
    yrs = pooled.groupby(pooled.index.year).mean()
    out["years_pct"] = {str(y): round(float(v) * 100, 3) for y, v in yrs.items()}
    if (yrs > 0).sum() < len(yrs) * 2 / 3:
        problems.append("years")

    leak = {}
    for name, miss in (("industry", o.industry.eq("UNKNOWN")), ("results", o.days_since_result.isna())):
        tp = pd.concat([picks(i) for i in range(hold)])
        tmiss = (tp.industry.eq("UNKNOWN") if name == "industry" else tp.days_since_result.isna())
        leak[name] = {"universe_missing_pct": round(float(miss.mean()) * 100, 1),
                      "picks_missing_pct": round(float(tmiss.mean()) * 100, 1),
                      "excess_missing_pct": round(float(o[miss][ex].mean()) * 100, 3) if miss.any() else None,
                      "excess_present_pct": round(float(o[~miss][ex].mean()) * 100, 3)}
        L = leak[name]
        if L["excess_missing_pct"] is not None and L["universe_missing_pct"] > 2 and \
                L["excess_missing_pct"] < L["excess_present_pct"] - 0.3 and \
                L["picks_missing_pct"] < 0.6 * L["universe_missing_pct"]:
            problems.append("leak:" + name)
    out["leak"] = leak
    out["problems"] = problems
    out["verdict"] = ("KILL" if out["mean_pct"] <= 0 else
                      "KEEP" if not problems and out["t_offset0"] >= 2 else "DOUBT")
    with open(os.path.join(config.DATA, "models", "swing", "review-h%d.json" % hold), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out
