# can-slim-grader

A Claude skill that **evaluates a single stock ticker against the CAN SLIM growth-investing
model** and returns a structured, letter-by-letter scorecard with a **BUY-RANGE / WATCH /
AVOID** verdict — delivered as a **PDF dashboard by default** (A4, 15 mm margins, light palette
so it prints cleanly), rendered from a self-contained HTML working file. Ask for the **HTML** and
you get that too: the same report in a **dark** theme, plus a hover readout on the chart.

It activates whenever you want to judge the *quality* of one stock — *"evaluate NVDA"*, *"is
TSLA a good stock"*, *"rate AAPL"*, *"does PLTR pass CAN SLIM"*, *"how does AMD score"*, *"is
CRWD a buy"*, *"grade this stock"*.

It is the **sister skill of [`can-slim-recommend`](https://github.com/thewongdirection/can-slim-recommend)** —
the two are a matched pair on one CAN SLIM methodology (they share
`references/canslim-methodology.md` and `scripts/relative_strength.py`, and material rule changes
are ported between them). This one is the
single-stock **grading lens** — one ticker in, a CAN SLIM verdict out; `can-slim-recommend` is
the market-wide screener that returns a ranked *list* of ideas. For a data-rich single-stock
dashboard, use `ibkr-review-ticker`.

## What it does

Every run pulls its own data — prices, volume and fundamentals are re-fetched on each invocation
and the report is rebuilt from them. Nothing is carried over from a previous run, so asking again
tomorrow (or ten minutes later) re-measures rather than repeating. For the ticker you name it:
1. Assesses **market direction (M)** first (the gate).
2. Pulls the stock's **live price/volume/52-week stats** (TradingView `get_ohlcv` +
   `get_symbol_data`, or IBKR) and computes relative strength,
   % off 52-week high, base shape, and breakout volume — and draws a **daily candlestick chart**
   of the last **300 sessions (~14 months)** with the **50- and 200-day EMA**, a **volume pane**
   (with the 50-day average line), and dashed markers at the pivot and stop. The long window is
   deliberate: a 200-day EMA on a three-month chart is a line with almost no chart under it.
3. Gathers **fundamentals** (quarterly & annual earnings with YoY growth, street EPS vs
   consensus, ROE, margins, debt) from **TradingView** by preference — one call each for the
   quarterly series, the annual series, the earnings history and the TTM ratios — falling back to
   Daloopa / bigdata.com / LSEG / Massive / FMP / SEC EDGAR or the web. Institutional sponsorship
   is the one input TradingView does not carry, so that comes from 13F/Form 4 or the web.
4. **Grades each of C·A·N·S·L·I·M** pass / partial / fail against the method's thresholds, with
   the concrete numbers behind each grade — and the finished report **checks itself**, bannering
   any letter whose grade contradicts the evidence printed beside it, a buy point that isn't in
   new-high ground, a score that doesn't add up, or a stop that isn't 7–8%.
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
  `can-slim-recommend`): criteria & thresholds, chart-base patterns, buy/sell rules, money
  management, and the classic mistakes.
- `references/data-and-scoring-guide.md` — the single-ticker data-gathering sequence, the
  fundamental source-priority ladder, and the pass/partial/fail scoring rubric + verdict rules.
- `scripts/relative_strength.py` — computes the RS proxy, % off 52-week high, base
  depth/length, and breakout volume from the ticker's OHLCV bars. Reads TradingView
  `{t,o,h,l,c,v}` dicts and `[t,o,h,l,c,v]` rows interchangeably. Standard library only.
- `scripts/chart_data.py` — turns daily OHLCV bars (TradingView / IBKR / row-array /
  Polygon-Massive shapes) into the report's `priceChart` block: candles for the display window plus the 50/200-day
  EMA (or SMA) computed over the *full* history and the 50-day average volume. Standard
  library only.
- `scripts/check_parity.py` + `parity-manifest.json` — guards the shared methodology. This skill and
  `can-slim-recommend` share `references/canslim-methodology.md` and `scripts/relative_strength.py`,
  so any change to a threshold, a scoring rule, the pivot definition, the RS maths or
  the data-freshness policy is *material* and must be ported to the sister skill in the same piece
  of work. The script reports byte-level drift; see "Keep `can-slim-recommend` at parity" in
  `SKILL.md` for the full rule.
- `scripts/html_to_pdf.py` — renders the **default deliverable**, the PDF on A4 with 15 mm
  margins (headless Chrome/Chromium/Edge → Playwright → WeasyPrint → wkhtmltopdf).
- `assets/evaluation_template.html` — the report: a self-contained single-stock CAN SLIM dashboard
  driven by a `CONFIG` object. **One file, two media** — dark on screen (the HTML deliverable),
  light on A4 with 15 mm margins in print (the PDF), handled by `@media print`. The candlestick chart is hand-rolled inline SVG — no chart library and no network
  calls, so the file stays a single self-contained page — with candle, EMA and volume colours
  that switch with the palette. Pure-ASCII source.

## Requirements
- **TradingView MCP** (`Trading_View`) — preferred: bars *and* financials in one connector,
  read-only tools only (the skill never touches TradingView portfolios or watchlists).
- IBKR MCP connector or Massive Market Data as the price/volume alternate (read-only; never trades).
- Fundamental-data connectors and/or web search for anything the above don't carry — notably
  institutional sponsorship.
- A PDF engine for the default output — headless **Chrome/Chromium/Edge** (preferred), or
  `pip install playwright weasyprint`, or `wkhtmltopdf`. Without one the skill falls back to
  handing you the HTML and says so. A modern browser to view the HTML version if you ask for it.

## Disclaimer
Informational decision support, **not investment advice.** It never places orders and never
gives personalized buy/sell directives — it grades a stock against a published growth-investing
framework and pairs every read with the 7–8% loss-cutting rule. CAN SLIM is a probability edge,
not a guarantee; markets carry risk of loss. This is an independent implementation of a publicly
known investing framework and reproduces no third-party copyrighted text.
