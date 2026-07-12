# can-slim-evaluate

A Claude skill that **evaluates a single stock ticker against the CAN SLIM growth-investing
model** and returns a structured, letter-by-letter scorecard with a **BUY-RANGE / WATCH /
AVOID** verdict — rendered as a self-contained HTML report.

It activates whenever you want to judge the *quality* of one stock — *"evaluate NVDA"*, *"is
TSLA a good stock"*, *"rate AAPL"*, *"does PLTR pass CAN SLIM"*, *"how does AMD score"*, *"is
CRWD a buy"*, *"grade this stock"*.

It is the single-stock **grading lens** — one ticker in, a CAN SLIM verdict out. For a ranked
*list* of screened ideas, use the companion [`stock-recommend`](https://github.com/thewongdirection/stock-recommend)
skill; for a data-rich single-stock dashboard, use `ibkr-review-ticker`.

## What it does

For the ticker you name it:
1. Assesses **market direction (M)** first (the gate).
2. Pulls the stock's **live price/volume/52-week stats** (IBKR) and computes relative strength,
   % off 52-week high, base shape, and breakout volume.
3. Gathers **fundamentals** (quarterly & annual earnings, sales, ROE, ownership) from connected
   financial-data sources (Daloopa / bigdata.com / LSEG / Financial Modeling Prep / SEC EDGAR)
   or the web.
4. **Grades each of C·A·N·S·L·I·M** pass / partial / fail against the method's thresholds, with
   the concrete numbers behind each grade.
5. Returns a **verdict** — BUY-RANGE (with pivot buy point + 7–8% stop), WATCH (what needs to
   happen), or AVOID (which letters it fails) — and never pretends high price strength alone
   makes a stock a buy without the earnings behind it.

## The CAN SLIM criteria it grades

- **C** — current quarterly earnings & sales up big (≥25%) and accelerating
- **A** — annual earnings up 3 years, ROE ≥17%
- **N** — a new product/management/condition **and** a breakout to new highs off a sound base
- **S** — supply/demand: volume surge, tight float, buybacks, low debt
- **L** — leader, not laggard: high relative strength, #1 in a strong group
- **I** — increasing institutional sponsorship
- **M** — the general market in a confirmed uptrend

## Contents
- `SKILL.md` — activation + workflow.
- `references/canslim-methodology.md` — the full distilled CAN SLIM rule set (shared with
  `stock-recommend`): criteria & thresholds, chart-base patterns, buy/sell rules, money
  management, and the classic mistakes.
- `references/data-and-scoring-guide.md` — the single-ticker data-gathering sequence, the
  fundamental source-priority ladder, and the pass/partial/fail scoring rubric + verdict rules.
- `scripts/relative_strength.py` — computes the RS proxy, % off 52-week high, base
  depth/length, and breakout volume from the ticker's OHLCV bars. Standard library only.
- `assets/evaluation_template.html` — the default output: a self-contained, theme-aware,
  print-ready single-stock CAN SLIM scorecard driven by a `CONFIG` object. Pure-ASCII source.

## Requirements
- IBKR MCP connector (read-only market data; never trades).
- Fundamental-data connectors and/or web search in the session.

## Disclaimer
Informational decision support, **not investment advice.** It never places orders and never
gives personalized buy/sell directives — it grades a stock against a published growth-investing
framework and pairs every read with the 7–8% loss-cutting rule. CAN SLIM is a probability edge,
not a guarantee; markets carry risk of loss. This is an independent implementation of a publicly
known investing framework and reproduces no third-party copyrighted text.
