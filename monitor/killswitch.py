"""The kill switch: one file every live scorer reads before it publishes picks.

data/monitor/killswitch.json
  {"halt": {"on": bool, "reason": str, "at": str, "auto": bool},        stops everything
   "strategies": {id: {"killed": bool, "reason": str, "at": str, "auto": bool}}}

Strategy ids: "intraday_long", "intraday_short", "swing_model", "lab:<spec id>".

The monitor turns a strategy off automatically on a RED drift alert and halts everything when the
data checks fail. A halt for bad data lifts itself once the data is healthy again; a killed
strategy stays off until a person revives it (`python -m monitor revive ID --why ...`).
"""
import datetime
import json
import os

from warehouse import config

PATH = os.path.join(config.DATA, "monitor", "killswitch.json")


def _now():
    return datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M IST")


def load(path=None):
    try:
        with open(path or PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"halt": {"on": False}, "strategies": {}}


def save(ks, path=None):
    p = path or PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(ks, fh, indent=1)
    os.replace(p + ".tmp", p)


def blocked(strategy, ks=None):
    """-> reason string if the strategy must not publish picks, else None."""
    ks = ks or load()
    if ks.get("halt", {}).get("on"):
        return "HALTED: " + ks["halt"].get("reason", "")
    s = ks.get("strategies", {}).get(strategy)
    if s and s.get("killed"):
        return "KILLED: " + s.get("reason", "")
    return None


def kill(ks, strategy, reason, auto=False):
    ks.setdefault("strategies", {})[strategy] = {"killed": True, "reason": reason, "at": _now(), "auto": auto}


def revive(ks, strategy, reason):
    ks.setdefault("strategies", {})[strategy] = {"killed": False, "reason": "revived: " + reason, "at": _now(),
                                                 "auto": False}


def halt(ks, on, reason="", auto=True):
    ks["halt"] = {"on": bool(on), "reason": reason, "at": _now(), "auto": auto}
