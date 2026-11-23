"""Paper test of the lab idea `results_jump_10` (PROMISING, not proven: t 2.09 below the 2.89 hurdle).

The rule: on a quarterly-results day, a stock that rises 4%+ on 2x its usual turnover and closes in the top quarter
of the day's range, while NIFTY is above its 200-day average -> buy at 9:30 the next session, hold 10 sessions,
no stop-loss (the lab tested it without one). Each evening this writes that session's matches; the paper book
(papertrade/book.py) tracks them at Rs 50,000 each.

-> data/picks/rjump-YYYY-MM-DD.json
"""
import json
import os

from lab import backtest, paper as lab_paper, spec as lab_spec
from warehouse import config

SPEC_ID = "results_jump_10"
PICKS = os.path.join(config.DATA, "picks")


def run(con, log=print):
    sp = next((s for s in lab_spec.load_ideas()[0] if s["id"] == SPEC_ID), None)
    if sp is None:
        log("results jump: idea %s not found" % SPEC_ID)
        return None
    last, day = lab_paper.today_rows(con)
    hits = backtest.select(day, sp)
    picks = [{"symbol": r.sym_id, "ref_close": round(float(r.adj_close), 2), "stop": None,
              "hold_sessions": int(sp["hold"]), "result_ret": round(float(r.result_ret), 4)}
             for r in hits.sort_values("result_ret", ascending=False).itertuples()]
    doc = {"session": str(last), "rule": sp["name"], "spec": SPEC_ID, "picks": picks}
    os.makedirs(PICKS, exist_ok=True)
    p = os.path.join(PICKS, "rjump-%s.json" % last)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(p + ".tmp", p)
    log("results jump: %d pick(s) after %s%s" % (len(picks), last,
                                                 (": " + ", ".join(x["symbol"] for x in picks)) if picks else ""))
    return doc
