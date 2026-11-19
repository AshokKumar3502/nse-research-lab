"""Live monitoring (Phase 5).

  python -m monitor run                 data health -> settle every pick -> drift checks per strategy ->
                                        auto kill on RED -> status.md / status.json / dashboard.html ->
                                        phone alert for anything new (Telegram, if configured)
  python -m monitor status              print the last status
  python -m monitor kill ID --why "..." turn a strategy off (ids: intraday_long, intraday_short,
                                        swing_model, lab:<spec id>)
  python -m monitor revive ID --why "..."
  python -m monitor halt --why "..." / python -m monitor unhalt --why "..."

Phone alerts use the same TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID as nse-picks-engine/phone_alerts.py;
without them alerts are only written to data/monitor/alerts.jsonl. Nothing here places an order.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

import pandas as pd

from warehouse import config, db, quality

from . import dashboard, drift, expect, killswitch, settle

OUT = os.path.join(config.DATA, "monitor")
LEGACY = os.path.join(os.path.dirname(config.ROOT), "nse-picks-tracker", "data", "picks.json")


def _now():
    return datetime.datetime.now(config.IST)


def send_phone(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok, data, timeout=20).read()
        return True
    except OSError:
        return False


def legacy_scoreboard():
    try:
        with open(LEGACY, encoding="utf-8") as fh:
            sb = json.load(fh).get("scoreboard", {})
        return [{k: r.get(k) for k in ("rule", "closed", "win_pct", "avg_net_pct", "total_net_rs")}
                for r in sb.get("rows", [])]
    except (OSError, ValueError):
        return []


def per_period(live, strategy):
    g = live[live.strategy == strategy].sort_values("period")
    if strategy.startswith("lab:"):
        return g.net.tolist()                       # per trade
    return g.groupby("period").net.mean().tolist()  # per day / per rebalance


def run(log=print):
    os.makedirs(OUT, exist_ok=True)
    ks = killswitch.load()
    events = []
    # 1. data health
    con = db.connect(read_only=True)
    q = quality.check(con)
    if q["status"] == "fail":
        bad = [c["name"] for c in q["checks"] if c["status"] == "fail"]
        if not ks.get("halt", {}).get("on"):
            killswitch.halt(ks, True, "data checks failed: " + ", ".join(bad))
            events.append(("RED", "ALL", "HALTED: data checks failed (%s)" % ", ".join(bad)))
    elif ks.get("halt", {}).get("on") and ks["halt"].get("auto"):
        killswitch.halt(ks, False, "data healthy again")
        events.append(("GREEN", "ALL", "halt lifted: data checks pass again"))
    # 2. settle every published pick
    live = settle.build(con)
    errors = live[live.strategy == "_errors"]
    live = live[live.strategy != "_errors"]
    for e in errors.itertuples():
        events.append(("AMBER", "monitor", "could not settle a source: %s %s" % (e.sym, e.source)))
    # 3. drift per strategy
    exp = expect.load()
    names = sorted(set(live.strategy) | set(k for k in exp if not k.startswith("lab:") or
                                            exp[k].get("stage") in ("paper", "live_small")))
    rows = []
    for s in names:
        e = exp.get(s) or {}
        a = drift.assess(per_period(live, s), e.get("mean"), e.get("sd"), e.get("dd"), e.get("win"))
        a["strategy"] = s
        a["blocked"] = killswitch.blocked(s, ks)
        a["series"] = per_period(live, s)
        rows.append(a)
        if a["status"] == "RED" and not ks.get("strategies", {}).get(s, {}).get("killed"):
            killswitch.kill(ks, s, "; ".join(a["reasons"]), auto=True)
            a["blocked"] = killswitch.blocked(s, ks)
            events.append(("RED", s, "KILLED automatically: " + "; ".join(a["reasons"])))
        elif a["status"] == "AMBER":
            events.append(("AMBER", s, "; ".join(a["reasons"])))
    killswitch.save(ks)
    # 4. missed runs: a model on file but no picks for the last session
    last = con.execute("SELECT max(date) FROM calendar").fetchone()[0]
    if os.path.exists(os.path.join(config.DATA, "models", "swing", "model-h10.txt")) and \
            not os.path.exists(os.path.join(settle.PICKS, "swing-%s.json" % last)):
        events.append(("AMBER", "swing_model", "no swing plan for the session of %s (scan not run?)" % last))
    # 5. outputs
    status = {"built": _now().strftime("%Y-%m-%d %H:%M IST"), "last_session": str(last), "data": q["status"],
              "halt": ks.get("halt"), "strategies": [{k: v for k, v in r.items() if k != "series"} for r in rows],
              "events": [{"level": l, "strategy": s, "text": t} for l, s, t in events],
              "legacy": legacy_scoreboard()}
    with open(os.path.join(OUT, "status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=1, default=str)
    write_md(status)
    dashboard.write(os.path.join(OUT, "dashboard.html"), status, rows, exp)
    sent_path = os.path.join(OUT, "alerts-sent.json")
    try:
        with open(sent_path, encoding="utf-8") as fh:
            sent = json.load(fh)
    except (OSError, ValueError):
        sent = {}
    today = _now().date().isoformat()
    with open(os.path.join(OUT, "alerts.jsonl"), "a", encoding="utf-8") as fh:
        for l, s, t in events:
            key = "%s|%s|%s" % (today, s, l)
            fh.write(json.dumps({"at": status["built"], "level": l, "strategy": s, "text": t}) + "\n")
            if l in ("RED", "AMBER") and key not in sent:
                sent[key] = send_phone("NSE monitor %s - %s: %s" % (l, s, t))
    with open(sent_path, "w", encoding="utf-8") as fh:
        json.dump(sent, fh)
    return status


def write_md(st):
    md = ["# Live monitor", "", "Built %s, last session %s. Data checks: **%s**.%s" % (
        st["built"], st["last_session"], st["data"].upper(),
        "  **HALTED: %s**" % st["halt"].get("reason") if st.get("halt", {}).get("on") else ""), "",
          "| strategy | status | live n | live mean % | backtest mean % | z | live drawdown % | blocked | why |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in st["strategies"]:
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["strategy"], r["status"], r["n"], r["live_mean_pct"], r["bt_mean_pct"], r.get("z", "-"),
            r["live_max_dd_pct"], r["blocked"] or "-", "; ".join(r["reasons"])))
    if st["events"]:
        md += ["", "## Events", ""] + ["- **%s** %s: %s" % (e["level"], e["strategy"], e["text"]) for e in st["events"]]
    if st["legacy"]:
        md += ["", "## Old system (nse-picks-tracker scoreboard), for comparison", "",
               "| rule | closed | win % | avg net % | total Rs |", "| --- | --- | --- | --- | --- |"]
        md += ["| %s | %s | %s | %s | %s |" % (r["rule"], r["closed"], r["win_pct"], r["avg_net_pct"], r["total_net_rs"])
               for r in st["legacy"]]
    md += ["", "Paper research only. Past results are not predictions; nothing here places orders."]
    with open(os.path.join(OUT, "status.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="monitor")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("status")
    for c in ("kill", "revive"):
        p = sub.add_parser(c)
        p.add_argument("id")
        p.add_argument("--why", required=True)
    for c in ("halt", "unhalt"):
        p = sub.add_parser(c)
        p.add_argument("--why", required=True)
    a = ap.parse_args(argv)
    if a.cmd in ("run", "status"):
        if a.cmd == "run":
            run()
        print(open(os.path.join(OUT, "status.md"), encoding="utf-8").read())
        return 0
    ks = killswitch.load()
    if a.cmd == "kill":
        killswitch.kill(ks, a.id, a.why)
    elif a.cmd == "revive":
        killswitch.revive(ks, a.id, a.why)
    elif a.cmd == "halt":
        killswitch.halt(ks, True, a.why, auto=False)
    elif a.cmd == "unhalt":
        killswitch.halt(ks, False, a.why, auto=False)
    else:
        ap.print_help()
        return 2
    killswitch.save(ks)
    print(json.dumps(ks, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
