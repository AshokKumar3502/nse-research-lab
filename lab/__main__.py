"""Research lab (Phase 4).

  python -m lab refresh              rebuild the cached swing dataset (after a warehouse sync)
  python -m lab test [--id X] [--force]
                                     put new or changed ideas in lab/ideas/ through the Skeptic
  python -m lab show ID              one idea's full review and history
  python -m lab paper                record tonight's signals for paper/live strategies, settle
                                     matured ones, apply promotion / demotion rules
  python -m lab report               leaderboard -> data/lab/leaderboard.md / .json
  python -m lab retire ID --why "..."
  python -m lab nightly [--fetch]    Data Steward (warehouse sync + checks) -> refresh -> test ->
                                     paper -> report. Stops before testing if the data checks fail.
"""
import argparse
import json
import sys

from warehouse import cli as wcli, config, db, quality

from . import backtest, paper, registry, report, skeptic, spec as specmod


def cmd_test(only=None, force=False, log=print):
    specs, errors = specmod.load_ideas()
    for e in errors:
        log("idea rejected before testing: " + e)
    reg = registry.load()
    df = None
    tested = 0
    todo = [s for s in specs if (not only or s["id"] == only) and (force or registry.needs_test(reg, s))]
    for s in todo:                       # count the whole batch first: every idea faces the same bar
        registry.count_trial(reg, s)
    n = reg["trials"]
    for s in todo:
        if df is None:
            df = backtest.load()
        rv = skeptic.review(df, s, n)
        e = registry.record_review(reg, s, rv)
        tested += 1
        log("%-28s %-9s mean %+.3f%%  t %5.2f (hurdle %.2f)  -> %s%s" % (
            s["id"], rv["verdict"], rv["mean_pct"], rv["t"], rv["hurdle"], e["stage"],
            ("  failed: " + ", ".join(rv["failed"])) if rv["failed"] else ""))
        registry.save(reg)
    if not tested:
        log("nothing new to test (%d ideas on file; --force to re-test)" % len(specs))
    return reg


def cmd_paper(con, log=print):
    reg = registry.load()
    from monitor import killswitch
    ks = killswitch.load()
    active = []
    for e in reg["strategies"].values():
        if e["stage"] not in ("paper", "live_small"):
            continue
        why = killswitch.blocked("lab:" + e["spec"]["id"], ks)
        if why:
            log("paper %s: %s - no new signals" % (e["spec"]["id"], why))
        else:
            active.append(e["spec"])
    ledger = paper.load()
    by_id = {e["spec"]["id"]: e["spec"] for e in reg["strategies"].values()}
    ledger = paper.settle(con, ledger, by_id)
    if active:
        ledger = paper.record_signals(con, active, ledger, log)
    else:
        log("paper: no strategy has passed the Skeptic yet")
    paper.save(ledger)
    ps = paper.stats(ledger)
    for sid, to in registry.promote(reg, ps):
        log("promoter: %s -> %s" % (sid, to))
    registry.save(reg)
    return reg, ps


def cmd_report(reg=None, ps=None):
    reg = reg or registry.load()
    ps = ps if ps is not None else paper.stats(paper.load())
    return report.build(reg, ps, report.model_summaries())


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="lab")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("refresh")
    p = sub.add_parser("test")
    p.add_argument("--id")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("id")
    sub.add_parser("paper")
    sub.add_parser("report")
    p = sub.add_parser("retire")
    p.add_argument("id")
    p.add_argument("--why", required=True)
    p = sub.add_parser("nightly")
    p.add_argument("--fetch", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "refresh":
        backtest.refresh(db.connect(read_only=True))
    elif a.cmd == "test":
        cmd_test(a.id, a.force)
        cmd_report()
    elif a.cmd == "show":
        e = registry.load()["strategies"].get(a.id)
        print(json.dumps(e, indent=1, default=str) if e else "no idea %s in the registry" % a.id)
    elif a.cmd == "paper":
        reg, ps = cmd_paper(db.connect(read_only=True))
        cmd_report(reg, ps)
    elif a.cmd == "report":
        doc = cmd_report()
        print(open(config.DATA + "/lab/leaderboard.md", encoding="utf-8").read())
        return 0 if doc else 1
    elif a.cmd == "retire":
        reg = registry.load()
        registry.set_stage(reg, a.id, "retired", a.why)
        registry.save(reg)
    elif a.cmd == "nightly":
        # Data Steward
        rc = wcli.main(["sync"] + (["--fetch"] if a.fetch else []))
        q = quality.check(db.connect(read_only=True))
        if q["status"] == "fail":
            print("DATA STEWARD: quality checks failed; the lab will not test or trade on this data.")
            for c in q["checks"]:
                if c["status"] == "fail":
                    print("  %s: %s" % (c["name"], c["detail"]))
            return 1
        con = db.connect(read_only=True)
        backtest.refresh(con)
        cmd_test()
        reg, ps = cmd_paper(con)
        cmd_report(reg, ps)
        print(open(config.DATA + "/lab/leaderboard.md", encoding="utf-8").read())
        return rc
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
