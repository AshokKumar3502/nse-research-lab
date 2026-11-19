# NSE Research Lab

Results site: https://ashokkumar3502.github.io/nse-research-lab/

## Phase 1: research warehouse

One local, point-in-time store of Indian market data for research: Parquet files (one per dataset
per month) with a DuckDB file of views and derived tables on top. Every later phase (intraday
model, swing meta-model, research lab) reads from here instead of fetching its own data.

## Setup

Expects the india-day-trader-skill pack next to this folder (`../india-day-trader-skill`): the warehouse
reuses its NSE day-file cache, downloader and results parser. Override with `NSEWH_SOURCE` if it lives
elsewhere.

```powershell
cd nse-warehouse
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Commands

```powershell
.venv\Scripts\python -m warehouse sync --fetch          # evening: new days of everything, rebuild, checks
.venv\Scripts\python -m warehouse minute --universe fo  # 1-minute candles, F&O stocks + indices, since 2022
.venv\Scripts\python -m warehouse status                # rows, symbols, date range of every table
.venv\Scripts\python -m warehouse check                 # quality checks -> data/quality.json
.venv\Scripts\python -m warehouse sql "SELECT * FROM eq_daily WHERE sym_id='RELIANCE' ORDER BY date DESC LIMIT 5"
.venv\Scripts\python -m pytest -q                       # offline tests
```

Every loader can be re-run: rows are merged by key, the newest wins, and days NSE never published
are remembered in `data/state.json` so they are not asked again.

## What is in it

| table / view | from | since | notes |
| --- | --- | --- | --- |
| `eq_daily` | NSE CM bhavcopy (via the skill pack's cache) | 2016-11 | raw OHLCV + `sym_id` (today's ticker), bonus/split `adj_*` columns |
| `idx_daily` | NSE ind_close_all | 2016-11 | every NSE index and India VIX |
| `delivery` | sec_bhavdata_full; MTO files before 2019-08-23 | 2016-11 | delivery qty and %, average price, trades |
| `fo_futures` | NSE F&O bhavcopy (old format + UDiFF) | 2016-11 | every future: OI, change in OI, settle, underlying |
| `fo_options` | same | 2016-11 | all strikes of the nearest 2 expiries per underlying |
| `participant_oi` | NSE fao_participant_oi | 2016-11 | Client / DII / FII / Pro positions in index and stock F&O |
| `minute_bars` | Upstox public candle API; skill recorder (Yahoo) | 2022-01 | 1-minute OHLCV, `source` column |
| `preopen` | skill recorder | 2026-10 | NSE pre-open book: IEP, buy/sell qty, top of book (cannot be backfilled) |
| `corp_actions`, `symbol_changes`, `results_dates`, `instruments` | NSE, Upstox | | reference |
| `fo_universe` | derived | | (date, symbol) with stock futures: exact point-in-time F&O list |
| `liquidity_universe` | derived | | daily rank by 60-session turnover, delisted stocks included |
| `minute_scale` | derived | | converts Upstox's back-adjusted minute prices to NSE's raw price |

## Known limits (read before trusting a backtest)

- **Adjustments**: `adj_*` covers bonuses, splits and consolidations from NSE's corporate action list.
  Dividends, rights issues and demergers are not adjusted. `check` lists liquid one-day moves over
  35% with no action on record so they can be inspected.
- **Minute prices are back-adjusted by Upstox** (for example HDFCBANK x 0.50 before its 2025 bonus).
  Intraday returns are unaffected; multiply by `minute_scale.scale` to compare with NSE's daily prices.
- **Minute history covers today's listed stocks only**: Upstox's keys are ISIN-based from its current
  instrument list, so a stock that has since been delisted or renamed may be missing (`minute` names
  them). Daily data has no such gap.
- **Index membership**: NSE's historical index constituents are not freely downloadable, so
  research uses `liquidity_universe` (top-N by trailing turnover) and `fo_universe` instead of today's
  NIFTY lists. Both are point-in-time.
- **Pre-open book**: only from the day the recorder started; NSE publishes only the latest session.
- F&O volume: `contracts` (old format) and `qty` (UDiFF) are kept apart; open interest is in shares
  in both.

## Phase 2: intraday engine (`intraday/`)

Decides at 09:30 (configurable) using what the opening 15 minutes showed, not at 9:00 on daily
bars. One row per F&O stock per day (point-in-time F&O list), 28 features: opening-window return,
range vs ATR, close location, VWAP distance, relative volume, gap, move vs NIFTY; prior-day trend,
ATR, 20-day high/low distance; delivery % and its z-score; futures OI change and buildup; NIFTY
trend, VIX; pre-open imbalance where recorded.

```powershell
.venv\Scripts\python -m intraday validate --t 09:30 --k 5   # walk-forward test -> data/models/intraday/
.venv\Scripts\python -m intraday report                     # print the last report
.venv\Scripts\python -m intraday live                       # after 09:30: today's picks -> data/picks/
```

- **Labels**: entry at the 09:30 bar's open; exit at the 15:09 close, or at the opening-window
  low (long) / high (short) when touched, at the bar's open if it gaps through.
- **Costs**: Zerodha MIS charges + 0.05% slippage a side, about 0.21% a round trip, on every trade.
- **Validation**: LightGBM ranks stocks by expected excess return; trained only on earlier months
  (5-session embargo), retrained monthly; scored against "buy everything" and a simple
  opening-range-breakout rule. Verdict per strategy: EDGE / UNPROVEN / NO EDGE / TOO LITTLE DATA.
- **Promotion gate**: `live` labels every pick PAPER ONLY unless the walk-forward verdict is EDGE.

## Phase 3: swing engine (`swing/`)

Ranks the point-in-time liquid universe (top 500 by turnover each day) at every close for 5–20
session holds. 34 features: trend and 52-week position, volume and up-volume share, delivery
accumulation, futures OI buildup, strength vs NIFTY and vs the stock's NSE industry, industry
strength and its change, the last quarterly result's move/volume/close location and its age,
the day's candle, and the market (NIFTY trend, breadth, VIX).

```powershell
.venv\Scripts\python -m swing validate --hold 10    # walk-forward + portfolio sim -> data/models/swing/
.venv\Scripts\python -m swing report --hold 10
.venv\Scripts\python -m swing scan                  # after the evening sync -> data/picks/swing-DATE.json
```

- **Same yardstick for everything**: the model and the pack's own rules (sector leaders, momentum,
  results jump) are re-scored on identical next-open entries after Zerodha delivery charges +
  0.1% slippage a side (~0.44% a round trip), with and without a 2-ATR stop.
- **Honest statistics**: rebalanced every `hold` sessions so trades never overlap; the model is
  trained only on data ending 25 sessions before each test block (labels look 20 ahead).
- **Portfolio simulation**: Rs 10 lakh, 1% risk a trade, 15% max weight, 10 positions, 3 per
  industry, exposure cut to half / zero when NIFTY is below its 200-day or breadth is weak;
  compared with NIFTY 50 buy-and-hold.
- **Promotion gate**: `scan` marks its plan PAPER ONLY unless the model's verdict is EDGE **and**
  the skeptic review (`swing/review.py`, run by every `validate`) says KEEP.
- **No survival leak**: results filing times come from `python -m warehouse results` for every stock
  ever in the universe, delisted ones included (both NSE lists, every ticker a company traded under),
  and industries from `python -m swing industries`: each stock assigned monthly to the NSE industry
  basket it moved with over the past 120 sessions. Neither depends on a stock being listed today.
  The first review (data/models/swing/REVIEW.md) found that today's-list data inflated the edge.

## Phase 4: research lab (`lab/`)

Ideas are JSON specs in `lab/ideas/` (format in `lab/spec.py`): a filter over the swing features,
a rank, K, hold and optional stop. Filters are checked against a whitelist before they run.

```powershell
.venv\Scripts\python -m lab nightly --fetch   # data steward -> refresh -> test -> paper -> leaderboard
.venv\Scripts\python -m lab test --id my_idea # one idea through the Skeptic (seconds)
.venv\Scripts\python -m lab show my_idea      # full review and history
.venv\Scripts\python -m lab report            # data/lab/leaderboard.md / .json
```

- **Skeptic** (`lab/skeptic.py`): profitable after costs; t above a multiple-testing hurdle that
  rises with every idea ever tried; positive in both halves and 2/3 of years; survives double
  costs; beats random picks on the same days (permutation test); 70% of +-20% parameter variants
  positive; plus concentration and capacity warnings.
- **Lifecycle** (`lab/registry.py`): research -> rejected | paper -> live_small -> retired. Paper
  signals are recorded nightly and settled on real prices (`lab/paper.py`); promotion needs 30+
  paper trades consistent with the backtest; drift below it retires the idea.
- **Claude agents** (`.claude/agents/`): `lab-hypothesis` proposes ideas from market mechanisms
  (not data mining), `lab-skeptic` reviews passes for look-ahead / survivorship / overfitting,
  `lab-data-steward` keeps the data honest. Skill: `.claude/skills/research-lab`.

## Phase 5: live monitoring (`monitor/`)

```powershell
.venv\Scripts\python -m monitor run                        # settle picks, drift checks, dashboard, alerts
.venv\Scripts\python -m monitor kill swing_model --why "reviewing"
.venv\Scripts\python -m monitor revive swing_model --why "reviewed, back on"
```

- **One live ledger** (`data/monitor/live.parquet`): every intraday and swing pick file and every
  lab paper trade, settled on real prices with the same rules and costs as its backtest.
- **Drift checks per strategy** against its own backtest: z-test of the live mean, a CUSUM for a
  sustained shortfall, drawdown vs the backtest's worst, and losing streaks. No judgement before 10
  periods; GREEN / AMBER / RED after.
- **Kill switch** (`data/monitor/killswitch.json`): a RED alert turns that strategy off
  automatically; failed data checks halt everything until the data is healthy again. `intraday
  live`, `swing scan` and `lab paper` publish nothing for a killed or halted strategy. Only a
  person revives a killed strategy.
- **Outputs**: `data/monitor/dashboard.html` (open in a browser), `status.md`, `alerts.jsonl`.
  RED / AMBER events go to your phone through the same Telegram bot as nse-picks-engine when
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set.
- **Old system for comparison**: the nse-picks-tracker scoreboard is shown beside the new numbers.

### Schedule (Windows Task Scheduler)

`run_daily.ps1 -Phase morning|evening|monthly` runs each day's steps and logs to `data\logs\`.
To register it, run once in PowerShell with `<path to nse-warehouse>` replaced by the folder's full path:

```powershell
schtasks /Create /TN "NSE morning" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:31 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"<path to nse-warehouse>\run_daily.ps1\" -Phase morning"
schtasks /Create /TN "NSE evening" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:45 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"<path to nse-warehouse>\run_daily.ps1\" -Phase evening"
schtasks /Create /TN "NSE monthly" /SC MONTHLY /D 1 /ST 10:00 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"<path to nse-warehouse>\run_daily.ps1\" -Phase monthly"
```

The laptop must be on at those times; the cloud engine (nse-picks-engine) keeps running the old
system on GitHub meanwhile.

Paper research only. Nothing here places orders.
