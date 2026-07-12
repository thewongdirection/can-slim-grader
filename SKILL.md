---
name: can-slim-evaluate
description: >-
  Evaluate a single specified stock ticker against the CAN SLIM growth-investing model and
  deliver a structured letter-by-letter (C-A-N-S-L-I-M) scorecard with a BUY-RANGE / WATCH /
  AVOID verdict, rendered as a self-contained HTML report. Use this whenever the user wants to
  judge the QUALITY of one stock or asks whether a specific ticker is any good — "evaluate
  NVDA", "is TSLA a good stock", "rate AAPL", "does PLTR pass CAN SLIM", "how does AMD score",
  "should I be interested in MSFT", "grade this stock", "assess LLY", "is CRWD a buy", "what do
  you think of ANET", "check the quality of <ticker>", "how strong is <company>". Works for any
  publicly traded ticker; pulls live price/volume from the Interactive Brokers (IBKR) connector
  and fundamentals from connected financial-data sources or the web. This is the single-stock
  GRADING lens (one ticker in, a CAN SLIM verdict out) — for a ranked LIST of screened ideas
  use the `stock-recommend` skill instead; for a data-rich single-stock dashboard use
  `ibkr-review-ticker`. Analysis and decision support, never personalized investment advice and
  never trading.
---

# can-slim-evaluate — grade one ticker against CAN SLIM

Takes **one ticker** and grades it, letter by letter, against the seven CAN SLIM criteria,
then returns a **BUY-RANGE / WATCH / AVOID** verdict with the evidence, a chart-position read,
and — if it's actionable — the pivot buy point and the 7-8% loss-cutting stop. Output is a
self-contained HTML scorecard. **Decision support, not advice, and never an order.**

## What CAN SLIM is (the standard this skill grades against)
CAN SLIM is a growth-stock selection framework built on how the market actually behaves
(supply/demand and crowd psychology), not on valuation "cheapness." The seven traits shared by
the biggest winning stocks just before their big moves:
**C** current quarterly earnings & sales up big and accelerating · **A** annual earnings growth
3 yrs + high ROE · **N** a new product/management/condition AND a breakout to new highs from a
sound base · **S** supply/demand (volume, tight float, buybacks) · **L** leader not laggard
(high relative strength, #1 in a strong group) · **I** increasing institutional sponsorship ·
**M** general market in a confirmed uptrend.

**Read `references/canslim-methodology.md` in full before grading** — it has every threshold,
the chart-base patterns, the sell rules, and the classic mistakes. **Read
`references/data-and-scoring-guide.md`** for the exact data to gather for one ticker, the
fundamental source-priority ladder, and the pass/partial/fail rubric per letter.

## Prerequisites
- **IBKR MCP connector** (read-only market data) for live price/volume/52-week stats and the
  stock's group. IBKR tools are deferred — load with `ToolSearch` first.
- **Fundamental data** via connectors (Daloopa / bigdata.com / LSEG / SEC EDGAR through
  `securities-filings-lookup`) or **web search**. See the source ladder in the data guide.
- If IBKR is unavailable, source price/technicals from the web too and say so — don't block.

## Workflow
Work in order; keep the user informed.

### 1 — Resolve the ticker
`search_contracts` → exact symbol, primary listing; keep `contract_id`, company name, and the
stock's sector/industry group (`get_company_themes`). If the user names a company rather than a
symbol, resolve it.

### 2 — Assess market direction (M)
Pull SPY/QQQ daily bars (or web), count distribution days, check the 50/200-day trend. Classify
Confirmed uptrend / Under pressure / Correction. M is market-wide context and one of the seven
graded letters.

### 3 — Gather the stock's data
Per `data-and-scoring-guide.md`: `get_price_snapshot` (52-week high/low, price, YTD) and
`get_price_history` (weekly ~1-2 yr for base shape; daily ~6 mo for breakout volume & RS). Run
`scripts/relative_strength.py` on the ticker's bars + SPY's bars for the RS proxy, % off
52-week high, base depth/length, and breakout volume. Then gather fundamentals (C, A, N, I) from
the source ladder — prefer connected financial data over generic web search. For a deeper
financial picture you may fold in the `ibkr-review-ticker` skill.

### 4 — Score each letter
Grade C, A, N, S, L, I, M **pass / partial / fail** against the thresholds in the methodology
(rubric in the data guide). Weight the earnings letters (C, A) and leadership (L) most. Keep
each letter's evidence concrete — cite the actual EPS/sales %, ROE, RS figure, base type, and %
off high.

### 5 — Reach a verdict
- **BUY-RANGE** — passes core C, A, L with a valid N (at/near a proper pivot in an uptrend).
- **WATCH** — strong fundamentals but no valid buy point now (extended, base repairing, or M
  weak). Say what needs to happen.
- **AVOID / does not fit** — fails the core earnings letters (no/low or decelerating profits)
  or is a laggard near lows. Name the failing letters. Be explicit that high RS alone is not
  enough without earnings, and that a beaten-down "cheap" stock is a laggard the method avoids.

### 6 — Deliver an HTML scorecard (default), offer PDF
Render from `assets/evaluation_template.html`: copy it to `<TICKER>-canslim.html`, fill the
`CONFIG` object (the only thing you edit) — header (ticker/company/price/as-of), `verdict`
(label + tone + pass-weighted score /7 + one-line summary + buy point/stop), the seven
`letters` (each with score, the bar, the actual value, and a CAN-SLIM-only `read`), the `chart`
technicals (RS, % off high, base, pivot, breakout volume), the `buyPlan` (pivot, 7-8% stop,
profit-taking, sell signals to watch), disclaimer and sources. Present the file, then keep the
chat reply short: the verdict, the two or three letters that drove it, and the buy point/stop
if actionable. **Always offer PDF** and convert on request (headless Chrome `--print-to-pdf`,
the `pdf` skill, or `weasyprint`).

**The `read` for every letter must be expressed only in CAN SLIM concepts** — the letters,
bases/pivots, relative strength, new highs, volume/accumulation, leadership, sponsorship, market
direction. No generic macro takes, analyst targets, or "good company" vibes. Always pair the
verdict with the defensive rule (cut losses 7-8%).

## Companion skills
- **`stock-recommend`** — the market-wide screener (a ranked list of CAN SLIM ideas). Use it
  when the user wants ideas/a list rather than a verdict on one named stock.
- **`ibkr-review-ticker`** — a data-rich single-stock dashboard (fundamentals vs peers,
  valuation, options, price outlook). Fold its data into the evaluation when useful.
- **`securities-filings-lookup`** — the official filing PDFs (10-K/10-Q/20-F) behind C/A and
  13F/Form 4 for I.
- **If a companion skill you want isn't installed**, tell the user and point them to its repo,
  then continue with the source ladder:
  - `stock-recommend` -> https://github.com/thewongdirection/stock-recommend
  - `ibkr-review-ticker` -> https://github.com/thewongdirection/ibkr-review-ticker
  - `securities-filings-lookup` -> https://github.com/thewongdirection/securities-filings-lookup

## Guardrails
- **Read-only, market data only.** IBKR tools allowed: `search_contracts`, `get_price_snapshot`,
  `get_price_history`, `get_company_themes`, `search_investment_topics`, `get_theme_details`.
  **Never** call order tools or account tools (balances, positions, orders, trades, summary, PA
  analytics), even if asked.
- **Never** display or store contract IDs, account numbers, or any account-bound data. Present
  by symbol/name only.
- **No personalized advice or directives.** Grade the stock against the model and give the
  factual setup; if asked "should I buy", present the scorecard and risks, not a yes/no.
- Timestamp everything; flag approximations (RS is a proxy; web fundamentals may lag). Obey
  copyright (paraphrase; short quotes only). The methodology is a probability edge, not a
  guarantee — always pair the read with the 7-8% loss-cutting rule.

## Files in this skill
- `references/canslim-methodology.md` — the full CAN SLIM rules, thresholds, base patterns,
  sell rules, money management, and mistake list. (Shared with `stock-recommend`.)
- `references/data-and-scoring-guide.md` — the single-ticker data-gathering sequence, the
  fundamental source ladder, and the pass/partial/fail scoring rubric + verdict definitions.
- `scripts/relative_strength.py` — computes the RS proxy, % off 52-week high, base
  depth/length, and breakout volume from the ticker's OHLCV bars vs SPY. Pure standard library.
- `assets/evaluation_template.html` — the default output: a self-contained, theme-aware,
  print-ready single-stock CAN SLIM scorecard driven by a `CONFIG` object (verdict badge, the
  seven-letter scorecard with evidence, technicals, and the buy/sell plan). Pure-ASCII source.
