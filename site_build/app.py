"""The main page (docs/index.html): an app-style view of today's picks, open positions, every past
trade and the stats, in light / dark / system themes, laid out for phones and desktops.

Data is embedded in the page (so it also works inside the Android app and when opened from disk) and
written to docs/data/app.json; docs/data/notify.json tells the Android app when new picks are out.
"""
import datetime
import glob
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
    base = {"at": now.strftime("%d %b %Y, %H:%M IST"), "ts": int(now.timestamp()), "by": who, "url": None}   # no link: keeps the account name out of the app
    if env.get("PHASE") == "live" and now.time() < datetime.time(15, 20):
        base["by"] = "GitHub cloud · live prices every 10 min (run #%s)" % env.get("GITHUB_RUN_NUMBER", "?")
        nxt = now + datetime.timedelta(minutes=10)
        base["next"] = "Live prices around %s" % nxt.replace(minute=nxt.minute - nxt.minute % 10).strftime("%H:%M")
        return base
    # the next scheduled update (weekdays: intraday picks at 9:30, swing picks after the 18:45 run)
    t = now
    for _ in range(8):
        if t.weekday() < 5:
            for hh, mm, label in ((9, 46, "the morning option watch"), (18, 55, "the day's final option result")):
                slot = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if slot > now:
                    when = ("today" if slot.date() == now.date() else slot.strftime("%a %d %b"))
                    return dict(base, next="%s around %s %s" % (label[0].upper() + label[1:],slot.strftime("%H:%M"), when))
        t = (t + datetime.timedelta(days=1)).replace(hour=0, minute=0)
    return dict(base, next=None)


def collect():
    book = _j(os.path.join(DOCS, "paper.json")) or {"trades": [], "daily": [], "summary": {}}
    for t in book.get("trades", []):          # option trades: results only in the app, no wording about the rule
        if t.get("kind") == "option":
            w = str(t.get("exit_reason") or "")
            if w:
                t["exit_reason"] = "Stop-loss" if w.startswith(("SL", "Stop")) else "Time exit" if "15:15" in w or w.startswith("Time") else w
            n = str(t.get("sl_note") or "")
            t["sl_note"] = n.replace("Option below Rs ", "Rs ")
    option_day = {}                          # latest session: status and BE READY moments per instrument (for the app timeline)
    try:
        from optionlab import nifty_orb
        files = glob.glob(os.path.join(nifty_orb.PICKS, "option-*-*.json"))
        last = max((os.path.basename(f)[-15:-5] for f in files), default=None)
        for ix, c in nifty_orb.INDEXES.items():
            p = os.path.join(nifty_orb.PICKS, "option-%s-%s.json" % (c["file"], last)) if last else ""
            if p and os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    o = json.load(fh)
                option_day[ix] = {"day": o.get("day"), "status": o.get("status"), "near": o.get("near") or []}
    except Exception:          # noqa: BLE001
        pass
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
            "verdicts": verdicts, "option_day": option_day}


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
    msgs = []                     # option-only app: no stock-pick messages
    for ixn in ("NIFTY", "SENSEX", "BSE", "ADANIENT", "DIXON", "PNB"):
        ops = sorted((t for t in data.get("trades", []) if t.get("kind") == "option"
                      and (t.get("index") or ("SENSEX" if str(t.get("symbol", "")).startswith("SENSEX") else "NIFTY")) == ixn),
                     key=lambda t: t["signal_date"])
        if not ops:
            continue
        o = ops[-1]
        v = o["net"] if o["status"] == "closed" else (o.get("gross") or 0)
        msgs.append({"id": "%s-%s-%s" % ("option" if ixn == "NIFTY" else ixn.lower(), o["signal_date"], o["status"]), "ts": now,
                     "title": "%s option %s" % (ixn, "closed" if o["status"] == "closed" else "open"),
                     "text": "%s · entry Rs %s · %s Rs %s" % (o["symbol"], o["entry"], "net" if o["status"] == "closed" else "now",
                                                          format(int(round(v)), ","))})
    notify = {"built": data["built"], "messages": msgs}
    with open(os.path.join(DOCS, "data", "notify.json"), "w", encoding="utf-8") as fh:
        json.dump(notify, fh)
    _icons()
    with open(os.path.join(DOCS, "sw.js"), "w", encoding="utf-8") as fh:
        fh.write(SW)
    manifest = {"name": "NIFTY Option Lab", "short_name": "Option Lab", "start_url": "./", "scope": "./", "display": "standalone",
                "orientation": "portrait", "background_color": "#070b16", "theme_color": "#0a0f1d",
                "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                          {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                          {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                          {"src": "logo.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}
    with open(os.path.join(DOCS, "manifest.webmanifest"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    html = PAGE.replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/"))
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    return os.path.join(DOCS, "index.html")


SW = r"""// offline: network first, last good copy when offline (never serves stale pages while online)
const C = "option-lab-v1";
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || !/^https?:/.test(u.protocol)) return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && (u.origin === location.origin || u.hostname === "raw.githubusercontent.com")) {
      const c = r.clone(); caches.open(C).then(x => x.put(e.request.url.split("?")[0], c));
    }
    return r;
  }).catch(() => caches.match(e.request.url.split("?")[0]).then(r => r || caches.match("./"))));
});
"""


def _icons():
    """App icons (PNG) for the home screen: gradient square with a rising line."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    for n in (192, 512):
        out = os.path.join(DOCS, "icon-%d.png" % n)
        if os.path.exists(out):
            continue
        im = Image.new("RGB", (n, n))
        px = im.load()
        a, b = (99, 102, 241), (34, 211, 238)
        for yy in range(n):
            for xx in range(n):
                t = (xx + yy) / (2 * n)
                px[xx, yy] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
        d = ImageDraw.Draw(im)
        w = max(4, n // 18)
        pts = [(n * .18, n * .70), (n * .40, n * .48), (n * .55, n * .60), (n * .82, n * .28)]
        d.line(pts, fill=(255, 255, 255), width=w, joint="curve")
        r = w * 1.1
        d.ellipse([n * .82 - r, n * .28 - r, n * .82 + r, n * .28 + r], fill=(255, 255, 255))
        im.save(out, "PNG", optimize=True)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>NIFTY Option Lab</title><meta name="description" content="Paper-trading dashboard: live option trades, results and backtests.">
<meta name="theme-color" content="#0a0f1d"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet"><link rel="icon" href="logo.svg" type="image/svg+xml"><link rel="manifest" href="manifest.webmanifest"><link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Option Lab">
<script>try{var t=localStorage.getItem("theme");if(t&&t!=="system")document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{--bg:#f4f6fc;--surface:#ffffff;--surface2:#eef1f9;--surface3:#e4e9f5;--text:#0c1224;--muted:#5d6782;--line:#e2e7f2;
--brand:#4f46e5;--brand2:#0891b2;--brandtx:#fff;--up:#059669;--upbg:#d1fae5;--dn:#e11d48;--dnbg:#ffe4e6;
--warn:#b45309;--warnbg:#fef3c7;--info:#2563eb;--infobg:#dbeafe;
--grad:linear-gradient(135deg,#4f46e5 0%,#7c3aed 55%,#0891b2 100%);--glow:rgba(79,70,229,.10);
--shadow:0 1px 2px rgba(12,18,36,.05),0 8px 24px rgba(12,18,36,.06);--shadow2:0 12px 32px rgba(12,18,36,.12);
--sans:"Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:"JetBrains Mono",ui-monospace,"SFMono-Regular",Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--bg:#070b16;--surface:#0e1527;--surface2:#151e35;--surface3:#1c2742;
--text:#e9edf7;--muted:#8b97b4;--line:#1e2a46;--brand:#818cf8;--brand2:#22d3ee;--brandtx:#0a0f1d;--up:#34d399;--upbg:rgba(52,211,153,.13);
--dn:#fb7185;--dnbg:rgba(251,113,133,.13);--warn:#fbbf24;--warnbg:rgba(251,191,36,.11);--info:#60a5fa;--infobg:rgba(96,165,250,.13);
--grad:linear-gradient(135deg,#6366f1 0%,#a855f7 55%,#22d3ee 100%);--glow:rgba(129,140,248,.16);--shadow:0 1px 0 rgba(255,255,255,.03) inset,0 10px 30px rgba(0,0,0,.35);--shadow2:0 16px 40px rgba(0,0,0,.5)}}
:root[data-theme=dark]{color-scheme:dark;--bg:#070b16;--surface:#0e1527;--surface2:#151e35;--surface3:#1c2742;
--text:#e9edf7;--muted:#8b97b4;--line:#1e2a46;--brand:#818cf8;--brand2:#22d3ee;--brandtx:#0a0f1d;--up:#34d399;--upbg:rgba(52,211,153,.13);
--dn:#fb7185;--dnbg:rgba(251,113,133,.13);--warn:#fbbf24;--warnbg:rgba(251,191,36,.11);--info:#60a5fa;--infobg:rgba(96,165,250,.13);
--grad:linear-gradient(135deg,#6366f1 0%,#a855f7 55%,#22d3ee 100%);--glow:rgba(129,140,248,.16);--shadow:0 1px 0 rgba(255,255,255,.03) inset,0 10px 30px rgba(0,0,0,.35);--shadow2:0 16px 40px rgba(0,0,0,.5)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;color:var(--text);font:15px/1.5 var(--sans);font-feature-settings:"cv11","ss01";
background:radial-gradient(900px 420px at 0% -8%,var(--glow),transparent 70%),radial-gradient(700px 380px at 100% 0%,color-mix(in srgb,var(--brand2) 9%,transparent),transparent 70%),var(--bg);background-attachment:fixed;min-height:100vh}
header.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 78%,transparent);backdrop-filter:saturate(1.4) blur(14px);-webkit-backdrop-filter:saturate(1.4) blur(14px);border-bottom:1px solid var(--line)}
.bar{max-width:1180px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:11px;font-weight:800;font-size:18px;letter-spacing:-.02em;color:var(--text);text-decoration:none}
.brand>span{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.brand img{width:36px;height:36px;border-radius:11px;box-shadow:0 4px 14px var(--glow)}
.brand small{display:block;font-weight:500;font-size:11.5px;color:var(--muted);letter-spacing:0;-webkit-text-fill-color:var(--muted)}
nav.tabs{display:flex;gap:2px;margin-left:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:4px}
nav.tabs button,.bnav button{font:inherit;border:0;background:none;color:var(--muted);cursor:pointer}
nav.tabs button{padding:7px 13px;border-radius:10px;font-weight:600;font-size:14px;transition:background .15s,color .15s}
nav.tabs button:hover{color:var(--text);background:var(--surface2)}
nav.tabs button[aria-current=page]{background:var(--grad);color:#fff;box-shadow:0 4px 14px var(--glow)}
.iconbtn{width:40px;height:40px;border-radius:12px;border:1px solid var(--line);background:var(--surface);color:var(--text);display:grid;place-items:center;cursor:pointer;margin-left:auto;transition:transform .15s}
.iconbtn:hover{transform:translateY(-1px)}nav.tabs+.iconbtn{margin-left:0}.iconbtn svg{width:18px;height:18px}
main{max-width:1180px;margin:0 auto;padding:18px 16px 100px}
h2{font-size:19px;font-weight:800;margin:26px 0 12px;letter-spacing:-.02em}h2 .sub{display:block;font-weight:500;font-size:13px;color:var(--muted);margin-top:3px;letter-spacing:0}
.view{display:none}.view.on{display:block;animation:fadein .25s ease}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;padding:6px 11px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--muted)}
.chip b{color:var(--text)}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.dot.up{background:var(--up);box-shadow:0 0 0 3px var(--upbg)}.dot.dn{background:var(--dn);box-shadow:0 0 0 3px var(--dnbg)}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:var(--shadow);position:relative;transition:transform .15s,box-shadow .15s}
.card.pick{overflow:hidden}.card.pick::before{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:var(--grad)}
.card.pick.win::before{background:linear-gradient(90deg,var(--up),color-mix(in srgb,var(--up) 30%,transparent))}
.card.pick.loss::before{background:linear-gradient(90deg,var(--dn),color-mix(in srgb,var(--dn) 30%,transparent))}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.sym{font-weight:800;font-size:17px;letter-spacing:-.01em}.ind{font-size:12.5px;color:var(--muted)}
.rank{font-size:11px;font-weight:800;color:var(--brand);background:color-mix(in srgb,var(--brand) 14%,transparent);border-radius:7px;padding:2px 7px}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}.kv div{font-size:11px;color:var(--muted);background:var(--surface2);border-radius:10px;padding:7px 9px;text-transform:uppercase;letter-spacing:.04em;font-weight:600}
.kv b{display:block;font-size:14px;color:var(--text);font-family:var(--mono);font-weight:700;letter-spacing:0;text-transform:none;margin-top:2px}
.kv b.dn{color:var(--dn)}.kv b.up{color:var(--up)}
.pill{font-size:10.5px;font-weight:800;letter-spacing:.05em;padding:4px 9px;border-radius:999px;text-transform:uppercase;white-space:nowrap}
.p-up{background:var(--upbg);color:var(--up)}.p-dn{background:var(--dnbg);color:var(--dn)}.p-warn{background:var(--warnbg);color:var(--warn)}
.p-info{background:var(--infobg);color:var(--info)}.p-mute{background:var(--surface2);color:var(--muted)}
.up{color:var(--up)}.dn{color:var(--dn)}.muted{color:var(--muted)}.num{font-variant-numeric:tabular-nums;font-family:var(--mono)}
.pnl{font-size:21px;font-weight:700;font-family:var(--mono);letter-spacing:-.02em}
.banner{border-radius:14px;padding:12px 14px;font-size:13.5px;margin:10px 0}
.banner.warn{background:var(--warnbg);color:var(--text);border:1px solid color-mix(in srgb,var(--warn) 35%,transparent)}
.cols{display:grid;gap:14px;grid-template-columns:1fr 1fr}
.list{background:var(--surface);border:1px solid var(--line);border-radius:18px;overflow:hidden;overflow-x:auto;box-shadow:var(--shadow)}
.list>table{min-width:max-content}.list>table td,.list>table th{white-space:nowrap}
.list h3{margin:0;padding:12px 16px;font-size:14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:8px;background:var(--surface2)}
.li{display:grid;grid-template-columns:1fr auto;gap:4px 10px;padding:11px 16px;border-bottom:1px solid var(--line);align-items:center;transition:background .12s}
.li:hover{background:var(--surface2)}
.li:last-child{border-bottom:0}.li .s{font-weight:700}.li .d{font-size:12.5px;color:var(--muted)}.li .r{text-align:right;font-weight:700;font-family:var(--mono);white-space:nowrap}
.kpis{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.kpi{overflow:hidden}.kpi::after{content:"";position:absolute;right:-30px;top:-30px;width:90px;height:90px;border-radius:50%;background:var(--glow)}
.kpi .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-weight:700}
.kpi .v{font-size:24px;font-weight:700;font-family:var(--mono);letter-spacing:-.03em;margin-top:4px}.kpi .s{font-size:12px;color:var(--muted);margin-top:2px}
.prog{height:6px;border-radius:4px;background:var(--surface3);overflow:hidden;margin-top:10px}.prog i{display:block;height:100%;background:var(--grad);border-radius:4px}
.pos{cursor:pointer}.pos .more{display:none;margin-top:10px}.pos.open .more{display:block}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;background:var(--surface2)}
tbody tr:hover td{background:var(--surface2)}
td.r,th.r{text-align:right;font-family:var(--mono);white-space:nowrap}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0 12px}
.seg{display:inline-flex;background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:4px;gap:2px}
.seg button{font:inherit;font-size:13px;border:0;background:none;color:var(--muted);padding:6px 12px;border-radius:9px;cursor:pointer;font-weight:600;transition:background .15s,color .15s}
.seg button:hover{color:var(--text)}
.seg button[aria-pressed=true]{background:var(--grad);color:#fff;box-shadow:0 3px 10px var(--glow)}
input.search{font:inherit;padding:9px 13px;border-radius:12px;border:1px solid var(--line);background:var(--surface);color:var(--text);min-width:0;flex:1;max-width:280px}
.dayhead{display:flex;justify-content:space-between;align-items:center;margin:16px 2px 6px;font-weight:700;font-size:14px}
.chart{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:14px;box-shadow:var(--shadow)}
svg.c{width:100%;height:auto;display:block}svg.c .g{stroke:var(--line)}svg.c .ax{fill:var(--muted);font-size:11px;font-family:var(--sans)}
.empty{color:var(--muted);padding:20px;text-align:center;background:var(--surface);border:1px dashed var(--line);border-radius:18px}
footer{margin-top:40px;font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:16px}footer a{color:var(--brand)}
.upd{display:flex;gap:10px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-radius:16px;
padding:11px 15px;margin:4px 0 12px;font-size:13.5px;box-shadow:var(--shadow)}.upd .dot{margin-top:7px;flex:none}.upd a{color:var(--brand)}
.upd .muted{font-size:12.5px}
.lastupd{position:fixed;left:0;right:0;bottom:0;z-index:25;background:color-mix(in srgb,var(--surface) 88%,transparent);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-top:1px solid var(--line);
font-size:12.5px;color:var(--muted);padding:7px 16px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lastupd b{color:var(--text)}.lastupd .dot{display:inline-block;margin-right:6px;vertical-align:middle;animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.45}}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.skip{position:absolute;left:-999px;top:8px;z-index:60;background:var(--brand);color:var(--brandtx);padding:8px 14px;border-radius:10px;font-weight:700}.skip:focus{left:8px}
:focus-visible{outline:3px solid var(--brand2);outline-offset:2px;border-radius:6px}
.seg{max-width:100%;overflow-x:auto;scrollbar-width:none}.seg::-webkit-scrollbar{display:none}.seg button{white-space:nowrap}.seg .n,.jump .n{font-size:11.5px;opacity:.8;margin-left:3px;font-family:var(--mono)}
.jump{display:flex;gap:8px;overflow-x:auto;padding:2px 0 6px;margin:14px 0 0;scrollbar-width:none}.jump::-webkit-scrollbar{display:none}
.jump button{flex:none;font:inherit;font-size:13px;font-weight:600;padding:8px 13px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--text);cursor:pointer;transition:border-color .15s}
.jump button:hover{border-color:var(--brand)}
.sec{scroll-margin-top:76px}.sec>h2{margin:24px 0 12px}
.secbtn{all:unset;box-sizing:border-box;display:flex;gap:10px;align-items:flex-start;width:100%;cursor:pointer;font-size:19px;font-weight:800;letter-spacing:-.02em}
.secbtn:focus-visible{outline:3px solid var(--brand2);outline-offset:4px}
.secbtn .chev{flex:none;width:10px;height:10px;margin-top:9px;border-right:2.5px solid var(--muted);border-bottom:2.5px solid var(--muted);transform:rotate(-45deg);transition:transform .15s}
.secbtn[aria-expanded=true] .chev{transform:rotate(45deg)}
.cnt{display:inline-block;font-size:12px;font-weight:800;color:var(--brand);background:color-mix(in srgb,var(--brand) 14%,transparent);border-radius:999px;padding:1px 9px;vertical-align:middle;font-family:var(--mono)}
details.why{padding:10px 14px}details.why summary{cursor:pointer}details.why[open] summary{margin-bottom:2px}
.linkbtn{font:inherit;font-size:12.5px;font-weight:700;border:0;background:none;color:var(--brand);cursor:pointer;padding:4px 0}
.more-btn{display:block;margin:18px auto 0;font:inherit;font-weight:700;padding:11px 20px;border-radius:13px;border:1px solid var(--line);background:var(--surface);color:var(--text);cursor:pointer;box-shadow:var(--shadow)}
.more-btn:hover{border-color:var(--brand)}
select.sel{font:inherit;font-size:13px;padding:8px 10px;border-radius:12px;border:1px solid var(--line);background:var(--surface);color:var(--text)}
.sumline{font-size:13px;color:var(--muted);margin:0 2px 8px}
h3.month{display:flex;justify-content:space-between;margin:24px 2px 2px;font-size:15px;padding-bottom:7px;border-bottom:2px solid var(--line)}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
.bt-kpis .v{font-size:20px}.bt-kpis .v.sm{font-size:15px;line-height:1.35}.nw{white-space:nowrap}.bt-kpis .kpi{padding:14px}
.btlist{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));margin-top:10px}
.btday{padding:14px 16px;overflow:hidden}.btday::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--line)}
.btday.win::before{background:var(--up)}.btday.loss::before{background:var(--dn)}
.btday .dt{font-weight:700;font-size:14.5px}.btday .pnl{font-size:19px}
.kv.kv4{grid-template-columns:repeat(4,1fr)}
.btmeta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}
.btd{margin-top:10px;font-size:12.5px;color:var(--muted);line-height:1.6;background:var(--surface2);border-radius:12px;padding:9px 11px}
/* crosshair, arrows, flash, ring, sheet nav */
.chwrap{position:relative;touch-action:pan-y}.chtip{position:absolute;top:8px;pointer-events:none;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow2);border-radius:10px;padding:5px 9px;font:700 12px var(--mono);white-space:nowrap;transform:translateX(-50%);display:none}
.arr{font-size:.7em;margin-right:4px;vertical-align:.12em}
@keyframes flashup{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--up) 60%,transparent)}100%{box-shadow:0 0 0 14px transparent}}
@keyframes flashdn{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--dn) 60%,transparent)}100%{box-shadow:0 0 0 14px transparent}}
.flash-up{animation:flashup 1s ease-out}.flash-dn{animation:flashdn 1s ease-out}
.glance{display:grid;grid-template-columns:auto 1fr;gap:16px;align-items:center;margin:4px 0 2px}
.ring{width:118px;height:118px}.ring text{font-family:var(--mono)}
.glg{display:grid;grid-template-columns:repeat(2,auto);gap:6px 18px;font-size:13px;justify-content:start}.glg span{display:inline-flex;align-items:center;gap:7px}.glg i{width:10px;height:10px;border-radius:3px;display:inline-block}
.sumbar .sp{flex-basis:100%;height:46px;margin-top:2px}.sumbar .sp svg{width:100%;height:46px;display:block}
.tdnav{display:flex;gap:6px}.tdnav button{border:0;background:rgba(255,255,255,.18);color:#fff;width:36px;height:36px;border-radius:10px;font-size:18px;cursor:pointer}.tdnav button[disabled]{opacity:.35}
.tdpos{font-size:11.5px;opacity:.85;margin-top:2px}
#install{display:none}
/* reels */
.reels{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.reel video{width:100%;aspect-ratio:9/16;border-radius:14px;background:#000;display:block}
/* market status bar */
.mkt{max-width:1180px;margin:0 auto;padding:0 16px 8px;display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--muted)}
.mkt .st{display:inline-flex;align-items:center;gap:7px;font-weight:700;color:var(--text)}.mkt .st .dot{width:9px;height:9px}
.mkt .st.open .dot{background:var(--up);box-shadow:0 0 0 3px var(--upbg);animation:pulse 1.6s infinite}
.mkt .sep{opacity:.5}.mkt .ago{margin-left:auto;white-space:nowrap}
.mkt button{font:inherit;font-size:12px;font-weight:700;border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;padding:4px 10px;cursor:pointer}
.mkt button[disabled]{opacity:.6}
@media (max-width:520px){.mkt .nx{display:none}}
/* pull to refresh */
.ptr{position:fixed;left:50%;top:0;z-index:40;transform:translate(-50%,-60px);transition:transform .2s;pointer-events:none}
.ptr span{display:inline-block;background:var(--surface);color:var(--text);border:1px solid var(--line);box-shadow:var(--shadow2);border-radius:999px;padding:8px 14px;font-size:12.5px;font-weight:700}
.ptr.show{transform:translate(-50%,calc(env(safe-area-inset-top) + 70px))}
/* instrument colours */
.ixd{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--ix,var(--brand));margin-right:7px;vertical-align:middle;flex:none}
.tile{border-left:4px solid var(--ix,var(--line))}
/* trade screen */
dialog#tradedlg{border:1px solid var(--line);border-radius:22px;padding:0;width:min(760px,calc(100vw - 24px));max-height:calc(100vh - 40px);background:var(--surface);color:var(--text);box-shadow:var(--shadow2)}
dialog#tradedlg::backdrop{background:rgba(3,6,15,.55);backdrop-filter:blur(3px)}
.tdh{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 18px;background:var(--grad);color:#fff}
.tdh h3{margin:0;font-size:18px;letter-spacing:-.01em}.tdh .x{border:0;background:rgba(255,255,255,.18);color:#fff;width:36px;height:36px;border-radius:10px;font-size:20px;cursor:pointer}
.tdb{padding:16px 18px 20px}.tdkv{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.tdkv div{background:var(--surface2);border-radius:12px;padding:9px 11px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:700}
.tdkv b{display:block;font-family:var(--mono);font-size:15px;color:var(--text);text-transform:none;letter-spacing:0;margin-top:3px}
.tl{list-style:none;margin:10px 0 0;padding:0}.tl li{display:grid;grid-template-columns:62px 14px 1fr auto;gap:10px;align-items:center;padding:9px 0;border-bottom:1px dashed var(--line);font-size:13.5px}
.tl li:last-child{border-bottom:0}.tl .tm{font-family:var(--mono);color:var(--muted)}.tl i{width:12px;height:12px;border-radius:50%;background:var(--muted)}
.tl i.up{background:var(--up)}.tl i.dn{background:var(--dn)}.tl i.br{background:var(--brand)}.tl .r{font-family:var(--mono);font-weight:700}
@media (max-width:520px){.tdkv{grid-template-columns:repeat(2,1fr)}}
.grab{display:none}
@media (max-width:560px){
 dialog#tradedlg{margin:0;position:fixed;inset:auto 0 0 0;width:100%;max-width:100%;max-height:90vh;border-radius:24px 24px 0 0;border-bottom:0;animation:sheetup .28s cubic-bezier(.2,.8,.2,1)}
 .grab{display:block;position:absolute;left:50%;top:7px;transform:translateX(-50%);width:44px;height:5px;border-radius:3px;background:rgba(255,255,255,.55)}
 .tdh{padding-top:20px;touch-action:none}}
@keyframes sheetup{from{transform:translateY(100%)}to{transform:none}}
/* live board */
.board{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));margin:6px 0 4px}
.tile{cursor:pointer;padding:14px 14px 10px;overflow:hidden}.tile:hover{transform:translateY(-2px);box-shadow:var(--shadow2)}
.tile::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--grad)}
.tile.win::before{background:var(--up)}.tile.loss::before{background:var(--dn)}
.tile .nm{font-weight:800;font-size:15px;letter-spacing:-.01em}.tile .sy{font-size:12px;color:var(--muted);margin-top:2px;min-height:18px}
.tile .pl{font-family:var(--mono);font-weight:700;font-size:22px;letter-spacing:-.03em;margin-top:6px}
.tile svg{display:block;width:100%;height:34px;margin-top:6px}
.sumbar{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;justify-content:space-between;padding:14px 18px;margin:6px 0 12px;
background:var(--grad);color:#fff;border-radius:18px;box-shadow:0 10px 30px var(--glow)}
.sumbar .k{font-size:11px;text-transform:uppercase;letter-spacing:.07em;opacity:.85;font-weight:700}
.sumbar .v{font-family:var(--mono);font-size:22px;font-weight:700;letter-spacing:-.03em}
/* calendar */
.cal{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
.calm{padding:12px}.calm .t{font-weight:700;font-size:13px;display:flex;justify-content:space-between;margin-bottom:8px}
.calg{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}.calg span{aspect-ratio:1;border-radius:5px;background:var(--surface2)}
.calg .h{background:none;font-size:9.5px;color:var(--muted);text-align:center;aspect-ratio:auto;font-weight:700}
.legend{display:flex;gap:6px;align-items:center;font-size:11.5px;color:var(--muted);margin:8px 2px}.legend i{width:12px;height:12px;border-radius:3px;display:inline-block}
/* compare */
.cmpbar{display:grid;grid-template-columns:110px 1fr 70px;gap:10px;align-items:center;padding:8px 16px;border-bottom:1px solid var(--line);font-size:13px}
.cmpbar:last-child{border-bottom:0}.cmpbar b{font-family:var(--mono);text-align:right}
.cmpbar .trk{height:10px;border-radius:6px;background:var(--surface3);overflow:hidden}.cmpbar .trk i{display:block;height:100%;border-radius:6px}
.lgd{display:flex;flex-wrap:wrap;gap:12px;font-size:12.5px;margin-top:8px}.lgd span{display:inline-flex;align-items:center;gap:6px}.lgd i{width:14px;height:4px;border-radius:2px;display:inline-block}
@media (max-width:520px){.kv.kv4{grid-template-columns:repeat(2,1fr)}.btlist{grid-template-columns:1fr}.bt-kpis .v{font-size:18px}
 .kpis{grid-template-columns:repeat(2,1fr);gap:10px}.kpi{padding:12px}.kpi .v,.bt-kpis .v{font-size:17px}.bt-kpis .v.sm{font-size:12.5px}
 .board{grid-template-columns:repeat(2,1fr)}.tile .pl{font-size:18px}.cal{grid-template-columns:repeat(2,1fr)}.cmpbar{grid-template-columns:84px 1fr 60px;padding:8px 12px}}
.bnav{display:none}
@media (max-width:820px){nav.tabs{display:none}nav.tabs+.iconbtn{margin-left:auto}
 .lastupd{bottom:calc(62px + env(safe-area-inset-bottom));font-size:11.5px}main{padding-bottom:136px}
 .bnav{display:grid;grid-template-columns:repeat(7,1fr);position:fixed;left:0;right:0;bottom:0;z-index:30;background:color-mix(in srgb,var(--surface) 90%,transparent);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid var(--line);padding:6px 2px calc(6px + env(safe-area-inset-bottom))}
 .bnav button{display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;font-weight:700;padding:5px 1px;border-radius:12px;margin:0 2px}
 .bnav button svg{width:21px;height:21px}.bnav button[aria-current=page]{color:var(--brand);background:color-mix(in srgb,var(--brand) 13%,transparent)}
 .cols{grid-template-columns:1fr}.brand small{display:none}.kpi .v{font-size:21px}}
</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="bar">
<a class="brand" href="#today"><img src="logo.svg" alt=""><span>NIFTY Option Lab<small id="upd"></small></span></a>
<nav class="tabs" aria-label="Sections"></nav>
<button class="iconbtn" id="theme" title="Theme" aria-label="Change theme"></button>
</div><div class="mkt" id="mkt" role="status" aria-live="polite"></div></header>
<div class="ptr" id="ptr" aria-hidden="true"><span></span></div>
<main id="main" tabindex="-1">
<section class="view" id="v-today"></section>

<section class="view" id="v-history"></section>
<section class="view" id="v-stats"><div id="v-portfolio"></div><div id="v-stats-in"></div></section>
<section class="view" id="v-backtest"></section>
<section class="view" id="v-compare"></section>
<section class="view" id="v-reels"></section>
<section class="view" id="v-highs"></section>
<footer>Paper trading 1 lot of NIFTY options a day on real NSE option prices, after Zerodha charges. For education and research only; not
investment advice, and the author is not a SEBI-registered adviser. Nothing here places orders.</footer>
</main>
<dialog id="tradedlg" aria-labelledby="tdh"><div id="tdbody"></div></dialog>
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
 backtest: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 3v4M7 15v6M17 3v8M17 17v4"/><rect x="4.5" y="7" width="5" height="8" rx="1"/><rect x="14.5" y="11" width="5" height="6" rx="1"/></svg>',
 highs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18"/><path d="M5 16l4-6 4 3 6-9"/><circle cx="19" cy="4" r="1.6"/></svg>',
 reels: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 8h18M8 3l3 5M14 3l3 5"/><path d="M10 12l5 3-5 3z" fill="currentColor"/></svg>',
 compare: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-6"/><path d="M7 19l4-3 3 1 5-4" opacity=".5"/></svg>',
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
const views = [["today", "Dashboard"], ["backtest", "Backtest"], ["compare", "Compare"], ["reels", "Reels"], ["highs", "Exit vs High"], ["history", "History"], ["stats", "Stats"]];
$("nav.tabs").innerHTML = views.map(([k, l]) => `<button data-v="${k}">${l}</button>`).join("");
$(".bnav").innerHTML = views.map(([k, l]) => `<button data-v="${k}" aria-label="${l}">${I[k]}<span>${{highs: "Highs", today: "Home"}[k] || l}</span></button>`).join("");
function show(v) {
  setTimeout(() => decorate(), 60);
  if (!views.some(x => x[0] === v)) v = "today";
  document.querySelectorAll(".view").forEach(s => s.classList.toggle("on", s.id === "v-" + v));
  document.querySelectorAll("[data-v]").forEach(b => { if (b.dataset.v === v) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
  if (v === "backtest") loadBacktest();
  if (v === "highs") loadHighs();
  if (v === "compare") loadCompare();
  if (v === "reels") loadReels();
  window.scrollTo(0, 0);
}
document.querySelectorAll("[data-v]").forEach(b => b.onclick = () => show(b.dataset.v));
window.addEventListener("hashchange", () => show(location.hash.slice(1)));


// ---------- EXIT VS HIGH: actual exit P&L vs selling at the trade's high (hindsight), from the backtest file
const HF = {year: "all", limit: 30};
async function loadHighs() {
  const box = $("#v-highs");
  if (!BTS[BTI]) {
    box.innerHTML = `<h2>Exit vs High</h2>${ixSeg()}<div class="empty" role="status">Loading…</div>`;
    try { await getBT(); }
    catch (e) { box.innerHTML = `<h2>Exit vs High</h2><div class="empty" role="alert">Could not load the data (${esc(String(e.message || e))}).</div>`; return; }
  }
  BT = BTS[BTI];
  const tr = BT.days.filter(d => d.status === "closed" && d.high_pnl != null);
  const act = tr.reduce((a, d) => a + d.pnl, 0), hi = tr.reduce((a, d) => a + d.high_pnl, 0);
  const yrs = {};
  tr.forEach(d => { const y = d.date.slice(0, 4); yrs[y] = yrs[y] || {n: 0, a: 0, h: 0}; yrs[y].n++; yrs[y].a += d.pnl; yrs[y].h += d.high_pnl; });
  const hrs = {};
  tr.forEach(d => { const hh = String(d.high_time || "").slice(0, 2); if (hh) hrs[hh] = (hrs[hh] || 0) + 1; });
  const hmax = Math.max(1, ...Object.values(hrs));
  let h = `<h2>Exit vs High<span class="sub">What each trade made at exit vs at its highest price (hindsight) · ${fdl(BT.from)} – ${fdl(BT.to)} · ${esc(BT.index || "NIFTY")} · 1 lot, after charges</span></h2>${ixSeg()}
    <details class="why banner warn"><summary><b>Hindsight, not a target.</b> <span class="muted">Why?</span></summary><p style="margin:6px 0 0">Nobody knows the high while a trade is open. Option prices before the paper trades are modelled.</p></details>
    <div class="kpis bt-kpis">
      <div class="card kpi"><div class="l">Actual exit P&L</div><div class="v ${cl(act)}">${sgn(act)}</div><div class="s">at exit · ${tr.length} trades</div></div>
      <div class="card kpi"><div class="l">If sold at the high</div><div class="v ${cl(hi)}">${sgn(hi)}</div><div class="s">hindsight best</div></div>
      <div class="card kpi"><div class="l">Left on the table</div><div class="v">${sgn(hi - act)}</div><div class="s">avg ${sgn((hi - act) / Math.max(1, tr.length))} a trade</div></div>
      <div class="card kpi"><div class="l">Trades that were in profit</div><div class="v">${Math.round(tr.filter(d => d.high_pnl > 0).length / Math.max(1, tr.length) * 100)}%</div><div class="s">at some point after entry</div></div></div>
    <h2>By year</h2><div class="list"><table><caption class="sr">Actual vs at the high by year</caption><thead><tr><th scope="col">Year</th><th scope="col" class="r">Trades</th><th scope="col" class="r">Actual</th><th scope="col" class="r">At the high</th><th scope="col" class="r">Left</th></tr></thead><tbody>${
      Object.keys(yrs).sort().map(y => `<tr><td>${y}</td><td class="r">${yrs[y].n}</td><td class="r ${cl(yrs[y].a)}"><b>${sgn(yrs[y].a)}</b></td><td class="r ${cl(yrs[y].h)}">${sgn(yrs[y].h)}</td><td class="r muted">${sgn(yrs[y].h - yrs[y].a)}</td></tr>`).join("")}</tbody></table></div>
    <h2>When the high came<span class="sub">Number of trades whose best price was in each hour</span></h2>
    <div class="list">${Object.keys(hrs).sort().map(hh => `<div class="li"><div><b>${hh}:00–${hh}:59</b><div class="prog" style="margin-top:6px"><i style="width:${(hrs[hh] / hmax * 100).toFixed(0)}%"></i></div></div><span class="r">${hrs[hh]}</span></div>`).join("")}</div>
    <h2>When the high came · every 15 minutes<span class="sub">Trades whose best price was in each 15-minute slot · bar = all trades · W = winning trades, L = losing trades</span></h2>
    <div class="list">${(() => {
      const q = {};
      tr.forEach(d => { const t = String(d.high_time || ""); if (!t) return; const mm = Math.floor(+t.slice(3, 5) / 15) * 15;
        const k = t.slice(0, 2) + ":" + String(mm).padStart(2, "0"); q[k] = q[k] || {a: 0, w: 0, l: 0}; q[k].a++; if (d.pnl > 0) q[k].w++; else q[k].l++; });
      const qmax = Math.max(1, ...Object.values(q).map(v => v.a));
      let cum = 0;
      return Object.keys(q).sort().map(k => { cum += q[k].a; const [hh, mm] = k.split(":"); const end = new Date(2000, 0, 1, +hh, +mm + 14);
        return `<div class="li"><div><b>${k}–${String(end.getHours()).padStart(2, "0")}:${String(end.getMinutes()).padStart(2, "0")}</b>
          <span class="muted" style="font-size:12px"> · W ${q[k].w} · L ${q[k].l} · ${(q[k].a / tr.length * 100).toFixed(1)}% · cumulative ${(cum / tr.length * 100).toFixed(0)}%</span>
          <div class="prog" style="margin-top:6px"><i style="width:${(q[k].a / qmax * 100).toFixed(0)}%"></i></div></div><span class="r">${q[k].a}</span></div>`; }).join("");
    })()}</div>
    <h2>Every trade</h2><div class="tools"><div class="seg" role="group" aria-label="Year">${[["all", "All"], ...Object.keys(yrs).sort().map(y => [y, y])].map(([k, l]) => `<button type="button" data-hy="${k}" aria-pressed="${HF.year === k}">${l}</button>`).join("")}</div></div>
    <p class="sumline" id="hisum" aria-live="polite" tabindex="-1"></p><div id="hilist"></div>`;
  box.innerHTML = h;
  wireIx(box, loadHighs);
  box.querySelectorAll("[data-hy]").forEach(b => b.onclick = () => { HF.year = b.dataset.hy; HF.limit = 30;
    box.querySelectorAll("[data-hy]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); drawHighs(tr); });
  drawHighs(tr);
}
function drawHighs(tr) {
  const rows = tr.filter(d => HF.year === "all" || d.date.startsWith(HF.year)).slice().reverse();
  const a = rows.reduce((x, d) => x + d.pnl, 0), hh = rows.reduce((x, d) => x + d.high_pnl, 0);
  $("#hisum").textContent = rows.length + " trades · actual " + sgn(a) + " · at the high " + sgn(hh) + " · left " + sgn(hh - a);
  let h = `<div class="btlist">` + rows.slice(0, HF.limit).map(d => `<article class="card btday ${d.pnl > 0 ? "win" : "loss"}">
    <div class="row"><span class="dt">${esc(fdl(d.date))}</span><b>${esc(BT.index || "NIFTY")} ${d.strike} ${d.side}</b></div>
    <div class="kv kv4"><div>Entry · ${esc(d.entry_time)}<b>${inr(d.entry)}</b></div><div>High · ${esc(d.high_time)}<b>${inr(d.high)}</b></div>
      <div>Exit · ${esc(d.exit_time)}<b>${inr(d.exit)}</b></div><div>Exit reason<b style="font-size:12px">${esc(d.why || "–")}</b></div></div>
    <div class="kv kv4"><div>P&L at the high<b class="${cl(d.high_pnl)}">${sgn(d.high_pnl)}</b></div><div>P&L at exit<b class="${cl(d.pnl)}">${sgn(d.pnl)}</b></div>
      <div>Left on table<b class="muted">${sgn(d.high_pnl - d.pnl)}</b></div><div>Capital<b>${inr(d.capital)}</b></div></div></article>`).join("") + `</div>`;
  if (!rows.length) h = `<div class="empty">No trades.</div>`;
  else if (rows.length > HF.limit) h += `<button type="button" class="more-btn" id="himore">Show more (${rows.length - HF.limit} older trades)</button>`;
  $("#hilist").innerHTML = h;
  const more = $("#himore");
  if (more) more.onclick = () => { HF.limit += 30; drawHighs(tr); const n = $("#himore"); (n || $("#hisum")).focus(); };
}

// ---------- BACKTEST: the option rule replayed day by day, NIFTY (docs/data/backtest.json, 2022-) or SENSEX
// (docs/data/backtest-sensex.json, Jun 2023-), each loaded on first open
let BT = null, BTI = "NIFTY";
const BTS = {};
const BTF = {month: "all", kind: "all", limit: 30};
const IXN = {NIFTY: "NIFTY", SENSEX: "SENSEX", BSE: "BSE Ltd", ADANIENT: "Adani Ent", DIXON: "Dixon", PNB: "PNB"};
const ixSeg = () => `<div class="tools"><div class="seg" role="group" aria-label="Index">${["NIFTY", "SENSEX", "BSE", "ADANIENT", "DIXON", "PNB"].map(i =>
  `<button type="button" data-bti="${i}" aria-pressed="${BTI === i}">${ixDot(i)}${IXN[i]}</button>`).join("")}</div></div>`;
async function fetchBT(i) {
  if (BTS[i]) return BTS[i];
  const r = await fetch(({SENSEX: "data/backtest-sensex.json", BSE: "data/backtest-bse.json", ADANIENT: "data/backtest-adanient.json",
    DIXON: "data/backtest-dixon.json", PNB: "data/backtest-pnb.json"}[i] || "data/backtest.json") + "?t=" + Date.now(), {cache: "no-store"});
  if (!r.ok) throw new Error("HTTP " + r.status);
  return (BTS[i] = await r.json());
}
const getBT = () => fetchBT(BTI);
function wireIx(box, reload) {
  box.querySelectorAll("[data-bti]").forEach(b => b.onclick = () => {
    if (BTI === b.dataset.bti) return;
    BTI = b.dataset.bti; BT = BTS[BTI] || null; BTF.month = "all"; BTF.limit = 30; HF.year = "all"; HF.limit = 30; reload();
  });
}
async function loadBacktest() {
  const box = $("#v-backtest");
  if (BTS[BTI]) { BT = BTS[BTI]; renderBacktest(); return; }
  box.innerHTML = `<h2>Backtest</h2>${ixSeg()}<div class="empty" role="status">Loading the backtest…</div>`;
  wireIx(box, loadBacktest);
  try {
    BT = await getBT();
  } catch (e) {
    box.innerHTML = `<h2>Backtest</h2><div class="empty" role="alert">Could not load the backtest (${esc(String(e.message || e))}). Check the connection and open this tab again.</div>`;
    return;
  }
  renderBacktest();
}
function renderBacktest() {
  const box = $("#v-backtest"), S = BT.summary, M = BT.method || {};
  const mshort = m => new Date(m + "-01T00:00:00").toLocaleDateString("en-IN", {month: "short"});
  const k = [["Net P&L", sgn(S.net), cl(S.net), "1 lot a day, after charges"],
    ["After model correction", `<span class="nw">${sgn(S.corrected_net[0])}</span> to <span class="nw">${sgn(S.corrected_net[1])}</span>`, cl(S.corrected_net[0]), "realistic range"],
    ["Win rate", (S.win_pct ?? "–") + "%", "", S.won + " of " + S.trades + " trades won"],
    ["Avg win / loss", `<span class="nw">${sgn(S.avg_win)}</span> / <span class="nw">${sgn(S.avg_loss)}</span>`, "", "per trade"],
    ["Capital a trade", inr(S.capital_avg), "", "average · highest " + inr(S.capital_max)],
    ["Worst fall", sgn(S.max_drawdown), "dn", "from a peak · worst day " + sgn(S.worst)],
    ["If sold at the high", S.high_net != null ? sgn(S.high_net) : "–", cl(S.high_net), "hindsight best · vs actual " + sgn(S.net)],
    ["Targets reached", (() => { const r = tgRates(BT.days); return r ? r.map((v, i) => "T" + (i + 1) + " " + v + "%").join(" · ") : "–"; })(), "",
     "before the stop-loss"]];
  let h = `<h2>Backtest · ${String(BT.from).slice(0, 4)}–${String(BT.to).slice(0, 4)}<span class="sub">${fdl(BT.from)} – ${fdl(BT.to)} · ${esc(IXN[BT.index] || BT.index || "NIFTY")} · 1 lot (${M.lot || 65}${["BSE", "ADANIENT", "DIXON", "PNB"].includes(BT.index) ? " shares" : ""}) a day · after charges</span></h2>${ixSeg()}
    <details class="why banner warn"><summary><b>Model prices, not proven.</b> <span class="muted">How to read this</span></summary>
      <p style="margin:6px 0 0">${esc(M.note || "")} Capital = the premium paid for 1 lot. Past results are not predictions.</p></details>
    <div class="kpis bt-kpis">` + k.map(([l, v, c, s2]) => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c}${v.length > 11 ? " sm" : ""}">${v}</div><div class="s">${esc(s2)}</div></div>`).join("") + `</div>`;
  const pts = BT.days.filter(d => d.status !== "holiday");
  if (pts.length > 1) {
    const W = 760, H = 190, P = 34, vals = pts.map(d => d.cum), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), sp = (hi - lo) || 1;
    const x = i => P + i * (W - 2 * P) / (pts.length - 1), y = v => H - 22 - (v - lo) / sp * (H - 44);
    const line = pts.map((d, i) => `${x(i).toFixed(1)},${y(d.cum).toFixed(1)}`).join(" ");
    const many = BT.months.length > 12;
    const ticks = BT.months.map((m, mi) => { if (many && !m.month.endsWith("-01") && mi !== 0) return "";
      const i = pts.findIndex(d => d.date.startsWith(m.month));
      return i < 0 ? "" : `<text class="ax" x="${x(i)}" y="${H - 4}" text-anchor="start">${many ? m.month.slice(0, 4) : mshort(m.month)}</text>`; }).join("");
    h += `<div class="chart" style="margin-top:12px"><div class="row"><b>Running total</b><span class="${cl(vals[vals.length - 1])} num"><b>${sgn(vals[vals.length - 1])}</b></span></div>
      <svg class="c" viewBox="0 0 ${W} ${H}" role="img" aria-label="Running total of the backtest, ${esc(sgn(vals[vals.length - 1]))} at the end">
      <line class="g" x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}"/>
      <polygon points="${x(0)},${y(0)} ${line} ${x(pts.length - 1)},${y(0)}" fill="color-mix(in srgb,var(--brand) 16%,transparent)"/>
      <polyline points="${line}" fill="none" stroke="var(--brand)" stroke-width="2.5" stroke-linejoin="round"/>${ticks}</svg></div>`;
  }
  const yrs = {};
  BT.months.forEach(m => { const y = m.month.slice(0, 4); yrs[y] = (yrs[y] || 0) + m.net; });
  h += `<h2>By year<span class="sub">Tap a year to see its days</span></h2>
    <div class="tools"><div class="seg" role="group" aria-label="Year">` +
    [["all", "All", S.net], ...Object.keys(yrs).sort().map(y => [y, y, yrs[y]])].map(([kk, l, n]) =>
      `<button type="button" data-btm="${kk}" aria-pressed="${BTF.month === kk}">${l} <span class="n ${cl(n)}">${sgn(n)}</span></button>`).join("") + `</div></div>
    <div class="tools"><div class="seg" role="group" aria-label="Show">` +
    [["all", "All days"], ["win", "Wins"], ["loss", "Losses"], ["none", "No trade"]].map(([kk, l]) =>
      `<button type="button" data-btk="${kk}" aria-pressed="${BTF.kind === kk}">${l}</button>`).join("") + `</div></div>
    <div id="btcal"></div>${weekdayTable()}
    <h2>Every day<span class="sub">Newest first · tap Details for the expiry</span></h2>
    <p class="sumline" id="btsum" aria-live="polite" tabindex="-1"></p><div id="btlist"></div>`;
  box.innerHTML = h;
  wireIx(box, loadBacktest);
  box.querySelectorAll("[data-btm]").forEach(b => b.onclick = () => { BTF.month = b.dataset.btm; BTF.limit = 30;
    box.querySelectorAll("[data-btm]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); drawBacktest(); });
  box.querySelectorAll("[data-btk]").forEach(b => b.onclick = () => { BTF.kind = b.dataset.btk; BTF.limit = 30;
    box.querySelectorAll("[data-btk]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); drawBacktest(); });
  box.onclick = e => {
    const b = e.target.closest("[data-btd]");
    if (!b) return;
    const open = b.getAttribute("aria-expanded") !== "true";
    b.setAttribute("aria-expanded", String(open)); b.textContent = open ? "Hide details" : "Details";
    document.getElementById(b.getAttribute("aria-controls")).hidden = !open;
  };
  drawBacktest();
}
function drawBacktest() {
  const all = BT.days.filter(d => d.status !== "holiday").slice().reverse();
  const rows = all.filter(d => (BTF.month === "all" || d.date.startsWith(BTF.month)) &&
    (BTF.kind === "all" || (BTF.kind === "none" ? d.status !== "closed" : d.status === "closed" && (BTF.kind === "win" ? d.pnl > 0 : d.pnl <= 0))));
  const tr = rows.filter(d => d.status === "closed"), net = tr.reduce((a, d) => a + d.pnl, 0), won = tr.filter(d => d.pnl > 0).length;
  const capMax = Math.max(0, ...tr.map(d => d.capital || 0));
  drawCalendar();
  $("#btsum").textContent = rows.length + " days · " + tr.length + " trades" + (tr.length ? " · " + Math.round(won / tr.length * 100) + "% won · net " + sgn(net) + " · capital up to " + inr(capMax) : "");
  let h = "", last = "";
  rows.slice(0, BTF.limit).forEach(d => {
    const mo = d.date.slice(0, 7);
    if (mo !== last) {
      const mm = BT.months.find(m => m.month === mo) || {};
      h += (last ? `</div>` : "") + `<h3 class="month"><span>${new Date(mo + "-01T00:00:00").toLocaleDateString("en-IN", {month: "long", year: "numeric"})}</span><span class="${cl(mm.net)} num">${sgn(mm.net)}</span></h3><div class="btlist">`;
      last = mo;
    }
    if (d.status !== "closed") {
      h += `<article class="card btday"><div class="row"><span class="dt">${esc(fdl(d.date))}</span><span class="pill p-mute">No trade</span></div>
        <div class="ind" style="margin-top:6px">${esc(d.why || "No signal")}</div></article>`;
      return;
    }
    const id = "btd-" + d.date, sl = /^SL/.test(d.why || "");
    h += `<article class="card btday ${d.pnl > 0 ? "win" : "loss"}">
      <div class="row"><span class="dt">${esc(fdl(d.date))}</span><span class="pnl ${cl(d.pnl)}">${sgn(d.pnl)}</span></div>
      <div class="btmeta"><span class="pill ${d.side === "CE" ? "p-up" : "p-dn"}">${d.side === "CE" ? "Call" : "Put"}</span><b>${esc(BT.index || "NIFTY")} ${d.strike} ${d.side}</b>
        <span class="pill ${sl ? "p-dn" : "p-mute"}">${sl ? "Stop-loss" : "Time exit"}</span>${d.source === "real" ? `<span class="pill p-info">Real prices</span>` : ""}${d.expiry_day ? `<span class="pill p-warn">Expiry day</span>` : ""}</div>
      <div class="kv kv4"><div>Entry · ${esc(d.entry_time)}<b>${inr(d.entry)}</b></div><div>Exit · ${esc(d.exit_time)}<b>${inr(d.exit)}</b></div>
        <div>Capital<b>${inr(d.capital)}</b></div><div>Total so far<b class="${cl(d.cum)}">${sgn(d.cum)}</b></div></div>
      ${tgRow(d)}${d.high != null ? `<div class="kv kv4"><div>High · ${esc(d.high_time)}<b>${inr(d.high)}</b></div><div>P&L at the high<b class="${cl(d.high_pnl)}">${sgn(d.high_pnl)}</b></div>
        <div>P&L at exit<b class="${cl(d.pnl)}">${sgn(d.pnl)}</b></div><div>Left on table<b class="muted">${sgn(d.high_pnl - d.pnl)}</b></div></div>` : ""}
      <button type="button" class="linkbtn" data-btd aria-expanded="false" aria-controls="${id}">Details</button>
      <div class="btd" id="${id}" hidden>Expiry ${esc(d.expiry || "–")} · exit reason: ${esc(d.why || "")}${d.sl_option != null ? " · stop-loss " + inr(d.sl_option) : ""}</div></article>`;
  });
  if (last) h += `</div>`;
  if (!rows.length) h = `<div class="empty">No days match.</div>`;
  else if (rows.length > BTF.limit) h += `<button type="button" class="more-btn" id="btmore">Show more (${rows.length - BTF.limit} older days)</button>`;
  $("#btlist").innerHTML = h;
  const more = $("#btmore");
  if (more) more.onclick = () => { BTF.limit += 30; drawBacktest(); const n = $("#btmore"); (n || $("#btsum")).focus(); };
}


// ---------- TARGETS (information only)
const TGR = [0.75, 1.5, 2.25];
const tgOf = d => d.status === "closed" && d.high != null && d.sl_option != null && d.sl_option < d.entry ? TGR.map(k => d.entry + k * (d.entry - d.sl_option)) : null;
function tgRates(days) {
  const ok = days.filter(tgOf);
  return ok.length ? [0, 1, 2].map(i => Math.round(ok.filter(d => d.high >= tgOf(d)[i]).length / ok.length * 100)) : null;
}
function tgRow(d) {
  const tg = tgOf(d); if (!tg) return "";
  return `<div class="kv">${tg.map((v, i) => `<div>T${i + 1}${d.high >= v ? " ✓" : ""}<b class="${d.high >= v ? "up" : ""}">${inr(Math.round(v * 100) / 100)}</b></div>`).join("")}</div>`;
}


// ---------- TRADE SCREEN: one paper trade, its price since entry, stop-loss, targets and timeline
function tradeChart(t) {
  const pts = (t.path || []).map(([hm, v]) => [+hm.slice(0, 2) * 60 + +hm.slice(3, 5), v]);
  if (pts.length < 2) return `<div class="empty" style="margin-top:12px">The minute-by-minute chart is kept for paper trades from 27 Nov 2026.</div>`;
  const tg = t.targets || [], hits = t.target_hits || [];
  const nxt = tg.find((v, i) => !hits.some(h => h.t === i + 1));
  const lv = [t.entry, t.sl_price, ...tg.filter((v, i) => hits.some(h => h.t === i + 1)), nxt].filter(v => v != null);
  const vals = pts.map(p => p[1]).concat(lv), lo = Math.min(...vals), hi = Math.max(...vals), sp = (hi - lo) || 1;
  const small = innerWidth < 560, W = small ? 420 : 720, H = small ? 260 : 300, L = 10, R = small ? 84 : 92, T = 14, B = 26;
  const x0 = pts[0][0], x1 = Math.max(pts[pts.length - 1][0], x0 + 30);
  const x = m => L + (m - x0) / (x1 - x0) * (W - L - R), y = v => T + (hi - v) / sp * (H - T - B);
  const v = t.status === "closed" ? t.net : t.gross, col = v >= 0 ? "var(--up)" : "var(--dn)";
  const line = (val, color, label, dash) => val == null ? "" : `<line x1="${L}" x2="${W - R}" y1="${y(val).toFixed(1)}" y2="${y(val).toFixed(1)}" stroke="${color}" stroke-width="1.5" ${dash ? 'stroke-dasharray="5 4"' : ""}/>
    <text x="${W - R + 6}" y="${(y(val) + 4).toFixed(1)}" fill="${color}" font-size="11.5" font-weight="700" font-family="var(--mono)">${label} ${Number(val).toFixed(2)}</text>`;
  const st = small ? 60 : 30, ticks = []; for (let m = Math.ceil(x0 / st) * st; m <= x1; m += st) ticks.push(m);
  const poly = pts.map(p => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const mk = hm => +hm.slice(0, 2) * 60 + +hm.slice(3, 5);
  CH = {pts, x0, x1, W, L, R, T, H, B, lo, hi, sp, entry: t.entry, qty: t.qty};
  return `<div class="chart chwrap" style="margin-top:12px"><div class="chtip" id="chtip"></div><svg class="c" id="chsvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Option price since entry · touch or hover to read the price">
    ${ticks.map(m => `<line class="g" x1="${x(m)}" x2="${x(m)}" y1="${T}" y2="${H - B}" stroke-dasharray="2 5"/><text class="ax" x="${x(m)}" y="${H - 8}" text-anchor="middle">${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}</text>`).join("")}
    ${line(t.sl_price, "var(--dn)", "SL", true)}${line(t.entry, "var(--muted)", "Entry", false)}
    ${tg.map((val, i) => (hits.some(h => h.t === i + 1) || val === nxt) ? line(val, "var(--up)", "T" + (i + 1), !hits.some(h => h.t === i + 1)) : "").join("")}
    <polygon points="${x(pts[0][0])},${y(t.entry)} ${poly} ${x(pts[pts.length - 1][0])},${y(t.entry)}" fill="${col}" opacity=".10"/>
    <polyline points="${poly}" fill="none" stroke="${col}" stroke-width="2.4" stroke-linejoin="round"/>
    ${hits.map(h => `<circle cx="${x(mk(h.at))}" cy="${y(h.price)}" r="5" fill="var(--up)" stroke="var(--surface)" stroke-width="2"><title>T${h.t} at ${h.at}</title></circle>`).join("")}
    <circle cx="${x(pts[pts.length - 1][0])}" cy="${y(pts[pts.length - 1][1])}" r="5.5" fill="${col}" stroke="var(--surface)" stroke-width="2"/>
    <line id="chx" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--text)" stroke-width="1" opacity="0"/><circle id="chc" r="5" fill="var(--text)" opacity="0"/></svg></div>`;
}
let CH = null;
function chartRead(clientX) {
  const svg = $("#chsvg"); if (!svg || !CH) return;
  const b = svg.getBoundingClientRect(), vx = (clientX - b.left) / b.width * CH.W;
  const m = CH.x0 + (vx - CH.L) / (CH.W - CH.L - CH.R) * (CH.x1 - CH.x0);
  let best = CH.pts[0]; CH.pts.forEach(p => { if (Math.abs(p[0] - m) < Math.abs(best[0] - m)) best = p; });
  const px = CH.L + (best[0] - CH.x0) / (CH.x1 - CH.x0) * (CH.W - CH.L - CH.R), py = CH.T + (CH.hi - best[1]) / CH.sp * (CH.H - CH.T - CH.B);
  const hm = String(Math.floor(best[0] / 60)).padStart(2, "0") + ":" + String(best[0] % 60).padStart(2, "0"), pl = (best[1] - CH.entry) * CH.qty;
  $("#chx").setAttribute("x1", px); $("#chx").setAttribute("x2", px); $("#chx").setAttribute("opacity", ".45");
  $("#chc").setAttribute("cx", px); $("#chc").setAttribute("cy", py); $("#chc").setAttribute("opacity", "1");
  const tip = $("#chtip"); tip.style.display = "block"; tip.style.left = Math.min(Math.max(px / CH.W * b.width, 60), b.width - 60) + "px";
  tip.innerHTML = `${hm} · ${inr(best[1])} · <span class="${cl(pl)}">${sgn(pl)}</span>`;
}
function chartHide() { if (!$("#chsvg")) return; $("#chx").setAttribute("opacity", "0"); $("#chc").setAttribute("opacity", "0"); $("#chtip").style.display = "none"; }
document.addEventListener("pointermove", e => { if (e.target.closest && e.target.closest("#chsvg")) chartRead(e.clientX); });
document.addEventListener("pointerdown", e => { if (e.target.closest && e.target.closest("#chsvg")) chartRead(e.clientX); });
document.addEventListener("pointerleave", e => chartHide(), true);
document.addEventListener("touchmove", e => { if (e.target.closest && e.target.closest("#chsvg")) chartRead(e.touches[0].clientX); }, {passive: true});
document.addEventListener("touchend", e => { if (e.target.closest && e.target.closest("#chsvg")) setTimeout(chartHide, 1500); });
const tradeList = () => (D.trades || []).filter(x => x.kind === "option" && x.status !== "pending")
  .sort((a, b) => b.signal_date.localeCompare(a.signal_date) || String(b.entry_at || "").localeCompare(String(a.entry_at || "")));
let CUR = null;
function stepTrade(d) {
  const L = tradeList(), i = L.findIndex(x => x.id === CUR);
  const n = L[i + d]; if (n) { openTrade(n.id, true); $("#tdbody").animate && $("#tdbody").animate([{transform: `translateX(${d > 0 ? 40 : -40}px)`, opacity: .3}, {transform: "none", opacity: 1}], {duration: 220, easing: "ease-out"}); }
}
function openTrade(id, keepOpen) {
  const t = (D.trades || []).find(x => x.id === id); if (!t) return;
  CUR = id;
  const LST = tradeList(), pos = LST.findIndex(x => x.id === id);
  const v = t.status === "closed" ? t.net : (t.gross || 0), cap = t.entry * t.qty;
  const hits = t.target_hits || [], tg = t.targets || [];
  const ev = [[tm(t.entry_at), "br", "Entry", inr(t.entry)]];
  hits.forEach(h => ev.push([h.at, "up", "Target " + h.t + " reached", inr(h.price)]));
  if (t.status === "closed") ev.push([t.exit_time || tm(t.last_at), v >= 0 ? "up" : "dn", t.exit_reason || "Exit", inr(t.exit)]);
  else ev.push([tm(t.last_at), "", "Now (open)", inr(t.last)]);
  const ic = IXC[ixOf(t)] || "#6366f1";
  $("#tdbody").innerHTML = `<div class="tdh" style="background:linear-gradient(135deg,${ic},color-mix(in srgb,${ic} 55%,#0a0f1d))"><span class="grab"></span><div><h3 id="tdh">${esc(t.symbol)}</h3><div style="font-size:12.5px;opacity:.9">${esc(fdl(t.signal_date))}${t.expiry ? " · expiry " + esc(fd(t.expiry)) : ""} · ${t.qty} qty (1 lot)</div>${pos >= 0 ? `<div class="tdpos">Trade ${pos + 1} of ${LST.length} · swipe sideways for others</div>` : ""}</div>
      <div class="tdnav"><button type="button" aria-label="Newer trade" data-step="-1" ${pos <= 0 ? "disabled" : ""}>‹</button><button type="button" aria-label="Older trade" data-step="1" ${pos < 0 || pos >= LST.length - 1 ? "disabled" : ""}>›</button>
      <button class="x" type="button" aria-label="Close" data-close>×</button></div></div>
    <div class="tdb"><div class="row"><span class="pill ${t.status === "open" ? "p-warn" : v > 0 ? "p-up" : "p-dn"}">${t.status === "open" ? "Open" : v > 0 ? "Won" : "Lost"}</span>
      <span><span class="pnl ${cl(v)}">${sgn(v)}</span> <span class="${cl(v)}" style="font-size:13px">${pct(v, cap)}</span></span></div>
    <div class="tdkv" style="margin-top:12px"><div>Entry · ${esc(tm(t.entry_at))}<b>${inr(t.entry)}</b></div><div>${t.status === "closed" ? "Exit" : "Now"} · ${esc(t.status === "closed" ? (t.exit_time || "") : tm(t.last_at))}<b class="${cl(v)}">${inr(t.status === "closed" ? t.exit : t.last)}</b></div>
      <div>Stop-loss<b class="dn">${t.sl_price != null ? inr(t.sl_price) : esc(String(t.sl_note || "–").replace(/^Rs /, "₹"))}</b></div><div>Capital<b>${inr(Math.round(cap))}</b></div></div>
    ${tg.length ? `<div class="tdkv" style="margin-top:8px;grid-template-columns:repeat(3,1fr)">${tg.map((val, i) => { const h = hits.find(z => z.t === i + 1);
      return `<div>T${i + 1}${h ? " ✓ " + esc(h.at) : ""}<b class="${h ? "up" : ""}">${inr(val)}</b></div>`; }).join("")}</div>` : ""}
    ${tradeChart(t)}
    <h3 style="margin:16px 0 0;font-size:14px">Timeline</h3><ul class="tl">${ev.map(([a, c, l, r]) => `<li><span class="tm">${esc(a || "–")}</span><i class="${c}"></i><span>${esc(l)}</span><span class="r">${r}</span></li>`).join("")}</ul>
    <p class="muted" style="font-size:12px;margin:12px 0 0">Paper trade on real option prices · after ₹${Math.round(t.charges || 0).toLocaleString("en-IN")} charges · targets are information only.</p></div>`;
  const dlg = $("#tradedlg"); if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
  decorate($("#tdbody"));
  location.hash.includes("trade=") || history.replaceState(null, "", location.hash + "");
}
document.addEventListener("click", e => {
  const tr = e.target.closest("[data-trade]"); if (tr) { openTrade(tr.dataset.trade); return; }
  const stp = e.target.closest("[data-step]"); if (stp) { stepTrade(+stp.dataset.step); return; }
  if (e.target.closest("[data-close]") || e.target.id === "tradedlg") $("#tradedlg").close();
});
document.addEventListener("keydown", e => { const tr = e.target.closest && e.target.closest("[data-trade]"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openTrade(tr.dataset.trade); } });

// ---------- REAL VS BACKTEST: each real paper trade next to the backtest model for the same day (docs/data/realcheck.json)
async function realVsModel() {
  let doc;
  try { const r = await fetch("data/realcheck.json?t=" + Date.now(), {cache: "no-store"}); if (!r.ok) throw 0; doc = await r.json(); }
  catch (e) { return `<h2>Real vs backtest<span class="sub">Real paper trades next to the backtest model for the same days</span></h2><div class="empty">The comparison appears after the evening run.</div>`; }
  const rows = (doc.rows || []).slice().sort((a, b) => b.date.localeCompare(a.date));
  const both = rows.filter(r => r.model), same = both.filter(r => r.model.side === r.real.side);
  const rt = rows.reduce((a, r) => a + r.real.pnl, 0), mt = both.reduce((a, r) => a + r.model.pnl, 0), rtb = both.reduce((a, r) => a + r.real.pnl, 0);
  const per = {};
  rows.forEach(r => { const k = r.index; per[k] = per[k] || {n: 0, real: 0, model: 0, m: 0}; per[k].n++; per[k].real += r.real.pnl; if (r.model) { per[k].m++; per[k].model += r.model.pnl; } });
  return `<h2>Real vs backtest<span class="sub">Each real paper trade next to what the backtest model gives for the same day · built ${esc(doc.built || "")}</span></h2>
    <div class="kpis"><div class="card kpi"><div class="l">Real paper trades</div><div class="v ${cl(rt)}">${sgn(rt)}</div><div class="s">${rows.length} trades</div></div>
      <div class="card kpi"><div class="l">Model, same days</div><div class="v ${cl(mt)}">${both.length ? sgn(mt) : "–"}</div><div class="s">${both.length} days with a model result</div></div>
      <div class="card kpi"><div class="l">Real minus model</div><div class="v ${cl(rtb - mt)}">${both.length ? sgn(rtb - mt) : "–"}</div><div class="s">same days only</div></div>
      <div class="card kpi"><div class="l">Same call / put</div><div class="v">${both.length ? Math.round(same.length / both.length * 100) + "%" : "–"}</div><div class="s">${same.length} of ${both.length} days</div></div></div>
    <div class="list" style="margin-top:12px"><table><caption class="sr">Real vs model by instrument</caption><thead><tr><th scope="col">Instrument</th><th scope="col" class="r">Real trades</th><th scope="col" class="r">Real P&L</th><th scope="col" class="r">Model P&L</th><th scope="col" class="r">Difference</th></tr></thead><tbody>${
      Object.keys(per).map(k => `<tr><td><b>${esc(IXN[k] || k)}</b></td><td class="r">${per[k].n}</td><td class="r ${cl(per[k].real)}">${sgn(per[k].real)}</td><td class="r ${cl(per[k].model)}">${per[k].m ? sgn(per[k].model) : "–"}</td><td class="r ${cl(per[k].real - per[k].model)}">${per[k].m ? sgn(per[k].real - per[k].model) : "–"}</td></tr>`).join("")
      || `<tr><td colspan="5" class="muted">No real trades yet.</td></tr>`}</tbody></table></div>
    <div class="list" style="margin-top:12px"><table><caption class="sr">Real vs model by day</caption><thead><tr><th scope="col">Date</th><th scope="col">Instrument</th><th scope="col">Real</th><th scope="col" class="r">Real P&L</th><th scope="col">Model</th><th scope="col" class="r">Model P&L</th><th scope="col" class="r">Match</th></tr></thead><tbody>${
      rows.map(r => `<tr><td>${esc(fd(r.date))}</td><td>${esc(IXN[r.index] || r.index)}</td><td>${esc(r.real.strike + " " + r.real.side)} · ${esc(r.real.entry_time)}→${esc(r.real.exit_time)}</td>
        <td class="r ${cl(r.real.pnl)}"><b>${sgn(r.real.pnl)}</b></td><td>${r.model ? esc(r.model.strike + " " + r.model.side) + " · " + esc(r.model.entry_time) + "→" + esc(r.model.exit_time) : `<span class="muted">not available</span>`}</td>
        <td class="r ${r.model ? cl(r.model.pnl) : ""}">${r.model ? sgn(r.model.pnl) : "–"}</td><td class="r">${r.model ? (r.model.side === r.real.side ? `<span class="pill p-up">same</span>` : `<span class="pill p-dn">differs</span>`) : "–"}</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">No real trades yet.</td></tr>`}</tbody></table></div>`;
}

// ---------- day's running P&L (all trades of the day, minute by minute from their price paths)
function daySpark(ts) {
  const ws = ts.filter(t => (t.path || []).length > 1); if (!ws.length) return "";
  const mm = hm => +hm.slice(0, 2) * 60 + +hm.slice(3, 5);
  const ser = ws.map(t => ({m: t.path.map(p => [mm(p[0]), (p[1] - t.entry) * t.qty]), fin: t.status === "closed" ? t.net : null}));
  const a = Math.min(...ser.map(z => z.m[0][0])), b = Math.max(...ser.map(z => z.m[z.m.length - 1][0]));
  const pts = [];
  for (let m = a; m <= b; m += 2) {
    let tot = 0;
    ser.forEach(z => { if (m < z.m[0][0]) return; let v = z.m[0][1]; for (const q of z.m) { if (q[0] <= m) v = q[1]; else break; } if (m > z.m[z.m.length - 1][0] && z.fin != null) v = z.fin; tot += v; });
    pts.push([m, tot]);
  }
  if (pts.length < 2) return "";
  const lo = Math.min(0, ...pts.map(p => p[1])), hi = Math.max(0, ...pts.map(p => p[1])), sp = (hi - lo) || 1;
  const x = m => (m - a) / ((b - a) || 1) * 300, y = v => 42 - (v - lo) / sp * 38;
  return `<div class="sp" aria-hidden="true"><svg viewBox="0 0 300 46" preserveAspectRatio="none"><line x1="0" x2="300" y1="${y(0)}" y2="${y(0)}" stroke="rgba(255,255,255,.45)" stroke-dasharray="3 3"/>
    <polyline points="${pts.map(p => x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1)).join(" ")}" fill="none" stroke="#fff" stroke-width="2" vector-effect="non-scaling-stroke"/></svg></div>`;
}
// ---------- today at a glance: won / lost / open / waiting across the instruments
function glance(ops, lastDay) {
  const c = {won: 0, lost: 0, open: 0, wait: 0};
  BOARD.forEach(([ix]) => {
    const t = ops.filter(x => ixOf(x) === ix && x.signal_date === lastDay)[0];
    if (!t) c.wait++; else if (t.status === "open") c.open++; else if (t.net > 0) c.won++; else c.lost++;
  });
  const segs = [["won", "var(--up)", "Won"], ["lost", "var(--dn)", "Lost"], ["open", "var(--warn)", "Open"], ["wait", "var(--surface3)", "No trade / waiting"]];
  const tot = BOARD.length, R = 46, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = segs.map(([k, col]) => { const len = c[k] / tot * C; const a = `<circle cx="59" cy="59" r="${R}" fill="none" stroke="${col}" stroke-width="14" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 59 59)"/>`; off += len; return a; }).join("");
  return `<div class="card glance"><svg class="ring" viewBox="0 0 118 118" role="img" aria-label="${c.won} won, ${c.lost} lost, ${c.open} open, ${c.wait} no trade or waiting">${arcs}
    <text x="59" y="56" text-anchor="middle" font-size="22" font-weight="700" fill="var(--text)">${c.won + c.lost + c.open}/${tot}</text><text x="59" y="74" text-anchor="middle" font-size="10" fill="var(--muted)" font-family="var(--sans)">traded</text></svg>
    <div><b style="font-size:15px">Today at a glance${lastDay ? " · " + esc(fd(lastDay)) : ""}</b><div class="glg" style="margin-top:8px">${segs.map(([k, col, l]) => `<span><i style="background:${col}"></i>${l} <b class="num">${c[k]}</b></span>`).join("")}</div></div></div>`;
}

// ---------- LIVE BOARD (Today): one tile per instrument from the paper-trade data already in the app
const IXC = {NIFTY: "#818cf8", SENSEX: "#22d3ee", BSE: "#f472b6", ADANIENT: "#fbbf24", DIXON: "#34d399", PNB: "#f97316"};
const ixDot = ix => `<span class="ixd" style="--ix:${IXC[ix] || "var(--brand)"}" aria-hidden="true"></span>`;
const BOARD = [["NIFTY", "NIFTY 50", "option"], ["SENSEX", "SENSEX", "sensex"], ["BSE", "BSE Ltd", "bse"], ["ADANIENT", "Adani Ent", "adanient"], ["DIXON", "Dixon", "dixon"], ["PNB", "PNB", "pnb"]];
const STARTS = {BSE: "2026-11-27", ADANIENT: "2026-11-27", DIXON: "2026-11-27", PNB: "2026-11-27"};
function spark(vals, color) {
  if (vals.length < 2) return `<svg viewBox="0 0 100 34" aria-hidden="true"><line x1="0" x2="100" y1="17" y2="17" stroke="var(--line)" stroke-dasharray="3 3"/></svg>`;
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), sp = (hi - lo) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * 100).toFixed(1)},${(31 - (v - lo) / sp * 28).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="100" y1="${(31 - (0 - lo) / sp * 28).toFixed(1)}" y2="${(31 - (0 - lo) / sp * 28).toFixed(1)}" stroke="var(--line)" stroke-dasharray="3 3"/>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}
// ---------- period switch for the dashboard totals
let PER = "day";
try { PER = localStorage.getItem("period") || "day"; } catch (e) {}
function inPeriod(d, lastDay) {
  if (!lastDay) return false;
  if (PER === "all") return true;
  if (PER === "day") return d === lastDay;
  if (PER === "month") return d.slice(0, 7) === lastDay.slice(0, 7);
  const L = new Date(lastDay + "T00:00:00"), mon = new Date(L); mon.setDate(L.getDate() - ((L.getDay() + 6) % 7));
  return d >= mon.toISOString().slice(0, 10) && d <= lastDay;
}
function liveBoard() {
  const ops = (D.trades || []).filter(t => t.kind === "option" && t.status !== "pending");
  const lastDay = ops.reduce((m, t) => t.signal_date > m ? t.signal_date : m, "");
  const val = t => t.status === "closed" ? t.net : (t.gross || 0);
  const ps = ops.filter(t => inPeriod(t.signal_date, lastDay)), pc = ps.filter(t => t.status === "closed");
  const ptot = ps.reduce((a, t) => a + val(t), 0), open = ops.filter(t => t.status === "open").length;
  const plab = {day: lastDay ? "Latest day · " + fd(lastDay) : "Latest day", week: "This week", month: "This month", all: "All time"}[PER];
  let h = `<div class="tools" style="margin:6px 0 8px"><div class="seg" role="group" aria-label="Period">${[["day", "Today"], ["week", "Week"], ["month", "Month"], ["all", "All time"]]
      .map(([k, l]) => `<button type="button" data-per="${k}" aria-pressed="${PER === k}">${l}</button>`).join("")}</div></div>
    <div class="sumbar" role="group" aria-label="Paper trading summary">
    <div><div class="k">${esc(plab)}</div><div class="v">${ps.length ? sgn(ptot) : "–"}</div></div>
    <div><div class="k">Trades</div><div class="v">${ps.length}</div></div>
    <div><div class="k">Win rate</div><div class="v">${pc.length ? Math.round(pc.filter(t => t.net > 0).length / pc.length * 100) + "%" : "–"}</div></div>
    <div><div class="k">Open now</div><div class="v">${open}</div></div>${PER === "day" ? daySpark(ops.filter(t => t.signal_date === lastDay)) : ""}</div>${glance(ops, lastDay)}
    <h2 style="margin-top:14px">Live board<span class="sub">Latest paper trade for each instrument · tap a tile to open the trade, or "All trades" for the instrument</span></h2><div class="board">`;
  BOARD.forEach(([ix, name, id]) => {
    const ts = ops.filter(t => ixOf(t) === ix).sort((a, b) => a.signal_date.localeCompare(b.signal_date));
    const t = ts[ts.length - 1];
    let c = 0; const cum = ts.map(x => (c += val(x)));
    const v = t ? val(t) : null;
    const st = !t ? (STARTS[ix] && STARTS[ix] > (D.last_session || "") ? `<span class="pill p-info">Starts ${esc(fd(STARTS[ix]))}</span>` : `<span class="pill p-mute">Watching</span>`)
      : t.status === "open" ? `<span class="pill p-warn">Open</span>` : `<span class="pill ${v > 0 ? "p-up" : "p-dn"}">${v > 0 ? "Won" : "Lost"}</span>`;
    h += `<div class="card tile ${t ? (v > 0 ? "win" : "loss") : ""}" style="--ix:${IXC[ix]}" role="button" tabindex="0" ${t ? `data-trade="${esc(t.id)}"` : `data-ix="${ix}"`} aria-label="${esc(name)}${t ? " trade details" : " details"}">
      <div class="row"><span class="nm">${ixDot(ix)}${esc(name)}</span>${st}</div>
      <div class="sy">${t ? esc(t.symbol) + " · " + esc(fd(t.signal_date)) : "No trades yet"}</div>
      <div class="pl ${cl(v)}">${t ? sgn(v) : "–"}</div>
      ${spark(cum, cum.length && cum[cum.length - 1] < 0 ? "var(--dn)" : "var(--up)")}
      <div class="ind" style="margin-top:2px">${t && t.targets ? [1, 2, 3].map(k => (t.target_hits || []).some(x => x.t === k) ? `<b class="up">T${k}✓</b>` : `T${k}`).join(" ") + " · " : ""}${ts.length} trade${ts.length === 1 ? "" : "s"} · total ${ts.length ? sgn(cum[cum.length - 1]) : "–"}</div>
      <button type="button" class="linkbtn" data-ix="${ix}" style="margin-top:4px">All ${esc(name)} trades ›</button></div>`;
  });
  return h + `</div>` + todayTimeline(ops, lastDay);
}

// ---------- today's timeline in the app: be ready -> entry -> targets -> exit, with time, call/put and strike
function todayTimeline(ops, lastDay) {
  const ev = [], od = D.option_day || {};
  const day = Object.values(od).reduce((m, x) => (x.day || "") > m ? x.day : m, lastDay || "");
  Object.entries(od).forEach(([ix, x]) => { if (x.day === day) (x.near || []).forEach(n => ev.push([n.at, ix, "BE READY", (n.side === "CE" ? "Call" : "Put") + " side · " + n.strike + " " + n.side, "p-warn"])); });
  ops.filter(t => t.signal_date === day).forEach(t => {
    const ix = ixOf(t), sp = t.symbol.split(" ").slice(1).join(" ");
    if (!Object.keys(od).length) (t.near || []).forEach(n => ev.push([n.at, ix, "BE READY", (n.side === "CE" ? "Call" : "Put") + " side · " + n.strike + " " + n.side, "p-warn"]));
    ev.push([tm(t.entry_at), ix, "ENTRY", "Buy " + sp + " @ " + inr(t.entry), / CE$/.test(t.symbol) ? "p-up" : "p-dn"]);
    (t.target_hits || []).forEach(x => ev.push([x.at, ix, "TARGET " + x.t, sp + " hit " + inr(x.price), "p-up"]));
    if (t.status === "closed") ev.push([t.exit_time || tm(t.last_at), ix, "EXIT", sp + " out " + inr(t.exit) + " · " + sgn(t.net), t.net > 0 ? "p-up" : "p-dn"]);
  });
  Object.entries(od).forEach(([ix, x]) => { if (x.day === day && x.status === "no trade") ev.push(["13:00", ix, "NO TRADE", "No trade today", "p-mute"]); });
  ev.sort((a, b) => (a[0] || "").localeCompare(b[0] || ""));
  return `<h2>Today's timeline<span class="sub">${day ? esc(fdl(day)) + " · " : ""}every alert in time order</span></h2>` + (ev.length ? `<div class="list">` + ev.map(([hm, ix, tag, det, pcl]) =>
    `<div class="li"><div style="display:flex;gap:12px;align-items:center"><span class="num muted" style="min-width:44px">${esc(hm || "–")}</span>${ixDot(ix)}<div><b>${esc(IXN[ix] || ix)}</b><div class="d">${esc(det)}</div></div></div><span class="pill ${pcl}">${esc(tag)}</span></div>`).join("") + `</div>`
    : `<div class="empty">No alerts yet today.</div>`);
}

// ---------- instrument page (opens in the trade sheet)
async function openIx(ix) {
  const ops = (D.trades || []).filter(t => t.kind === "option" && t.status !== "pending" && ixOf(t) === ix).sort((a, b) => b.signal_date.localeCompare(a.signal_date));
  const cls = ops.filter(t => t.status === "closed"), val = t => t.status === "closed" ? t.net : (t.gross || 0);
  const tot = ops.reduce((a, t) => a + val(t), 0), w = cls.filter(t => t.net > 0).length;
  const tgt = k => { const z = ops.filter(t => t.targets); return z.length ? Math.round(z.filter(t => (t.target_hits || []).some(x => x.t === k)).length / z.length * 100) + "%" : "–"; };
  let c = 0; const cum = ops.slice().reverse().map(t => (c += val(t)));
  const ic = IXC[ix] || "#6366f1", name = IXN[ix] || ix;
  CUR = null;
  $("#tdbody").innerHTML = `<div class="tdh" style="background:linear-gradient(135deg,${ic},color-mix(in srgb,${ic} 55%,#0a0f1d))"><span class="grab"></span><div><h3 id="tdh">${esc(name)}</h3><div style="font-size:12.5px;opacity:.9">Paper trades on real option prices · 1 lot each</div></div>
      <div class="tdnav"><button class="x" type="button" aria-label="Close" data-close>×</button></div></div>
    <div class="tdb"><div class="tdkv"><div>Trades<b>${ops.length}</b></div><div>Win rate<b>${cls.length ? Math.round(w / cls.length * 100) + "%" : "–"}</b></div>
      <div>Net P&L<b class="${cl(tot)}">${ops.length ? sgn(tot) : "–"}</b></div><div>Avg / trade<b class="${cl(tot)}">${ops.length ? sgn(tot / ops.length) : "–"}</b></div></div>
    <div class="tdkv" style="margin-top:8px"><div>Best<b class="up">${cls.length ? sgn(Math.max(...cls.map(t => t.net))) : "–"}</b></div><div>Worst<b class="dn">${cls.length ? sgn(Math.min(...cls.map(t => t.net))) : "–"}</b></div>
      <div>T1 / T2 / T3 reached<b>${tgt(1)} · ${tgt(2)} · ${tgt(3)}</b></div><div>Lot<b>${ops[0] ? ops[0].qty + " qty" : "–"}</b></div></div>
    <div class="chart" style="margin-top:12px"><b style="font-size:13px">Running total (paper trades)</b>${spark(cum, tot < 0 ? "var(--dn)" : "var(--up)")}</div>
    <div id="ixbt" class="card" style="margin-top:12px"><span class="muted">Loading the backtest…</span></div>
    <h3 style="margin:16px 0 6px;font-size:14px">All trades</h3>${ops.length ? `<div class="list">` + ops.map(t => { const v = val(t);
      return `<div class="li" role="button" tabindex="0" data-trade="${esc(t.id)}" style="cursor:pointer"><div><b>${esc(t.symbol)}</b> <span class="muted">${esc(fd(t.signal_date))}</span>
        <div class="d">In ${esc(tm(t.entry_at))} ${inr(t.entry)} → ${t.status === "closed" ? "out" : "now"} ${inr(t.status === "closed" ? t.exit : t.last)}${t.exit_reason ? " · " + esc(t.exit_reason) : ""}</div></div><span class="r ${cl(v)}">${sgn(v)}</span></div>`; }).join("") + `</div>`
      : `<div class="empty">No paper trades yet${STARTS[ix] ? " · starts " + esc(fd(STARTS[ix])) : ""}.</div>`}</div>`;
  const dlg = $("#tradedlg"); if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
  decorate($("#tdbody"));
  try {
    const B = await fetchBT(ix), S = B.summary;
    $("#ixbt").innerHTML = `<div class="row"><b>Backtest (model prices)</b><span class="muted" style="font-size:12px">${esc(String(B.from).slice(0, 7))} – ${esc(String(B.to).slice(0, 7))}</span></div>
      <div class="tdkv" style="margin-top:8px"><div>Trades<b>${S.trades}</b></div><div>Win rate<b>${S.win_pct}%</b></div><div>Net P&L<b class="${cl(S.net)}">${sgn(S.net)}</b></div><div>Avg capital<b>${inr(S.capital_avg)}</b></div></div>`;
  } catch (e) { $("#ixbt").innerHTML = `<span class="muted">No backtest for this instrument.</span>`; }
}
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-ix]");
  if (b) { e.stopPropagation(); e.preventDefault(); openIx(b.dataset.ix); return; }
  const pb = e.target.closest && e.target.closest("[data-per]");
  if (pb) { PER = pb.dataset.per; try { localStorage.setItem("period", PER); } catch (er) {} const y = window.scrollY; renderAll(); show("today"); window.scrollTo(0, y); decorate(); }
}, true);

// ---------- REELS: every daily video, newest first (stored in the repository's "reels" release)
function reelUrl(file) {
  return location.hostname.endsWith(".github.io")
    ? "https://github.com/" + location.hostname.split(".")[0] + "/" + location.pathname.split("/")[1] + "/releases/download/reels/" + file
    : "reels/" + file;
}
async function loadReels() {
  const box = $("#v-reels");
  box.innerHTML = `<h2>Reels<span class="sub">The daily recap video · made after each close · ready for Instagram</span></h2><div class="empty" role="status">Loading…</div>`;
  let doc;
  try { const r = await fetch("data/reels.json?t=" + Date.now(), {cache: "no-store"}); if (!r.ok) throw 0; doc = await r.json(); }
  catch (e) { box.innerHTML = `<h2>Reels</h2><div class="empty">The first reel appears here after the next close.</div>`; return; }
  const it = doc.items || [];
  box.innerHTML = `<h2>Reels<span class="sub">${it.length} daily recap video${it.length === 1 ? "" : "s"} · tap to play · use "Open" to save</span></h2>` + (it.length ? `<div class="reels">` + it.map(x =>
    `<div class="card reel"><video preload="none" controls playsinline src="${esc(reelUrl(x.file))}"></video>
      <div class="row" style="margin-top:10px"><b>${esc(fdl(x.date))}</b><span class="pnl ${cl(x.total)}" style="font-size:16px">${sgn(x.total)}</span></div>
      <div class="row" style="margin-top:4px"><span class="ind">${x.trades} trade${x.trades === 1 ? "" : "s"} · ${x.won} won · ${x.lost} lost</span>
      <a class="linkbtn" href="${esc(reelUrl(x.file))}" target="_blank" rel="noopener">Open ↗</a></div></div>`).join("") + `</div>` : `<div class="empty">No reels yet.</div>`);
}
document.addEventListener("keydown", e => { const t = e.target.closest && e.target.closest(".tile"); if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); t.click(); } });

// ---------- BACKTEST extras: P&L calendar (one year) and weekday table, from the same backtest file
function drawCalendar() {
  const box = $("#btcal"); if (!box || !BT) return;
  const yrs = [...new Set(BT.days.map(d => d.date.slice(0, 4)))].sort();
  const yr = BTF.month !== "all" ? BTF.month : yrs[yrs.length - 1];
  const by = {}; BT.days.forEach(d => { by[d.date] = d; });
  const mx = Math.max(1, ...BT.days.filter(d => d.status === "closed" && d.date.startsWith(yr)).map(d => Math.abs(d.pnl)));
  const col = d => { if (!d) return ""; if (d.status !== "closed") return "background:var(--surface3)";
    const a = Math.min(1, Math.sqrt(Math.abs(d.pnl) / mx)) * 0.85 + 0.15; return `background:color-mix(in srgb,var(${d.pnl > 0 ? "--up" : "--dn"}) ${Math.round(a * 100)}%,transparent)`; };
  let h = `<h2>P&L calendar · ${esc(yr)}<span class="sub">Each square is a trading day · green = profit, red = loss, grey = no trade · ${BTF.month === "all" ? "latest year (tap a year above to change)" : "the year selected above"}</span></h2><div class="cal">`;
  for (let m = 0; m < 12; m++) {
    const first = new Date(+yr, m, 1), days = new Date(+yr, m + 1, 0).getDate(), mo = `${yr}-${String(m + 1).padStart(2, "0")}`;
    if (!BT.days.some(d => d.date.startsWith(mo))) continue;
    let cells = ["M", "T", "W", "T", "F"].map(x => `<span class="h">${x}</span>`).join("");
    let lead = (first.getDay() + 6) % 7; if (lead > 4) lead = 0;
    cells += `<span style="visibility:hidden"></span>`.repeat(lead);
    let net = 0;
    for (let dd = 1; dd <= days; dd++) {
      const dt = new Date(+yr, m, dd), wd = dt.getDay(); if (wd === 0 || wd === 6) continue;
      const key = `${mo}-${String(dd).padStart(2, "0")}`, d = by[key];
      if (d && d.status === "closed") net += d.pnl;
      cells += `<span style="${col(d)}" title="${esc(fd(key))}: ${d ? (d.status === "closed" ? esc(sgn(d.pnl)) + " · " + esc(d.side) + " " + esc(d.strike) : "no trade") : "market closed"}"></span>`;
    }
    h += `<div class="card calm"><div class="t"><span>${first.toLocaleDateString("en-IN", {month: "short"})}</span><span class="${cl(net)} num">${sgn(net)}</span></div><div class="calg">${cells}</div></div>`;
  }
  box.innerHTML = h + `</div><div class="legend">Loss <i style="background:var(--dn)"></i><i style="background:color-mix(in srgb,var(--dn) 35%,transparent)"></i><i style="background:var(--surface3)"></i><i style="background:color-mix(in srgb,var(--up) 35%,transparent)"></i><i style="background:var(--up)"></i> Profit</div>`;
}
function weekdayTable() {
  const wd = ["Mon", "Tue", "Wed", "Thu", "Fri"], a = wd.map(() => ({n: 0, w: 0, p: 0}));
  BT.days.filter(d => d.status === "closed").forEach(d => { const i = (new Date(d.date + "T00:00:00").getDay() + 6) % 7; if (i < 5) { a[i].n++; a[i].p += d.pnl; if (d.pnl > 0) a[i].w++; } });
  return `<h2>By weekday<span class="sub">All trades in this backtest, grouped by the day of the week</span></h2><div class="list"><table><caption class="sr">P&L by weekday</caption>
    <thead><tr><th scope="col">Day</th><th scope="col" class="r">Trades</th><th scope="col" class="r">Won</th><th scope="col" class="r">Avg</th><th scope="col" class="r">Net</th></tr></thead><tbody>${
    a.map((x, i) => `<tr><td>${wd[i]}</td><td class="r">${x.n}</td><td class="r">${x.n ? Math.round(x.w / x.n * 100) + "%" : "–"}</td><td class="r ${cl(x.p)}">${x.n ? sgn(x.p / x.n) : "–"}</td><td class="r ${cl(x.p)}"><b>${x.n ? sgn(x.p) : "–"}</b></td></tr>`).join("")}</tbody></table></div>`;
}

// ---------- COMPARE: all six backtests side by side (same files as the Backtest tab)
const CMP = [["NIFTY", "#818cf8"], ["SENSEX", "#22d3ee"], ["BSE", "#f472b6"], ["ADANIENT", "#fbbf24"], ["DIXON", "#34d399"], ["PNB", "#f97316"]];
async function loadCompare() {
  const box = $("#v-compare");
  box.innerHTML = `<h2>Compare<span class="sub">All six backtests side by side</span></h2><div class="empty" role="status">Loading the six backtests…</div>`;
  let docs;
  try { docs = await Promise.all(CMP.map(([i]) => fetchBT(i))); }
  catch (e) { box.innerHTML = `<h2>Compare</h2><div class="empty" role="alert">Could not load the backtests (${esc(String(e.message || e))}).</div>`; return; }
  const rows = CMP.map(([i, color], k) => {
    const d = docs[k], S = d.summary, tr = d.days.filter(x => x.status === "closed");
    const ret = tr.length ? tr.reduce((a, x) => a + (x.capital ? x.pnl / x.capital : 0), 0) / tr.length * 100 : 0;
    const yrs = {}; tr.forEach(x => { const y = x.date.slice(0, 4); yrs[y] = (yrs[y] || 0) + x.pnl; });
    return {i, color, name: IXN[i] || i, d, S, ret, losing: Object.values(yrs).filter(v => v < 0).length, years: Object.keys(yrs).length};
  });
  let h = `<h2>Compare<span class="sub">Same strategy on every instrument · 1 lot, after charges · model option prices (they flatter option buyers)</span></h2>
    <div class="list"><table><caption class="sr">Backtest comparison</caption><thead><tr><th scope="col">Instrument</th><th scope="col">Period</th><th scope="col" class="r">Trades</th>
    <th scope="col" class="r">Won</th><th scope="col" class="r">Net P&L</th><th scope="col" class="r">Avg / trade</th><th scope="col" class="r">Avg capital</th>
    <th scope="col" class="r">Return / trade</th><th scope="col" class="r">Worst fall</th><th scope="col" class="r">Losing years</th></tr></thead><tbody>${
    rows.map(r => `<tr><td><span class="dot" style="background:${r.color};display:inline-block;margin-right:8px"></span><b>${esc(r.name)}</b></td>
      <td class="muted" style="white-space:nowrap">${esc(String(r.d.from).slice(0, 7))} – ${esc(String(r.d.to).slice(0, 7))}</td>
      <td class="r">${r.S.trades}</td><td class="r">${r.S.win_pct}%</td><td class="r ${cl(r.S.net)}"><b>${sgn(r.S.net)}</b></td>
      <td class="r ${cl(r.S.avg)}">${sgn(r.S.avg)}</td><td class="r">${inr(r.S.capital_avg)}</td><td class="r ${cl(r.ret)}">${r.ret.toFixed(1)}%</td>
      <td class="r dn">${sgn(r.S.max_drawdown)}</td><td class="r">${r.losing} of ${r.years}</td></tr>`).join("")}</tbody></table></div>`;
  const mr = Math.max(1, ...rows.map(r => Math.abs(r.ret)));
  h += `<div class="cols" style="margin-top:14px"><div class="list"><h3>Return per trade on capital</h3>${rows.slice().sort((a, b) => b.ret - a.ret).map(r =>
    `<div class="cmpbar"><span>${esc(r.name)}</span><span class="trk"><i style="width:${(Math.abs(r.ret) / mr * 100).toFixed(0)}%;background:${r.color}"></i></span><b class="${cl(r.ret)}">${r.ret.toFixed(1)}%</b></div>`).join("")}</div>
    <div class="list"><h3>Win rate</h3>${rows.slice().sort((a, b) => b.S.win_pct - a.S.win_pct).map(r =>
    `<div class="cmpbar"><span>${esc(r.name)}</span><span class="trk"><i style="width:${r.S.win_pct}%;background:${r.color}"></i></span><b>${r.S.win_pct}%</b></div>`).join("")}</div></div>`;
  // combined running totals on one time axis
  const t0 = Math.min(...rows.map(r => +new Date(r.d.from))), t1 = Math.max(...rows.map(r => +new Date(r.d.to)));
  const series = rows.map(r => ({r, pts: r.d.days.filter(x => x.status !== "holiday").map(x => [+new Date(x.date + "T00:00:00"), x.cum])}));
  const all = series.flatMap(s2 => s2.pts.map(p => p[1])), lo = Math.min(0, ...all), hi = Math.max(0, ...all), sp = (hi - lo) || 1;
  const W = 760, H = 260, P = 40, x = t => P + (t - t0) / ((t1 - t0) || 1) * (W - 2 * P), y = v => H - 24 - (v - lo) / sp * (H - 44);
  const yrsAx = []; for (let yy = new Date(t0).getFullYear() + 1; yy <= new Date(t1).getFullYear(); yy++) yrsAx.push(yy);
  h += `<h2>Running total, all six<span class="sub">Each line is one instrument's cumulative P&L from its first backtest day</span></h2><div class="chart">
    <svg class="c" viewBox="0 0 ${W} ${H}" role="img" aria-label="Cumulative P&L of the six backtests">
    <line class="g" x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}"/>
    ${yrsAx.map(yy => { const xx = x(+new Date(yy + "-01-01T00:00:00")); return `<line class="g" x1="${xx}" x2="${xx}" y1="10" y2="${H - 24}" stroke-dasharray="2 4"/><text class="ax" x="${xx + 3}" y="${H - 6}">${yy}</text>`; }).join("")}
    <text class="ax" x="2" y="${y(hi) + 4}">${esc(sgn(hi))}</text><text class="ax" x="2" y="${y(0) + 4}">₹0</text>
    ${series.map(s2 => `<polyline points="${s2.pts.map(p => x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1)).join(" ")}" fill="none" stroke="${s2.r.color}" stroke-width="2" stroke-linejoin="round"/>`).join("")}</svg>
    <div class="lgd">${rows.map(r => `<span><i style="background:${r.color}"></i>${esc(r.name)} <b class="num ${cl(r.S.net)}">${sgn(r.S.net)}</b></span>`).join("")}</div></div>
    <details class="why banner warn" style="margin-top:14px"><summary><b>How to read this.</b> <span class="muted">Why?</span></summary><p style="margin:6px 0 0">
    The periods differ: NIFTY and the two older stocks start in 2022, SENSEX in June 2023 and BSE Ltd in December 2024, so compare the return per trade and win rate rather than the rupee totals.
    Option prices are modelled and flatter option buyers; stock options also have wider spreads than index options. The real-price paper trades are the test.</p></details>`;
  box.innerHTML = (await realVsModel()) + h;
}

// ---------- shared UI state (kept across the 3-minute live refresh)
let COLLAPSED = new Set();
try { COLLAPSED = new Set(JSON.parse(localStorage.getItem("collapsed") || "[]")); } catch (e) {}
const PF = {kind: "all", sort: "pnl"};
const HS = {kind: "all", q: "", status: "all", limit: 20};
// option-only app: stock strategies keep running in the cloud but are not shown
const KINDS = [["option", "NIFTY option"]];
const MULTI = [];
let ODAILY = [];
const kindPill = t => t.kind === "momentum" ? `<span class="pill p-info">Momentum</span>` : t.kind === "results" ? `<span class="pill p-info">Results</span>`
  : t.kind === "option" ? `<span class="pill p-warn">Option</span>` : t.kind === "swing" ? `<span class="pill p-info">Swing</span>`
  : `<span class="pill ${t.side === "long" ? "p-up" : "p-dn"}">${t.side === "long" ? "Buy" : "Sell"}</span>`;
const IXT = {BSE: "BSE Ltd", ADANIENT: "Adani Ent", DIXON: "Dixon", PNB: "PNB"};
const ixOf = t => t.index || (/^SENSEX/.test(t.symbol || "") ? "SENSEX" : "NIFTY");
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

// ---------- numbers: up/down arrows, count-up when they change, a short glow on the tile
const PREV = {};
function parseRs(txt) { const m = String(txt).replace(/[,\s]/g, "").match(/([+−-]?)₹([\d.]+)/); return m ? (m[1] === "−" || m[1] === "-" ? -1 : 1) * parseFloat(m[2]) : null; }
function fmtRs(v, signed) { return (signed ? (v > 0 ? "+" : v < 0 ? "−" : "") : "") + "₹" + Math.abs(Math.round(v)).toLocaleString("en-IN"); }
function decorate(root) {
  (root || document).querySelectorAll(".pnl, .tile .pl, .sumbar .v, .kpi .v").forEach((el, i) => {
    if (el.dataset.dec) return;
    el.dataset.dec = "1";
    const txt = el.textContent.trim();
    if (!/^[+−-]?₹[\d,]+$/.test(txt)) return;            // whole-rupee values only; prices and ranges stay as they are
    const val = parseRs(txt), signed = /^[+−-]/.test(txt);
    const dir = signed && val > 0 ? "▲" : signed && val < 0 ? "▼" : "";
    const host = el.closest("[data-trade]"), sec = el.closest("section");
    const key = (host ? host.dataset.trade : "") + "|" + (sec ? sec.id : "") + "|" + el.className + "|" + i;
    const from = PREV[key];
    PREV[key] = val;
    const draw = v => { el.innerHTML = (dir ? `<span class="arr" aria-hidden="true">${dir}</span>` : "") + esc(fmtRs(v, signed)); };
    const start = from == null ? (el.closest(".sumbar, .tile") ? 0 : val) : from;
    if (reduceMotion() || start === val) { draw(val); return; }
    const t0 = performance.now(), d = 700;
    const step = now => { const k = Math.min(1, (now - t0) / d), e = 1 - Math.pow(1 - k, 3); draw(start + (val - start) * e); if (k < 1) requestAnimationFrame(step); else draw(val); };
    requestAnimationFrame(step);
    const card = el.closest(".tile, .kpi");
    if (card && from != null) { card.classList.remove("flash-up", "flash-dn"); void card.offsetWidth; card.classList.add(val > from ? "flash-up" : "flash-dn"); }
  });
}
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
    <span class="chip">Prices through <b>${fd(D.last_session)}</b></span></div>`;
  h += liveBoard();
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
  const keep = [];
  const recent = (D.trades || []).filter(t => t.kind === "option").sort((a, b) => b.signal_date.localeCompare(a.signal_date)).slice(0, 15);
  keep.push({id: "recent", title: "Recent trades", sub: "Last 15 paper trades on real option prices · 1 lot each, after charges · tap one to open it", count: recent.length,
    body: recent.length ? `<div class="list">` + recent.map(t => {
      const v = t.status === "closed" ? t.net : t.gross;
      return `<div class="li" role="button" tabindex="0" data-trade="${esc(t.id)}" style="cursor:pointer"><div>${ixDot(ixOf(t))}<b>${esc(t.symbol)}</b> <span class="muted">${esc(fdl(t.signal_date))}</span>
        <div class="d">In ${esc(tm(t.entry_at))} ${inr(t.entry)} → ${t.status === "closed" ? "out" : "now"} ${esc(tm(t.last_at))} ${inr(t.last)}${t.exit_reason ? " · " + esc(t.exit_reason) : ""}</div></div>
        <span class="r ${cl(v)}">${sgn(v)}${t.status === "open" ? ` <span class="pill p-warn">open</span>` : ""}</span></div>`;
    }).join("") + `</div>` : `<div class="empty">No option trades yet.</div>`});
  secs.splice(0, secs.length, ...keep);
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
T = (D.trades || []).filter(t => t.kind === "option"); S = {};
ODAILY = (() => {
  const m = {};
  T.forEach(t => { if (t.status !== "pending") m[t.signal_date] = (m[t.signal_date] || 0) + (t.status === "closed" ? t.net : (t.gross || 0)); });
  let c = 0;
  return Object.keys(m).sort().map(d => ({date: d, net: m[d], cumulative: (c += m[d])}));
})();
(function () {
  const openAll = T.filter(t => MULTI.includes(t.kind) && t.status === "open");
  const last = ODAILY.slice(-1)[0];
  const openCh = T.filter(t => t.status === "open").reduce((a, t) => a + (t.charges || 0), 0);
  const total = T.reduce((a, t) => a + (t.status === "closed" ? t.net : t.status === "open" ? (t.gross || 0) : 0), 0);
  const closed = T.filter(t => t.status === "closed"), wins = closed.filter(t => t.net > 0);
  const k = [["Total P&L", sgn(total), cl(total), "closed + open, after charges"],
    ["Latest day", last ? sgn(last.net) : "–", last ? cl(last.net) : "", last ? fdl(last.date) : "no trades yet"],
    ["Trades", T.filter(t => t.status !== "pending").length + "", "", closed.length ? "avg " + sgn(closed.reduce((a, t) => a + t.net, 0) / closed.length) + " a trade" : "none closed yet"],
    ["Win rate", closed.length ? Math.round(wins.length / closed.length * 100) + "%" : "–", "", closed.length + " closed trades"]];
  let h = `<h2>Option paper trading<span class="sub">NIFTY, SENSEX, BSE Ltd, Adani Ent, Dixon and PNB · 1 lot each a day on real option prices, after charges</span></h2><div class="kpis">` +
    k.map(([l, v, c, s]) => `<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${esc(s)}</div></div>`).join("") + `</div>`;
  h += `<h2>By index<span class="sub">Closed trades plus any open one</span></h2><div class="list"><table><caption class="sr">P&L by index</caption><thead><tr><th scope="col">Index</th><th scope="col" class="r">Trades</th><th scope="col" class="r">Won</th><th scope="col" class="r">Net</th></tr></thead><tbody>${
    ["NIFTY", "SENSEX", "BSE", "ADANIENT", "DIXON", "PNB"].map(ix => { const ts = T.filter(t => ixOf(t) === ix && t.status !== "pending"), c = ts.filter(t => t.status === "closed"),
      n = ts.reduce((a, t) => a + (t.status === "closed" ? t.net : (t.gross || 0)), 0);
      return `<tr data-ix="${ix}" style="cursor:pointer"><td>${ixDot(ix)}${IXT[ix] || ix}</td><td class="r">${ts.length}</td><td class="r">${c.length ? Math.round(c.filter(t => t.net > 0).length / c.length * 100) + "%" : "–"}</td><td class="r ${cl(n)}"><b>${ts.length ? sgn(n) : "–"}</b></td></tr>`; }).join("")}</tbody></table></div>`;
  if (!MULTI.length) {
    const mon = {};
    T.filter(t => t.status === "closed").forEach(t => { const k = String(t.signal_date).slice(0, 7); (mon[k] = mon[k] || []).push(t.net); });
    const rows = Object.keys(mon).sort().reverse();
    h += `<h2>By month<span class="sub">Closed option trades</span></h2>` + (rows.length ? `<div class="list"><table><caption class="sr">Option P&L by month</caption><thead><tr><th scope="col">Month</th><th scope="col" class="r">Trades</th><th scope="col" class="r">Won</th><th scope="col" class="r">Net</th></tr></thead><tbody>${
      rows.map(k => { const a = mon[k], n = a.reduce((x, y) => x + y, 0); return `<tr><td>${new Date(k + "-01T00:00:00").toLocaleDateString("en-IN", {month: "long", year: "numeric"})}</td><td class="r">${a.length}</td><td class="r">${Math.round(a.filter(x => x > 0).length / a.length * 100)}%</td><td class="r ${cl(n)}"><b>${sgn(n)}</b></td></tr>`; }).join("")}</tbody></table></div>` : `<div class="empty">No closed option trades yet.</div>`);
    $("#v-portfolio").innerHTML = h;
    return;
  }
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
      <div class="tools"><label class="sr" for="hq">Search a strike</label><input id="hq" class="search" type="search" placeholder="Search a strike, e.g. 25500 CE" autocomplete="off" spellcheck="false">
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
  const dd = ODAILY;
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
  const g = [grp("NIFTY", t => ixOf(t) === "NIFTY"), grp("SENSEX", t => ixOf(t) === "SENSEX"), grp("BSE Ltd", t => ixOf(t) === "BSE"), grp("Adani Ent", t => ixOf(t) === "ADANIENT"), grp("Dixon", t => ixOf(t) === "DIXON"), grp("PNB", t => ixOf(t) === "PNB"), grp("Calls (CE)", t => / CE$/.test(t.symbol)),
    grp("Puts (PE)", t => / PE$/.test(t.symbol)), grp("All option trades", t => true)];
  h += `<h2>NIFTY vs SENSEX · calls vs puts</h2><div class="list"><table><thead><tr><th>Strategy</th><th class="r">Trades</th><th class="r">Won</th><th class="r">Avg</th><th class="r">Total</th></tr></thead><tbody>${
    g.map(r => `<tr><td>${r[0]}</td><td class="r">${r[1]}</td><td class="r">${r[2]}</td><td class="r">${r[3]}</td><td class="r ${cl(r[4])}"><b>${r[5] ? sgn(r[4]) : "–"}</b></td></tr>`).join("")}</tbody></table></div>`;
  const bySym = {}; closed.forEach(t => { const k = fd(t.signal_date) + " · " + t.symbol; bySym[k] = (bySym[k] || 0) + t.net; });
  const ranked = Object.entries(bySym).sort((a, b) => b[1] - a[1]);
  const mini = (title, arr) => `<div class="list"><h3>${title}</h3>${arr.length ? arr.map(([s, v]) => `<div class="li"><span class="s">${esc(s)}</span><span class="r ${cl(v)}">${sgn(v)}</span></div>`).join("") : `<div class="li"><span class="d">None yet</span></div>`}</div>`;
  h += `<h2>Best and worst trades</h2><div class="cols">${mini("Best", ranked.filter(x => x[1] > 0).slice(0, 5))}${mini("Worst", ranked.filter(x => x[1] < 0).slice(-5).reverse())}</div>`;
  const V = D.verdicts || {}, sv = V.swing || {}, iv = V.intraday || {};
  const vp = v => `<span class="pill ${/KEEP|EDGE/.test(v || "") && !/NO/.test(v || "") ? "p-up" : /NO/.test(v || "") ? "p-dn" : "p-warn"}">${esc(v || "–")}</span>`;
  $("#v-stats-in").innerHTML = h;
})();
}
let T = [], S = {};
renderAll(); decorate();
show(location.hash.slice(1) || "today");

// ---------- live prices: GitHub refreshes data/live.json every 10 minutes in market hours
// on *.github.io the fresh file comes straight from the repository (address built from this page's own address);
// on any other address (the shared link) a small proxy serves data/live.json from the same place
const LIVE = location.hostname.endsWith(".github.io")
  ? "https://raw.githubusercontent.com/" + location.hostname.split(".")[0] + "/" + location.pathname.split("/")[1] + "/main/docs/data/live.json"
  : "data/live.json";
async function pullLive() {
  try {
    const r = await fetch(LIVE + "?t=" + Date.now(), {cache: "no-store"});
    if (!r.ok) return "error";
    const L = await r.json();
    if ((L.updated || {}).ts > ((D.updated || {}).ts || 0)) {
      const keep = location.hash.slice(1) || "today", y = window.scrollY;
      D = L; renderAll(); show(keep); window.scrollTo(0, y); mkt(); decorate();
      return "new";
    }
    return "same";
  } catch (e) { return "error"; /* offline: keep what is shown */ }
}
// ---------- market status bar: open / closed (IST) and how old the numbers are
function istNow() {
  const p = {}; new Intl.DateTimeFormat("en-GB", {timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false})
    .formatToParts(new Date()).forEach(x => { p[x.type] = x.value; });
  return {wd: p.weekday, m: +p.hour * 60 + +p.minute, hm: p.hour + ":" + p.minute};
}
function mkt() {
  const n = istNow(), wkend = n.wd === "Sat" || n.wd === "Sun", open = !wkend && n.m >= 555 && n.m < 930;
  const nextDay = n.wd === "Fri" || wkend ? "Mon" : "tomorrow";
  let st, nx = "";
  if (open) { const q = Math.min(930, (Math.floor(n.m / 10) + 1) * 10); st = "Market open · " + n.hm; nx = "next update ~" + String(Math.floor(q / 60)).padStart(2, "0") + ":" + String(q % 60).padStart(2, "0"); }
  else if (!wkend && n.m < 555) st = "Pre-open · opens 9:15";
  else st = "Market closed · opens " + nextDay + " 9:15";
  const ts = (D.updated || {}).ts, mins = ts ? Math.max(0, Math.round((Date.now() / 1000 - ts) / 60)) : null;
  const ago = mins == null ? "" : mins < 1 ? "updated just now" : mins < 60 ? "updated " + mins + " min ago" : mins < 1440 ? "updated " + Math.round(mins / 60) + " h ago" : "updated " + Math.round(mins / 1440) + " d ago";
  $("#mkt").innerHTML = `<span class="st ${open ? "open" : ""}"><span class="dot"></span>${esc(st)}</span>${nx ? `<span class="sep nx">·</span><span class="nx">${esc(nx)}</span>` : ""}
    <span class="ago">${esc(ago)}</span><button type="button" id="install" aria-label="Install the app">⬇ Install</button><button type="button" id="refresh" aria-label="Refresh now">↻ Refresh</button>`;
  if (INSTALL) { $("#install").style.display = "inline-block"; $("#install").onclick = async () => { INSTALL.prompt(); await INSTALL.userChoice; INSTALL = null; mkt(); }; }
  $("#refresh").onclick = async () => { const b = $("#refresh"); b.disabled = true; b.textContent = "…"; const r = await pullLive(); toast(r === "new" ? "Updated" : r === "same" ? "Already up to date" : "Could not refresh"); mkt(); };
}
function toast(msg) {
  const el = $("#ptr"); el.firstElementChild.textContent = msg; el.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 1600);
}
let INSTALL = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); INSTALL = e; mkt(); });
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
mkt(); setInterval(mkt, 30 * 1000);
// ---------- pull to refresh (phones): pull down at the top of the page
(function () {
  let y0 = null, pulled = 0;
  const el = $("#ptr");
  document.addEventListener("touchstart", e => { y0 = (window.scrollY <= 0 && !$("#tradedlg").open) ? e.touches[0].clientY : null; pulled = 0; }, {passive: true});
  document.addEventListener("touchmove", e => {
    if (y0 == null) return;
    pulled = e.touches[0].clientY - y0;
    if (pulled > 20) { el.firstElementChild.textContent = pulled > 90 ? "Release to refresh" : "Pull to refresh"; el.classList.add("show"); }
  }, {passive: true});
  document.addEventListener("touchend", async () => {
    if (y0 == null) return;
    y0 = null;
    if (pulled > 90) { el.firstElementChild.textContent = "Refreshing…"; const r = await pullLive(); mkt(); toast(r === "new" ? "Updated" : r === "same" ? "Already up to date" : "Could not refresh"); }
    else el.classList.remove("show");
  });
})();
// ---------- trade screen as a bottom sheet on phones: swipe the header down to close
(function () {
  let y0 = null, dy = 0;
  const dlg = $("#tradedlg");
  let sx = null, sy = null;
  dlg.addEventListener("touchstart", e => { if (!e.target.closest("#chsvg")) { sx = e.touches[0].clientX; sy = e.touches[0].clientY; } }, {passive: true});
  dlg.addEventListener("touchend", e => {
    if (sx == null) return;
    const dx = e.changedTouches[0].clientX - sx, dyy = e.changedTouches[0].clientY - sy; sx = null;
    if (Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dyy) * 1.5) stepTrade(dx < 0 ? 1 : -1);
  });
  document.addEventListener("keydown", e => { if (dlg.open && (e.key === "ArrowRight" || e.key === "ArrowLeft")) stepTrade(e.key === "ArrowRight" ? 1 : -1); });
  dlg.addEventListener("touchstart", e => { if (e.target.closest(".tdh") && innerWidth < 560) { y0 = e.touches[0].clientY; dy = 0; dlg.style.transition = "none"; } }, {passive: true});
  dlg.addEventListener("touchmove", e => { if (y0 == null) return; dy = Math.max(0, e.touches[0].clientY - y0); dlg.style.transform = `translateY(${dy}px)`; }, {passive: true});
  dlg.addEventListener("touchend", () => {
    if (y0 == null) return;
    y0 = null; dlg.style.transition = "transform .2s"; dlg.style.transform = "";
    if (dy > 110) dlg.close();
  });
})();
pullLive(); setInterval(pullLive, 3 * 60 * 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pullLive(); });
</script></body></html>"""
