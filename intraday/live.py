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
        return [{"symbol": r.symbol, "side": side, "score": round(float(r.pred) * 100, 3),
                 "ref_price": round(float(r.w_close), 2),
                 "stop": round(float(r.w_low if side == "long" else r.w_high), 2),
                 "flat_by": "15:10", "verdict": v.get("verdict", "UNKNOWN"),
                 "backtest_mean_net_pct": v.get("mean_pct")} for r in rows.itertuples()]

    longs = f.sort_values("pred", ascending=False).head(k)
    shorts = f.sort_values("pred", ascending=True).head(k)
    validated = rep.get("best", {}).get("verdict") == "EDGE"
    doc = {"day": str(day), "decision_time": t, "made_at": datetime.datetime.now(config.IST).strftime("%H:%M:%S IST"),
           "status": "VALIDATED" if validated else "PAPER ONLY: the walk-forward test has not shown an edge",
           "model_report": {"from": rep.get("from"), "to": rep.get("to"), "best": rep.get("best")},
           "universe": len(f), "missing_keys": missing, "long": pack(longs, "long"), "short": pack(shorts, "short"),
           "blocked": {}}
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
