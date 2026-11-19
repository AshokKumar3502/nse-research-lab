"""Swing engine (Phase 3).

  python -m swing validate [--hold 10] [--k 10] [--from 2017-06-01] [--top-n 500] [--retrain-months 3]
        walk-forward test of the ranker vs the pack's rules, then the portfolio simulation
        -> data/models/swing/report-h10.json, portfolio-h10.json
  python -m swing scan [--capital 1000000]
        after the evening sync: tomorrow's plan -> data/picks/swing-YYYY-MM-DD.json
  python -m swing industries         rebuild point-in-time industries (monthly)
  python -m swing report [--hold 10]
"""
import argparse
import json
import os
import sys

from warehouse import config, db

from . import model, portfolio, scan


def show(rep, port=None):
    print("Swing ranker: hold %d sessions, K=%d, top %d by liquidity, %s to %s (out of sample from %s), "
          "cost %.3f%% a round trip" % (rep["hold"], rep["k"], rep["universe_top_n"], rep["from"], rep["to"],
                                         rep["oos_from"], rep["cost_round_trip_pct"]))
    print("%-30s %6s %8s %6s %6s %7s %8s  %s" % ("strategy", "n", "mean%", "t", "win%", "sharpe", "maxDD%", "verdict"))
    for n, s in rep["strategies"].items():
        if not s.get("days"):
            print("%-30s %6s" % (n, 0))
            continue
        print("%-30s %6d %8.3f %6s %6.1f %7s %8.2f  %s" % (n, s["days"], s["mean_pct"], s["t"], s["win_days_pct"],
                                                         s["sharpe"], s["max_drawdown_pct"], s["verdict"]))
    print("excess 10-day return by prediction decile:", rep.get("deciles_excess_pct"))
    print("top features (gain %):", rep.get("importance_gain_pct", [])[:10])
    print("best:", rep.get("best"))
    if port:
        print("portfolio simulation:", json.dumps(port, indent=1))


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="swing")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("validate")
    p.add_argument("--hold", type=int, default=10)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--top-n", type=int, default=500)
    p.add_argument("--min-train-months", type=int, default=24)
    p.add_argument("--retrain-months", type=int, default=3)
    p = sub.add_parser("scan")
    p.add_argument("--hold", type=int, default=10)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--capital", type=float, default=portfolio.CAPITAL)
    sub.add_parser("industries")
    p = sub.add_parser("report")
    p.add_argument("--hold", type=int, default=10)
    a = ap.parse_args(argv)
    mdir = os.path.join(config.DATA, model.MODEL_DIR_NAME)
    if a.cmd == "validate":
        con = db.connect(read_only=True)
        rep, oos = model.validate(con, config.DATA, a.start, a.end, a.hold, a.k, a.top_n, a.min_train_months,
                                  a.retrain_months)
        eq, trades, nifty = portfolio.simulate(con, oos, hold=a.hold)
        port = portfolio.summary(eq, trades, nifty)
        eq.to_csv(os.path.join(mdir, "equity-h%d.csv" % a.hold))
        trades.to_csv(os.path.join(mdir, "trades-h%d.csv" % a.hold), index=False)
        with open(os.path.join(mdir, "portfolio-h%d.json" % a.hold), "w", encoding="utf-8") as fh:
            json.dump(port, fh, indent=1)
        show(rep, port)
        from . import review
        rv = review.run(con, a.hold)
        print("skeptic review: %s%s (mean %+.2f%% a period, t %.2f; offsets %s)" % (
            rv["verdict"], ("  problems: " + ", ".join(rv["problems"])) if rv["problems"] else "",
            rv["mean_pct"], rv["t_offset0"], rv["offsets_pct"]))
        return 0
    if a.cmd == "scan":
        con = db.connect(read_only=True)
        doc = scan.run(con, a.hold, a.k, a.capital)
        print("%s  %s  regime exposure %.0f%%" % (doc["session"], doc["status"], doc["regime"]["exposure"] * 100))
        for p_ in doc["picks"]:
            print("%-12s %-32s score %6.2f  close %10.2f  stop %10.2f (%5.1f%%)  %5d sh  Rs %s" % (
                p_["symbol"], p_["industry"][:32], p_["score_pct"], p_["ref_close"], p_["stop"], p_["stop_pct"],
                p_["shares"], format(p_["value"], ",")))
        print("rules today:", doc["rules_today"])
        return 0
    if a.cmd == "industries":
        from . import industry_pit
        industry_pit.build(db.connect(read_only=True), top_n=600)
        return 0
    if a.cmd == "report":
        with open(os.path.join(mdir, "report-h%d.json" % a.hold), encoding="utf-8") as fh:
            rep = json.load(fh)
        port = None
        pp = os.path.join(mdir, "portfolio-h%d.json" % a.hold)
        if os.path.exists(pp):
            with open(pp, encoding="utf-8") as fh:
                port = json.load(fh)
        show(rep, port)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
