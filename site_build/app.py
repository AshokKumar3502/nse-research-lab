"""The main page (docs/index.html): an app-style view of today's picks, open positions, every past
trade and the stats, in light / dark / system themes, laid out for phones and desktops.

Data is embedded in the page (so it also works inside the Android app and when opened from disk) and
written to docs/data/app.json; docs/data/notify.json tells the Android app when new picks are out.
"""
import datetime
import json
import os

import pandas as pd

from warehouse import config

DOCS = os.path.join(config.ROOT, "docs")


def _j(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _latest(prefix):
    d = os.path.join(config.DATA, "picks")
    fs = sorted(f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(".json")) if os.path.isdir(d) else []
    return _j(os.path.join(d, fs[-1])) if fs else None


def updated_by():
    """Who built this page and when the next update is due (shown at the top of the app)."""
    now = datetime.datetime.now(config.IST)
    env = os.environ
    if env.get("GITHUB_ACTIONS") == "true":
        phase = env.get("PHASE", "")
        how = "scheduled" if env.get("GITHUB_EVENT_NAME") == "schedule" else "started by hand"
        who = "GitHub cloud · %s run #%s (%s)" % (phase or "daily", env.get("GITHUB_RUN_NUMBER", "?"), how)
        url = "%s/%s/actions/runs/%s" % (env.get("GITHUB_SERVER_URL", "https://github.com"),
                                         env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", ""))
    else:
        who, url = "Laptop", None
    base = {"at": now.strftime("%d %b %Y, %H:%M IST"), "ts": int(now.timestamp()), "by": who, "url": url}
    if env.get("PHASE") == "live" and now.time() < datetime.time(15, 20):
        base["by"] = "GitHub cloud · live prices every 10 min (run #%s)" % env.get("GITHUB_RUN_NUMBER", "?")
        nxt = now + datetime.timedelta(minutes=10)
        base["next"] = "Live prices around %s" % nxt.replace(minute=nxt.minute - nxt.minute % 10).strftime("%H:%M")
        return base
    # the next scheduled update (weekdays: intraday picks at 9:30, swing picks after the 18:45 run)
    t = now
    for _ in range(8):
        if t.weekday() < 5:
            for hh, mm, label in ((9, 31, "intraday picks"), (18, 55, "swing picks and P&L")):
                slot = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if slot > now:
                    when = ("today" if slot.date() == now.date() else slot.strftime("%a %d %b"))
                    return dict(base, next="%s around %s %s" % (label.capitalize(), slot.strftime("%H:%M"), when))
        t = (t + datetime.timedelta(days=1)).replace(hour=0, minute=0)
    return dict(base, next=None)


def collect():
    book = _j(os.path.join(DOCS, "paper.json")) or {"trades": [], "daily": [], "summary": {}}
    sw, it = _latest("swing-"), _latest("intraday-")
    ind = {}
    p = os.path.join(config.SETS, "industries", "all.parquet")
    if os.path.exists(p):
        t = pd.read_parquet(p)
        ind = dict(zip(t.symbol, t.industry))
    by_id = {t["id"]: t for t in book["trades"]}
    swing_today = []
    if sw:
        for i, pk in enumerate(sw.get("picks", []), 1):
            tr = by_id.get("S-%s-%s" % (sw["session"], pk["symbol"]), {})
            swing_today.append({"rank": i, "symbol": pk["symbol"], "industry": ind.get(pk["symbol"], pk.get("industry")),
                                "close": pk["ref_close"], "sl": tr.get("sl", pk["stop"]) if tr else (
                                    pk["stop"] if pk.get("use_stop") or sw["session"] < "2026-11-23" else None),
                                "sl_pct": pk["stop_pct"],
                                "score": pk["score_pct"], "qty": int(50000 // pk["ref_close"]) if pk["ref_close"] else 0,
                                "status": tr.get("status", "pending"), "entry": tr.get("entry"), "net": tr.get("net"),
                                "gross": tr.get("gross"), "day": (tr.get("marks") or [{}])[-1].get("session"),
                                "entry_at": tr.get("entry_at"), "last": tr.get("last"), "last_at": tr.get("last_at"),
                                "qty_held": tr.get("qty"), "exit": tr.get("exit"), "why": tr.get("exit_reason")})
    intraday_today = None
    if it:
        rows = []
        for side in ("long", "short"):
            for pk in it.get(side, []):
                tr = by_id.get("I-%s-%s-%s" % (it["day"], side[0].upper(), pk["symbol"]), {})
                rows.append({"side": side, "symbol": pk["symbol"], "ref": pk["ref_price"], "sl": pk["stop"],
                             "entry": tr.get("entry"), "exit": tr.get("exit"), "status": tr.get("status", "pending"),
                             "net": tr.get("net"), "why": tr.get("exit_reason"), "time": tr.get("exit_time"),
                             "gross": tr.get("gross"), "qty": tr.get("qty"), "entry_at": tr.get("entry_at"),
                             "last": tr.get("last"), "last_at": tr.get("last_at")})
        intraday_today = {"day": it["day"], "made_at": it.get("made_at"), "rows": rows,
                          "leverage": it.get("leverage", 5), "effect": it.get("leverage_effect", {}),
                          "blocked": it.get("blocked", {})}
    m = os.path.join(config.DATA, "models")
    ir, sr, rv = (_j(os.path.join(m, "intraday", "report-0930.json")), _j(os.path.join(m, "swing", "report-h10.json")),
                  _j(os.path.join(m, "swing", "review-h10.json")))
    verdicts = {}
    if sr:
        s = sr["strategies"].get("model_top/close", {})
        verdicts["swing"] = {"verdict": (rv or {}).get("verdict", s.get("verdict")), "mean_pct": s.get("mean_pct"),
                             "t": s.get("t"), "win_pct": s.get("win_days_pct"), "from": sr.get("oos_from"), "to": sr.get("to")}
    if ir:
        best = ir["strategies"].get("model_long_stop", {})
        verdicts["intraday"] = {"verdict": "NO EDGE" if ir.get("best", {}).get("verdict") != "EDGE" else "EDGE",
                                "mean_pct": best.get("mean_pct"), "from": ir.get("from"), "to": ir.get("to")}
    return {"built": datetime.datetime.now(config.IST).strftime("%d %b %Y, %H:%M IST"),
            "updated": updated_by(),
            "last_session": book.get("last_session"), "stake": book.get("stake", 50000),
            "regime": (sw or {}).get("regime"), "swing_session": (sw or {}).get("session"),
            "swing_status": (sw or {}).get("status"), "swing_today": swing_today, "intraday_today": intraday_today,
            "summary": book.get("summary", {}), "daily": book.get("daily", []), "trades": book.get("trades", []),
            "verdicts": verdicts}


def build():
    data = collect()
    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    with open(os.path.join(DOCS, "data", "app.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, default=str)
    names_s = ", ".join(r["symbol"] for r in data["swing_today"][:5])
    it = data["intraday_today"] or {}
    names_i = ", ".join(r["symbol"] for r in it.get("rows", []) if r["side"] == "long")[:80]
    # the Android app shows each message id once (android/.../NotifyWorker.java)
    now = int(datetime.datetime.now(config.IST).timestamp())
    msgs = []
    if names_s:
        msgs.append({"id": "swing-%s" % data["swing_session"], "ts": now, "title": "Swing picks for the next open",
                     "text": "%s%s · hold 10 sessions" % (names_s, " +%d more" % (len(data["swing_today"]) - 5)
                                                          if len(data["swing_today"]) > 5 else "")})
    if names_i:
        msgs.append({"id": "intraday-%s" % it.get("day"), "ts": now, "title": "Intraday picks (paper only)",
                     "text": "Buy: %s" % names_i})
    notify = {"built": data["built"], "messages": msgs}
    with open(os.path.join(DOCS, "data", "notify.json"), "w", encoding="utf-8") as fh:
        json.dump(notify, fh)
    manifest = {"name": "NSE Lab", "short_name": "NSE Lab", "start_url": "./", "scope": "./", "display": "standalone",
                "background_color": "#0b1220", "theme_color": "#0f766e",
                "icons": [{"src": "logo.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}]}
    with open(os.path.join(DOCS, "manifest.webmanifest"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    html = PAGE.replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/"))
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    return os.path.join(DOCS, "index.html")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>NSE Lab</title><meta name="description" content="Daily swing and intraday picks for NSE stocks, paper traded at Rs 50,000 a stock, with every result kept.">
<meta name="theme-color" content="#0f766e"><link rel="icon" href="logo.svg" type="image/svg+xml"><link rel="manifest" href="manifest.webmanifest">
<script>try{var t=localStorage.getItem("theme");if(t&&t!=="system")document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{--bg:#f4f6fb;--surface:#fff;--surface2:#eef2f8;--text:#0f172a;--muted:#5b6578;--line:#e2e8f0;
--brand:#0f766e;--brand2:#1d2b64;--brandtx:#fff;--up:#15803d;--upbg:#dcfce7;--dn:#b91c1c;--dnbg:#fee2e2;
--warn:#a16207;--warnbg:#fef3c7;--info:#1d4ed8;--infobg:#dbeafe;--shadow:0 1px 2px rgba(15,23,42,.06),0 4px 14px rgba(15,23,42,.05)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0b1220;--surface:#121b2e;--surface2:#19243b;--text:#e6ebf3;
--muted:#93a0b5;--line:#243149;--brand:#2dd4bf;--brand2:#93c5fd;--brandtx:#06221f;--up:#4ade80;--upbg:rgba(34,197,94,.15);
--dn:#f87171;--dnbg:rgba(239,68,68,.15);--warn:#fbbf24;--warnbg:rgba(251,191,36,.13);--info:#93c5fd;--infobg:rgba(59,130,246,.16);--shadow:none}}
:root[data-theme=dark]{--bg:#0b1220;--surface:#121b2e;--surface2:#19243b;--text:#e6ebf3;--muted:#93a0b5;--line:#243149;
--brand:#2dd4bf;--brand2:#93c5fd;--brandtx:#06221f;--up:#4ade80;--upbg:rgba(34,197,94,.15);--dn:#f87171;--dnbg:rgba(239,68,68,.15);
--warn:#fbbf24;--warnbg:rgba(251,191,36,.13);--info:#93c5fd;--infobg:rgba(59,130,246,.16);--shadow:none}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.bar{max-width:1120px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;letter-spacing:-.01em;color:var(--text);text-decoration:none}
.brand img{width:34px;height:34px;border-radius:9px}.brand small{display:block;font-weight:500;font-size:11.5px;color:var(--muted);letter-spacing:0}
nav.tabs{display:flex;gap:4px;margin-left:auto}
nav.tabs button,.bnav button{font:inherit;border:0;background:none;color:var(--muted);cursor:pointer}
nav.tabs button{padding:8px 14px;border-radius:10px;font-weight:600}
nav.tabs button[aria-current=page]{background:var(--surface2);color:var(--text)}
.iconbtn{width:38px;height:38px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--text);display:grid;place-items:center;cursor:pointer;margin-left:auto}
nav.tabs+.iconbtn{margin-left:0}.iconbtn svg{width:18px;height:18px}
main{max-width:1120px;margin:0 auto;padding:16px 16px 96px}
h2{font-size:19px;margin:22px 0 10px;letter-spacing:-.01em}h2 .sub{display:block;font-weight:500;font-size:13px;color:var(--muted);margin-top:2px}
.view{display:none}.view.on{display:block}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;padding:5px 10px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--muted)}
.chip b{color:var(--text)}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.dot.up{background:var(--up)}.dot.dn{background:var(--dn)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow)}
.card.pick{border-left:4px solid var(--brand)}.card.pick.win{border-left-color:var(--up)}.card.pick.loss{border-left-color:var(--dn)}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.sym{font-weight:800;font-size:17px;letter-spacing:.01em}.ind{font-size:12.5px;color:var(--muted)}
.rank{font-size:11px;font-weight:800;color:var(--brand);background:color-mix(in srgb,var(--brand) 14%,transparent);border-radius:7px;padding:2px 7px}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.kv div{font-size:11.5px;color:var(--muted)}.kv b{display:block;font-size:14px;color:var(--text);font-variant-numeric:tabular-nums}
.kv b.dn{color:var(--dn)}.kv b.up{color:var(--up)}
.pill{font-size:11px;font-weight:800;letter-spacing:.03em;padding:3px 8px;border-radius:999px;text-transform:uppercase;white-space:nowrap}
.p-up{background:var(--upbg);color:var(--up)}.p-dn{background:var(--dnbg);color:var(--dn)}.p-warn{background:var(--warnbg);color:var(--warn)}
.p-info{background:var(--infobg);color:var(--info)}.p-mute{background:var(--surface2);color:var(--muted)}
.up{color:var(--up)}.dn{color:var(--dn)}.muted{color:var(--muted)}.num{font-variant-numeric:tabular-nums}
.pnl{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}
.banner{border-radius:14px;padding:12px 14px;font-size:13.5px;margin:10px 0}.banner.warn{background:var(--warnbg);color:var(--text);border:1px solid color-mix(in srgb,var(--warn) 40%,transparent)}
.cols{display:grid;gap:12px;grid-template-columns:1fr 1fr}
.list{background:var(--surface);border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:var(--shadow)}
.list h3{margin:0;padding:11px 14px;font-size:14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:8px}
.li{display:grid;grid-template-columns:1fr auto;gap:4px 10px;padding:10px 14px;border-bottom:1px solid var(--line);align-items:center}
.li:last-child{border-bottom:0}.li .s{font-weight:700}.li .d{font-size:12.5px;color:var(--muted)}.li .r{text-align:right;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.kpis{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
.kpi .l{font-size:12px;color:var(--muted)}.kpi .v{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;margin-top:2px}.kpi .s{font-size:12px;color:var(--muted)}
.prog{height:6px;border-radius:4px;background:var(--surface2);overflow:hidden;margin-top:10px}.prog i{display:block;height:100%;background:var(--brand)}
.pos{cursor:pointer}.pos .more{display:none;margin-top:10px}.pos.open .more{display:block}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0 12px}
.seg{display:inline-flex;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:3px}
.seg button{font:inherit;font-size:13px;border:0;background:none;color:var(--muted);padding:6px 12px;border-radius:9px;cursor:pointer;font-weight:600}
.seg button[aria-pressed=true]{background:var(--brand);color:var(--brandtx)}
input.search{font:inherit;padding:8px 12px;border-radius:12px;border:1px solid var(--line);background:var(--surface);color:var(--text);min-width:0;flex:1;max-width:260px}
.dayhead{display:flex;justify-content:space-between;align-items:center;margin:16px 2px 6px;font-weight:700;font-size:14px}
.chart{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:12px;box-shadow:var(--shadow)}
svg.c{width:100%;height:auto;display:block}svg.c .g{stroke:var(--line)}svg.c .ax{fill:var(--muted);font-size:11px}
.empty{color:var(--muted);padding:18px;text-align:center;background:var(--surface);border:1px dashed var(--line);border-radius:16px}
footer{margin-top:36px;font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}footer a{color:var(--brand)}
.upd{display:flex;gap:10px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-radius:14px;
padding:10px 14px;margin:4px 0 12px;font-size:13.5px;box-shadow:var(--shadow)}.upd .dot{margin-top:7px;flex:none}.upd a{color:var(--brand)}
.upd .muted{font-size:12.5px}
.lastupd{position:fixed;left:0;right:0;bottom:0;z-index:25;background:var(--surface);border-top:1px solid var(--line);
font-size:12.5px;color:var(--muted);padding:7px 16px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lastupd b{color:var(--text)}.lastupd .dot{display:inline-block;margin-right:6px;vertical-align:middle}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.skip{position:absolute;left:-999px;top:8px;z-index:60;background:var(--brand);color:var(--brandtx);padding:8px 14px;border-radius:10px;font-weight:700}.skip:focus{left:8px}
:focus-visible{outline:3px solid var(--brand2);outline-offset:2px;border-radius:6px}
.seg{max-width:100%;overflow-x:auto;scrollbar-width:thin}.seg button{white-space:nowrap}.seg .n,.jump .n{font-size:11.5px;opacity:.75;margin-left:2px}
.jump{display:flex;gap:8px;overflow-x:auto;padding:2px 0 6px;margin:14px 0 0;scrollbar-width:thin}
.jump button{flex:none;font:inherit;font-size:13px;font-weight:600;padding:7px 12px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--text);cursor:pointer}
.sec{scroll-margin-top:72px}.sec>h2{margin:22px 0 10px}
.secbtn{all:unset;box-sizing:border-box;display:flex;gap:8px;align-items:flex-start;width:100%;cursor:pointer;font-size:19px;font-weight:700;letter-spacing:-.01em}
.secbtn:focus-visible{outline:3px solid var(--brand2);outline-offset:4px}
.secbtn .chev{flex:none;width:10px;height:10px;margin-top:9px;border-right:2.5px solid var(--muted);border-bottom:2.5px solid var(--muted);transform:rotate(-45deg);transition:transform .15s}
.secbtn[aria-expanded=true] .chev{transform:rotate(45deg)}
.cnt{display:inline-block;font-size:12px;font-weight:800;color:var(--brand);background:color-mix(in srgb,var(--brand) 14%,transparent);border-radius:999px;padding:1px 8px;vertical-align:middle}
details.why{padding:9px 14px}details.why summary{cursor:pointer}details.why[open] summary{margin-bottom:2px}
.linkbtn{font:inherit;font-size:12.5px;font-weight:600;border:0;background:none;color:var(--brand);cursor:pointer;padding:4px 0}
.more-btn{display:block;margin:16px auto 0;font:inherit;font-weight:600;padding:10px 18px;border-radius:12px;border:1px solid var(--line);background:var(--surface);color:var(--text);cursor:pointer}
select.sel{font:inherit;font-size:13px;padding:8px 10px;border-radius:12px;border:1px solid var(--line);background:var(--surface);color:var(--text)}
.sumline{font-size:13px;color:var(--muted);margin:0 2px 8px}
h3.month{display:flex;justify-content:space-between;margin:22px 2px 0;font-size:15px;padding-bottom:6px;border-bottom:2px solid var(--line)}
@media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
.bnav{display:none}
@media (max-width:760px){nav.tabs{display:none}
 .lastupd{bottom:calc(58px + env(safe-area-inset-bottom));font-size:11.5px}main{padding-bottom:130px}
 .bnav{display:grid;grid-template-columns:repeat(4,1fr);position:fixed;left:0;right:0;bottom:0;z-index:30;background:var(--surface);border-top:1px solid var(--line);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
 .bnav button{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:11px;font-weight:600;padding:4px}
 .bnav button svg{width:22px;height:22px}.bnav button[aria-current=page]{color:var(--brand)}
 .cols{grid-template-columns:1fr}.brand small{display:none}.kpi .v{font-size:21px}}
</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="bar">
<a class="brand" href="#today"><img src="logo.svg" alt=""><span>NSE Lab<small id="upd"></small></span></a>
<nav class="tabs" aria-label="Sections"></nav>
<button class="iconbtn" id="theme" title="Theme" aria-label="Change theme"></button>
</div></header>
<main id="main" tabindex="-1">
<section class="view" id="v-today"></section>
<section class="view" id="v-portfolio"></section>
<section class="view" id="v-history"></section>
<section class="view" id="v-stats"></section>
<footer>Paper trading at ₹50,000 a stock on real NSE prices, after Zerodha charges. For education and research only; not
investment advice, and the author is not a SEBI-registered adviser. Nothing here places orders.
<a href="research.html">How each method was tested →</a> · <a href="tracker.html">Classic tracker</a></footer>
</main>
<div class="lastupd" id="lastupd" role="status"></div>
<nav class="bnav" aria-label="Sections"></nav>
<script>
let D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const inr = v => v == null ? "–" : "₹" + Number(v).toLocaleString("en-IN", {maximumFractionDigits: 2});
const sgn = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + "₹" + Math.abs(Math.round(v)).toLocaleString("en-IN");
const cl = v => v > 0 ? "up" : v < 0 ? "dn" : "";
const fd = d => d ? new Date(String(d).slice(0, 10) + "T00:00:00").toLocaleDateString("en-IN", {day: "numeric", month: "short"}) : "–";
const fdl = d => d ? new Date(String(d).slice(0, 10) + "T00:00:00").toLocaleDateString("en-IN", {weekday: "short", day: "numeric", month: "short", year: "numeric"}) : "–";
const tm = s => s ? String(s).slice(11, 16) : "";
const when = s => s ? fd(s) + " " + tm(s) : "–";
const pct = (a, b) => b ? (a > 0 ? "+" : "") + (a / b * 100).toFixed(2) + "%" : "";
const I = {
 today: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
 portfolio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
 history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
 stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
 sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
 moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
 auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>'};
let U = D.updated || {at: D.built, by: "", next: null};

// ---------- theme: system -> light -> dark
const themes = ["system", "light", "dark"];
function setTheme(t) {
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch (e) {}
  $("#theme").innerHTML = t === "light" ? I.sun : t === "dark" ? I.moon : I.auto;
  $("#theme").title = "Theme: " + t + " (tap to change)";
}
let cur = "system"; try { cur = localStorage.getItem("theme") || "system"; } catch (e) {}
setTheme(cur);
$("#theme").onclick = () => { cur = themes[(themes.indexOf(cur) + 1) % 3]; setTheme(cur); };

// ---------- navigation
const views = [["today", "Today"], ["portfolio", "Portfolio"], ["history", "History"], ["stats", "Stats"]];
$("nav.tabs").innerHTML = views.map(([k, l]) => `<button data-v="${k}">${l}</button>`).join("");
$(".bnav").innerHTML = views.map(([k, l]) => `<button data-v="${k}">${I[k]}<span>${l}</span></button>`).join("");
function show(v) {
  if (!views.some(x => x[0] === v)) v = "today";
  document.querySelectorAll(".view").forEach(s => s.classList.toggle("on", s.id === "v-" + v));
  document.querySelectorAll("[data-v]").forEach(b => { if (b.dataset.v === v) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
  window.scrollTo(0, 0);
}
document.querySelectorAll("[data-v]").forEach(b => b.onclick = () => show(b.dataset.v));
window.addEventListener("hashchange", () => show(location.hash.slice(1)));

// ---------- shared UI state (kept across the 3-minute live refresh)
let COLLAPSED = new Set();
try { COLLAPSED = new Set(JSON.parse(localStorage.getItem("collapsed") || "[]")); } catch (e) {}
const PF = {kind: "all", sort: "pnl"};
const HS = {kind: "all", q: "", status: "all", limit: 20};
const KINDS = [["swing", "Swing"], ["results", "Results jump"], ["momentum", "Momentum"], ["intraday", "Intraday"], ["option", "Options"]];
const MULTI = ["swing", "results", "momentum"];
const kindPill = t => t.kind === "momentum" ? `<span class="pill p-info">Momentum</span>` : t.kind === "results" ? `<span class="pill p-info">Results</span>`
  : t.kind === "option" ? `<span class="pill p-warn">Option</span>` : t.kind === "swing" ? `<span class="pill p-info">Swing</span>`
  : `<span class="pill ${t.side === "long" ? "p-up" : "p-dn"}">${t.side === "long" ? "Buy" : "Sell"}</span>`;
const reduceMotion = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
document.addEventListener("click", e => {
  if (e.target.closest(".skip")) { e.preventDefault(); $("#main").focus(); return; }
  const s = e.target.closest("[data-sec]");
  if (s) {
    const id = s.dataset.sec, open = s.getAttribute("aria-expanded") !== "true";
    s.setAttribute("aria-expanded", String(open));
    const body = document.getElementById("body-" + id);
    if (body) body.hidden = !open;
    if (open) COLLAPSED.delete(id); else COLLAPSED.add(id);
    try { localStorage.setItem("collapsed", JSON.stringify([...COLLAPSED])); } catch (err) {}
    return;
  }
  const j = e.target.closest("[data-jump]");
  if (j) {
    const el = document.getElementById("sec-" + j.dataset.jump), b = el && el.querySelector("[data-sec]");
    if (b && b.getAttribute("aria-expanded") !== "true") b.click();
    if (el) { el.scrollIntoView({behavior: reduceMotion() ? "auto" : "smooth", block: "start"}); if (b) b.focus({preventScroll: true}); }
    return;
  }
  const p = e.target.closest("[data-pos]");
  if (p) {
    const open = p.getAttribute("aria-expanded") !== "true";
    p.setAttribute("aria-expanded", String(open));
    p.textContent = open ? "Hide day-by-day P&L" : "Show day-by-day P&L";
    const m = document.getElementById(p.getAttribute("aria-controls"));
    if (m) m.hidden = !open;
  }
});

function renderAll() {
U = D.updated || {at: D.built, by: "", next: null};
$("#upd").textContent = "Updated " + U.at;
$("#lastupd").innerHTML = `<span class="dot up"></span>Last updated <b>${esc(U.at)}</b> · ${esc(U.by)}`;
// ---------- TODAY
(function () {
  const r = D.regime || {}, sw = D.swing_today || [], it = D.intraday_today;
  let h = `<div class="upd"><span class="dot up"></span><div><b>Updated ${esc(U.at)}</b> by ${U.url ? `<a href="${esc(U.url)}" target="_blank" rel="noopener">${esc(U.by)}</a>` : esc(U.by)}
    ${U.next ? `<div class="muted">Next update: ${esc(U.next)}</div>` : ""}</div></div>
    <div class="chips" style="margin-top:4px">
    <span class="chip"><span class="dot ${r.nifty_above_ma200 ? "up" : "dn"}"></span>NIFTY <b>${r.nifty_above_ma200 ? "above" : "below"}</b> 200-day avg</span>
    <span class="chip">Breadth <b>${r.breadth_ma50_pct ?? "–"}%</b> above 50-day</span>
    <span class="chip">Swing exposure <b>${r.exposure != null ? Math.round(r.exposure * 100) + "%" : "–"}</b></span>
    <span class="chip">Prices through <b>${fd(D.last_session)}</b></span></div>`;
  const secs = [];
  const sec = (id, title, sub, count, body) => secs.push({id, title, sub, count, body});
  const why = (lead, text) => `<details class="why banner warn"><summary><b>${lead}</b> <span class="muted">Why?</span></summary><p style="margin:6px 0 0">${text}</p></details>`;
  const swingCard = p => {
    const val = p.status === "open" ? p.gross : p.net;
    const st = p.status === "pending" ? `<span class="pill p-info">Buy 9:30</span>` : p.status === "open" ? `<span class="pill p-warn">Day ${p.day}/10</span>` : `<span class="pill ${p.net >= 0 ? "p-up" : "p-dn"}">Closed</span>`;
    const res = p.status === "pending" ? "" : `<div class="row" style="margin-top:10px"><span class="muted" style="font-size:12.5px">${p.status === "open" ? "P&L now" : "Net P&L"}</span><span><span class="pnl ${cl(val)}">${sgn(val)}</span> <span class="${cl(val)}" style="font-size:12.5px">${pct(val, p.entry * (p.qty_held ?? p.qty))}</span></span></div>`;
    return `<div class="card pick ${p.status !== "pending" ? (val >= 0 ? "win" : "loss") : ""}">
      <div class="row"><div><span class="rank">#${p.rank}</span> <span class="sym">${esc(p.symbol)}</span><div class="ind">${esc(p.industry || "")}</div></div>${st}</div>
      ${p.status === "pending"
        ? `<div class="kv"><div>Last close<b>${inr(p.close)}</b></div><div>Stop-loss<b class="${p.sl != null ? "dn" : "muted"}">${p.sl != null ? inr(p.sl) : "none"}</b></div><div>Qty / ₹50k<b>${p.qty}</b></div></div>
           <div class="ind" style="margin-top:8px">Buy at 9:30 on the next trading day</div>`
        : `<div class="kv"><div>Entry${p.entry_at ? " · " + esc(when(p.entry_at)) : ""}<b>${inr(p.entry)}</b></div><div>${p.status === "closed" ? "Exit" : "Now"}${p.last_at ? " · " + esc(tm(p.last_at)) : ""}<b>${inr(p.last ?? (p.status === "closed" ? p.exit : null))}</b></div><div>Stop-loss<b class="${p.sl != null ? "dn" : "muted"}">${p.sl != null ? inr(p.sl) : "none"}</b></div></div>
           <div class="ind" style="margin-top:6px">${p.qty_held ?? p.qty} shares${p.why ? " · " + esc(p.why) : ""}</div>`}${res}</div>`;
  };
  sec("swing", "Swing picks", `Picked after the close of ${fdl(D.swing_session)} · bought at 9:30 next day · hold 10 sessions · ₹50,000 each`, sw.length,
    sw.length ? `<div class="grid">` + sw.map(swingCard).join("") + `</div>` : `<div class="empty">No swing picks today. ${esc(D.swing_status || "")}</div>`);
  if (it) {
    const lo = (it.effect || {}).long || {};
    const side = s => {
      const rows = it.rows.filter(x => x.side === s);
      const tot = rows.reduce((a, x) => a + ((x.status === "closed" ? x.net : x.gross) || 0), 0);
      return `<div class="list"><h3><span><span class="pill ${s === "long" ? "p-up" : "p-dn"}">${s === "long" ? "Buy" : "Sell"}</span> ${s === "long" ? "Long" : "Short"} picks</span>
        <span class="${cl(tot)} num">${rows.some(x => x.status !== "pending") ? sgn(tot) : ""}</span></h3>` + (rows.length ? rows.map(x => `
        <div class="li"><div><span class="s">${esc(x.symbol)}</span>
          <div class="d">Entry ${esc(tm(x.entry_at) || "9:30")} ${inr(x.entry ?? x.ref)} → ${x.status === "closed" ? "exit" : "now"} ${esc(tm(x.last_at) || x.time || "")} ${inr(x.last ?? x.exit)}</div>
          <div class="d">SL ${inr(x.sl)}${x.qty ? " · " + x.qty + " sh" : ""}${x.why ? " · " + esc(x.why.startsWith("SL") ? "SL hit" : "squared off") : ""}</div></div>
        <div class="r ${cl(x.status === "closed" ? x.net : x.gross)}">${x.status === "pending" ? "waiting" : sgn(x.status === "closed" ? x.net : x.gross)}
          <div class="d" style="font-weight:600">${x.status === "open" ? "open" : x.status === "closed" ? "closed" : ""}</div></div></div>`).join("") : `<div class="li"><div class="d">${esc((it.blocked || {})[s] || "No picks")}</div></div>`) + `</div>`;
    };
    sec("intraday", "Intraday", `${fdl(it.day)} · picked and bought at 9:30 · square off by 15:10 · ₹50,000 each`, (it.rows || []).length,
      why("Paper only — no proven edge.", `In the 2022–2026 test the long picks averaged ${lo.backtest_pct_per_day_on_exposure ?? "–"}% a day after charges; at ${it.leverage}x leverage that is about ${lo.pct_per_day_on_capital ?? "–"}% of capital a day. Leverage multiplies losses as well as gains.`)
      + `<div class="cols">${side("long")}${side("short")}</div>`);
  }
  const op = (D.trades || []).filter(t => t.kind === "option").sort((a, b) => b.signal_date.localeCompare(a.signal_date))[0];
  let ob = why("Unproven — paper only.", "The 2022–2026 backtest looked strong, but buying a random call or put at 9:45 did just as well in the same price model, and real NSE option prices showed the model flatters option buyers by about ₹160–330 a trade. On real prices a random buyer loses a little. This paper trade, on real prices, is the test.");
  if (!op) ob += `<div class="empty">No option trade yet. The rule watches NIFTY from 9:45 to 13:00 each trading day.</div>`;
  else { const v = op.status === "closed" ? op.net : op.gross;
    ob += `<div class="grid"><div class="card pick ${v >= 0 ? "win" : "loss"}"><div class="row"><div><span class="sym">${esc(op.symbol)}</span><div class="ind">${esc(op.industry)}</div></div>
      <span class="pill ${op.status === "open" ? "p-warn" : v >= 0 ? "p-up" : "p-dn"}">${op.status === "open" ? "Open" : "Closed"}</span></div>
      <div class="kv"><div>Entry · ${esc(when(op.entry_at))}<b>${inr(op.entry)}</b></div><div>${op.status === "closed" ? "Exit" : "Now"} · ${esc(tm(op.last_at))}<b>${inr(op.last)}</b></div><div>SL<b class="dn" style="font-size:12.5px">${esc(op.sl_note)}</b></div></div>
      <div class="ind" style="margin-top:6px">${op.qty} qty (1 lot)${op.exit_reason ? " · " + esc(op.exit_reason) : ""}</div>
      <div class="row" style="margin-top:10px"><span class="muted" style="font-size:12.5px">${op.status === "open" ? "P&L now" : "Net P&L"}</span><span><span class="pnl ${cl(v)}">${sgn(v)}</span> <span class="${cl(v)}" style="font-size:12.5px">${pct(v, op.entry * op.qty)}</span></span></div></div></div>`; }
  sec("option", "NIFTY option", "9:15–9:45 range breakout · ATM call or put · SL = other side of the range · exit 15:15 · 1 lot · paper only", op ? 1 : 0, ob);
  const multi = (kind, label) => {
    const ts = (D.trades || []).filter(t => t.kind === kind && t.status !== "closed").sort((a, b) => b.signal_date.localeCompare(a.signal_date) || (b.gross || 0) - (a.gross || 0));
    return [ts.length, ts.length ? `<div class="list">` + ts.map(t => {
      const m = (t.marks || []).slice(-1)[0] || {}, v = t.status === "open" ? t.gross : 0;
      const det = t.status === "pending" ? label + esc(fdl(t.signal_date)) + " · buy at 9:30 next session"
        : "In " + esc(when(t.entry_at || t.entry_date)) + " " + inr(t.entry) + " → now " + inr(t.last) + " · day " + (m.session || 1) + " of " + t.hold + " · " + t.qty + " sh";
      return `<div class="li"><div><b>${esc(t.symbol)}</b><div class="d">${det}</div></div><span class="r ${cl(v)}">${t.status === "pending" ? `<span class="pill p-info">pending</span>` : sgn(v)}</span></div>`;
    }).join("") + `</div>` : null];
  };
  const [nr, rb] = multi("results", "Results day ");
  sec("results", "Results jump", "results-day jump of 4%+ on 2x volume · buy 9:30 next day · hold 10 sessions · no stop-loss · ₹50,000 each · paper test", nr,
    why("Promising, not proven.", "On 2017–2026 data it averaged +0.81% a trade after charges and beat random picks, but its t of 2.09 is below the 2.89 bar, and a few big winners made all the profit. This paper trade on real prices is the test.")
    + (rb || `<div class="empty">No open results-jump trades. The rule fires in results season, about 75 times a year.</div>`));
  const [nm, mb] = multi("momentum", "Picked ");
  sec("momentum", "12-1 momentum", "10 strongest 12-month gainers (skipping the last month) · rebalanced every 20 sessions · buy 9:30 · no stop-loss · ₹50,000 each · paper test", nm,
    why("Promising, not proven.", "2017–2026: +22% a year vs NIFTY +12%, but it fell up to 45% from a peak (NIFTY 30%) and lost 25% in 2018 and 16% in 2022. A few big winners made the profit. This paper trade on real prices is the test.")
    + (mb || `<div class="empty">No open momentum trades yet. New picks come every 20 sessions.</div>`));
  h += `<nav class="jump" aria-label="Jump to a strategy">` + secs.map(s => `<button type="button" data-jump="${s.id}">${s.title} <span class="n">${s.count}</span></button>`).join("") + `</nav>`;
  h += secs.map(s => {
    const open = !COLLAPSED.has(s.id);
    return `<section class="sec" id="sec-${s.id}" aria-labelledby="h-${s.id}"><h2 id="h-${s.id}"><button type="button" class="secbtn" data-sec="${s.id}" aria-expanded="${open}" aria-controls="body-${s.id}">
      <span class="chev" aria-hidden="true"></span><span>${s.title} <span class="cnt">${s.count}</span><span class="sub">${s.sub}</span></span></button></h2>
      <div id="body-${s.id}"${open ? "" : " hidden"}>${s.body}</div></section>`;
  }).join("");
  $("#v-today").innerHTML = h;
})();

// ---------- PORTFOLIO
T = D.trades || []; S = D.summary || {};
(function () {
  const openAll = T.filter(t => MULTI.includes(t.kind) && t.status === "open");
  const last = (D.daily || []).slice(-1)[0];
  const openCh = T.filter(t => t.status === "open").reduce((a, t) => a + (t.charges || 0), 0);
  const total = ((S.all || {}).realised || 0) + ((S.all || {}).unrealised || 0) - openCh;
  const closed = T.filter(t => t.status === "closed"), wins = closed.filter(t => t.net > 0);
  const k = [["Total P&L", sgn(total), cl(total), "closed + open, after charges"],
    ["Latest day", last ? sgn(last.net) : "–", last ? cl(last.net) : "", last ? fdl(last.date) : "no trades yet"],
    ["Open positions", openAll.length + " stocks", "", "invested " + inr(Math.round(openAll.reduce((a, t) => a + t.entry * t.qty, 0)))],
    ["Win rate", closed.length ? Math.round(wins.length / closed.length * 100) + "%" : "–", "", closed.length + " closed trades"]];
  let h = `<h2>Portfolio<span class="sub">Paper trades at ₹50,000 a stock, after Zerodha charges</span></h2><div class="kpis">` +
    k.map(([l, v, c, s]) => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${esc(s)}</div></div>`).join("") + `</div>`;
  const kinds = [["all", "All"], ...KINDS.filter(x => MULTI.includes(x[0]))];
  h += `<h2 id="h-open">Open positions<span class="sub">Multi-day paper trades · open a card for its day-by-day P&L</span></h2>
    <div class="tools"><div class="seg" role="group" aria-label="Show strategy">${kinds.map(([kk, l]) => `<button type="button" data-pk="${kk}" aria-pressed="${PF.kind === kk}">${l} <span class="n">${kk === "all" ? openAll.length : openAll.filter(t => t.kind === kk).length}</span></button>`).join("")}</div>
    <label class="sr" for="psort">Sort open positions</label><select id="psort" class="sel">${[["pnl", "P&L: high to low"], ["pnl_asc", "P&L: low to high"], ["pct", "% change"], ["day", "Days held"], ["name", "Name A–Z"]].map(([kk, l]) => `<option value="${kk}"${PF.sort === kk ? " selected" : ""}>${l}</option>`).join("")}</select></div>
    <p class="sumline" id="psum" aria-live="polite"></p><div id="pgrid"></div>`;
  $("#v-portfolio").innerHTML = h;
  const day = t => ((t.marks || []).slice(-1)[0] || {}).session || 0;
  const draw = () => {
    let rows = openAll.filter(t => PF.kind === "all" || t.kind === PF.kind);
    const key = {pnl: t => -t.gross, pnl_asc: t => t.gross, pct: t => -t.gross / (t.entry * t.qty), day: t => -day(t), name: t => t.symbol}[PF.sort];
    rows = rows.slice().sort((a, b) => { const x = key(a), y = key(b); return typeof x === "string" ? x.localeCompare(y) : x - y; });
    const tot = rows.reduce((a, t) => a + t.gross, 0);
    $("#psum").textContent = rows.length + " open " + (rows.length === 1 ? "position" : "positions") + " · P&L now " + sgn(tot);
    if (!rows.length) {
      const pend = T.filter(t => MULTI.includes(t.kind) && (PF.kind === "all" || t.kind === PF.kind) && t.status === "pending");
      $("#pgrid").innerHTML = `<div class="empty">${pend.length ? pend.length + " stocks will be bought at 9:30 on the next trading day: " + pend.map(t => esc(t.symbol)).join(", ") : "No open positions."}</div>`;
      return;
    }
    $("#pgrid").innerHTML = `<div class="grid">` + rows.map((t, i) => {
      const m = t.marks[t.marks.length - 1] || {}, id = "pm-" + i;
      return `<div class="card pick ${t.gross >= 0 ? "win" : "loss"}">
        <div class="row"><div>${kindPill(t)} <span class="sym">${esc(t.symbol)}</span><div class="ind">Bought ${esc(when(t.entry_at || t.entry_date))} at ${inr(t.entry)} · ${t.qty} shares</div></div>
        <div style="text-align:right"><div class="pnl ${cl(t.gross)}">${sgn(t.gross)}</div><div class="${cl(t.gross)}" style="font-size:12.5px">${pct(t.gross, t.entry * t.qty)}</div></div></div>
        <div class="kv"><div>Now · ${esc(tm(t.last_at) || m.at || "")}<b>${inr(m.mark)}</b></div><div>Today<b class="${cl(m.day_pnl)}">${sgn(m.day_pnl)}</b></div><div>Stop-loss<b class="dn">${t.sl != null ? inr(t.sl) : "none"}</b></div></div>
        <div class="prog" role="progressbar" aria-label="Days held" aria-valuemin="0" aria-valuemax="${t.hold}" aria-valuenow="${m.session}"><i style="width:${(m.session / t.hold * 100).toFixed(0)}%"></i></div>
        <div class="row" style="margin-top:4px"><span class="ind">Day ${m.session} of ${t.hold}</span>
        <button type="button" class="linkbtn" data-pos aria-expanded="false" aria-controls="${id}">Show day-by-day P&L</button></div>
        <div class="more" id="${id}" hidden><table><caption class="sr">${esc(t.symbol)} day-by-day P&L</caption><thead><tr><th scope="col">Day</th><th scope="col">Date</th><th scope="col" class="r">Close</th><th scope="col" class="r">Day P&L</th><th scope="col" class="r">Total</th></tr></thead><tbody>${
          t.marks.map(x => `<tr><td>${x.session}</td><td>${fd(x.date)}</td><td class="r">${inr(x.mark)}</td><td class="r ${cl(x.day_pnl)}">${sgn(x.day_pnl)}</td><td class="r ${cl(x.total_pnl)}">${sgn(x.total_pnl)}</td></tr>`).join("")}</tbody></table></div></div>`;
    }).join("") + `</div>`;
  };
  $("#v-portfolio").querySelectorAll("[data-pk]").forEach(b => b.onclick = () => {
    PF.kind = b.dataset.pk; $("#v-portfolio").querySelectorAll("[data-pk]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); draw(); });
  $("#psort").onchange = e => { PF.sort = e.target.value; draw(); };
  draw();
})();

// ---------- HISTORY
(function () {
  const box = $("#v-history");
  const count = kk => kk === "all" ? T.length : T.filter(t => t.kind === kk).length;
  const statuses = [["all", "All statuses"], ["open", "Open"], ["closed", "Closed"], ["won", "Closed: won"], ["lost", "Closed: lost"], ["pending", "Pending"]];
  if (!box.querySelector("#hl")) {
    box.innerHTML = `<h2>History<span class="sub">Every pick ever published, with its result</span></h2>
      <div class="tools"><div class="seg" role="group" aria-label="Show strategy">${[["all", "All"], ...KINDS].map(([kk, l]) => `<button type="button" data-k="${kk}" aria-pressed="${HS.kind === kk}">${l} <span class="n"></span></button>`).join("")}</div></div>
      <div class="tools"><label class="sr" for="hq">Search a stock</label><input id="hq" class="search" type="search" placeholder="Search a stock" autocomplete="off" spellcheck="false">
      <label class="sr" for="hs">Status</label><select id="hs" class="sel">${statuses.map(([kk, l]) => `<option value="${kk}">${l}</option>`).join("")}</select></div>
      <p class="sumline" id="hsum" aria-live="polite" tabindex="-1"></p><div id="hl"></div>`;
    box.querySelectorAll("[data-k]").forEach(b => b.onclick = () => {
      HS.kind = b.dataset.k; HS.limit = 20; box.querySelectorAll("[data-k]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); draw(); });
    let timer;
    $("#hq").oninput = e => { clearTimeout(timer); timer = setTimeout(() => { HS.q = e.target.value.trim().toUpperCase(); HS.limit = 20; draw(); }, 150); };
    $("#hs").onchange = e => { HS.status = e.target.value; HS.limit = 20; draw(); };
  }
  function draw() {
    box.querySelectorAll("[data-k]").forEach(b => { b.querySelector(".n").textContent = count(b.dataset.k); });
    const okStatus = t => HS.status === "all" || t.status === HS.status || (HS.status === "won" && t.status === "closed" && t.net > 0)
      || (HS.status === "lost" && t.status === "closed" && t.net <= 0);
    const rows = T.filter(t => (HS.kind === "all" || t.kind === HS.kind) && (!HS.q || t.symbol.includes(HS.q)) && okStatus(t));
    const cls = rows.filter(t => t.status === "closed"), won = cls.filter(t => t.net > 0).length, net = cls.reduce((a, t) => a + t.net, 0);
    $("#hsum").textContent = rows.length + " trades · " + cls.length + " closed" + (cls.length ? " · " + Math.round(won / cls.length * 100) + "% won · net " + sgn(net) : "");
    const days = [...new Set(rows.map(t => t.signal_date))].sort().reverse();
    const shown = days.slice(0, HS.limit);
    let lastMonth = "", h = "";
    shown.forEach(d => {
      const mon = String(d).slice(0, 7);
      if (mon !== lastMonth) {
        const ms = rows.filter(t => String(t.signal_date).startsWith(mon) && t.status === "closed").reduce((a, t) => a + t.net, 0);
        h += `<h3 class="month"><span>${new Date(mon + "-01T00:00:00").toLocaleDateString("en-IN", {month: "long", year: "numeric"})}</span><span class="${cl(ms)} num">${sgn(ms)}</span></h3>`;
        lastMonth = mon;
      }
      const ts = rows.filter(t => t.signal_date === d);
      const tot = ts.filter(t => t.status === "closed").reduce((a, t) => a + t.net, 0);
      h += `<div class="dayhead"><span>${fdl(d)}</span><span class="${cl(tot)} num">${ts.some(t => t.status === "closed") ? sgn(tot) : ""}</span></div>
        <div class="list">` + ts.map(t => {
          const st = t.status === "closed" ? `<span class="r ${cl(t.net)}">${sgn(t.net)}</span>` :
            t.status === "open" ? `<span class="r ${cl(t.gross)}">${sgn(t.gross)} <span class="pill p-warn">open</span></span>` : `<span class="r"><span class="pill p-info">pending</span></span>`;
          const det = t.entry == null ? "Waiting for 9:30 entry" + (t.sl != null ? " · SL " + inr(t.sl) : "") :
            `In ${esc(when(t.entry_at || t.entry_date))} ${inr(t.entry)} → ${t.status === "closed" ? "out" : "now"} ${esc(when(t.last_at))} ${inr(t.last ?? t.exit)} · ${t.qty} sh${t.exit_reason ? " · " + esc(t.exit_reason) : ""}`;
          return `<div class="li"><div>${kindPill(t)} <span class="s">${esc(t.symbol)}</span><div class="d">${det}</div></div>${st}</div>`;
        }).join("") + `</div>`;
    });
    if (!days.length) h = `<div class="empty">No trades match.</div>`;
    else if (days.length > HS.limit) h += `<button type="button" class="more-btn" id="hmore">Show older days (${days.length - HS.limit} more)</button>`;
    $("#hl").innerHTML = h;
    const more = $("#hmore");
    if (more) more.onclick = () => { HS.limit += 20; draw(); const n = $("#hmore"); if (n) n.focus(); else $("#hsum").focus(); };
  }
  draw();
})();

// ---------- STATS
(function () {
  const dd = D.daily || [];
  let h = `<h2>Stats<span class="sub">Live paper results, and how each method tested</span></h2>`;
  if (dd.length) {
    const W = 760, H = 210, P = 40, vals = dd.map(r => r.cumulative), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), sp = (hi - lo) || 1;
    const x = i => P + (dd.length === 1 ? (W - 2 * P) / 2 : i * (W - 2 * P) / (dd.length - 1)), y = v => H - P + 10 - (v - lo) / sp * (H - 2 * P);
    const line = dd.map((r, i) => `${x(i).toFixed(1)},${y(r.cumulative).toFixed(1)}`).join(" ");
    h += `<div class="chart"><div class="row"><b>Cumulative P&L</b><span class="${cl(vals[vals.length - 1])} num"><b>${sgn(vals[vals.length - 1])}</b></span></div>
      <svg class="c" viewBox="0 0 ${W} ${H}" role="img" aria-label="Cumulative P&L"><line class="g" x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}"/>
      <polyline points="${line}" fill="none" stroke="var(--brand)" stroke-width="2.5"/>${dd.map((r, i) => `<circle cx="${x(i)}" cy="${y(r.cumulative)}" r="4" fill="var(--brand)"><title>${fd(r.date)}: ${sgn(r.cumulative)}</title></circle>`).join("")}
      ${dd.map((r, i) => (dd.length < 12 || i % Math.ceil(dd.length / 8) === 0) ? `<text class="ax" x="${x(i)}" y="${H - 6}" text-anchor="middle">${fd(r.date)}</text>` : "").join("")}</svg></div>`;
    const m = Math.max(...dd.map(r => Math.abs(r.net)), 1), slot = (W - 2 * P) / dd.length, bw = Math.min(36, slot - 4), y0 = H / 2;
    h += `<div class="chart" style="margin-top:12px"><b>P&L by day</b><svg class="c" viewBox="0 0 ${W} ${H}" role="img" aria-label="Net P&L per day">
      <line class="g" x1="${P}" x2="${W - P}" y1="${y0}" y2="${y0}"/>` + dd.map((r, i) => {
        const bx = P + i * slot + (slot - bw) / 2, bh = Math.abs(r.net) / m * (H / 2 - 26);
        return `<rect x="${bx}" y="${r.net >= 0 ? y0 - bh : y0}" width="${bw}" height="${Math.max(bh, 1)}" rx="3" fill="${r.net >= 0 ? "var(--up)" : "var(--dn)"}"><title>${fd(r.date)}: ${sgn(r.net)}</title></rect>`;
      }).join("") + `</svg></div>`;
  } else h += `<div class="empty">Charts appear once trades start closing.</div>`;
  const closed = T.filter(t => t.status === "closed");
  const grp = (name, f) => { const ts = closed.filter(f), w = ts.filter(t => t.net > 0).length, tot = ts.reduce((a, t) => a + t.net, 0);
    return [name, ts.length, ts.length ? Math.round(w / ts.length * 100) + "%" : "–", ts.length ? sgn(tot / ts.length) : "–", tot, ts.length]; };
  const g = [grp("Swing", t => t.kind === "swing"), grp("Intraday — buy", t => t.kind === "intraday" && t.side === "long"), grp("Intraday — sell", t => t.kind === "intraday" && t.side === "short"), grp("NIFTY option (1 lot)", t => t.kind === "option"), grp("Results jump (paper test)", t => t.kind === "results"), grp("12-1 momentum (paper test)", t => t.kind === "momentum")];
  h += `<h2>By strategy</h2><div class="list"><table><thead><tr><th>Strategy</th><th class="r">Trades</th><th class="r">Won</th><th class="r">Avg</th><th class="r">Total</th></tr></thead><tbody>${
    g.map(r => `<tr><td>${r[0]}</td><td class="r">${r[1]}</td><td class="r">${r[2]}</td><td class="r">${r[3]}</td><td class="r ${cl(r[4])}"><b>${r[5] ? sgn(r[4]) : "–"}</b></td></tr>`).join("")}</tbody></table></div>`;
  const bySym = {}; closed.forEach(t => bySym[t.symbol] = (bySym[t.symbol] || 0) + t.net);
  const ranked = Object.entries(bySym).sort((a, b) => b[1] - a[1]);
  const mini = (title, arr) => `<div class="list"><h3>${title}</h3>${arr.length ? arr.map(([s, v]) => `<div class="li"><span class="s">${esc(s)}</span><span class="r ${cl(v)}">${sgn(v)}</span></div>`).join("") : `<div class="li"><span class="d">None yet</span></div>`}</div>`;
  h += `<h2>Best and worst stocks</h2><div class="cols">${mini("Best", ranked.filter(x => x[1] > 0).slice(0, 5))}${mini("Worst", ranked.filter(x => x[1] < 0).slice(-5).reverse())}</div>`;
  const V = D.verdicts || {}, sv = V.swing || {}, iv = V.intraday || {};
  const vp = v => `<span class="pill ${/KEEP|EDGE/.test(v || "") && !/NO/.test(v || "") ? "p-up" : /NO/.test(v || "") ? "p-dn" : "p-warn"}">${esc(v || "–")}</span>`;
  h += `<h2>How each method tested<span class="sub">Walk-forward on 10 years of NSE data, after charges</span></h2><div class="cols">
    <div class="card"><div class="row"><b>Swing · 10 sessions</b>${vp(sv.verdict)}</div><p class="muted" style="margin:8px 0 0;font-size:13.5px">Top 10 picks averaged
    <b class="${cl(sv.mean_pct)}">${sv.mean_pct != null ? (sv.mean_pct > 0 ? "+" : "") + sv.mean_pct.toFixed(2) + "%" : "–"}</b> per 10 sessions after costs (t ${sv.t ?? "–"}), ${sv.from ? String(sv.from).slice(0, 4) : ""}–${sv.to ? String(sv.to).slice(0, 4) : ""}, out of sample.</p></div>
    <div class="card"><div class="row"><b>Intraday · 9:30</b>${vp(iv.verdict)}</div><p class="muted" style="margin:8px 0 0;font-size:13.5px">Long picks averaged
    <b class="${cl(iv.mean_pct)}">${iv.mean_pct != null ? iv.mean_pct.toFixed(2) + "%" : "–"}</b> a day after costs. The ranking works, but the edge is smaller than the charges.</p></div>
    <div class="card"><div class="row"><b>NIFTY option · 9:45 breakout</b>${vp("UNPROVEN")}</div><p class="muted" style="margin:8px 0 0;font-size:13.5px">Did no better than a
    random call or put in the 2022–2026 price model, and real option prices show that model flatters buyers by ₹160–330 a trade. Paper-traded on real prices to find out.</p></div>
    <div class="card"><div class="row"><b>Results jump · 10 sessions</b>${vp("PROMISING")}</div><p class="muted" style="margin:8px 0 0;font-size:13.5px">+0.81% a trade after
    charges on 2017–2026 (798 trades, 8 of 10 years positive, beat random picks), but t 2.09 is below the 2.89 bar and a few big winners made all the profit. Paper-traded to find out.</p></div>
    <div class="card"><div class="row"><b>12-1 momentum · 20 sessions</b>${vp("PROMISING")}</div><p class="muted" style="margin:8px 0 0;font-size:13.5px">2017–2026: +22% a year vs NIFTY +12%,
    but a worst fall of −45% (NIFTY −30%), losing years 2018 and 2022, and t 2.60 below the 3.01 bar. The best 5% of trades made the profit. Paper-traded to find out.</p></div></div>
    <p><a href="research.html" style="color:var(--brand)">Full research results →</a></p>`;
  $("#v-stats").innerHTML = h;
})();
}
let T = [], S = {};
renderAll();
show(location.hash.slice(1) || "today");

// ---------- live prices: GitHub refreshes data/live.json every 10 minutes in market hours
const LIVE = "https://raw.githubusercontent.com/AshokKumar3502/nse-research-lab/main/docs/data/live.json";
async function pullLive() {
  try {
    const r = await fetch(LIVE + "?t=" + Date.now(), {cache: "no-store"});
    if (!r.ok) return;
    const L = await r.json();
    if ((L.updated || {}).ts > ((D.updated || {}).ts || 0)) {
      const keep = location.hash.slice(1) || "today", y = window.scrollY;
      D = L; renderAll(); show(keep); window.scrollTo(0, y);
    }
  } catch (e) { /* offline: keep what is shown */ }
}
pullLive(); setInterval(pullLive, 3 * 60 * 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pullLive(); });
</script></body></html>"""
