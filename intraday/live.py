"""Live scoring at the decision time (default 09:30 IST) on a trading day.

1. Today's F&O stocks = the latest day in fo_universe.
2. Today's 1-minute candles so far from Upstox's public intraday endpoint (no login).
3. The same feature query as the backtest, on the warehouse's history plus today's candles.
4. The saved model ranks them; writes data/picks/intraday-YYYY-MM-DD.json: top K long and bottom
   K short, each with the entry reference, the opening-window stop and the flat-by time.

Promotion gate: every pick carries the strategy's walk-forward verdict. Unless that verdict is
EDGE the file says PAPER ONLY and the tracker must treat it as an experiment. Nothing here places
an order.
"""
import datetime
import json
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from warehouse import config, net
from warehouse.loaders import minute

from . import features, model

INTRADAY_URL = "https://api.upstox.com/v3/historical-candle/intraday/%s/minutes/1"
PER_CAPITAL = 100000.0   # sizes per Rs 1 lakh of capital
LEVERAGE = 5.0           # Zerodha MIS intraday leverage; multiplies gains AND losses on capital


def fetch_today(keys, day, workers=6):
    def job(item):
        sym, key = item
        minute._throttle()
        try:
            doc = net.get_json(INTRADAY_URL % urllib.parse.quote(key, safe=""))
        except (IOError, ValueError, net.NotPublished):
            return []
        out = []
        for c in (doc.get("data") or {}).get("candles") or []:
            ts = datetime.datetime.fromisoformat(c[0]).replace(tzinfo=None)
            if ts.date() == day:
                out.append((ts.date(), sym, ts, c[1], c[2], c[3], c[4], c[5], c[6] if len(c) > 6 else None, "live"))
        return out

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for got in ex.map(job, keys.items()):
            rows += got
    return pd.DataFrame(rows, columns=minute.COLS)


def score(con, t="09:30", k=5, day=None, log=print):
    day = day or config.today_ist()
    tag = t.replace(":", "")
    mdir = os.path.join(config.DATA, model.MODEL_DIR_NAME)
    mpath = os.path.join(mdir, "model-%s.txt" % tag)
    if not os.path.exists(mpath):
        raise SystemExit("no model for %s: run python -m intraday validate --t %s first" % (t, t))
    with open(os.path.join(mdir, "report-%s.json" % tag), encoding="utf-8") as fh:
        rep = json.load(fh)
    uni = [r[0] for r in con.execute(
        "SELECT symbol FROM fo_universe WHERE date = (SELECT max(date) FROM fo_universe)").fetchall()]
    keys, missing = minute.resolve_keys(["NIFTY 50"] + uni, log)
    log("live %s: fetching today's candles for %d symbols" % (day, len(keys)))
    today = fetch_today(keys, day)
    if today.empty:
        raise SystemExit("Upstox returned no candles for %s (holiday, or before 09:15?)" % day)
    # keep today's candles so the paper trades can be entered right away (the evening sync replaces them)
    from warehouse import store
    store.upsert("minute_bars", today, ["date", "symbol", "ts"])
    con.register("today_df", today)
    con.execute("""CREATE OR REPLACE TEMP VIEW bars_live AS
                   SELECT * FROM minute_bars WHERE date >= DATE '%s' - INTERVAL 45 DAY AND date < DATE '%s'
                   UNION ALL SELECT * FROM today_df""" % (day, day))
    f = features.build(con, t, bars="bars_live", start=day, end=day)
    if f.empty:
        raise SystemExit("no feature rows for %s: is the opening window complete (now past %s)?" % (day, t))
    import lightgbm
    f["pred"] = lightgbm.Booster(model_file=mpath).predict(f[features.FEATURES])
    strat = rep.get("strategies", {})

    def pack(rows, side):
        v = strat.get("model_%s_stop" % side, {})
        out = []
        per = PER_CAPITAL * LEVERAGE / max(k, 1)          # exposure per pick for Rs 1 lakh at LEVERAGE x
        for r in rows.itertuples():
            ref = float(r.w_close)
            stop = float(r.w_low if side == "long" else r.w_high)
            qty = int(per // ref) if ref > 0 else 0
            risk = qty * abs(ref - stop)
            out.append({"symbol": r.symbol, "side": side, "score": round(float(r.pred) * 100, 3),
                        "ref_price": round(ref, 2), "stop": round(stop, 2), "flat_by": "15:10",
                        "shares_per_lakh": qty, "exposure_per_lakh": round(qty * ref),
                        "risk_to_stop_rs_per_lakh": round(risk), "verdict": v.get("verdict", "UNKNOWN"),
                        "backtest_mean_net_pct": v.get("mean_pct")})
        return out

    longs = f.sort_values("pred", ascending=False).head(k)
    shorts = f.sort_values("pred", ascending=True).head(k)
    validated = rep.get("best", {}).get("verdict") == "EDGE"
    doc = {"day": str(day), "decision_time": t, "made_at": datetime.datetime.now(config.IST).strftime("%H:%M:%S IST"),
           "status": "VALIDATED" if validated else "PAPER ONLY: the walk-forward test has not shown an edge",
           "model_report": {"from": rep.get("from"), "to": rep.get("to"), "best": rep.get("best")},
           "universe": len(f), "missing_keys": missing, "long": pack(longs, "long"), "short": pack(shorts, "short"),
           "blocked": {}, "per_capital": PER_CAPITAL, "leverage": LEVERAGE,
           "leverage_effect": {side: {"backtest_pct_per_day_on_exposure": (strat.get("model_%s_stop" % side) or {}).get("mean_pct"),
                                      "pct_per_day_on_capital": round(((strat.get("model_%s_stop" % side) or {}).get("mean_pct") or 0)
                                                                      * LEVERAGE, 3)}
                               for side in ("long", "short")}}
    from monitor import killswitch
    for side in ("long", "short"):
        why = killswitch.blocked("intraday_" + side)
        if why:
            doc["blocked"][side] = why
            doc[side] = []
            log("live: intraday_%s is %s - no picks published" % (side, why))
    out = os.path.join(config.DATA, "picks")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "intraday-%s.json" % day)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    doc["path"] = path
    return doc
