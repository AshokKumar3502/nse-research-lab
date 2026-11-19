"""Is a strategy behaving like its backtest? Each check compares live results per period with the
backtest's own per-period mean and spread.

  WARMING UP  fewer than MIN_N live periods: no judgement yet
  z-test      live mean vs backtest mean: AMBER below -2, RED below -2.5 with 20+ periods
  CUSUM       one-sided cumulative sum of standardised shortfalls (k = 0.5, h = 5): RED when it
              crosses, i.e. a sustained shift down, even while the overall mean still looks fine
  drawdown    live cumulative drawdown deeper than the backtest's worst: AMBER; 1.5x: RED
  streak      a losing run longer than the backtest's win rate makes 99% unlikely: AMBER
Overall: the worst of these.
"""
import math

import numpy as np

MIN_N = 10
CUSUM_K = 0.5
CUSUM_H = 5.0
LEVEL = {"GREEN": 0, "WARMING UP": 0, "AMBER": 1, "RED": 2}


def cusum_low(x, mu, sd, k=CUSUM_K):
    """Lowest value of the downward CUSUM S_t = min(0, S_{t-1} + (x_t - mu)/sd + k)."""
    s, low = 0.0, 0.0
    for v in x:
        s = min(0.0, s + (v - mu) / sd + k)
        low = min(low, s)
    return low, s


def max_drawdown(x):
    c = np.cumsum(x)
    return float((c - np.maximum.accumulate(np.concatenate([[0.0], c]))[1:]).min()) if len(c) else 0.0


def longest_losing_run(x):
    best = cur = 0
    for v in x:
        cur = cur + 1 if v <= 0 else 0
        best = max(best, cur)
    return best


def streak_limit(win_rate, n, p=0.01):
    """Losing-run length that a strategy with this win rate exceeds in n periods with probability < p."""
    q = min(max(1 - win_rate, 1e-6), 1 - 1e-6)
    return int(math.ceil(math.log(p / max(n, 1)) / math.log(q)))


def assess(live, bt_mean, bt_sd, bt_dd=None, bt_win=None):
    """live: per-period net returns, oldest first (fractions). -> dict with status and reasons."""
    x = np.asarray([v for v in live if v == v], dtype=float)
    n = len(x)
    out = {"n": n, "live_mean_pct": round(float(x.mean()) * 100, 3) if n else None,
           "bt_mean_pct": round(bt_mean * 100, 3) if bt_mean is not None else None,
           "live_total_pct": round(float(x.sum()) * 100, 2) if n else 0.0,
           "live_win_pct": round(float((x > 0).mean()) * 100, 1) if n else None,
           "live_max_dd_pct": round(max_drawdown(x) * 100, 2), "status": "WARMING UP", "reasons": []}
    if n < MIN_N or bt_mean is None:
        out["reasons"].append("%d of %d periods needed before judging" % (n, MIN_N) if n < MIN_N
                              else "no backtest expectation on file")
        return out
    sd = bt_sd if bt_sd and bt_sd > 0 else float(x.std(ddof=1)) or 1e-9
    status = "GREEN"

    def worse(s):
        nonlocal status
        if LEVEL[s] > LEVEL[status]:
            status = s

    z = (x.mean() - bt_mean) / (sd / math.sqrt(n))
    out["z"] = round(float(z), 2)
    if z < -2.5 and n >= 20:
        worse("RED")
        out["reasons"].append("live mean %.2f%% is far below the backtest's %.2f%% (z %.1f)" % (
            x.mean() * 100, bt_mean * 100, z))
    elif z < -2:
        worse("AMBER")
        out["reasons"].append("live mean below the backtest (z %.1f)" % z)
    low, now = cusum_low(x, bt_mean, sd)
    out["cusum_low"] = round(low, 2)
    if low <= -CUSUM_H:
        worse("RED")
        out["reasons"].append("sustained shortfall vs the backtest (CUSUM %.1f, limit -%.0f)" % (low, CUSUM_H))
    dd = max_drawdown(x)
    if bt_dd is not None and bt_dd < 0:
        if dd < 1.5 * bt_dd:
            worse("RED")
            out["reasons"].append("drawdown %.1f%% is past 1.5x the backtest's worst (%.1f%%)" % (dd * 100, bt_dd * 100))
        elif dd < bt_dd:
            worse("AMBER")
            out["reasons"].append("drawdown %.1f%% is past the backtest's worst (%.1f%%)" % (dd * 100, bt_dd * 100))
    if bt_win:
        lim = streak_limit(bt_win, n)
        run = longest_losing_run(x)
        out["losing_run"] = run
        if run >= lim:
            worse("AMBER")
            out["reasons"].append("%d losing periods in a row (99%% limit %d)" % (run, lim))
    out["status"] = status
    if status == "GREEN":
        out["reasons"].append("in line with the backtest")
    return out
