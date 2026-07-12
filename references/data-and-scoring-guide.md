# Data & scoring guide — evaluating ONE ticker against CAN SLIM

Read this with `canslim-methodology.md` before evaluating. It maps each of the seven CAN SLIM
letters to concrete data you gather for the single specified ticker, and defines how to score
each letter **pass / partial / fail** and reach an overall verdict.

The IBKR connector (if available) supplies **live price/volume, 52-week stats, and
sector/theme groupings**; it does **not** supply company fundamentals. So:

- **IBKR / price data** covers the technical letters: **N** (new highs, bases), **S**
  (volume/liquidity/float), **L** (relative strength), **M** (market direction).
- **Fundamental-data connectors or the web** cover the fundamental letters: **C** (quarterly
  EPS & sales), **A** (annual EPS, ROE, margins), and the ownership half of **I**.

Load IBKR tools with `ToolSearch` (they are deferred) e.g. `"search contracts price history
price snapshot company themes"`. **Strictly read-only market data** — only `search_contracts`,
`get_price_snapshot`, `get_price_history`, `get_company_themes` (and `search_investment_topics`
/ `get_theme_details` to judge the stock's group). **Never** call order or account tools.

If IBKR is unavailable, get price/technicals from the web too (52-week high/low, YTD, a
1-year chart read) and say so.

---

## Fundamental source priority (use the highest that's connected)

Same ladder as the screener — prefer real financial data over generic web search:

1. **Daloopa** (`daloopa:*`, e.g. `daloopa:tearsheet`) — model-ready quarterly & annual EPS,
   sales, margins, ROE, KPIs. Best for **C** and **A**.
2. **bigdata.com** (`bigdata-com:*`, e.g. `company-brief`, `earnings-digest`,
   `earnings-quality-screen`) — latest-quarter beat/acceleration/guidance for **C**, the
   earnings-quality check, and the **N** story.
3. **LSEG** (`lseg:equity-research`) — analyst consensus estimates + revisions/surprises.
4. **Financial Modeling Prep (FMP)** — a structured fundamentals API (via an FMP MCP
   connector or a direct API call with the user's key). Good, affordable coverage of the exact
   CAN SLIM inputs: income-statement / cash-flow history and **EPS & revenue growth** (C),
   multi-year annuals, **ROE, margins, debt/equity** (A/S), analyst **estimates** (forward A),
   institutional-ownership and insider data (I), key-metrics/ratios, and a **stock-screener**
   endpoint. Prefer its `income-statement`, `key-metrics`, `ratios`, `financial-growth`,
   `analyst-estimates`, and `institutional-ownership` endpoints. Requires the user's FMP API
   key / connector; if absent, skip to the next source.
5. **SEC EDGAR** via the **`securities-filings-lookup`** skill — authoritative 10-K/10-Q/20-F
   for ground-truth statements, and 13F/Form 4 for **I** (also non-US listings).
6. **General web search** — only when none of the above are connected. Favor primary/recent
   sources; obey copyright (paraphrase; short quotes only).

**Deep companion report:** for a fuller single-stock financial picture (fundamentals vs.
peers, valuation, options positioning, price outlook), you may also invoke the
**`ibkr-review-ticker`** skill and fold its findings in — this CAN SLIM evaluation is the
*grading lens*; ibkr-review-ticker is the *data-rich dashboard*. If a companion skill you want
isn't installed, tell the user and point them to its repo (see SKILL.md), then continue with
the source ladder above.

---

## Data to gather for the ticker

1. **Resolve** the symbol with `search_contracts` (exact symbol, primary listing) → keep the
   `contract_id`. Note the company name and its sector/industry group (`get_company_themes`).
2. **`get_price_snapshot`** `["last","year_to_date_change","misc_statistics"]` →
   last price, **52-week high/low** (compute **% off 52-week high**), YTD.
3. **`get_price_history`** weekly ~1-2 yr (base shape) and daily ~6 mo (breakout volume, RS).
   Run `scripts/relative_strength.py` (feed the ticker's bars + SPY's bars) for the **RS proxy**,
   **% off 52-week high**, **base depth/length**, and **breakout volume** deterministically.
4. **Fundamentals** (from the ladder): last 2-3 quarters' EPS & sales growth YoY (accelerating?
   margins?); last 3 years' annual EPS + ROE + margins + next-year estimate; the "new" story
   (product/management/industry, IPO recency); institutional ownership trend; float, buybacks,
   debt/equity, management ownership.
5. **Market direction (M):** pull SPY/QQQ daily bars (or web), count distribution days, check
   50/200-day trend → Confirmed uptrend / Under pressure / Correction.

---

## Scoring each letter (pass / partial / fail)

Grade against the thresholds in `canslim-methodology.md`. Suggested rubric:

- **C — Current quarterly earnings & sales.** PASS: latest-quarter EPS up ≥25% YoY (ideally
  40%+) **and** sales up ≥25% (or accelerating), growth accelerating. PARTIAL: positive but
  10-25%, or strong EPS with soft sales, or decelerating. FAIL: <10%, flat, or down. (Exclude
  one-time items.)
- **A — Annual earnings.** PASS: EPS up each of last 3 yrs at ≥25%, ROE ≥17%. PARTIAL: growth
  10-25% or one down year recovered, or ROE 12-17%. FAIL: erratic/declining, ROE <12%.
- **N — New + new high off a base.** PASS: a clear new product/management/industry driver **and**
  the stock breaking out to a **new high from a sound base** now (at/near pivot). PARTIAL: has
  a "new" driver but extended, or repairing a base (not at a pivot). FAIL: no new driver, or
  making new lows / wide-loose base.
- **S — Supply & demand.** PASS: volume surging on up-moves / dry-up in the base, reasonable
  float, buybacks, low debt, management ownership. PARTIAL: mixed. FAIL: heavy distribution,
  bloated float, high debt/dilution.
- **L — Leader or laggard.** PASS: RS clearly beating SPY (top tier; proxy well positive),
  #1-3 in a strong group. PARTIAL: roughly in line with SPY. FAIL: lagging SPY / near 52-week
  lows.
- **I — Institutional sponsorship.** PASS: several quality funds, increasing owners, recent
  buying. PARTIAL: adequate but flat, or over-owned. FAIL: little/no sponsorship.
- **M — Market direction.** PASS: confirmed uptrend. PARTIAL: uptrend under pressure. FAIL:
  correction/downtrend. (M is market-wide context, not stock-specific.)

Count `pass` = 1, `partial` = 0.5, weight **C, A (earnings) and L (leadership)** most — they
were the most predictive traits.

## Overall verdict (one of)

- **BUY-RANGE CAN SLIM leader** — passes the core earnings letters (C, A) and L, has a valid N
  (at/near a proper pivot in an uptrend). State the pivot buy point and the 7-8% stop (3% in a
  correction), and that it should not be chased >5% past the pivot.
- **WATCH / not yet buyable** — strong fundamentals but no valid buy point now (extended, or
  repairing a base, or M is weak). Say what needs to happen (a new base + breakout, or a
  follow-through day).
- **DOES NOT FIT / AVOID** — fails the core earnings letters (no/low profits, decelerating) or
  is a laggard near lows. Name the specific failing letters. Be explicit that strong price
  action alone (high RS) is **not** enough without the earnings behind it, and that a cheap /
  beaten-down stock is a laggard the method avoids.

Always pair the verdict with the **defensive rule** (cut losses 7-8%) and note this is a
point-in-time read that changes as data and the market change.

## Notes & guardrails
- Never display/store contract IDs, account numbers, or any account-bound data. Present by
  symbol/name only. Timestamp everything; flag approximations (RS is a proxy; web data may lag).
- Decision support, not advice. No order placement, no personalized buy/sell directives — grade
  the stock against the model and let the user decide.
