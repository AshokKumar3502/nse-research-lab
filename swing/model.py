"""Walk-forward validation of the swing ranker against the pack's own proven rules, all scored the
same way: entry at the next open, every trade after Zerodha delivery charges + slippage.

Model: LightGBM predicts each stock's 10-session return in excess of the universe average. Trained
only on data that ended at least EMBARGO sessions before the first day it predicts (labels look 20
sessions ahead), retrained every `retrain_months`.

Strategies, rebalanced every `hold` sessions so trades never overlap (an honest t-statistic):
  model_top           top K by prediction
  model_top_regime    the same, only while NIFTY 50 is above its 200-day average
  rule_sector_leaders the india-swing-desk rule: industry 2+ pts ahead of NIFTY and strengthening,
                      stock ahead of its industry, not on the avoid list, NIFTY above its 200-day
  rule_momentum       top K by 20-day strength vs NIFTY, 50-day above 200-day average
  all                 every stock in the universe (the baseline)
Event rule, scored per trade:
  rule_results_jump   results-day move >= 4% on 2x turnover closing in the top quarter of the range,
                      NIFTY above its 200-day; held 20 sessions
"""
import datetime
import json
import os

import numpy as np
import pandas as pd

from intraday.model import stats, verdict

from . import charges, features, labels

MODEL_DIR_NAME = os.path.join("models", "swing")
EMBARGO = 25
PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=500, subsample=0.7,
              subsample_freq=1, colsample_bytree=0.7, reg_lambda=2.0, verbose=-1)


def dataset(con, start=None, end=None, top_n=500, log=print):
    f = features.build(con, start, end, top_n)
    log("swing: %d feature rows, %d sessions" % (len(f), f.date.nunique()))
    y = labels.build(con, f)
    df = f.merge(y, on=["date", "sym_id"], how="inner")
    df["date"] = pd.to_datetime(df["date"])
    for h in labels.HORIZONS:
        df["excess_%d" % h] = df["fwd_%d" % h] - df.groupby("date")["fwd_%d" % h].transform("mean")
    return df.sort_values(["date", "sym_id"]).reset_index(drop=True)


def _model():
    import lightgbm
    return lightgbm.LGBMRegressor(**PARAMS)


def walk_forward(df, target="excess_10", min_train_months=24, retrain_months=3, log=print):
    months = sorted(df.date.dt.to_period("M").unique())
    days = np.array(sorted(df.date.unique()))
    out = []
    for i in range(min_train_months, len(months), retrain_months):
        block = months[i:i + retrain_months]
        test = df[df.date.dt.to_period("M").isin(block)]
        first = np.datetime64(test.date.min())
        cut = days[max(0, np.searchsorted(days, first) - EMBARGO)]
        train = df[(df.date < cut) & df[target].notna()]
        mdl = _model()
        mdl.fit(train[features.FEATURES], train[target].clip(-0.3, 0.3))
        out.append(test.assign(pred=mdl.predict(test[features.FEATURES])))
        log("  walk-forward %s..%s: trained on %d rows to %s" % (block[0], block[-1], len(train),
                                                                 pd.Timestamp(cut).date()))
    return pd.concat(out, ignore_index=True) if out else df.iloc[0:0].assign(pred=[])


def avoid(d):
    """The pack's avoid list: a 3% fall, a bearish marubozu, a shooting star, a hype rise."""
    return (d.ret_1 <= -0.03) | (d.body_1 <= -0.8) | ((d.upper_wick_1 >= 0.6) & (d.ret_5 > 0.05)) | (d.ret_5 > 0.25)


def picks(day, name, k):
    if name == "model_top":
        return day.nlargest(k, "pred")
    if name == "model_top_regime":
        return day.nlargest(k, "pred") if day.nifty_above_ma200.iloc[0] == 1 else day.iloc[0:0]
    if name == "rule_sector_leaders":
        c = day[(day.ind_rel_20 >= 0.02) & (day.ind_rel_chg_5 > 0) & (day.rs_industry_20 > 0)
                & (day.nifty_above_ma200 == 1) & ~avoid(day)]
        return c.nlargest(k, "rs_industry_20")
    if name == "rule_momentum":
        c = day[(day.ma50_over_ma200 > 0) & ~avoid(day)]
        return c.nlargest(k, "rs_nifty_20")
    if name == "all":
        return day
    raise KeyError(name)


COHORT = ["model_top", "model_top_regime", "rule_sector_leaders", "rule_momentum", "all"]


def cohorts(oos, hold=10, k=10, cost=None, use_stop=True):
    cost = charges.cnc_round_trip() if cost is None else cost
    col = ("fwd_stop_%d" if use_stop else "fwd_%d") % hold
    dates = sorted(oos.date.unique())[::hold]
    rows = []
    for d in dates:
        day = oos[(oos.date == d) & oos[col].notna()]
        if day.empty:
            continue
        r = {"date": d}
        for name in COHORT:
            p = picks(day, name, k)
            r[name] = (p[col].mean() - cost) if len(p) else np.nan
            r[name + "_n"] = len(p)
        rows.append(r)
    return pd.DataFrame(rows).set_index("date")


def results_jump(df, hold=20, cost=None):
    cost = charges.cnc_round_trip() if cost is None else cost
    ev = df[(df.days_since_result == 0) & (df.result_ret >= 0.04) & (df.result_turn_mult >= 2)
            & (df.result_close_loc >= 0.75) & (df.nifty_above_ma200 == 1)]
    s = ev.groupby("date")["fwd_%d" % hold].mean() - cost
    return s, len(ev)


def validate(con, data_dir, start=None, end=None, hold=10, k=10, top_n=500, min_train_months=24,
             retrain_months=3, log=print):
    df = dataset(con, start, end, top_n, log)
    if df.empty:
        raise SystemExit("no swing rows")
    target = "excess_%d" % hold
    oos = walk_forward(df, target, min_train_months, retrain_months, log)
    cost = charges.cnc_round_trip()
    out_dir = os.path.join(data_dir, MODEL_DIR_NAME)
    os.makedirs(out_dir, exist_ok=True)
    rep = {"created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "hold": hold, "k": k,
           "universe_top_n": top_n, "cost_round_trip_pct": round(cost * 100, 3),
           "from": str(df.date.min().date()), "to": str(df.date.max().date()),
           "oos_from": str(oos.date.min().date()) if len(oos) else None, "strategies": {}}
    for use_stop in (True, False):
        pf = cohorts(oos, hold, k, cost, use_stop)
        base = stats(pf["all"])
        tag = "stop" if use_stop else "close"
        for name in COHORT:
            st = stats(pf[name])
            st["avg_picks"] = round(float(pf[name + "_n"].mean()), 1) if len(pf) else 0
            rep["strategies"]["%s/%s" % (name, tag)] = dict(
                st, verdict="BASELINE" if name == "all" else verdict(st, base))
        pf.to_csv(os.path.join(out_dir, "cohorts-h%d%s.csv" % (hold, "" if use_stop else "-close")))
    oos_df = df[df.date >= oos.date.min()] if len(oos) else df
    s, n = results_jump(oos_df, 20, cost)
    st = stats(s)
    st["trades"] = int(n)
    st["note"] = "per event day; trades can overlap, so t is optimistic"
    rep["strategies"]["rule_results_jump/h20"] = dict(st, verdict=verdict(st) if st.get("days", 0) >= 120 else
                                                       ("UNPROVEN" if st.get("mean_pct", 0) > 0 else "NO EDGE"))
    if len(oos):
        q = oos.dropna(subset=[target]).copy()
        q["decile"] = q.groupby("date").pred.transform(lambda p: pd.qcut(p.rank(method="first"), 10, labels=False))
        rep["deciles_excess_pct"] = {int(a): round(b * 100, 3) for a, b in q.groupby("decile")[target].mean().items()}
        oos.drop(columns=[c for c in oos.columns if c.startswith("excess_") and c != target]).to_parquet(
            os.path.join(out_dir, "oos-h%d.parquet" % hold), index=False)
    final = _model()
    tr = df[df[target].notna()]
    final.fit(tr[features.FEATURES], tr[target].clip(-0.3, 0.3))
    final.booster_.save_model(os.path.join(out_dir, "model-h%d.txt" % hold))
    g = final.booster_.feature_importance(importance_type="gain")
    rep["importance_gain_pct"] = [(n_, round(v / (g.sum() or 1) * 100, 1))
                                  for n_, v in sorted(zip(features.FEATURES, g), key=lambda x: -x[1])][:15]
    cands = {n_: s_ for n_, s_ in rep["strategies"].items() if not n_.startswith("all/")}
    best = max(cands.items(), key=lambda x: (x[1].get("verdict") == "EDGE", x[1].get("mean_pct") or -9))
    rep["best"] = {"strategy": best[0], "verdict": best[1].get("verdict")}
    with open(os.path.join(out_dir, "report-h%d.json" % hold), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)
    return rep, oos
