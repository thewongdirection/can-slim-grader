---
name: can-slim-grader
description: >-
  Grade a single specified stock ticker against the CAN SLIM growth-investing model and return a
  letter-by-letter (C-A-N-S-L-I-M) scorecard with a BUY-RANGE / WATCH / AVOID verdict, as a
  light-themed PDF dashboard (the HTML version on request). Use whenever the user wants to judge the
  QUALITY of one stock or whether a specific ticker is any good — "evaluate NVDA", "is TSLA a good
  stock", "rate AAPL", "does PLTR pass CAN SLIM", "grade this stock", "should I be interested in
  MSFT", "is CRWD a buy", "how strong is <company>". Works for any publicly traded ticker; pulls
  live price/volume from Interactive Brokers (IBKR) and fundamentals from connected financial-data
  sources or the web. This is the single-stock GRADING lens (one ticker in, one verdict) and the
  sister skill of `can-slim-recommend` — for a ranked LIST of screened ideas use
  `can-slim-recommend`; for a data-rich single-stock dashboard use `ibkr-review-ticker`. Analysis
  and decision support, never personalized investment advice and never trading.
---

# can-slim-grader — grade one ticker against CAN SLIM

**Sister skill of [`can-slim-recommend`](https://github.com/thewongdirection/can-slim-recommend).**
The two are a matched pair on one CAN SLIM methodology: `can-slim-recommend` is the market-wide
screener that returns a ranked LIST of ideas; **`can-slim-grader` is the single-ticker grading
lens** — one ticker in, one verdict out. They share `references/canslim-methodology.md` and
`scripts/relative_strength.py` verbatim.

Takes **one ticker** and grades it, letter by letter, against the seven CAN SLIM criteria,
then returns a **BUY-RANGE / WATCH / AVOID** verdict with the evidence, a chart-position read
(including a **daily candlestick chart** of the last **300 sessions (~14 months)** with the
50/200-day EMA and volume), and — if it's actionable — the pivot buy point and the 7-8%
loss-cutting stop. Output is a **PDF dashboard by default** — a light-themed, print-ready report
rendered from a self-contained HTML working file — and the **HTML itself only if the user asks**
(same report, plus the chart's hover readout).
**Decision support, not advice, and never an order.**

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
- **Price/volume** for the technical letters (N/S/L/M) from either the **IBKR MCP connector**
  (read-only market data; 52-week stats + the stock's group) or **Massive Market Data**
  (Polygon-style `/v2/aggs` OHLCV bars + ticker overview) — either can feed
  `scripts/relative_strength.py`. Both are deferred; load with `ToolSearch` first.
  **Throttle Massive to at most 5 calls/minute** (see the data guide for batching).
- **Fundamental data** via connectors (Daloopa / bigdata.com / LSEG / **Massive** — *preferred
  over FMP* when its financials are plan-entitled / **Financial Modeling Prep (FMP)** / SEC EDGAR
  through `securities-filings-lookup`) or **web search**. See the source ladder in the data guide;
  **prefer Massive over FMP**, falling back to FMP only when Massive's financials return 403.
- If no market-data connector is available, source price/technicals from the web too and say
  so — don't block.

## Workflow
Work in order; keep the user informed.

### 0 — Pull everything fresh, every single run
**A grade is only as good as the moment it was measured.** Every number in the report must come
from a tool call made *in this run*:

- **Never reuse** prices, bars, fundamentals, script output (`bars.json`, `chart.json`), or a filled
  `<TICKER>-canslim.html` from an earlier run, an earlier session, or earlier in this conversation —
  **even for the same ticker, even minutes later.** Re-call the tools and rebuild from scratch.
  Prices move intraday, and a quarter can land between two runs.
- **A re-check is a full re-run.** "Re-assess it", "is that still true?", "check MSFT again" means
  pull the data again and re-grade — never answer from the previous verdict, and never patch one
  figure into an old report.
- **Overwrite the intermediates.** Write this run's bars/chart JSON fresh; never read whatever
  happens to be on disk from last time.
- **Timestamp from the data, not the wall clock.** Take the as-of from what the provider returned
  (newest bar date, snapshot `ts`), and state whether it is a **close or intraday**, plus the feed
  delay (IBKR quotes here are 15-minute delayed). An intraday grade is provisional — say so.
- **Verify the newest bar is actually current** before publishing. `scripts/chart_data.py` prints
  the newest bar date and warns when it is more than a few days old (`--stale-after`); a stale
  newest bar means the *feed* is the problem, not the stock. Do not publish around it silently.
- **If a source fails, say so in the report** and drop down the ladder in the data guide. Never
  fill a gap with a remembered or previously-fetched number.

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
`get_price_history` (weekly ~1-2 yr for base shape; **daily `period=TWO_YEARS`** ≈ 500 bars —
6 months covers breakout volume & RS, but the report's chart displays 300 sessions and needs
~200 more before that window to seed the 200-day EMA, so pull the long daily series once and use
it for both; `period=FIVE_YEARS` if you want margin). Run
`scripts/relative_strength.py` on the ticker's bars + SPY's bars for the RS proxy, % off
52-week high, base depth/length, and breakout volume, and `scripts/chart_data.py` on the same
daily bars for the report's candlestick chart. Then gather fundamentals (C, A, N, I) from
the source ladder — prefer connected financial data over generic web search. For a deeper
financial picture you may fold in the `ibkr-review-ticker` skill.

### 4 — Score each letter
Grade C, A, N, S, L, I, M **pass / partial / fail** against the thresholds in the methodology
(rubric in the data guide). Weight the earnings letters (C, A) and leadership (L) most. Keep
each letter's evidence concrete — cite the actual EPS/sales %, ROE, RS figure, base type, and %
off high.

**The grade must follow mechanically from the threshold and the actual you printed beside it.**
This is where a grade quietly inflates: strong company, impressive quarter, and the letter drifts
up to PASS while its own evidence says the bar was missed. Specifically:

- If the `actual` or `read` concedes a miss — "just under the 25% mark", "hasn't cleared the
  high", "falls short" — the grade **cannot be PASS**. Say partial and move on; a partial with
  honest evidence is worth more than a pass that argues with itself.
- Where the threshold says **each** ("EPS up each of the last 3 yrs at ≥25%"), *every* period must
  clear it. One strong year among three below-bar years is a PARTIAL, not a PASS. Phrase these
  thresholds with the word "each" so the report's own checks can see the requirement.
- Magnitude of a beat, backlog, guidance, or a big volume day are **not** substitutes for the
  numeric bar. They belong in the `read` as colour, not as grounds for promotion.
- The report **audits itself** on render and prints a red "Report checks" banner listing any
  contradictions it finds (grade vs evidence, score arithmetic, an entry price without a valid N,
  a pivot below new-high ground, a stop that isn't 7-8%). **Never ship a report showing that
  banner** — fix the grade or fix the evidence. Do not delete the check.

### 5 — Reach a verdict

**What counts as a pivot** — get this wrong and the report invents a trade that the method would
never take. A pivot exists only when **both** hold:

1. **A sound base.** Per `canslim-methodology.md`: at least ~7-8 weeks (5-6 for a flat base, 4-7
   for a square box), within the pattern's depth limits, formed *after* a prior uptrend of ≥30%,
   with any handle in the **upper half** of the base, above the 10-week line, drifting down rather
   than wedging up. A V-shaped snap-back off a low with no handle is explicitly a faulty pattern.
2. **New high ground.** The breakout must be at or very near the **52-week high**, so there is no
   meaningful overhead supply. As a hard filter, **a candidate pivot more than ~10% below the
   52-week high is not a pivot** — it is a lower high with underwater holders stacked above it.

A recent local high, a three-month high, or the top of a spike inside a downtrend is **not** a
pivot, however strong the quarter or the volume on the day. A stock well below its 52-week high
has no buy point, and the honest entry is **"None now" plus the condition that would create one**
— even when C and A are strong, and even when the stock just gapped up on earnings. Record the
52-week high in `CONFIG.high52` so the report can check any pivot you name against it.

- **BUY-RANGE** — passes core C, A, L with a valid N (at/near a proper pivot in an uptrend).
- **WATCH** — strong fundamentals but no valid buy point now (extended, base repairing, or M
  weak). Say what needs to happen.
- **AVOID / does not fit** — fails the core earnings letters (no/low or decelerating profits)
  or is a laggard near lows. Name the failing letters. Be explicit that high RS alone is not
  enough without earnings, and that a beaten-down "cheap" stock is a laggard the method avoids.

### 6 — Deliver a PDF dashboard (default)
1. **Fill the report.** Copy `assets/evaluation_template.html` to `<TICKER>-canslim.html` and
   fill the `CONFIG` object (the only thing you edit) — header (ticker/company/price/as-of),
   `verdict` (label + tone + pass-weighted score /7 + one-line summary + buy point/stop), the
   `entryStop` band — **the prices the framework proposes: entry = the pivot buy point (buy up
   to ~5% past it), stop = 7-8% below entry (3% in a correction)**; give real prices when there
   is a valid pivot, else "None now" + the condition, the
   seven `letters` (each with score, the bar, the actual value, and a CAN-SLIM-only `read`), the
   `priceChart` daily candlestick chart (step 1b), the
   `chart` technicals (RS, % off high, base, pivot, breakout volume), the optional
   `essentials` reference stats (P/E, forward P/E, market cap, EPS, yield, beta, shares,
   avg $ volume, next earnings — **reference only, not a CAN SLIM input**; leave empty to
   hide), the `buyPlan` (pivot, 7-8% stop, profit-taking, sell signals to watch),
   disclaimer and sources.
1b. **Build the daily chart** — candlesticks + 50/200-day EMA + volume for the **last 300
   sessions (~14 months)**, the window that makes a 200-day EMA meaningful. Never hand-transcribe
   bars; pipe the daily OHLCV you already pulled through the script:
   `python scripts/chart_data.py <bars>.json --window 300 --marker <pivot>:Pivot:accent --js`
   (it reads IBKR `get_price_history` responses, `[t,o,h,l,c,v]` rows, or Polygon/Massive
   `/v2/aggs` results) and paste its output as `CONFIG.priceChart`. **Feed it ≥500 daily bars**
   (`period=TWO_YEARS`, `step=ONE_DAY`; `period=FIVE_YEARS` for margin) — the 200-day EMA needs
   ~200 sessions *before* the first visible candle, on top of the 300 displayed, and the script
   warns and annotates the chart when the history is too thin. Add `--marker` lines for the pivot
   and the 7-8% stop so the chart shows the same prices as the entry/stop band. If price data is
   unavailable, leave `bars` empty — the chart section hides itself — and say the chart was
   omitted for lack of data.
2. **Render the PDF — this is the default deliverable.** The filled `<TICKER>-canslim.html` is the
   working file (self-contained, light-themed, print-optimized); the user gets the PDF:
   `python scripts/html_to_pdf.py <TICKER>-canslim.html <TICKER>-canslim.pdf` (headless
   Chrome/Chromium/Edge → Playwright → WeasyPrint → wkhtmltopdf; it prints the engine used).
   **Re-read `CONFIG` against the self-audit rules before you export** — grade vs the evidence
   printed beside it, the score arithmetic, the pivot against `high52`, the 7-8% stop — because
   the PDF freezes whatever the page says and nobody will see the red banner in time. If you can
   view the rendered page, confirm it is absent. Hand over the PDF. If no PDF engine is available,
   say so and hand over the HTML instead — never block the grade on the export.
3. **HTML on request only.** Give the `<TICKER>-canslim.html` file (and/or open it in the browser)
   when the user asks for the HTML, an interactive version, or the chart's hover readout — it is
   the same report with a crosshair readout the PDF cannot carry. It renders itself from `CONFIG`;
   do not hand-edit the DOM. A dark rendering is likewise on request: set
   `<html lang="en" data-theme="dark">` in the filled file (light is the default, on screen and
   in print).
4. Keep the chat reply short: the verdict, the two or three letters that drove it, and the buy
   point/stop if actionable.

**The `read` for every letter must be expressed only in CAN SLIM concepts** — the letters,
bases/pivots, relative strength, new highs, volume/accumulation, leadership, sponsorship, market
direction. No generic macro takes, analyst targets, or "good company" vibes. Always pair the
verdict with the defensive rule (cut losses 7-8%).

## Changing this skill? Keep `can-slim-recommend` at parity

`can-slim-recommend` is not a separate implementation — it is the same methodology aimed at a
different question. Two files are shared **verbatim** between the repos, and the rules in the rest
of this document are meant to hold on both sides:

| Shared verbatim | Why it must match |
|---|---|
| `references/canslim-methodology.md` | the rule set both skills grade against |
| `scripts/relative_strength.py` | the RS proxy, % off high, base metrics, breakout volume |

**A change is MATERIAL — and must be ported to the sister skill in the same piece of work —
whenever it alters what a letter means, what a threshold is, how a number is computed, or how
fresh the data has to be.** Specifically:

- any threshold, weight, or pass/partial/fail rule, **including what counts as a pivot or a sound
  base**;
- the maths in `relative_strength.py` (RS windows and weights, base metrics, the volume basis);
- data-sourcing policy — the source ladder, the freshness requirement, staleness limits;
- guardrails — the read-only tool list, what may never be displayed.

**Not material (grader-only — do not port):** the single-ticker HTML report and its self-audit,
`scripts/chart_data.py`, the BUY-RANGE / WATCH / AVOID verdict labels, and anything else that only
makes sense for one-ticker-in / one-verdict-out. The screener has its own output format; port the
substance, adapt the framing.

### Procedure
1. **Before committing, run `python scripts/check_parity.py`.** It hashes the shared files against
   `parity-manifest.json` and names exactly what drifted.
2. If a shared file changed — or if you changed a rule in the material list above, **which the
   script cannot detect** — port the same change to
   **https://github.com/thewongdirection/can-slim-recommend**. Add the repo to the session first
   if it isn't there (`add_repo`). Adapt only the wording (a ranked list rather than one verdict),
   never the substance.
3. Re-run the check and `python scripts/check_parity.py --update` in the same commit, so the
   recorded hashes describe what was actually synced.
4. **If you cannot reach the sister repo, say so in your reply and in the commit message, and
   leave the manifest un-updated** so the drift stays visible. Never land a material change
   silently on one side: a screener and a grader that disagree about the rules are worse than
   either alone.

## Sister & companion skills
- **`can-slim-recommend` (sister skill)** — the market-wide screener (a ranked list of CAN SLIM
  ideas) built on the same methodology and RS script. Use it when the user wants ideas/a list
  rather than a verdict on one named stock; use this grader for the reverse.
- **`ibkr-review-ticker`** — a data-rich single-stock dashboard (fundamentals vs peers,
  valuation, options, price outlook). Fold its data into the evaluation when useful.
- **`securities-filings-lookup`** — the official filing PDFs (10-K/10-Q/20-F) behind C/A and
  13F/Form 4 for I.
- **If a companion skill you want isn't installed**, tell the user and point them to its repo,
  then continue with the source ladder:
  - `can-slim-recommend` -> https://github.com/thewongdirection/can-slim-recommend
  - `ibkr-review-ticker` -> https://github.com/thewongdirection/ibkr-review-ticker
  - `securities-filings-lookup` -> https://github.com/thewongdirection/securities-filings-lookup

## Guardrails
- **Fresh data every run — no cached grades.** Re-pull price, volume and fundamentals on every
  invocation and rebuild the report from them; never reuse a prior run's figures or output file,
  and never answer a follow-up from the previous verdict. See step 0.
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
  sell rules, money management, and mistake list. (Shared with `can-slim-recommend`.)
- `references/data-and-scoring-guide.md` — the single-ticker data-gathering sequence, the
  fundamental source ladder, and the pass/partial/fail scoring rubric + verdict definitions.
- `scripts/relative_strength.py` — computes the RS proxy, % off 52-week high, base
  depth/length, and breakout volume from the ticker's OHLCV bars vs SPY. Pure standard library.
- `scripts/chart_data.py` — builds the report's `priceChart` block (daily candles + 50/200-day
  EMA/SMA + 50-day average volume) from daily OHLCV bars. Accepts IBKR, row-array, or
  Polygon/Massive shapes; computes the averages over the full history and emits only the
  display window (default 300 sessions, so feed it ~500 bars). Pure standard library.
- `scripts/check_parity.py` + `parity-manifest.json` — hashes the files shared verbatim with
  `can-slim-recommend` and reports drift since the last recorded sync. Run before committing any
  change to this skill; byte-level only, so material rule changes still need porting by hand.
- `scripts/html_to_pdf.py` — renders the filled HTML into the **PDF that is the default
  deliverable**. Multi-engine (headless Chrome/Chromium/Edge with header/footer suppressed →
  Playwright → WeasyPrint → wkhtmltopdf); prints the engine used. Pure standard library (uses
  whatever browser/lib is present).
- `assets/evaluation_template.html` — the report itself: a self-contained, **light-themed**
  single-stock CAN SLIM dashboard driven by a `CONFIG` object (verdict badge, the seven-letter
  scorecard with evidence, the daily candlestick chart, technicals, and the buy/sell plan). It is
  the working file behind the PDF, and the deliverable itself when the user asks for the HTML;
  `data-theme="dark"` on `<html>` flips it to the dark palette on request. **It audits itself on
  render** and banners any grade that contradicts its own evidence, a pivot that isn't in
  new-high ground, a score that doesn't add up, or a stop that isn't 7-8%. The chart is
  hand-rolled inline SVG — no chart library, no network calls — with candle/EMA colours picked
  for contrast on white. Pure-ASCII source; print CSS is A4/Letter with
  `print-color-adjust:exact` so badges and chips survive the export.
