"""Builds the public results site (docs/index.html + docs/results.json) for GitHub Pages from the
reports the engines write. Every verdict is shown, failures included. No raw NSE data is published,
only summary statistics.

  python -m site_build
"""
import datetime
import html
import json
import os

import pandas as pd

from warehouse import config, db

DOCS = os.path.join(config.ROOT, "docs")
M = os.path.join(config.DATA, "models")


def _j(*p):
    path = os.path.join(*p)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def e(x):
    return html.escape("" if x is None else str(x))


def pct(v, d=2, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    return ("%+.*f%%" if sign else "%.*f%%") % (d, v)


PILL = {"EDGE": "good", "KEEP": "good", "PASS": "good", "GREEN": "good", "OK": "good",
        "PROMISING": "warn", "UNPROVEN": "warn", "DOUBT": "warn", "WARMING UP": "muted", "AMBER": "warn", "WARN": "warn",
        "NO EDGE": "bad", "FAIL": "bad", "KILL": "bad", "RED": "bad", "TOO LITTLE DATA": "muted", "BASELINE": "muted"}


def pill(v):
    return '<span class="pill %s">%s</span>' % (PILL.get(str(v).upper(), "muted"), e(v))


def line_chart(series, labels, height=220):
    """series: {name: pd.Series indexed by date}; inline SVG, both lines rebased to 100."""
    w, h, pad = 760, height, 36
    allv = pd.concat(series.values())
    lo, hi = float(allv.min()), float(allv.max())
    span = (hi - lo) or 1
    idx = sorted(set().union(*[s.index for s in series.values()]))
    t0, t1 = idx[0], idx[-1]
    tspan = (t1 - t0).days or 1

    def xy(d, v):
        return pad + (d - t0).days / tspan * (w - 2 * pad), h - pad - (v - lo) / span * (h - 2 * pad)
    out = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s">' % (w, h, e(" vs ".join(series)))]
    for k in range(5):
        v = lo + span * k / 4
        y = h - pad - k / 4 * (h - 2 * pad)
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/><text x="4" y="%.1f" class="ax">%.0f</text>'
                   % (pad, w - pad, y, y, y + 4, v))
    for y in range(t0.year, t1.year + 1):
        d = pd.Timestamp(y, 1, 1)
        if t0 <= d <= t1:
            x, _ = xy(d, lo)
            out.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%d</text>' % (x, h - 10, y))
    for (name, s), cls in zip(series.items(), labels):
        pts = " ".join("%.1f,%.1f" % xy(d, v) for d, v in s.items())
        out.append('<polyline class="%s" points="%s"/>' % (cls, pts))
    out.append("</svg>")
    return "".join(out)


def bars(d, unit="%"):
    """{label: value} -> small diverging bar chart (HTML)."""
    m = max([abs(v) for v in d.values()] + [1e-9])
    rows = []
    for k, v in d.items():
        w = abs(v) / m * 48
        side = "pos" if v >= 0 else "neg"
        style = "left:50%%;width:%.1f%%" % w if v >= 0 else "left:%.1f%%;width:%.1f%%" % (50 - w, w)
        rows.append('<div class="bar"><span class="bl">%s</span><span class="track"><span class="fill %s" style="%s">'
                    '</span></span><span class="bv">%+.2f%s</span></div>' % (e(k), side, style, v, unit))
    return '<div class="bars">%s</div>' % "".join(rows)


def table(head, rows, cls=""):
    return ('<div class="tw"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (
        cls, "".join("<th>%s</th>" % h for h in head),
        "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)))


def _latest(prefix):
    d = os.path.join(config.DATA, "picks")
    if not os.path.isdir(d):
        return None
    fs = sorted(f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(".json"))
    return _j(d, fs[-1]) if fs else None


def rs(v):
    if v is None:
        return "–"
    n = int(round(v))
    return ("−₹" if n < 0 else "₹") + format(abs(n), ",")


def picks_html():
    sw, it = _latest("swing-"), _latest("intraday-")
    live_p = os.path.join(config.DATA, "monitor", "live.parquet")
    live = pd.read_parquet(live_p) if os.path.exists(live_p) else pd.DataFrame()
    parts = []
    nse_ind = {}
    ip = os.path.join(config.SETS, "industries", "all.parquet")
    if os.path.exists(ip):
        t = pd.read_parquet(ip)
        nse_ind = dict(zip(t.symbol, t.industry))
    if sw:
        rows = [[i + 1, "<b>%s</b>" % e(p["symbol"]), e(nse_ind.get(p["symbol"], p["industry"])), "%.2f" % p["score_pct"], rs(p["ref_close"]),
                 p.get("shares_per_lakh", "–"), rs(p.get("value_per_lakh")), "%s (%+.1f%%)" % (rs(p["stop"]), p["stop_pct"])]
                for i, p in enumerate(sw.get("picks", []))]
        reg = sw.get("regime", {})
        parts.append("""<h3>Swing · buy at the open after %s, hold 10 sessions %s</h3>
<p class="muted">%s Market: NIFTY %s its 200-day average, %s%% of stocks above their 50-day; exposure %d%%.
Sizes are per ₹1 lakh of capital, split equally across the list. The backtest passed and its review found no
bias; the live paper record below starts with these picks, so treat them as unproven until it builds up.
*Reference stop only: the tested rule has no stop.</p>%s""" % (
            e(sw["session"]), pill(e(sw["status"]) if sw["status"].startswith(("KILLED", "HALTED")) else
                                   "PAPER ONLY" if sw["status"].startswith("PAPER") else "BACKTEST PASSED"),
            e(sw.get("tested_rule", "")), "above" if reg.get("nifty_above_ma200") else "below",
            reg.get("breadth_ma50_pct"), int(reg.get("exposure", 0) * 100),
            table(["#", "Stock", "Industry", "Score", "Last close", "Shares / ₹1L", "Value", "Ref. stop*"], rows)
            if rows else "<p><b>No swing picks today:</b> %s</p>" % e(sw.get("status"))))
    if it:
        le = it.get("leverage_effect", {})
        lev = it.get("leverage", 5)
        lo = le.get("long", {})
        warn = ("<div class='warnbox'><b>Paper only: no proven edge.</b> In the 2022–2026 test the long picks averaged "
                "%s a day after charges on the amount traded. At %dx leverage that is about <b>%s of capital a day</b> "
                "(≈ %s per ₹1 lakh). Leverage multiplies losses as well as gains.</div>" % (
                    pct(lo.get("backtest_pct_per_day_on_exposure"), 2), lev, pct(lo.get("pct_per_day_on_capital"), 2),
                    rs((lo.get("pct_per_day_on_capital") or 0) / 100 * 100000)))
        settled = {}
        if len(live):
            for r in live[(live.date == it["day"]) & live.strategy.str.startswith("intraday_")].itertuples():
                settled[(r.strategy.split("_")[1], r.sym)] = r.net
        tabs = ""
        for side in ("long", "short"):
            rows = [["<b>%s</b>" % e(p["symbol"]), rs(p["ref_price"]), rs(p["stop"]), p.get("shares_per_lakh", "–"),
                     rs(p.get("exposure_per_lakh")), rs(p.get("risk_to_stop_rs_per_lakh")),
                     pct(settled[(side, p["symbol"])] * 100) if (side, p["symbol"]) in settled else "open"]
                    for p in it.get(side, [])]
            if rows:
                tabs += "<h4>%s</h4>%s" % ("Buy (long)" if side == "long" else "Sell short", table(
                    ["Stock", "9:30 price", "Stop", "Shares / ₹1L at %dx" % lev, "Exposure", "Loss at stop", "Result"], rows))
            elif it.get("blocked", {}).get(side):
                tabs += "<p><b>%s:</b> %s</p>" % (side, e(it["blocked"][side]))
        parts.append("""<h3>Intraday · %s, decided at %s (made %s) %s</h3>%s
<p class="muted">Entry at the 9:30 price, stop at the other side of the 9:15–9:30 range, square off by 15:10.%s
One day's results say nothing about the method; the multi-year test above does.</p>%s""" % (
            e(it["day"]), e(it.get("decision_time")), e(it.get("made_at")), pill("PAPER ONLY"), warn,
            " These picks were computed after the close from the 9:15–9:30 candles only; from the next session they are"
            " made live at 9:31." if (it.get("made_at") or "00")[:2] >= "15" else "", tabs))
    track = ""
    if len(live):
        g = live.groupby("strategy").net.agg(["count", "mean"]).reset_index()
        rows = [[e(r.strategy), r["count"], pct(r["mean"] * 100)] for _, r in g.iterrows()]
        track = "<h3>Track record of published picks</h3><p class='muted'>Settled on real NSE prices after charges.</p>%s" % (
            table(["Strategy", "Closed picks", "Average net"], rows))
    else:
        track = "<p class='muted'>Track record: no published pick has closed yet; results appear here as they settle.</p>"
    if not parts:
        return ""
    return ('<section id="picks"><h2>Today\'s picks</h2><p><a href="tracker.html"><b>Paper trades: every pick at '
            '₹50,000, P&amp;L per stock per day →</b></a></p>%s%s</section>' % ("".join(parts), track))


def build():
    con = db.connect(read_only=True)
    intr = _j(M, "intraday", "report-0930.json")
    sw = _j(M, "swing", "report-h10.json")
    sw20 = _j(M, "swing", "report-h20.json")
    rv = _j(M, "swing", "review-h10.json")
    port = _j(M, "swing", "portfolio-h10.json")
    lab = _j(config.DATA, "lab", "leaderboard.json")
    mon = _j(config.DATA, "monitor", "status.json")
    q = _j(config.DATA, "quality.json")
    full = _j(config.ROOT, "state", "warehouse_summary.json")      # the laptop's 10-year warehouse
    summ = full["tables"] if full else db.summary(con)
    built = datetime.datetime.now(config.IST).strftime("%d %b %Y, %H:%M IST")
    last = q.get("last_session") if q else None

    # ---------- verdict cards
    sw_best = (sw or {}).get("strategies", {}).get("model_top/close", {})
    cards = []
    if intr:
        b = intr["strategies"]
        cards.append(("Intraday · 9:30 ranker", "NO EDGE" if intr["best"]["verdict"] != "EDGE" else "EDGE",
                      "Best: %s %s a day after %.2f%% costs. The model ranks stocks correctly, but the spread is smaller "
                      "than the cost of a round trip." % (e(intr["best"]["strategy"]),
                                                          pct(b[intr["best"]["strategy"]]["mean_pct"]),
                                                          intr["cost_round_trip_pct"])))
    if sw:
        cards.append(("Swing · 10-day ranker", (rv or {}).get("verdict", sw["best"]["verdict"]),
                      "Top 10 picks: %s per 10 sessions after %.2f%% costs (t %.2f). Skeptic review: %s." % (
                          pct((rv or {}).get("mean_pct", sw_best.get("mean_pct"))), sw["cost_round_trip_pct"],
                          (rv or {}).get("t_offset0", sw_best.get("t") or 0),
                          ", ".join((rv or {}).get("problems", [])) or "no problems found")))
    if lab:
        st = pd.Series([r["stage"] for r in lab["rows"]]).value_counts().to_dict()
        cards.append(("Research lab · %d ideas" % len(lab["rows"]),
                      "PASS" if st.get("paper") or st.get("live_small") else ("PROMISING" if st.get("research") else "FAIL"),
                      "%d passed, %d promising, %d rejected against a multiple-testing bar of t ≥ %.2f." % (
                          st.get("paper", 0) + st.get("live_small", 0), st.get("research", 0), st.get("rejected", 0),
                          max([r["hurdle"] or 0 for r in lab["rows"]] + [0]))))
    if mon:
        live = [s for s in mon["strategies"]]
        cards.append(("Live monitor", "WARMING UP" if all(s["status"] == "WARMING UP" for s in live) else
                      max((s["status"] for s in live), key=lambda x: {"GREEN": 0, "WARMING UP": 0, "AMBER": 1, "RED": 2}[x]),
                      "Paper picks are settled on real prices and compared with each backtest; a red alert switches "
                      "a strategy off."))
    card_html = "".join('<div class="card"><div class="ch"><h3>%s</h3>%s</div><p>%s</p></div>' % (e(t), pill(v), d)
                        for t, v, d in cards)

    # ---------- swing section
    swing_html = ""
    if sw and port:
        eq = pd.read_csv(os.path.join(M, "swing", "equity-h10.csv"), parse_dates=["date"]).set_index("date").equity
        nf = con.execute("SELECT date, close FROM idx_daily WHERE index_key='nifty 50' AND date BETWEEN ? AND ?",
                         [eq.index.min().date(), eq.index.max().date()]).df()
        nf["date"] = pd.to_datetime(nf.date)
        nf = nf.set_index("date").close
        chart = line_chart({"Swing portfolio": eq / eq.iloc[0] * 100, "NIFTY 50": nf / nf.iloc[0] * 100},
                           ["l1", "l2"])
        p, n = port["portfolio"], port["nifty50_buy_hold"]
        stats = table(["", "Swing portfolio", "NIFTY 50 buy & hold"], [
            ["Annual return", pct(p["cagr_pct"]), pct(n["cagr_pct"])],
            ["Worst drawdown", pct(p["max_drawdown_pct"]), pct(n["max_drawdown_pct"])],
            ["Sharpe", p["sharpe"], n["sharpe"]],
            ["Total", pct(p["total_pct"], 0), pct(n["total_pct"], 0)]])
        yrs = table(["Year"] + list(p["by_year_pct"]), [["Portfolio"] + [pct(v, 1) for v in p["by_year_pct"].values()],
                                                     ["NIFTY 50"] + [pct(n["by_year_pct"].get(y), 1) for y in p["by_year_pct"]]], "num")
        strat = table(["Strategy", "Periods", "Mean / period", "t", "Win %", "Verdict"], [
            [e(k), s.get("days", 0), pct(s.get("mean_pct")), s.get("t") if s.get("t") is not None else "–",
             s.get("win_days_pct", "–"), pill(s.get("verdict"))] for k, s in sw["strategies"].items() if s.get("days")])
        dec = bars({"D%d" % (int(k) + 1): v for k, v in sw.get("deciles_excess_pct", {}).items()})
        review = ""
        if rv:
            rows = [["Survivorship", "%.3f%% of stock-days lose their next 10 sessions" % rv["survivorship_pct"]],
                    ["Entry fills", "%.2f%% of picks gap 5%%+, %d locked; edge without them %s" % (
                        rv["fills"]["gap5_pct"], rv["fills"]["locked"], pct(rv["fills"]["edge_without_pct"]))],
                    ["Picks (K)", ", ".join("K=%s %s" % (k, pct(v)) for k, v in rv["k"].items())],
                    ["Rebalance day", ", ".join(pct(v) for v in rv["offsets_pct"])],
                    ["Years", ", ".join("%s %s" % (y, pct(v)) for y, v in rv["years_pct"].items())]]
            for name, L in rv["leak"].items():
                rows.append(["Missing %s data" % name, "%.1f%% of the universe, %.1f%% of picks; excess %s when missing vs %s when present"
                             % (L["universe_missing_pct"], L["picks_missing_pct"], pct(L["excess_missing_pct"]),
                                pct(L["excess_present_pct"]))])
            review = ("<h3>Skeptic review %s</h3><p class='muted'>Checks that statistics cannot see: survivorship, "
                      "fills, robustness, and whether missing data quietly encodes which stocks survived.</p>%s" % (
                          pill(rv["verdict"]), table(["Check", "Result"], rows)))
        swing_html = """
<section id="swing"><h2>Swing: 10-session holds</h2>
<p>Every evening, every stock in the point-in-time liquid universe (top 500 by turnover that day, ETFs excluded,
delisted stocks included) is ranked by a LightGBM model trained only on earlier data (walk-forward, retrained
quarterly, 25-session embargo). Entry at the next open; Zerodha delivery charges + 0.1%% slippage a side on
every trade (%.2f%% a round trip).</p>
<h3>Portfolio simulation, Rs 10 lakh, out of sample from %s</h3>
<p class="muted">1%% risk a trade, 2-ATR stop, at most 10 positions and 3 per industry, exposure cut when NIFTY
is below its 200-day average or breadth is weak.</p>
<div class="chart">%s<div class="legend"><span class="k l1"></span>Swing portfolio <span class="k l2"></span>NIFTY 50</div></div>
%s%s
<h3>Every strategy on the same yardstick</h3>%s
<h3>Excess 10-day return by prediction decile</h3><p class="muted">D1 = the stocks the model likes least.</p>%s
%s</section>""" % (sw["cost_round_trip_pct"], e(sw.get("oos_from")), chart, stats, yrs, strat, dec, review)
        if sw20:
            s20 = sw20["strategies"].get("model_top/close", {})
            swing_html = swing_html.replace("</section>", "<p class='muted'>20-session holds: %s a period (t %s), "
                                                          "fewer independent periods (%s).</p></section>" % (
                                                              pct(s20.get("mean_pct")), s20.get("t"), s20.get("days")))

    # ---------- intraday
    intr_html = ""
    if intr:
        rows = [[e(k), s.get("days", 0), pct(s.get("mean_pct"), 3), s.get("t"), s.get("win_days_pct"), pill(s.get("verdict"))]
                for k, s in intr["strategies"].items() if s.get("days")]
        intr_html = """<section id="intraday"><h2>Intraday: decide at 9:30</h2>
<p>%d sessions (%s to %s, %d out of sample), every F&amp;O stock, 28 features from the first 15 minutes, the
previous day, delivery, futures positioning and the market. Entry at the 9:30 open, exit at the opening-window
stop or 15:10. Zerodha MIS charges + 0.05%% slippage a side: %.2f%% a round trip.</p>%s
<h3>Excess return by prediction decile</h3>%s
<p class="muted">The ranking is real (the deciles are ordered) but the gap between best and worst is smaller than
the cost of trading it. Intraday picks stay paper-only.</p></section>""" % (
            intr["sessions"], e(intr["from"]), e(intr["to"]), intr["oos_sessions"], intr["cost_round_trip_pct"],
            table(["Strategy", "Days", "Mean / day", "t", "Win %", "Verdict"], rows),
            bars({"D%d" % (int(k) + 1): v for k, v in intr.get("deciles_excess_pct", {}).items()}))

    # ---------- lab
    lab_html = ""
    if lab:
        rows = [[e(r["name"]), e(r["author"]), pill(r["verdict"]), e(r["stage"]), pct(r["mean_pct"]),
                 "%s / %s" % (r["t"], r["hurdle"]), e(", ".join(r["failed"] or []) or "–")] for r in lab["rows"]]
        lab_html = """<section id="lab"><h2>Research lab</h2>
<p>Each idea is a written rule tested on the same 10 years, after costs. It must be profitable, clear a t-statistic
hurdle that rises with every idea ever tried (%d so far), be positive in both halves and in two-thirds of the years,
survive double costs, beat random picks on the same days, and survive &plusmn;20%% changes to its numbers.</p>%s
</section>""" % (lab["trials"], table(["Idea", "From", "Verdict", "Stage", "Mean / period", "t / hurdle", "Failed checks"], rows))

    # ---------- data
    keep = {"eq_daily": "Stock bars (adjusted)", "idx_daily": "Index closes", "delivery": "Delivery %",
            "fo_futures": "Futures", "fo_options": "Options (nearest 2 expiries)", "participant_oi": "FII / DII positions",
            "minute_bars": "1-minute candles", "results_dates": "Results filing times", "industry_pit": "Point-in-time industries"}
    drows = [[keep[r["name"]], "{:,}".format(r["rows"]), r["symbols"] or "–", "%s → %s" % (r["first"], r["last"])]
             for r in summ if r["name"] in keep]
    qrows = [[e(c["name"]), pill(c["status"]), e(c["detail"])] for c in (q or {}).get("checks", [])]
    data_html = """<section id="data"><h2>Data</h2><p>NSE's own end-of-day files (bhavcopy, delivery, F&amp;O,
participant OI, index closes), Upstox's public 1-minute candles and NSE results filings, in a local Parquet + DuckDB
warehouse. Raw data is not published here.</p>%s<h3>Quality checks %s</h3>%s</section>""" % (
        table(["Dataset", "Rows", "Symbols", "Range"], drows), pill((q or {}).get("status", "–")),
        table(["Check", "Status", "Detail"], qrows))

    doc = TEMPLATE.format(css=CSS, built=e(built), last=e(last), cards=card_html, swing=swing_html, intraday=intr_html,
                          lab=lab_html, data=data_html, picks=picks_html())
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "research.html"), "w", encoding="utf-8") as fh:   # the app is index.html
        fh.write(doc)
    with open(os.path.join(DOCS, "results.json"), "w", encoding="utf-8") as fh:
        json.dump({"built": built, "last_session": last, "intraday": intr, "swing": sw, "swing_review": rv,
                   "swing_portfolio": port, "lab": lab, "monitor": {k: (mon or {}).get(k) for k in ("built", "strategies")}},
                  fh, indent=1, default=str)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    from . import app, tracker
    tracker.build(CSS)
    return app.build()


CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1b1b19;--muted:#66655f;--line:#e2e0d8;--good:#1d7a4c;--warn:#9a5b00;--bad:#b3261e;
--l1:#2457c5;--l2:#9a9a94;--pos:#2e8b57;--neg:#c0392b}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121211;--card:#1c1c1a;--ink:#ecebe5;--muted:#a3a29b;
--line:#31312d;--good:#5cc28c;--warn:#e3a24a;--bad:#f08a80;--l1:#7aa2ff;--l2:#6d6d68;--pos:#5cc28c;--neg:#f08a80}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 64px}header h1{font-size:30px;margin:0 0 6px;letter-spacing:-.01em}
.lede{color:var(--muted);margin:0 0 6px;max-width:70ch}nav{display:flex;gap:14px;flex-wrap:wrap;margin:16px 0 24px;font-size:14px}
nav a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--line)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card p{margin:8px 0 0;font-size:14px;color:var(--muted)}.ch{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
.ch h3{margin:0;font-size:15px}.pill{font-size:11px;font-weight:700;letter-spacing:.03em;padding:2px 8px;border-radius:99px;
border:1px solid currentColor;white-space:nowrap;text-transform:uppercase}.good{color:var(--good)}.warn{color:var(--warn)}
.bad{color:var(--bad)}.muted{color:var(--muted)}section{margin-top:44px}h2{font-size:22px;margin:0 0 8px}
h3{font-size:16px;margin:24px 0 8px}p{max-width:75ch}.tw{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14px;background:var(--card);border:1px solid var(--line);border-radius:10px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
td{font-variant-numeric:tabular-nums}.chart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px}
svg{width:100%;height:auto;display:block}svg .grid{stroke:var(--line)}svg .ax{fill:var(--muted);font-size:11px}
svg polyline{fill:none;stroke-width:2}svg .l1{stroke:var(--l1)}svg .l2{stroke:var(--l2);stroke-dasharray:5 4}
.legend{font-size:13px;color:var(--muted);display:flex;gap:14px;align-items:center;padding:4px 6px}
.k{display:inline-block;width:18px;height:3px;margin-right:4px;vertical-align:middle}.k.l1{background:var(--l1)}.k.l2{background:var(--l2)}
.bars{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.bar{display:grid;grid-template-columns:40px 1fr 70px;gap:8px;align-items:center;font-size:13px;margin:3px 0}
.track{position:relative;height:12px;background:linear-gradient(var(--line),var(--line)) 50% 0/1px 100% no-repeat}
.fill{position:absolute;top:2px;height:8px;border-radius:2px}.fill.pos{background:var(--pos)}.fill.neg{background:var(--neg)}
.warnbox{background:color-mix(in srgb,var(--bad) 10%,var(--card));border:1px solid var(--bad);border-radius:10px;
padding:10px 14px;margin:10px 0;font-size:14px;max-width:75ch}h4{font-size:14px;margin:16px 0 4px}
.bv{text-align:right;font-variant-numeric:tabular-nums}footer{margin-top:48px;font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}
"""

TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NSE Research Lab</title>
<meta name="description" content="Walk-forward tested intraday and swing strategies on NSE, after costs, with every verdict shown.">
<style>{css}</style></head><body><main>
<header><h1>NSE Research Lab</h1>
<p class="lede">Intraday and swing ideas for Indian stocks, tested on 10 years of NSE data after Zerodha charges, out of
sample, and reviewed for bias. Failures are shown as plainly as successes.</p>
<p class="muted">Built {built} · data through {last}</p>
<nav><a href="#picks"><b>Today's picks</b></a><a href="tracker.html"><b>Paper trades &amp; P&amp;L</b></a><a href="#swing">Swing</a><a href="#intraday">Intraday</a><a href="#lab">Research lab</a><a href="#data">Data</a>
<a href="results.json">results.json</a></nav></header>
{picks}
<h2 style="margin-top:44px">How each method tested</h2>
<div class="cards">{cards}</div>
{swing}{intraday}{lab}{data}
<footer>For education and research only. Not investment advice; the author is not a SEBI-registered adviser.
Past results, including out-of-sample backtests, do not predict future returns. Nothing here places orders.</footer>
</main></body></html>"""
