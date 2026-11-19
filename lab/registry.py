"""Every idea the lab has ever tested, its stage, and why it moved.

Stages
  research     tested; not good enough yet (PROMISING ideas stay here and are re-tested as data grows)
  rejected     the Skeptic said FAIL
  paper        passed the Skeptic; its signals are recorded nightly and settled on real prices
  live_small   30+ paper trades consistent with the backtest; eligible for real money at a small size
               (a person decides; nothing here places orders)
  retired      paper / live results drifted below the backtest, or the person retired it

Promotion and demotion (promote()):
  research -> paper       Skeptic PASS
  paper -> live_small     >= 30 closed paper trades, paper mean > 0, and the paper mean is not
                          significantly worse than the backtest (z > -2)
  paper/live -> retired   >= 20 closed trades and z < -2.5, or the paper drawdown is worse than
                          1.5x the backtest's worst drawdown

Every spec change is a new trial: `trials` counts them all and sets the Skeptic's t hurdle, so
trying many variants of one idea raises the bar for all of them.
"""
import datetime
import hashlib
import json
import math
import os

from warehouse import config

PATH = os.path.join(config.DATA, "lab", "registry.json")
MIN_PAPER = 30


def _now():
    return datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST")


def fingerprint(spec):
    core = {k: spec.get(k) for k in ("mode", "filter", "rank", "k", "hold", "stop_atr", "params")}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"trials": 0, "fingerprints": [], "strategies": {}}


def save(reg):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=1, default=str)
    os.replace(tmp, PATH)


def _log(entry, event, detail=""):
    entry.setdefault("history", []).append({"at": _now(), "event": event, "detail": detail})


def needs_test(reg, spec):
    e = reg["strategies"].get(spec["id"])
    return e is None or e.get("fingerprint") != fingerprint(spec)


def count_trial(reg, spec):
    fp = fingerprint(spec)
    if fp not in reg["fingerprints"]:
        reg["fingerprints"].append(fp)
        reg["trials"] = len(reg["fingerprints"])
    return reg["trials"]


def record_review(reg, spec, review):
    e = reg["strategies"].setdefault(spec["id"], {"stage": "research"})
    changed = e.get("fingerprint") not in (None, fingerprint(spec))
    e.update(spec=spec, fingerprint=fingerprint(spec), review=review, reviewed_at=_now())
    if changed and e.get("stage") in ("paper", "live_small"):
        _log(e, "reset", "spec changed: back to research, paper record kept but no longer counted")
        e["stage"] = "research"
        e["paper_since"] = None
    if e["stage"] in ("research", "rejected"):
        if review["verdict"] == "PASS":
            e["stage"] = "paper"
            e["paper_since"] = datetime.date.today().isoformat()
            _log(e, "promoted to paper", "Skeptic PASS: t %.2f vs hurdle %.2f" % (review["t"], review["hurdle"]))
        elif review["verdict"] == "FAIL":
            if e["stage"] != "rejected":
                _log(e, "rejected", "failed: " + ", ".join(review["failed"]))
            e["stage"] = "rejected"
        else:
            if e["stage"] != "research":
                _log(e, "research", "PROMISING")
            e["stage"] = "research"
            _log(e, "promising", "failed: " + ", ".join(review["failed"]))
    return e


def promote(reg, paper_stats):
    """paper_stats: {id: {"n", "mean", "sd", "max_dd"}} from the paper ledger (fractions)."""
    moves = []
    for sid, e in reg["strategies"].items():
        if e.get("stage") not in ("paper", "live_small"):
            continue
        ps = paper_stats.get(sid)
        if not ps or ps["n"] == 0:
            continue
        e["paper"] = ps
        bt_mean = (e.get("review") or {}).get("mean_pct", 0) / 100.0
        se = (ps["sd"] or 0) / math.sqrt(ps["n"]) if ps["n"] > 1 else None
        z = (ps["mean"] - bt_mean) / se if se else None
        e["paper"]["z_vs_backtest"] = round(z, 2) if z is not None else None
        bt_dd = abs(min(0.0, (e.get("review") or {}).get("bt_max_dd", -0.2)))
        if ps["n"] >= 20 and ((z is not None and z < -2.5) or abs(ps["max_dd"]) > 1.5 * max(bt_dd, 0.05)):
            e["stage"] = "retired"
            _log(e, "retired", "paper drifted below the backtest (z %s, drawdown %.1f%%)" % (z, ps["max_dd"] * 100))
            moves.append((sid, "retired"))
        elif e["stage"] == "paper" and ps["n"] >= MIN_PAPER and ps["mean"] > 0 and (z is None or z > -2):
            e["stage"] = "live_small"
            _log(e, "promoted to live_small", "%d paper trades, mean %.2f%%, z %s" % (ps["n"], ps["mean"] * 100, z))
            moves.append((sid, "live_small"))
    return moves


def set_stage(reg, sid, stage, why):
    e = reg["strategies"][sid]
    e["stage"] = stage
    _log(e, "set to " + stage, why)
