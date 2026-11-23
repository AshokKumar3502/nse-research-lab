"""Paper test of 12-1 month momentum (lab: PROMISING, t 2.60 below the 3.01 hurdle; best 5% of trades = most of
the profit).

The rule: every 20 sessions, among the 500 most liquid NSE stocks, take the stocks whose return from 252 to 21
sessions ago (12 months, skipping the latest month) is above 20%, whose 50-day average is above the 200-day, and
which are not flagged to avoid; buy the 10 strongest at 9:30 the next session, hold 20 sessions, no stop-loss.
The paper book (papertrade/book.py) tracks them at Rs 50,000 each.

-> data/picks/mom-YYYY-MM-DD.json
"""
import datetime
import glob
import json
import os

from lab import paper as lab_paper
from warehouse import config

PICKS = os.path.join(config.DATA, "picks")
HOLD, K, MIN_MOM = 20, 10, 0.20


def mom12_1(con, syms, last):
    """{sym: close 21 sessions ago / close 252 sessions ago - 1} on adjusted prices."""
    df = con.execute("""SELECT sym_id, date, adj_close FROM eq_daily
                        WHERE date > ? AND date <= ? AND sym_id IN (SELECT unnest(?)) ORDER BY sym_id, date""",
                     [last - datetime.timedelta(days=420), last, list(syms)]).df()
    out = {}
    for s, g in df.groupby("sym_id"):
        c = g.adj_close.to_numpy()
        if len(c) >= 253 and c[-253] > 0:
            out[s] = float(c[-22] / c[-253] - 1)
    return out


def _sessions_since(con, day, last):
    return con.execute("SELECT count(*) FROM calendar WHERE date > ? AND date <= ?", [day, last]).fetchone()[0]


def run(con, log=print):
    last, day = lab_paper.today_rows(con)
    prev = sorted(glob.glob(os.path.join(PICKS, "mom-*.json")))
    if prev:
        pday = datetime.date.fromisoformat(os.path.basename(prev[-1])[4:14])
        n = _sessions_since(con, pday, last)
        if pday >= last or n < HOLD:
            log("momentum: next rebalance in %d session(s) (last picks %s)" % (max(HOLD - n, 0), pday))
            return None
    mom = mom12_1(con, day.sym_id.unique(), last)
    day = day.assign(mom12_1=day.sym_id.map(mom))
    hits = day[(day.mom12_1 > MIN_MOM) & (day.ma50_over_ma200 > 0) & (~day.avoid.astype(bool))]
    hits = hits.sort_values("mom12_1", ascending=False).head(K)
    picks = [{"symbol": r.sym_id, "ref_close": round(float(r.adj_close), 2), "stop": None, "hold_sessions": HOLD,
              "mom12_1": round(float(r.mom12_1), 4)} for r in hits.itertuples()]
    doc = {"session": str(last), "rule": "12-1 month momentum, top %d, hold %d sessions" % (K, HOLD), "picks": picks}
    os.makedirs(PICKS, exist_ok=True)
    p = os.path.join(PICKS, "mom-%s.json" % last)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(p + ".tmp", p)
    log("momentum: %d pick(s) after %s: %s" % (len(picks), last, ", ".join(x["symbol"] for x in picks)))
    return doc
