"""Intraday engine (Phase 2).

  python -m intraday validate [--t 09:30] [--k 5] [--from 2022-01-01] [--min-train-months 12]
        walk-forward test of the ranker and the rule baselines -> data/models/intraday/report-0930.json
  python -m intraday live [--t 09:30] [--k 5]
        after T on a trading day: today's top K long / short -> data/picks/intraday-YYYY-MM-DD.json
  python -m intraday report [--t 09:30]
        print the last validation report
"""
import argparse
import json
import os
import sys

from warehouse import config, db

from . import live, model


def show(rep):
    print("Intraday ranker at %s, %s to %s, %d sessions (%d out of sample), cost %.3f%% a round trip, K=%d" % (
        rep["decision_time"], rep["from"], rep["to"], rep["sessions"], rep["oos_sessions"],
        rep["cost_round_trip_pct"], rep["k"]))
    print("%-20s %6s %9s %6s %7s %7s %8s  %s" % ("strategy", "days", "mean%", "t", "win%", "sharpe", "maxDD%", "verdict"))
    for n, s in rep["strategies"].items():
        if not s.get("days"):
            print("%-20s %6s" % (n, 0))
            continue
        print("%-20s %6d %9.4f %6s %7.1f %7s %8.2f  %s" % (n, s["days"], s["mean_pct"], s["t"], s["win_days_pct"],
                                                        s["sharpe"], s["max_drawdown_pct"], s["verdict"]))
    if rep.get("deciles_excess_pct"):
        print("excess return by prediction decile (0 = lowest):", rep["deciles_excess_pct"])
    print("top features (gain %):", rep.get("importance_gain_pct", [])[:10])
    print("best:", rep.get("best"))


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="intraday")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("validate")
    p.add_argument("--t", default="09:30")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--min-train-months", type=int, default=12)
    p = sub.add_parser("live")
    p.add_argument("--t", default="09:30")
    p.add_argument("--k", type=int, default=5)
    p = sub.add_parser("report")
    p.add_argument("--t", default="09:30")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        con = db.connect(read_only=True)
        rep = model.validate(con, config.DATA, a.t, a.k, a.start, a.end, a.min_train_months)
        show(rep)
        return 0
    if a.cmd == "live":
        con = db.connect(read_only=True)
        doc = live.score(con, a.t, a.k)
        print(json.dumps({k: v for k, v in doc.items() if k not in ("long", "short")}, indent=1))
        for side in ("long", "short"):
            for r in doc[side]:
                print("%-5s %-12s score %7.3f  ref %10.2f  stop %10.2f  [%s]" % (
                    side, r["symbol"], r["score"], r["ref_price"], r["stop"], r["verdict"]))
        return 0
    if a.cmd == "report":
        with open(os.path.join(config.DATA, model.MODEL_DIR_NAME, "report-%s.json" % a.t.replace(":", "")),
                  encoding="utf-8") as fh:
            show(json.load(fh))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
