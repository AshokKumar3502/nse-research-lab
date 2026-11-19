"""The Skeptic: tries to break every idea before it gets near money. Each check is pass / fail with a
reason; an idea is PASS only when every critical check passes.

  profitable      mean net return per period > 0 after charges                         critical
  significance    t >= the multiple-testing hurdle: the more ideas the lab has ever    critical
                  tried, the higher the bar (Bonferroni on N trials, two-sided 5%)
  halves          positive in both the first and the second half of the history       critical
  years           positive in at least 2/3 of the calendar years (3+ years)           critical
  cost_x2         still positive with double charges and slippage                     critical
  random          beats picking the same number of stocks at random on the same days  critical
                  (permutation test, p < 0.05)
  robust          positive in >= 70% of +-20% perturbations of every numeric param    critical
  concentration   the best 5% of trades make less than 60% of the total profit        warning
  capacity        median pick turnover >= Rs 20 crore a day (a Rs 1 lakh trade is      warning
                  well under 0.1% of a day's volume)
  sample          at least 60 periods and 150 trades                                  critical

Verdicts: PASS (all critical pass), PROMISING (profitable and t >= 2 but something critical failed),
FAIL.
"""
import itertools
import math
from statistics import NormalDist

import numpy as np

from swing import charges

from . import backtest


def t_hurdle(n_trials, alpha=0.05):
    """Two-sided Bonferroni threshold for n_trials independent tries (1 trial -> 1.96)."""
    n = max(1, int(n_trials))
    return round(NormalDist().inv_cdf(1 - alpha / (2 * n)), 2)


def _t(s):
    s = s.dropna()
    if len(s) < 3 or s.std(ddof=1) == 0:
        return 0.0
    return float(s.mean() / (s.std(ddof=1) / math.sqrt(len(s))))


def variants(params, rel=0.2):
    """Every combination of each numeric param at x(1-rel), x1, x(1+rel), excluding the original."""
    keys = [k for k, v in params.items() if isinstance(v, (int, float)) and v != 0]
    if not keys:
        return []
    grids = [[params[k] * (1 - rel), params[k], params[k] * (1 + rel)] for k in keys]
    out = []
    for combo in itertools.product(*grids):
        p = dict(params, **dict(zip(keys, combo)))
        if p != params:
            out.append(p)
    return out[:80]


def review(df, spec, n_trials, n_random=500):
    cost = charges.cnc_round_trip()
    series, trades = backtest.evaluate(df, spec, cost=cost)
    checks = []

    def add(name, ok, detail, critical=True):
        checks.append({"check": name, "pass": bool(ok), "critical": critical, "detail": detail})

    n_p, n_t = len(series), len(trades)
    mean = float(series.mean()) if n_p else 0.0
    t = _t(series)
    hurdle = t_hurdle(n_trials)
    add("sample", n_p >= 60 and n_t >= 150, "%d periods, %d trades" % (n_p, n_t))
    add("profitable", mean > 0, "mean %.3f%% a period after %.2f%% costs" % (mean * 100, cost * 100))
    add("significance", t >= hurdle, "t %.2f vs hurdle %.2f (%d ideas tried so far)" % (t, hurdle, n_trials))
    if n_p >= 4:
        mid = series.index[len(series) // 2]
        a, b = series[series.index < mid].mean(), series[series.index >= mid].mean()
        add("halves", a > 0 and b > 0, "first half %.3f%%, second half %.3f%% (split %s)" % (
            a * 100, b * 100, str(mid)[:10]))
    else:
        add("halves", False, "too few periods")
    yrs = series.groupby(series.index.year).mean() if n_p else series
    add("years", len(yrs) >= 3 and (yrs > 0).sum() >= len(yrs) * 2 / 3,
        "%d of %d years positive: %s" % ((yrs > 0).sum(), len(yrs),
                                          ", ".join("%d %+.2f%%" % (y, v * 100) for y, v in yrs.items())))
    s2, _ = backtest.evaluate(df, spec, cost=2 * cost)
    add("cost_x2", n_p and s2.mean() > 0, "mean %.3f%% with double costs" % ((s2.mean() if n_p else 0) * 100))
    if n_t:
        null = backtest.random_baseline(df, spec, trades, n_random)
        actual = float(trades.net.mean())
        p = float((null >= actual).mean())
        add("random", p < 0.05, "per trade %.3f%% vs random %.3f%% (p = %.3f)" % (actual * 100, null.mean() * 100, p))
    else:
        add("random", False, "no trades")
    vs = variants(spec.get("params", {}))
    if vs:
        means = [backtest.evaluate(df, spec, params=v, cost=cost)[0].mean() for v in vs]
        good = sum(1 for m in means if m is not None and np.isfinite(m) and m > 0)
        add("robust", good >= 0.7 * len(vs), "%d of %d param variants positive" % (good, len(vs)))
    else:
        add("robust", True, "no numeric params to perturb", critical=False)
    if n_t and trades.net.sum() > 0:
        top = trades.net.sort_values(ascending=False)
        share = top.head(max(1, len(top) // 20)).sum() / trades.net.sum()
        add("concentration", share < 0.6, "best 5%% of trades = %.0f%% of profit" % (share * 100), critical=False)
    if n_t:
        med = float(trades.turnover.median())
        add("capacity", med >= 2e8, "median pick turnover Rs %.1f crore a day" % (med / 1e7), critical=False)
    crit_ok = all(c["pass"] for c in checks if c["critical"])
    verdict = "PASS" if crit_ok else ("PROMISING" if mean > 0 and t >= 2 else "FAIL")
    by_year = {str(y): round(float(v) * 100, 3) for y, v in yrs.items()} if n_p else {}
    cum = series.cumsum()
    bt_dd = float((cum - cum.cummax()).min()) if n_p else 0.0
    return {"verdict": verdict, "periods": n_p, "trades": n_t, "mean_pct": round(mean * 100, 4), "t": round(t, 2),
            "bt_max_dd": round(bt_dd, 4),
            "hurdle": hurdle, "win_pct": round(float((trades.net > 0).mean()) * 100, 1) if n_t else None,
            "by_year_pct": by_year, "checks": checks,
            "failed": [c["check"] for c in checks if c["critical"] and not c["pass"]]}
