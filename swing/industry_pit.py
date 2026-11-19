"""Point-in-time industries for every stock, so industry features never depend on a stock having
survived onto today's NSE lists.

At each month-end M, every stock with 100+ sessions of the last 120 is assigned to the NSE industry
whose basket it moved with most: the highest correlation between the stock's daily returns and the
industry's median daily return over the 120 sessions up to M. The assignment is valid from the first
session after M. Every stock - listed today or delisted, labelled or not - is assigned by the same
rule; stocks too new to have 100 sessions are UNKNOWN, which is something known at the time.

Baskets: NSE's current industry lists define which stocks make up each industry's return series. A
basket of today's members is a mild survivor tilt in the industry *returns*, but no stock's own
assignment depends on whether it survived.

-> warehouse dataset `industry_pit`: valid_from, sym_id, industry, corr
"""
import numpy as np
import pandas as pd

from warehouse import store

WINDOW = 120
MIN_OBS = 100


def build(con, top_n=None, log=print):
    px = con.execute("""SELECT sym_id, date, adj_close FROM eq_daily WHERE series IN ('EQ', 'BE', 'BZ')""").df()
    px["date"] = pd.to_datetime(px.date)
    if top_n:
        keep = {r[0] for r in con.execute("SELECT DISTINCT sym_id FROM liquidity_universe WHERE liq_rank <= ?",
                                          [top_n]).fetchall()}
        px = px[px.sym_id.isin(keep)]
    wide = px.pivot_table(index="date", columns="sym_id", values="adj_close").sort_index()
    ret = wide.pct_change(fill_method=None).clip(-0.5, 0.5)
    lab = con.execute("SELECT symbol, industry FROM industries").df()
    lab = lab[lab.symbol.isin(ret.columns)]
    groups = {ind: list(g.symbol) for ind, g in lab.groupby("industry") if len(g) >= 3}
    log("industry_pit: %d stocks, %d industries, %d sessions" % (ret.shape[1], len(groups), ret.shape[0]))
    dates = ret.index
    month_ends = dates[(dates.to_series().dt.month.shift(-1) != dates.to_series().dt.month).values]
    out = []
    for m in month_ends:
        i = dates.get_loc(m)
        if i < WINDOW or i + 1 >= len(dates):
            continue
        win = ret.iloc[i - WINDOW + 1:i + 1]
        basket = pd.DataFrame({ind: win[s].median(axis=1) for ind, s in groups.items()})
        ok = win.columns[win.notna().sum() >= MIN_OBS]
        if not len(ok):
            continue
        x = win[ok].fillna(0.0)
        zx = (x - x.mean()) / x.std(ddof=0).replace(0, np.nan)
        b = basket.fillna(0.0)
        zb = (b - b.mean()) / b.std(ddof=0).replace(0, np.nan)
        corr = (zx.T.values @ zb.values) / len(win)
        corr = np.nan_to_num(corr, nan=-1.0)
        best = corr.argmax(axis=1)
        valid_from = dates[i + 1]
        cols = list(basket.columns)
        out.append(pd.DataFrame({"valid_from": valid_from, "sym_id": ok, "industry": [cols[j] for j in best],
                                 "corr": corr[np.arange(len(ok)), best]}))
    df = pd.concat(out, ignore_index=True)
    df["valid_from"] = df.valid_from.dt.date
    store.write_table("industry_pit", df)
    # how often the assignment agrees with NSE's label, where one exists (a sanity check, not a target)
    chk = df.merge(lab, left_on="sym_id", right_on="symbol", how="inner")
    agree = float((chk.industry_x == chk.industry_y).mean()) if len(chk) else float("nan")
    log("industry_pit: %d assignments over %d months; agrees with NSE's label %.0f%% of the time" % (
        len(df), df.valid_from.nunique(), agree * 100))
    return df
