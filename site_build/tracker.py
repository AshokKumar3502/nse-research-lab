"""docs/tracker.html: the paper-trade tracker. One self-contained page with the book embedded (no
fetch), so it works on GitHub Pages and when opened from disk. Tabs: Swing, Intraday, Day by day."""
import json
import os

from warehouse import config

DOCS = os.path.join(config.ROOT, "docs")

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper Trades</title>
<meta name="description" content="Paper trades of every NSE Research Lab pick at Rs 50,000 a stock, P&L per stock per day.">
<style>__CSS__
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:14px 0 6px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .l{font-size:12px;color:var(--muted)}.kpi .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.kpi .s{font-size:12px;color:var(--muted);margin-top:2px}
.up{color:var(--pos)}.dn{color:var(--neg)}
.tabs{display:flex;gap:6px;margin:22px 0 6px;flex-wrap:wrap}
.tabs button{font:inherit;font-size:14px;padding:7px 14px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
.tabs button[aria-selected=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.tab{display:none}.tab.on{display:block}
td.r,th.r{text-align:right;white-space:nowrap}tr.pos-row{cursor:pointer}tr.pos-row:hover td{background:color-mix(in srgb,var(--line) 40%,transparent)}
tr.detail td{background:var(--bg);padding:6px 10px 12px}tr.detail table{font-size:13px}
.tag{font-size:11px;font-weight:700;padding:1px 7px;border-radius:99px;border:1px solid currentColor;white-space:nowrap}
.daybox{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:12px 0;overflow:hidden}
.dayhd{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);font-weight:600}
.daybox table{border:0;border-radius:0}.prog{display:inline-block;width:60px;height:6px;background:var(--line);border-radius:3px;vertical-align:middle;margin-left:6px}
.prog i{display:block;height:6px;border-radius:3px;background:var(--l1)}
.empty{color:var(--muted);padding:12px 0}
</style></head><body><main>
<header><h1>Paper trades</h1>
<p class="lede">Every pick the system publishes, traded on paper with ₹50,000 a stock: swing picks bought at the next
open and held 10 sessions, intraday picks entered at 9:30 and squared off by 15:10, each with its suggested stop-loss.
Real NSE prices, after Zerodha charges.</p>
<p class="muted">Updated <span id="built"></span> · prices through <span id="last"></span> · <a href="index.html">← picks &amp; research</a></p></header>
<div class="kpis" id="kpis"></div>
<div class="tabs" role="tablist">
<button role="tab" aria-selected="true" data-t="swing">Swing</button>
<button role="tab" aria-selected="false" data-t="intraday">Intraday</button>
<button role="tab" aria-selected="false" data-t="days">Day by day</button></div>
<section class="tab on" id="t-swing"></section>
<section class="tab" id="t-intraday"></section>
<section class="tab" id="t-days"></section>
<footer>Paper trading only: nothing here places orders, and fills are at the quoted open / 9:30 / stop prices
without extra slippage. Not investment advice; the author is not a SEBI-registered adviser.</footer>
</main>
<script>
const D = __DATA__;
const inr = v => (v < 0 ? "−₹" : "₹") + Math.abs(Math.round(v)).toLocaleString("en-IN");
const sgn = v => (v > 0 ? "+" : v < 0 ? "−" : "") + "₹" + Math.abs(Math.round(v)).toLocaleString("en-IN");
const cls = v => v > 0 ? "up" : v < 0 ? "dn" : "";
const px = v => v == null ? "–" : "₹" + Number(v).toLocaleString("en-IN", {maximumFractionDigits: 2});
const pc = (a, b) => b ? ((a / b) * 100).toFixed(2) + "%" : "–";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fd = d => d ? new Date(d + "T00:00:00").toLocaleDateString("en-IN", {day: "numeric", month: "short"}) : "–";
document.getElementById("built").textContent = D.built;
document.getElementById("last").textContent = fd(D.last_session);

// ---- KPIs
const S = D.summary, last = D.daily.length ? D.daily[D.daily.length - 1] : null;
const openCharges = D.trades.filter(t => t.status === "open").reduce((a, t) => a + t.charges, 0);
const total = S.all.realised + S.all.unrealised - openCharges;
const k = [
  ["Total P&L", sgn(total), cls(total), "closed + open, after charges"],
  ["Latest day", last ? sgn(last.net) : "–", last ? cls(last.net) : "", last ? fd(last.date) : "no trades yet"],
  ["Swing open", S.swing.open + " stocks", "", S.swing.open ? "unrealised " + sgn(S.swing.unrealised) : (S.swing.pending ? S.swing.pending + " to buy at the next open" : "none")],
  ["Swing closed", sgn(S.swing.realised), cls(S.swing.realised), S.swing.closed + " trades" + (S.swing.win_pct != null ? " · " + S.swing.win_pct + "% won" : "")],
  ["Intraday", sgn(S.intraday.realised), cls(S.intraday.realised), S.intraday.closed + " trades" + (S.intraday.win_pct != null ? " · " + S.intraday.win_pct + "% won" : "")],
];
document.getElementById("kpis").innerHTML = k.map(([l, v, c, s]) =>
  `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${esc(s)}</div></div>`).join("");

// ---- tabs
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x.id === "t-" + b.dataset.t));
  try { localStorage.setItem("tab", b.dataset.t); } catch (e) {}
});
try { const t = localStorage.getItem("tab"); if (t) document.querySelector(`[data-t="${t}"]`)?.click(); } catch (e) {}

// ---- swing
const sw = D.trades.filter(t => t.kind === "swing");
const marks = t => `<table><thead><tr><th>Day</th><th>Date</th><th class="r">Close</th><th class="r">Day P&L</th><th class="r">Total P&L</th></tr></thead><tbody>${
  t.marks.map(m => `<tr><td>${m.session}/${t.hold}</td><td>${fd(m.date)}</td><td class="r">${px(m.mark)}</td>
  <td class="r ${cls(m.day_pnl)}">${sgn(m.day_pnl)}</td><td class="r ${cls(m.total_pnl)}">${sgn(m.total_pnl)}</td></tr>`).join("")}</tbody></table>`;
function swingTable(rows, open) {
  if (!rows.length) return `<p class="empty">None yet.</p>`;
  const head = open
    ? `<th>Stock</th><th>Bought</th><th class="r">Entry</th><th class="r">Qty</th><th class="r">SL</th><th class="r">Last</th><th>Day</th><th class="r">Today</th><th class="r">Total P&L</th>`
    : `<th>Stock</th><th>Bought</th><th class="r">Entry</th><th class="r">Qty</th><th>Sold</th><th class="r">Exit</th><th>Why</th><th class="r">Net P&L</th>`;
  const body = rows.map((t, i) => {
    const m = t.marks[t.marks.length - 1] || {};
    const cells = open
      ? `<td><b>${esc(t.symbol)}</b><div class="muted" style="font-size:12px">${esc(t.industry || "")}</div></td><td>${fd(t.entry_date)}</td>
         <td class="r">${px(t.entry)}</td><td class="r">${t.qty}</td><td class="r">${px(t.sl)}</td><td class="r">${px(m.mark)}</td>
         <td>${m.session}/${t.hold}<span class="prog"><i style="width:${(m.session / t.hold * 100).toFixed(0)}%"></i></span></td>
         <td class="r ${cls(m.day_pnl)}">${sgn(m.day_pnl)}</td>
         <td class="r ${cls(t.gross)}"><b>${sgn(t.gross)}</b><div style="font-size:12px">${pc(t.gross, t.entry * t.qty)}</div></td>`
      : `<td><b>${esc(t.symbol)}</b></td><td>${fd(t.entry_date)}</td><td class="r">${px(t.entry)}</td><td class="r">${t.qty}</td>
         <td>${fd(t.exit_date)}</td><td class="r">${px(t.exit)}</td><td>${esc(t.exit_reason)}</td>
         <td class="r ${cls(t.net)}"><b>${sgn(t.net)}</b><div style="font-size:12px">${pc(t.net, t.entry * t.qty)}</div></td>`;
    return `<tr class="pos-row" data-i="${open ? "o" : "c"}${i}">${cells}</tr>
            <tr class="detail" id="d-${open ? "o" : "c"}${i}" hidden><td colspan="9">${marks(t)}${t.flag ? `<p class="muted">${esc(t.flag)}</p>` : ""}</td></tr>`;
  }).join("");
  return `<div class="tw"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
const openS = sw.filter(t => t.status === "open").sort((a, b) => b.gross - a.gross);
const closedS = sw.filter(t => t.status === "closed").sort((a, b) => (b.exit_date || "").localeCompare(a.exit_date || ""));
const pend = sw.filter(t => t.status === "pending");
document.getElementById("t-swing").innerHTML =
  (pend.length ? `<h2>Buying at the next open</h2><p class="muted">₹50,000 each, at the next session's opening price.</p><p>${
    pend.map(t => `<b>${esc(t.symbol)}</b> <span class="muted">SL ${px(t.sl)}</span>`).join(" · ")}</p>` : "") +
  `<h2>Open swing positions</h2><p class="muted">Tap a stock for its day-by-day P&L. Each holds 10 sessions unless its SL is hit; P&L shown before sell-side charges.</p>${swingTable(openS, true)}` +
  `<h2>Closed swing trades</h2>${swingTable(closedS, false)}`;
document.querySelectorAll("tr.pos-row").forEach(r => r.onclick = () => {
  const d = document.getElementById("d-" + r.dataset.i); d.hidden = !d.hidden; });

// ---- intraday, one box per day
const it = D.trades.filter(t => t.kind === "intraday");
const days = [...new Set(it.map(t => t.signal_date))].sort().reverse();
document.getElementById("t-intraday").innerHTML = `<p class="muted">The intraday method showed no edge after charges in the
  2022–2026 test, so these are tracked to see how it does live, not as a recommendation.</p>` + (days.length ? days.map(d => {
  const ts = it.filter(t => t.signal_date === d);
  const net = ts.reduce((a, t) => a + t.net, 0);
  const why = t => t.status === "pending" ? "waiting" : t.status === "open" ? "open" :
    (t.exit_reason || "").startsWith("SL") ? "SL " + (t.exit_time || "") : "Close " + (t.exit_time || "15:09");
  const rows = ts.map(t => `<tr><td><span class="tag ${t.side === "long" ? "up" : "dn"}">${t.side === "long" ? "BUY" : "SELL"}</span></td>
    <td><b>${esc(t.symbol)}</b></td><td class="r ${cls(t.net)}"><b>${t.status === "pending" ? "–" : sgn(t.net)}</b></td>
    <td class="r">${px(t.entry)}</td><td class="r">${px(t.exit)}</td><td style="white-space:nowrap">${esc(why(t))}</td>
    <td class="r">${px(t.sl)}</td><td class="r">${t.qty}</td></tr>`).join("");
  return `<div class="daybox"><div class="dayhd"><span>${fd(d)} · ${ts.length} trades</span><span class="${cls(net)}">${sgn(net)}</span></div>
    <div class="tw" style="margin:0"><table><thead><tr><th></th><th>Stock</th><th class="r">Net P&L</th><th class="r">Entry 9:30</th>
    <th class="r">Exit</th><th>Exit at</th><th class="r">SL</th><th class="r">Qty</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}).join("") : `<p class="empty">No intraday picks yet.</p>`);

// ---- day by day: bars + table
const dd = D.daily;
let svg = "";
if (dd.length) {
  const W = 760, H = 200, P = 34, m = Math.max(...dd.map(r => Math.abs(r.net)), 1), slot = (W - 2 * P) / dd.length, bw = Math.min(40, slot - 4);
  const y0 = H / 2;
  svg = `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Net P&L per day">
    <line x1="${P}" x2="${W - P}" y1="${y0}" y2="${y0}" class="grid"/>` + dd.map((r, i) => {
      const x = P + i * slot + (slot - bw) / 2, h = Math.abs(r.net) / m * (H / 2 - 24);
      return `<rect x="${x}" y="${r.net >= 0 ? y0 - h : y0}" width="${bw}" height="${Math.max(h, 1)}" rx="2" fill="${r.net >= 0 ? "var(--pos)" : "var(--neg)"}"><title>${fd(r.date)}: ${sgn(r.net)}</title></rect>
      <text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle" class="ax">${fd(r.date)}</text>`; }).join("") + `</svg></div>`;
}
document.getElementById("t-days").innerHTML = `<h2>P&L by day</h2><p class="muted">Swing = the day's move in open positions (marked to the close);
  intraday = that day's closed trades; charges are taken on the day a trade closes.</p>${svg}
  <div class="tw"><table><thead><tr><th>Date</th><th class="r">Swing</th><th class="r">Intraday</th><th class="r">Charges</th><th class="r">Net</th><th class="r">Running total</th></tr></thead><tbody>${
  dd.slice().reverse().map(r => `<tr><td>${fd(r.date)}</td><td class="r ${cls(r.swing)}">${sgn(r.swing)}</td><td class="r ${cls(r.intraday)}">${sgn(r.intraday)}</td>
  <td class="r">${inr(-r.charges)}</td><td class="r ${cls(r.net)}"><b>${sgn(r.net)}</b></td><td class="r ${cls(r.cumulative)}">${sgn(r.cumulative)}</td></tr>`).join("")}</tbody></table></div>`;
</script></body></html>"""


def build(css, book=None):
    path = os.path.join(DOCS, "paper.json")
    if book is None:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            book = json.load(fh)
    data = json.dumps(book, default=str).replace("</", "<\\/")
    out = os.path.join(DOCS, "tracker.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(PAGE.replace("__CSS__", css).replace("__DATA__", data))
    return out
