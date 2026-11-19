---
name: research-lab
description: Run the NSE research lab - nightly data update, test new trading ideas through the Skeptic, record paper trades, read the leaderboard, and see today's intraday and swing picks. Use when the user says "run the lab", "nightly", "test my idea", "what passed", "leaderboard", "paper trades", "today's picks".
---

# Research lab

Everything runs from `nse-warehouse/` with `.venv\Scripts\python`. Paper research only: nothing
places orders, and every pick is labelled PAPER ONLY until its strategy is validated.

## Evening (after ~18:00 IST on a trading day)

```
.venv\Scripts\python -m lab nightly --fetch     # data -> checks -> dataset -> test -> paper -> leaderboard
.venv\Scripts\python -m swing scan              # tomorrow's swing plan
```
If the Data Steward step fails, stop and use the `lab-data-steward` agent; do not test on bad data.

## Morning (after 09:30 IST)

```
.venv\Scripts\python -m intraday live           # today's intraday ranks (PAPER ONLY unless EDGE)
```

## A new idea

1. The user describes it, or ask the `lab-hypothesis` agent for ideas.
2. Write `lab/ideas/<id>.json` (format in `lab/spec.py`); the mechanism goes in `hypothesis`.
3. `.venv\Scripts\python -m lab test --id <id>`, then `python -m lab show <id>`.
4. Before trusting a PASS, run the `lab-skeptic` agent on it.

Explain results in plain words: the verdict, the mean return per period after costs, how many
years were positive, which checks failed and what that means. Every new idea raises the bar for all
ideas (the t hurdle), so discourage trying many tweaks of one idea.

## Stages

research -> rejected | paper -> live_small -> retired. Promotion to live_small needs 30+ paper trades
consistent with the backtest; a person still decides whether to trade it, at a small size.

## Reports

- `data/lab/leaderboard.md`: every idea, stage, verdict, failed checks, paper results
- `data/models/intraday/report-0930.json`, `data/models/swing/report-h10.json`: model verdicts
- `data/picks/`: intraday and swing picks per day
