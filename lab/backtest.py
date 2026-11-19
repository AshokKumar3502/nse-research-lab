"""The lab's backtester: one spec against the cached swing dataset (features + forward labels for the
point-in-time top-500 universe, 2017 onward), every trade after Zerodha delivery charges + slippage.

The dataset is built once (`python -m lab refresh`, a few minutes) into data/lab/dataset.parquet and
reused for every idea and every robustness variant, so testing a new idea takes seconds.
"""
import os

import numpy as np
import pandas as pd

from swing import charges, model as swing_model
from warehouse import config

from . import spec as specmod

DATASET = os.path.join(config.DATA, "lab", "dataset.parquet")


def refresh(con, top_n=500, log=print):
    df = swing_model.dataset(con, None, None, top_n, log)
    df["avoid"] = swing_model.avoid(df)
    os.makedirs(os.path.dirname(DATASET), exist_ok=True)
    df.to_parquet(DATASET, index=False)
    log("lab dataset: %d rows, %d sessions, %s to %s -> %s" % (
        len(df), df.date.nunique(), df.date.min().date(), df.date.max().date(), DATASET))
    return df


def load():
    if not os.path.exists(DATASET):
        raise SystemExit("no lab dataset: run python -m lab refresh")
    return pd.read_parquet(DATASET)


def label_col(spec):
    return ("fwd_stop_%d" if spec.get("stop_atr") else "fwd_%d") % int(spec["hold"])


def select(df, spec, params=None):
    """Rows passing the spec's filter (params override the spec's own)."""
    expr = specmod.check_expr(specmod.render(spec["filter"], dict(spec["params"], **(params or {}))))
    mask = df.eval(expr, engine="python")
    return df[mask.fillna(False).astype(bool)]


def evaluate(df, spec, params=None, cost=None, dates=None):
    """-> (series of net return per rebalance/event date, trades DataFrame)."""
    cost = charges.cnc_round_trip() if cost is None else cost
    col = label_col(spec)
    base = df[df[col].notna()]
    hits = select(base, spec, params)
    if spec["mode"] == "event":
        trades = hits[["date", "sym_id", "industry", col, "turnover"]].rename(columns={col: "gross"})
    else:
        all_dates = np.array(sorted(base.date.unique())) if dates is None else dates
        keep = set(all_dates[::int(spec["hold"])])
        hits = hits[hits.date.isin(keep)]
        key = spec["rank"].lstrip("-")
        hits = hits.sort_values(["date", key], ascending=[True, not spec["rank"].startswith("-")])
        trades = hits.groupby("date").head(int(spec["k"]))[["date", "sym_id", "industry", col, "turnover"]] \
            .rename(columns={col: "gross"})
    trades = trades.assign(net=trades.gross - cost)
    series = trades.groupby("date").net.mean()
    return series, trades.reset_index(drop=True)


def random_baseline(df, spec, trades, n_iter=500, seed=7):
    """Mean net return of picking the same number of stocks at random from the same days' universe:
    -> array of n_iter simulated means (the null distribution for the permutation test)."""
    col = label_col(spec)
    cost = charges.cnc_round_trip()
    pool = df[df[col].notna() & df.date.isin(trades.date.unique())]
    groups = {d: g[col].to_numpy() for d, g in pool.groupby("date")}
    counts = trades.groupby("date").size()
    rng = np.random.default_rng(seed)
    out = np.empty(n_iter)
    for i in range(n_iter):
        tot, n = 0.0, 0
        for d, c in counts.items():
            arr = groups.get(d)
            if arr is None or len(arr) == 0:
                continue
            pick = rng.choice(arr, size=min(c, len(arr)), replace=False)
            tot += pick.sum()
            n += len(pick)
        out[i] = tot / max(n, 1) - cost
    return out
