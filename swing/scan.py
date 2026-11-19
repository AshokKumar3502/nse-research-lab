"""Evening swing scan (after the warehouse sync, ~18:30 IST): ranks the liquid universe with the saved
model and writes data/picks/swing-YYYY-MM-DD.json with a full plan per name: entry at tomorrow's
open (reference: today's close), the 2-ATR stop, shares for the given capital under the portfolio
rules, the industry, the regime exposure, and the walk-forward verdict.

Promotion gate: unless the validation report gave the model an EDGE verdict, the file says PAPER
ONLY. The proven rules (sector leaders, results jump) are listed beside the model so they can be
compared live. Nothing here places an order.
"""
import datetime
import json
import os

import pandas as pd

from warehouse import config

from . import features, model, portfolio


TESTED = "model_top/close"
PER_CAPITAL = 100000.0          # sizes are per Rs 1 lakh of capital (your own capital never goes on the site)


def run(con, hold=10, k=10, capital=portfolio.CAPITAL, log=print):
    mdir = os.path.join(config.DATA, model.MODEL_DIR_NAME)
    mpath = os.path.join(mdir, "model-h%d.txt" % hold)
    if not os.path.exists(mpath):
        raise SystemExit("no swing model: run python -m swing validate first")
    with open(os.path.join(mdir, "report-h%d.json" % hold), encoding="utf-8") as fh:
        rep = json.load(fh)
    last = con.execute("SELECT max(date) FROM calendar").fetchone()[0]
    start = last - datetime.timedelta(days=10)
    f = features.build(con, start=start, end=last)
    day = f[f.date == pd.Timestamp(last).date()].copy() if not f.empty else f
    if day.empty:
        day = f[pd.to_datetime(f.date) == pd.Timestamp(last)].copy()
    import lightgbm
    day["pred"] = lightgbm.Booster(model_file=mpath).predict(day[features.FEATURES])
    expo = portfolio.exposure(day.iloc[0])
    slots = int(round(k * expo))
    held = {}
    plan = []
    per = PER_CAPITAL / max(k, 1)            # equal weight: the tested set-up holds k names
    for r in day[day.pred > 0].sort_values("pred", ascending=False).itertuples():
        if len(plan) >= max(slots, 0):
            break
        if held.get(r.industry, 0) >= portfolio.MAX_PER_INDUSTRY and r.industry != "UNKNOWN":
            continue
        held[r.industry] = held.get(r.industry, 0) + 1
        stop = r.adj_close * (1 - portfolio.STOP_ATR * r.atr_pct)
        qty = int(per // r.adj_close)
        plan.append({"symbol": r.sym_id, "industry": r.industry, "score_pct": round(float(r.pred) * 100, 2),
                     "ref_close": round(float(r.adj_close), 2), "stop": round(float(stop), 2),
                     "stop_pct": round(-portfolio.STOP_ATR * float(r.atr_pct) * 100, 2), "use_stop": False,
                     "shares_per_lakh": qty, "value_per_lakh": round(qty * float(r.adj_close)),
                     "hold_sessions": hold, "ret_20_pct": round(float(r.ret_20) * 100, 1),
                     "rs_industry_20_pct": round(float(r.rs_industry_20) * 100, 1) if pd.notna(r.rs_industry_20) else None})
    rules = {"sector_leaders": [x for x in model.picks(day.assign(pred=day.pred), "rule_sector_leaders", 5).sym_id],
             "results_jump": list(day[(day.days_since_result == 0) & (day.result_ret >= 0.04)
                                      & (day.result_turn_mult >= 2) & (day.result_close_loc >= 0.75)
                                      & (day.nifty_above_ma200 == 1)].sym_id)}
    # the tested rule that passed: top k, buy at the next open, sell at the close of the hold-th session
    strat = rep["strategies"].get(TESTED, {})
    rv_path = os.path.join(mdir, "review-h%d.json" % hold)
    review = {}
    if os.path.exists(rv_path):
        with open(rv_path, encoding="utf-8") as fh:
            review = json.load(fh)
    # validated only when the walk-forward found an edge AND the skeptic review kept it
    validated = strat.get("verdict") == "EDGE" and review.get("verdict") == "KEEP"
    doc = {"session": str(last), "made_at": datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST"),
           "status": "VALIDATED" if validated else (
               "PAPER ONLY: skeptic review %s (%s)" % (review.get("verdict", "not run"), ", ".join(review.get("problems", [])))
               if strat.get("verdict") == "EDGE" else "PAPER ONLY: the walk-forward test has not shown an edge"),
           "review": {k: review.get(k) for k in ("verdict", "problems", "mean_pct", "t_offset0")},
           "regime": {"nifty_above_ma200": bool(day.nifty_above_ma200.iloc[0] == 1),
                      "breadth_ma50_pct": round(float(day.breadth_ma50.iloc[0]) * 100, 1), "exposure": expo},
           "model_backtest": {k_: strat.get(k_) for k_ in ("mean_pct", "t", "win_days_pct", "verdict", "years")},
           "tested_rule": "Buy at the next open, sell at the close of the %dth session. The 2-ATR stop is "
                          "shown for reference only: in the test it lowered results (%+.2f%% vs %+.2f%% a period)." % (
                              hold, (rep["strategies"].get("model_top/stop") or {}).get("mean_pct") or 0,
                              strat.get("mean_pct") or 0),
           "per_capital": PER_CAPITAL, "picks": plan, "rules_today": rules, "universe": int(len(day))}
    from monitor import killswitch
    why = killswitch.blocked("swing_model")
    if why:
        doc["blocked"] = why
        doc["picks"] = []
        doc["status"] = why
        log("scan: swing_model is %s - no picks published" % why)
    out = os.path.join(config.DATA, "picks")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "swing-%s.json" % last)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    doc["path"] = path
    return doc
