"""What each strategy's backtest says to expect per period: mean, spread, worst drawdown, win rate.

  intraday_long / intraday_short   data/models/intraday/daily-0930.csv (model_long_stop / model_short_stop)
  swing_model                      data/models/swing/cohorts-h10.csv (model_top), per rebalance
  lab:<id>                         the Skeptic's review in data/lab/registry.json, per period
"""
import json
import os

import numpy as np
import pandas as pd

from warehouse import config


def _from_series(s):
    s = pd.Series(s).dropna()
    if s.empty:
        return None
    c = s.cumsum()
    return {"mean": float(s.mean()), "sd": float(s.std(ddof=1)) if len(s) > 1 else None,
            "dd": float((c - np.maximum.accumulate(np.concatenate([[0.0], c.values]))[1:]).min()),
            "win": float((s > 0).mean()), "n": int(len(s))}


def load():
    out = {}
    p = os.path.join(config.DATA, "models", "intraday", "daily-0930.csv")
    if os.path.exists(p):
        d = pd.read_csv(p)
        for side, col in (("long", "model_long_stop"), ("short", "model_short_stop")):
            if col in d:
                out["intraday_" + side] = _from_series(d[col])
    p = os.path.join(config.DATA, "models", "swing", "cohorts-h10.csv")
    if os.path.exists(p):
        d = pd.read_csv(p)
        if "model_top" in d:
            out["swing_model"] = _from_series(d["model_top"])
    p = os.path.join(config.DATA, "lab", "registry.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            reg = json.load(fh)
        for sid, e in reg.get("strategies", {}).items():
            rv = e.get("review") or {}
            if rv.get("mean_pct") is not None:
                out["lab:" + sid] = {"mean": rv["mean_pct"] / 100.0, "sd": None, "dd": rv.get("bt_max_dd"),
                                     "win": (rv.get("win_pct") or 0) / 100.0 or None, "n": rv.get("periods"),
                                     "stage": e.get("stage")}
    return {k: v for k, v in out.items() if v}
