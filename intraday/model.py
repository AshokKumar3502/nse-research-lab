"""Walk-forward validation of the intraday ranker, simple-rule baselines, and one honest verdict.

The model (LightGBM) predicts each F&O stock's return from T to 15:10 in excess of that day's
average stock, from the features in features.py. It is trained only on months before the month it
predicts (expanding window, 5-session embargo), retrained every month, so every number in the
report is out of sample.

Strategies scored on those out-of-sample days, every trade after Zerodha MIS charges + slippage:
  model_long / model_short      top / bottom K by prediction, exit at 15:10 or at the window stop
  model_long_short              both legs together (market-neutral)
  rule_orb_long / _short        baseline: closed in the top (bottom) 30% of the window on 1.5x volume,
                                K highest relative volume, opening-window stop
  all_long                      every stock in the universe (what a random pick earns)

Verdict per strategy:
  EDGE           mean net > 0, t >= 2, positive in >= 2/3 of the years (2+ years), beats the baseline
  UNPROVEN       mean net > 0 but one of those fails
  NO EDGE        mean net <= 0
  TOO LITTLE DATA  fewer than 120 out-of-sample days
"""
import datetime
import json
import os

import numpy as np
import pandas as pd

from . import charges, features, labels

MODEL_DIR_NAME = os.path.join("models", "intraday")
PARAMS = dict(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=200, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1)


def dataset(con, t="09:30", start=None, end=None):
    f = features.build(con, t, start=start, end=end)
    y = labels.build(con, t, start=start, end=end)
    df = f.merge(y, on=["date", "symbol"], how="inner")
    df = df[np.isfinite(df.ret_close)]
    df["date"] = pd.to_datetime(df["date"])
    df["excess"] = df.ret_close - df.groupby("date").ret_close.transform("mean")
    return df.sort_values(["date", "symbol"]).reset_index(drop=True)


def _model():
    try:
        import lightgbm
        return lightgbm.LGBMRegressor(**PARAMS)
    except ImportError:   # pragma: no cover
        from sklearn.ensemble import HistGradientBoostingRegressor
        return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=200)


def walk_forward(df, min_train_months=12, embargo_days=5, log=print):
    """-> df of out-of-sample rows with a `pred` column."""
    months = sorted(df.date.dt.to_period("M").unique())
    if len(months) <= min_train_months:
        return df.iloc[0:0].assign(pred=[])
    days = np.array(sorted(df.date.unique()))
    out = []
    for m in months[min_train_months:]:
        test = df[df.date.dt.to_period("M") == m]
        first = test.date.min()
        i = np.searchsorted(days, np.datetime64(first))
        cut = days[max(0, i - embargo_days)]
        train = df[df.date < cut]
        mdl = _model()
        mdl.fit(train[features.FEATURES], train.excess.clip(-0.1, 0.1))
        out.append(test.assign(pred=mdl.predict(test[features.FEATURES])))
        log("  walk-forward %s: trained on %d rows, predicted %d" % (m, len(train), len(test)))
    return pd.concat(out, ignore_index=True)


def _pick(day, col, k, ascending):
    return day.sort_values(col, ascending=ascending).head(k)


def portfolios(oos, k=5, cost=None):
    """Daily net return of each strategy (one row per day, NaN when it did not trade)."""
    cost = charges.round_trip() if cost is None else cost
    rows = []
    for d, day in oos.groupby("date"):
        r = {"date": d, "all_long": day.ret_close.mean() - cost}
        top, bot = _pick(day, "pred", k, False), _pick(day, "pred", k, True)
        r["model_long_close"] = top.ret_close.mean() - cost
        r["model_long_stop"] = top.ret_long_stop.mean() - cost
        r["model_short_close"] = -bot.ret_close.mean() - cost
        r["model_short_stop"] = bot.ret_short_stop.mean() - cost
        r["model_long_short"] = (r["model_long_stop"] + r["model_short_stop"]) / 2
        up = day[(day.close_loc > 0.7) & (day.rvol > 1.5) & (day.ret_window > 0)]
        dn = day[(day.close_loc < 0.3) & (day.rvol > 1.5) & (day.ret_window < 0)]
        r["rule_orb_long"] = (_pick(up, "rvol", k, False).ret_long_stop.mean() - cost) if len(up) else np.nan
        r["rule_orb_short"] = (_pick(dn, "rvol", k, False).ret_short_stop.mean() - cost) if len(dn) else np.nan
        r["n_universe"] = len(day)
        rows.append(r)
    return pd.DataFrame(rows).set_index("date")


def stats(s):
    s = s.dropna()
    if s.empty:
        return {"days": 0}
    by_year = s.groupby(s.index.year).mean()
    cum = s.cumsum()
    return {
        "days": int(len(s)),
        "mean_pct": round(s.mean() * 100, 4),
        "t": round(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))), 2) if len(s) > 2 and s.std() > 0 else None,
        "win_days_pct": round((s > 0).mean() * 100, 1),
        "sharpe": round(s.mean() / s.std(ddof=1) * np.sqrt(250), 2) if s.std() > 0 else None,
        "max_drawdown_pct": round((cum - cum.cummax()).min() * 100, 2),
        "total_pct": round(s.sum() * 100, 2),
        "years": {str(y): round(v * 100, 3) for y, v in by_year.items()},
    }


BASELINE = {"model_long_close": "all_long", "model_long_stop": "all_long", "rule_orb_long": "all_long",
            "model_short_close": None, "model_short_stop": None, "rule_orb_short": None,
            "model_long_short": None}


def verdict(st, base=None):
    if st.get("days", 0) < 120:
        return "TOO LITTLE DATA"
    if st["mean_pct"] <= 0:
        return "NO EDGE"
    yrs = list(st["years"].values())
    good = (st["t"] or 0) >= 2 and len(yrs) >= 2 and sum(v > 0 for v in yrs) >= len(yrs) * 2 / 3
    if base is not None and base.get("days"):
        good = good and st["mean_pct"] > base["mean_pct"]
    return "EDGE" if good else "UNPROVEN"


def deciles(oos):
    q = oos.copy()
    q["decile"] = q.groupby("date").pred.transform(lambda p: pd.qcut(p.rank(method="first"), 10, labels=False)
                                                   if len(p) >= 10 else np.nan)
    return {int(k): round(v * 100, 4) for k, v in q.groupby("decile").excess.mean().items()}


def importance(df):
    mdl = _model()
    mdl.fit(df[features.FEATURES], df.excess.clip(-0.1, 0.1))
    imp = getattr(mdl, "booster_", None)
    if imp is not None:
        g = imp.feature_importance(importance_type="gain")
        tot = g.sum() or 1
        ranked = sorted(zip(features.FEATURES, g), key=lambda x: -x[1])
        return mdl, [(n, round(v / tot * 100, 1)) for n, v in ranked]
    return mdl, []


def validate(con, data_dir, t="09:30", k=5, start=None, end=None, min_train_months=12, log=print):
    log("intraday: building features and labels at %s ..." % t)
    df = dataset(con, t, start, end)
    if df.empty:
        raise SystemExit("no rows: load minute bars first (python -m warehouse minute)")
    log("intraday: %d stock-days, %d sessions, %s to %s" % (len(df), df.date.nunique(), df.date.min().date(),
                                                         df.date.max().date()))
    oos = walk_forward(df, min_train_months, log=log)
    out_dir = os.path.join(data_dir, MODEL_DIR_NAME)
    os.makedirs(out_dir, exist_ok=True)
    cost = charges.round_trip()
    rep = {"created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "decision_time": t, "k": k,
           "cost_round_trip_pct": round(cost * 100, 4), "rows": int(len(df)), "sessions": int(df.date.nunique()),
           "from": str(df.date.min().date()), "to": str(df.date.max().date()),
           "oos_sessions": int(oos.date.nunique()) if len(oos) else 0, "strategies": {}}
    if len(oos):
        pf = portfolios(oos, k, cost)
        base = stats(pf["all_long"])
        rep["strategies"]["all_long"] = dict(base, verdict="BASELINE")
        for col, b in BASELINE.items():
            st = stats(pf[col])
            rep["strategies"][col] = dict(st, verdict=verdict(st, base if b else None))
        rep["deciles_excess_pct"] = deciles(oos)
        oos.to_parquet(os.path.join(out_dir, "oos-%s.parquet" % t.replace(":", "")), index=False)
        pf.to_csv(os.path.join(out_dir, "daily-%s.csv" % t.replace(":", "")))
    mdl, imp = importance(df)
    rep["importance_gain_pct"] = imp[:15]
    best = max(((n, s) for n, s in rep["strategies"].items() if n != "all_long"),
               key=lambda x: (x[1].get("verdict") == "EDGE", x[1].get("mean_pct", -9)), default=(None, {}))
    rep["best"] = {"strategy": best[0], "verdict": best[1].get("verdict")}
    if hasattr(mdl, "booster_"):
        mdl.booster_.save_model(os.path.join(out_dir, "model-%s.txt" % t.replace(":", "")))
    with open(os.path.join(out_dir, "report-%s.json" % t.replace(":", "")), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)
    return rep
