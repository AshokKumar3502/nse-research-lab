"""The Reporter: data/lab/leaderboard.json (for the tracker site) and leaderboard.md (for people and
for the Claude agents that propose and review ideas)."""
import datetime
import json
import os

from warehouse import config

from . import registry

ORDER = {"live_small": 0, "paper": 1, "research": 2, "rejected": 3, "retired": 4}


def build(reg, paper_stats, model_reports=None):
    rows = []
    for sid, e in reg["strategies"].items():
        rv, ps = e.get("review") or {}, paper_stats.get(sid) or {}
        rows.append({"id": sid, "name": e["spec"]["name"], "author": e["spec"].get("author"), "stage": e["stage"],
                     "verdict": rv.get("verdict"), "mean_pct": rv.get("mean_pct"), "t": rv.get("t"),
                     "hurdle": rv.get("hurdle"), "periods": rv.get("periods"), "trades": rv.get("trades"),
                     "failed": rv.get("failed"), "paper_n": ps.get("n", 0),
                     "paper_mean_pct": round(ps["mean"] * 100, 3) if ps.get("n") else None,
                     "paper_open": ps.get("open", 0), "hold": e["spec"]["hold"],
                     "hypothesis": e["spec"]["hypothesis"]})
    rows.sort(key=lambda r: (ORDER.get(r["stage"], 9), -(r["t"] or -99)))
    doc = {"built": datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST"), "trials": reg["trials"],
           "rows": rows, "models": model_reports or {}}
    out = os.path.join(config.DATA, "lab")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "leaderboard.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    md = ["# Research lab leaderboard", "",
          "Built %s. %d ideas tried in total, so the Skeptic's t hurdle is now %.2f." % (
              doc["built"], reg["trials"], __import__("lab.skeptic", fromlist=["t_hurdle"]).t_hurdle(reg["trials"])),
          "", "| stage | id | verdict | mean %/period | t | periods | paper n | paper mean % | failed checks |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["stage"], r["id"], r["verdict"], r["mean_pct"], r["t"], r["periods"], r["paper_n"],
            r["paper_mean_pct"] if r["paper_mean_pct"] is not None else "-", ", ".join(r["failed"] or []) or "-"))
    if model_reports:
        md += ["", "## Models (walk-forward)", ""]
        for k, v in model_reports.items():
            md.append("- **%s**: best %s" % (k, v))
    md += ["", "Past results are not predictions. Paper research only; nothing here places orders."]
    with open(os.path.join(out, "leaderboard.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    return doc


def model_summaries():
    out = {}
    for name, path in (("intraday 09:30", os.path.join(config.DATA, "models", "intraday", "report-0930.json")),
                       ("swing 10d", os.path.join(config.DATA, "models", "swing", "report-h10.json")),
                       ("swing 20d", os.path.join(config.DATA, "models", "swing", "report-h20.json"))):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                out[name] = json.load(fh).get("best")
    return out
