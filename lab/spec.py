"""Strategy specs: a trading idea written as data, so a person or a Claude agent can propose one
without writing code, and the lab can test, perturb and track it.

  {
    "id": "sector_leaders",                       unique, [a-z0-9_]
    "name": "Sector leaders",
    "hypothesis": "Stocks leading a strengthening industry keep leading for two weeks.",
    "author": "human" | "claude" | "pack",
    "mode": "cohort" | "event",                   cohort: rebalance every `hold` sessions, top k
                                                  event:  every row passing the filter is a trade
    "filter": "ind_rel_20 >= {ind_min} and ind_rel_chg_5 > 0 and nifty_above_ma200 == 1 and not avoid",
    "rank": "-rs_industry_20",                    a feature; '-' = highest first (cohort mode)
    "k": 5, "hold": 10,                           hold must be one of swing.labels.HORIZONS
    "stop_atr": null | 2,                         2 = use the 2-ATR stop labels
    "params": {"ind_min": 0.02}                   numbers substituted into {name}; the skeptic
                                                  perturbs them +-20% to test robustness
  }

Filters may use only swing feature names, the derived flag `avoid`, the params, numbers, comparisons,
and/or/not and arithmetic. Anything else (calls, attributes, indexing) is rejected before it runs.
"""
import ast
import json
import os
import re

from swing import features as swing_features
from swing import labels as swing_labels

ALLOWED_NAMES = set(swing_features.FEATURES) | {"avoid", "adj_close", "turnover", "liq_rank", "pred"}
ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
                 ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.BinOp, ast.Add,
                 ast.Sub, ast.Mult, ast.Div, ast.Name, ast.Load, ast.Constant)
REQUIRED = ("id", "name", "hypothesis", "filter", "hold")
IDEAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ideas")


class SpecError(ValueError):
    pass


def render(expr, params):
    """Substitute {name} params; unknown placeholders are an error."""
    def sub(m):
        k = m.group(1)
        if k not in params:
            raise SpecError("filter uses {%s} but params has no %s" % (k, k))
        return repr(float(params[k]))
    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", sub, expr)


def check_expr(expr):
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise SpecError("filter does not parse: %s" % e)
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise SpecError("filter may not use %s" % type(node).__name__)
        if isinstance(node, ast.Name) and node.id not in ALLOWED_NAMES:
            raise SpecError("unknown name in filter: %s" % node.id)
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise SpecError("only numbers are allowed as constants")
    return expr


def validate(spec):
    for k in REQUIRED:
        if k not in spec:
            raise SpecError("spec is missing %s" % k)
    if not re.fullmatch(r"[a-z0-9_]{3,60}", spec["id"]):
        raise SpecError("id must be 3-60 chars of a-z, 0-9, _")
    spec.setdefault("mode", "cohort")
    spec.setdefault("k", 10)
    spec.setdefault("stop_atr", None)
    spec.setdefault("params", {})
    spec.setdefault("author", "human")
    if spec["mode"] not in ("cohort", "event"):
        raise SpecError("mode must be cohort or event")
    if int(spec["hold"]) not in swing_labels.HORIZONS:
        raise SpecError("hold must be one of %s" % (swing_labels.HORIZONS,))
    if spec["stop_atr"] not in (None, swing_labels.STOP_ATR):
        raise SpecError("stop_atr must be null or %s" % swing_labels.STOP_ATR)
    check_expr(render(spec["filter"], spec["params"]))
    if spec["mode"] == "cohort":
        rank = spec.get("rank")
        if not rank or rank.lstrip("-") not in ALLOWED_NAMES:
            raise SpecError("cohort mode needs rank: a feature name, '-' prefix for highest first")
    return spec


def load_ideas(folder=IDEAS_DIR):
    out, errors = [], []
    if not os.path.isdir(folder):
        return out, errors
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, f), encoding="utf-8") as fh:
                out.append(validate(json.load(fh)))
        except (ValueError, OSError) as e:
            errors.append("%s: %s" % (f, e))
    return out, errors
