"""A self-contained HTML dashboard (data/monitor/dashboard.html): one card per strategy with its
status, live vs backtest numbers and the cumulative live result against the backtest's expected
path, the day's events, and the old system's scoreboard. No external files; opens offline."""
import html

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--green:#1f7a4d;--amber:#a86400;
--red:#b3261e;--grey:#6b6b66;--live:#2457c5;--exp:#9a9a94}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;
--muted:#a3a29c;--line:#33332f;--green:#5cc28c;--amber:#e3a24a;--red:#f08a80;--grey:#a3a29c;--live:#7aa2ff;--exp:#6d6d68}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:1100px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px}.name{font-weight:600;word-break:break-word}
.pill{font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;border:1px solid currentColor;white-space:nowrap}
.GREEN{color:var(--green)}.AMBER{color:var(--amber)}.RED{color:var(--red)}.WARMING{color:var(--grey)}
.nums{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:10px 0}.nums div{font-size:12px;color:var(--muted)}
.nums b{display:block;font-size:16px;color:var(--ink);font-variant-numeric:tabular-nums}
.why{font-size:13px;color:var(--muted);margin:6px 0 0}.blocked{color:var(--red);font-weight:600;font-size:13px;margin-top:6px}
svg{width:100%;height:70px;display:block}h2{font-size:16px;margin:26px 0 8px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600}
.tbl{overflow-x:auto}ul{padding-left:18px}.banner{background:var(--red);color:#fff;padding:10px 14px;border-radius:8px;margin-bottom:14px;font-weight:600}
"""


def _spark(series, exp_mean):
    if not series:
        return '<svg viewBox="0 0 300 70" role="img" aria-label="no live results yet"></svg>'
    cum, c = [], 0.0
    for v in series:
        c += v
        cum.append(c)
    expected = [exp_mean * (i + 1) for i in range(len(series))] if exp_mean is not None else []
    vals = cum + expected + [0.0]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1e-9
    n = max(len(series) - 1, 1)

    def pts(arr):
        return " ".join("%.1f,%.1f" % (i / n * 296 + 2, 66 - (v - lo) / span * 62) for i, v in enumerate(arr))
    zero = 66 - (0 - lo) / span * 62
    out = ['<svg viewBox="0 0 300 70" role="img" aria-label="cumulative live result vs backtest expectation">',
           '<line x1="2" x2="298" y1="%.1f" y2="%.1f" stroke="var(--line)"/>' % (zero, zero)]
    if expected:
        out.append('<polyline fill="none" stroke="var(--exp)" stroke-dasharray="4 3" stroke-width="1.5" points="%s"/>'
                   % pts(expected))
    out.append('<polyline fill="none" stroke="var(--live)" stroke-width="2" points="%s"/>' % pts(cum))
    out.append("</svg>")
    return "".join(out)


def _f(v, suffix="%"):
    return "-" if v is None else "%s%s" % (v, suffix)


def write(path, status, rows, exp):
    e = html.escape
    cards = []
    for r in rows:
        st = r["status"]
        cls = "WARMING" if st == "WARMING UP" else st
        bt = (exp.get(r["strategy"]) or {}).get("mean")
        cards.append(
            '<div class="card"><div class="head"><span class="name">%s</span><span class="pill %s">%s</span></div>'
            '<div class="nums"><div>live mean<b>%s</b></div><div>backtest<b>%s</b></div><div>periods<b>%s</b></div>'
            '<div>total<b>%s</b></div><div>drawdown<b>%s</b></div><div>z<b>%s</b></div></div>%s'
            '<p class="why">%s</p>%s</div>' % (
                e(r["strategy"]), cls, e(st), _f(r["live_mean_pct"]), _f(r["bt_mean_pct"]), r["n"],
                _f(r["live_total_pct"]), _f(r["live_max_dd_pct"]), r.get("z", "-"), _spark(r["series"], bt),
                e("; ".join(r["reasons"])),
                '<p class="blocked">%s</p>' % e(r["blocked"]) if r["blocked"] else ""))
    ev = "".join("<li><b class='%s'>%s</b> %s: %s</li>" % (x["level"], e(x["level"]), e(x["strategy"]), e(x["text"]))
                 for x in status["events"]) or "<li>Nothing new.</li>"
    legacy = ""
    if status["legacy"]:
        legacy = ("<h2>Old system, for comparison</h2><div class='tbl'><table><tr><th>rule</th><th>closed</th>"
                  "<th>win %</th><th>avg net %</th><th>total Rs</th></tr>" +
                  "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                      e(str(r["rule"])), r["closed"], r["win_pct"], r["avg_net_pct"], r["total_net_rs"])
                      for r in status["legacy"]) + "</table></div>")
    banner = ('<div class="banner">HALTED: %s</div>' % e(status["halt"].get("reason", ""))
              if status.get("halt", {}).get("on") else "")
    doc = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
           "content='width=device-width,initial-scale=1'><title>Live Monitor</title><style>%s</style></head><body><main>"
           "<h1>Live monitor</h1><p class='sub'>Built %s · last session %s · data checks %s · "
           "blue = live cumulative, dashed = backtest expectation</p>%s<div class='grid'>%s</div>"
           "<h2>Events</h2><ul>%s</ul>%s<p class='sub' style='margin-top:24px'>Paper research only. Past results "
           "are not predictions; nothing here places orders.</p></main></body></html>") % (
        CSS, e(status["built"]), e(status["last_session"]), e(status["data"].upper()), banner,
        "".join(cards) or "<p>No strategies to monitor yet.</p>", ev, legacy)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
