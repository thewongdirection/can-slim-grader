# Data & scoring guide — evaluating ONE ticker against CAN SLIM

Read this with `canslim-methodology.md` before evaluating. It maps each of the seven CAN SLIM
letters to concrete data you gather for the single specified ticker, and defines how to score
each letter **pass / partial / fail** and reach an overall verdict.

## Freshness rule (read first)

**Every run attempts its own pull, and every figure in the report carries the date it is as of.**
Nothing below may be silently answered from a previous run, a previous session, or from earlier in
the same conversation — re-call the tools each time, even for the same ticker minutes later, and
rebuild the report from the new numbers. A "re-check" is a full re-run, not an edit of the last
report. Concretely:

- Re-fetch the snapshot **and** the price history every run; regenerate `bars.json` / the
  `priceChart` block rather than reusing files on disk.
- Record the as-of from the **data**: the newest bar's date, and the snapshot's `ts` /
  `is_close` flag. While the session is open the last bar is a live, still-moving candle in every
  feed (TradingView included) — label the grade intraday and provisional. IBKR quotes here are
  **15-minute delayed**, and its history has been seen lagging TradingView's by a session:
  when two connected feeds disagree about the newest bar, say which one the report used.
- `scripts/chart_data.py` prints `newest bar {date}` on every run and warns when it is older than
  `--stale-after` days (default 4). A stale newest bar is a data problem — fix it, don't publish
  around it.
- Re-pull fundamentals too. An earnings release between two runs can change C, A **and** the chart
  in one session (MSFT's FY26 Q4 landed overnight and moved the stock 16% the next morning).

**When a source is down, gated, or timing out, the run continues — visibly.** Connectors drop
mid-task and endpoints get plan-gated; a dated older figure beats refusing to grade. The order is:

1. **Drop down the ladder below** and try the next source for that letter.
2. **If nothing answers, reuse the most recent earlier figure** and record it in the report's
   `CONFIG.dataStatus.items` as `state:"carried"`, with its own `asOf` date and a `why` naming
   what failed. The dashboard then shows an amber **"Data notice"** at the top and tags that row
   *carried over* in its provenance table.
3. **If it cannot be sourced at all**, mark it `state:"unavailable"` with a `why` and grade the
   letter on what you do have, saying in that letter's `read` what is missing.
4. **Never present carried data as current**, and never carry the price series itself without
   saying the grade is stale — the technical letters are measured against the newest bar.

**Provenance is part of the report, not metadata.** `CONFIG.dataStatus` is required: `pulledAt`
for the run, and one dated row per class of figure (price/volume, C & A earnings, ownership,
essentials, market direction) with its source, its `asOf` and its state. The report's self-audit
banners a missing `dataStatus`, any item without an `asOf`, and any non-fresh item without a
`why` — so an undated or quietly-stale figure cannot ship.

## TradingView first (price/volume AND fundamentals in ~5 calls)

**`Trading_View` MCP is the preferred source for both halves of this skill** — it is the only
connector here that covers the technical letters *and* the earnings letters, so prefer it over
IBKR/Massive for bars and over FMP for financials. Symbols are `EXCHANGE:TICKER`
(`NASDAQ:WDC`); resolve with `search_symbols` when unsure. Tools are deferred — load with
`ToolSearch` first. The standard single-ticker pull:

| Call | Gives you | Feeds |
|---|---|---|
| `get_ohlcv(symbol, interval="1D", count=500)` | daily OHLCV bars, `{t,o,h,l,c,v}` | the chart + RS (**pipe straight into both scripts, unedited**) |
| `get_ohlcv(symbol, interval="1W", count=104)` | weekly bars | base shape/depth for **N** |
| `get_ohlcv("AMEX:SPY", interval="1D", count=300)` | benchmark bars | the RS proxy and **M** |
| `get_financial_history(symbol, period="fq", points=8)` | per-quarter revenue / EPS / net income / FCF / total debt, **each with `yoy_pct`** | **C** (and the acceleration sequence) |
| `get_financial_history(symbol, period="fy", points=5)` | the same lines per fiscal year | **A** |
| `get_earnings_history(symbol)` | actual vs consensus EPS/revenue, beat rate, `next_report_date`, and the last report's `price_reaction` | **C** quality, **N** timing |
| `get_financials(symbol)` | TTM ratios: ROE, margins, debt/equity, P/E, market cap, sector/industry | **A** (ROE), **S** (debt), essentials |
| `get_symbol_data(symbol, columns=[...])` | `price_52_week_high/low`, `float_shares_outstanding`, `average_volume_10d_calc`, `Perf.3M/6M/Y`, `earnings_release_next_date` | **N** (% off high), **S** (float), **L** cross-check |

**Four caveats that will misgrade a letter if you miss them:**

1. **Use street EPS for C, not the GAAP line.** `get_financial_history`'s `eps` is GAAP — for WDC's
   June-2026 quarter that is $8.18 (+981% YoY) against a street actual of $3.56 in
   `get_earnings_history`. CAN SLIM's C wants operating EPS excluding one-time items, so grade on
   `get_earnings_history.eps_actual` and use the GAAP series for the trend and the sanity check.
   When the two diverge wildly, say so in the letter's `read` — that gap *is* the earnings-quality
   check.
2. **TTM growth breaks across a spin-off or divestiture.** `get_financials` showed WDC
   `total_revenue_yoy_growth_ttm` = **-2.7%** while every quarter was growing 25-45%, because the
   year-ago TTM still contained the divested Sandisk business. Never grade C or A off the TTM
   growth fields; use the per-period `yoy_pct` from `get_financial_history`.
3. **TradingView has no institutional-ownership data.** **I** still comes from 13F/Form 4 via the
   ladder below (FMP `form13F`, `securities-filings-lookup`, or the web).
4. **The newest bar is live while the session is open** — `get_ohlcv` returns a partial candle whose
   close and volume are not final. Label the grade intraday and provisional (step 1).

Cross-checked 2026-08: TradingView's `price_52_week_high` (799.87) and ROE (131.4%) matched the
IBKR snapshot and the company's filings exactly, and its daily bars were **fresher than the IBKR
connector's** in the same session.

### Chart interval — daily for one or two tickers, weekly for a run of three or more

**The deciding constraint is bars per ticker.** A daily 200-day line needs ~200 sessions of history
*before* the first visible candle, so a 300-session chart wants **~500 daily bars per name**. At
three tickers that is 1,500+ bars, and the failure mode is silent: the chart still renders, the
long-term average just starts partway across it, and nothing in the PDF says so unless the script's
note is carried through.

| Tickers in the run | Chart | Display | Averages | Bars needed per ticker |
|---|---|---|---|---|
| 1-2 | daily | 300 sessions (~14 months) | 50 / 200-day EMA, 50-day volume | daily `count=500`, weekly `count=104` |
| **3 or more** | **weekly** | 150 weeks (~3 years) | 10 / 40-week EMA, 10-week volume | weekly `count=200`, daily `count=260` |

```bash
python scripts/chart_data.py {weekly-bars}.json --interval weekly --js
```
`--interval weekly` moves the window, both averages, the volume average, the labels and the
staleness rule together, so there is nothing to set by hand and nothing to get half-right.

**Why weekly is a lateral move, not a downgrade.** Bases are read on weekly charts — that is the
timeframe the pattern work in `canslim-methodology.md` describes — and **40 weeks ≈ 200 sessions**,
so the long-term line means the same thing. What changes is resolution, not the measurement.

**Three rules that keep it honest:**

1. **One interval for the whole run.** Mixing daily and weekly across a set makes the reports
   non-comparable, and the reader cannot see the mismatch. Pick from the ticker count up front.
2. **RS never changes.** `relative_strength.py` always runs on **daily** bars — its lookbacks are
   63/126/252 sessions — so the chart interval cannot move a graded number. This is why the daily
   pull stays at ~260 bars even when the chart is weekly.
3. **Say which interval the set used**, in the chat reply and in the chart's own window label
   ("last 150 weeks"). `--interval weekly` writes the label and `avgVolLabel` for you, so a weekly
   volume average is never read as a daily one.

A weekly bar is routinely several days old, so the weekly preset relaxes the staleness warning to
10 days. That is about the bar's *age*, not about skipping the re-pull: bars are still pulled fresh
every run.

### Rate limit — discovered every run, never assumed

**TradingView documents no limit for these endpoints, and it refuses rather than warns.** A
successful response carries no `limit`/`remaining`/`reset` field to pace off; a refused one comes
back as `{"success": false, "rate_limited": true, "error": "...403 Forbidden for url
'https://scanner.tradingview.com/america/scan'"}`. So the ceiling is **established each run and
adjusted from what actually happens**, and `scripts/tv_throttle.py` enforces it.

| | |
|---|---|
| **Hard bound** | **100 requests/minute**, never reached — the throttle spends **80%** of whatever limit is believed: 80/min connector-wide out of the box, 48/min per endpoint. |
| **Per endpoint** | `scanner` (`get_quote`, `get_symbol_data`, `run_screener`, `search_symbols`, the technicals tools) and `chart` (`get_ohlcv` and the structure tools) are limited **separately** — observed: both scanner tools refused while `get_ohlcv` answered in the same second. Fundamentals, news and anything unmapped get their own lanes under the global cap. |
| **On a refusal** | that endpoint's ceiling is **halved** (floor 5/min) and it cools down for the server's `retry_after`, else 30s → 60s → 120s → 300s. Only the refused endpoint backs off. |
| **Recovery** | after 20 *observed-clean* calls and 2 quiet minutes the ceiling creeps back +5/min, never above that lane's established ceiling (the 60/min default unless `set-limit` raised it). Learned ceilings persist between runs. |

The loop, per call: `wait --tool {name}` → make the call → `observe --tool {name} -` with the
response. `observe` is what keeps the limit current — it distinguishes a real refusal (the
server's `rate_limited` flag, a 429, "too many requests") from a plain error like a bad symbol,
which must **not** slow the run down. If a limit is ever actually established — advertised by a
newer server build, documented by TradingView, or given by the user — record it with
`set-limit --family {lane} --per-minute {N} --source "{where}"` and every budget re-derives from
it. Never raise the ceiling on a guess.

**A rate-limited call is a missing figure, not a guessable one.** Wait out the cooldown, retry
once, then take that letter down the ladder below and record the row in
`CONFIG.dataStatus.items` as `carried` or `unavailable` with a `why` naming the limit — the same
fallback discipline as any other dead source. A single-ticker grade is ~6-8 TradingView calls,
comfortably inside any plausible limit, so being throttled on one is a fact about the connector
worth reporting, not a pacing mistake to hide.

---

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

**Massive Market Data (`Massive_Market_Data` MCP) — a strong price/volume alternative to IBKR.**
A Polygon-style API: `search_endpoints` to discover, then `call_api`. Pull daily/weekly OHLCV
bars from `GET /v2/aggs/ticker/{TICKER}/range/1/{day|week}/{from}/{to}` for the ticker **and**
SPY, map each bar to `[t,o,h,l,c,v]`, and feed them straight into
`scripts/relative_strength.py` (no code change) — this fully covers the technical letters
**N/S/L/M** and replaces IBKR when IBKR isn't connected. `GET /v3/reference/tickers/{TICKER}`
gives **market cap, shares outstanding, industry** for the essentials block. Its financials
endpoints (`/stocks/financials/v1/income-statements`, `/ratios`, `/benzinga/v1/earnings`) would
cover **C/A** and P/E/ROE, **but are often plan-gated (HTTP 403 NOT_AUTHORIZED)** — if so, get
C/A from the fundamental ladder below. (Verified 2026-07: aggregates + ticker-overview entitled;
financials/ratios/earnings needed a plan upgrade. Cross-check: Massive bars reproduced the
IBKR-based RS and % off high exactly.)
**Rate limit — throttle Massive to at most 5 calls per minute** (space them ~12s apart) — far
tighter than TradingView's, and unrelated to it. Do **not** pace Massive with
`scripts/tv_throttle.py`: every call it records also spends a slot from the TradingView
connector-wide budget, and its `other` lane is the fallback for unmapped *TradingView* tools, so
one provider would throttle the other. Space Massive's calls yourself. Batch
to stay under it: one `/v2/aggs` call per ticker for daily and one for weekly, fetch SPY's bars
**once** and reuse the stored table across tickers, and prefer `query_data` (SQL over stored
tables) over re-fetching. A typical single-ticker grade needs only ~3-4 Massive calls (ticker
daily + weekly + SPY daily + ticker overview), well within the limit.

---

## Source priority (use the highest that's connected)

**Three sources have earned the top of this ladder by being reliable in practice: SEC filings,
TradingView, and IBKR.** Everything below them is a fallback, and several have been observed
plan-gated or refusing outright. Prefer the highest that is connected *for the kind of data you
need* — filings are authoritative for fundamentals, the market feeds for price and volume:

| Need | First | Then | Then |
|---|---|---|---|
| **C, A** — quarterly & annual EPS/sales, ROE | **SEC filings** (10-K/10-Q via `securities-filings-lookup` / EDGAR XBRL) | **TradingView** `get_financial_history` + `get_earnings_history` | the fallbacks below |
| **N, S, L, M** — bars, 52-week stats, volume | **TradingView** `get_ohlcv` / `get_symbol_data` | **IBKR** `get_price_history` / `get_price_snapshot` | the fallbacks below |
| **I** — institutional sponsorship | **SEC** 13F / Form 4 | web aggregators, stated as such | — |

**Why filings first for C and A.** They are the numbers the company is legally accountable for, and
they do not carry a vendor's derivation quirks — the two traps documented above (a GAAP/street EPS
gap, and TTM fields that break across a spin-off) are both vendor artefacts that a filing does not
have. Use the filing for the figure and TradingView for the street consensus it is measured against,
which is the one thing the filing cannot give you.

**Why TradingView before IBKR for price.** It carries both halves — bars *and* fundamentals — its
daily bars have been observed fresher than the IBKR connector's in the same session, and IBKR quotes
here are 15-minute delayed. IBKR is the better second opinion precisely because it is independent:
when the two disagree about the newest bar, say which one the report used.

Then, in order, the fallbacks:

1. **TradingView** (`Trading_View` MCP) — also the only source here that supplies the bars.
   `get_financial_history` (fq + fy) for **C**/**A**, `get_earnings_history`
   for the street EPS actual/estimate and the next report date, `get_financials` for ROE, margins,
   debt/equity and market cap. Read the four caveats above before grading off it. No 13F data, so
   **I** comes from SEC filings.
2. **Daloopa** (`daloopa:*`, e.g. `daloopa:tearsheet`) — model-ready quarterly & annual EPS,
   sales, margins, ROE, KPIs. Best for **C** and **A**.
3. **bigdata.com** (`bigdata-com:*`, e.g. `company-brief`, `earnings-digest`,
   `earnings-quality-screen`) — latest-quarter beat/acceleration/guidance for **C**, the
   earnings-quality check, and the **N** story.
4. **LSEG** (`lseg:equity-research`) — analyst consensus estimates + revisions/surprises.
5. **Massive Market Data** (`Massive_Market_Data` MCP) — **a structured alternative when
   TradingView is not connected, ahead of FMP.** `/stocks/financials/v1/income-statements` + `/ratios` cover **C/A** (EPS &
   revenue growth) and ROE / margins / debt / P/E / market cap from SEC data; `/benzinga/v1/earnings`
   gives the latest-quarter EPS/revenue surprise and the next-earnings date; `/v3/reference/tickers`
   gives market cap & shares for essentials. Endpoints and the **≤5-calls/min throttle** are in the
   price section above. **Caveat:** these financials endpoints are often plan-gated (HTTP 403
   NOT_AUTHORIZED) — if so, drop to FMP (#5). (Massive also supplies price/volume for N/S/L/M, so
   prefer it for the whole data pull when entitled.)
6. **Financial Modeling Prep (FMP)** — the fallback when TradingView and Massive are both
   unavailable or gated. **Verified 2026-08: on this account `statements` and `quote` return
   ACCESS DENIED at any period; only `company`/`profile-symbol` answered.** A
   structured fundamentals MCP (deferred; load its tools
   with `ToolSearch`). Broad, fast coverage of the exact CAN SLIM inputs. Preferred tools:
   `statements` (income / balance / cash-flow history → **EPS & revenue growth** for **C**,
   multi-year annuals, **margins / ROE / debt** for **A**/**S**), `analyst` / `tipranks`
   (forward estimates & consensus → forward **A**), `form13F` + `insiderTrades` (institutional
   & management ownership → **I**/**S**), `company` (profile, float, sector), `calendar`
   (next-earnings date → **N**/timing), `secFilings`, `discountedCashFlow`, `earningsTranscript`.
   **C caveat — compute quarterly growth YoY yourself:** pull `statements` → `income-statement`
   (`period="quarter"`, `limit≈8-12`) and compare each quarter to the *same quarter one year
   earlier* (4 rows back). Do **not** read C off `income-statement-growth` at `period="quarter"` —
   those figures are *sequential* quarter-over-quarter, not the YoY same-quarter compare CAN
   SLIM's C requires. (Annual growth via `period="annual"` is fine — annual periods aren't
   seasonal.)
   **Plan caveat:** on lower FMP tiers `statements` at `period="annual"` (income statement,
   key metrics) works, but `period="quarter"` is often plan-gated and returns *ACCESS DENIED*.
   If the quarterly call is blocked, don't stall — source the latest quarter's C from the web
   or the 10-Q via **`securities-filings-lookup`**, and keep FMP for the annual A/ROE data.
   Requires the user's FMP API key / connector; if absent, skip to the next source.
7. **SEC EDGAR** via the **`securities-filings-lookup`** skill — authoritative 10-K/10-Q/20-F
   for ground-truth statements, and 13F/Form 4 for **I** (also non-US listings).
8. **General web search** — only when none of the above are connected. Favor primary/recent
   sources; obey copyright (paraphrase; short quotes only).

**Deep companion report:** for a fuller single-stock financial picture (fundamentals vs.
peers, valuation, options positioning, price outlook), you may also invoke the
**`ibkr-review-ticker`** skill and fold its findings in — this CAN SLIM evaluation is the
*grading lens*; ibkr-review-ticker is the *data-rich dashboard*. If a companion skill you want
isn't installed, tell the user and point them to its repo (see SKILL.md), then continue with
the source ladder above.

---

## Data to gather for the ticker

1. **Resolve** the symbol. TradingView: `search_symbols` → `EXCHANGE:TICKER`. IBKR:
   `search_contracts` → exact symbol, primary listing, `contract_id`, plus the stock's
   sector/industry group (`get_company_themes`). Note the company name and group either way.
2. **Snapshot / 52-week stats.** TradingView: `get_quote` for the live price and
   `get_symbol_data(columns=["price_52_week_high","price_52_week_low","float_shares_outstanding",
   "average_volume_10d_calc","Perf.3M","Perf.6M","Perf.Y","earnings_release_next_date"])`.
   IBKR alternate: `get_price_snapshot ["last","year_to_date_change","misc_statistics"]`.
   Either way compute **% off the 52-week high** from the high you just pulled.
3. **Daily + weekly bars.** TradingView: `get_ohlcv(interval="1D", count=500)` and
   `get_ohlcv(interval="1W", count=104)`; IBKR alternate: `get_price_history` daily
   `period=TWO_YEARS, step=ONE_DAY` (~500 bars) + weekly ~1-2 yr. Six months is enough for
   breakout volume and RS, but pull the long daily series once and reuse it: the report's
   candlestick chart displays 300 sessions and needs ~200 more *before* that window to seed the
   200-day EMA — so ~500 bars is the floor, not a nicety.
   Run `scripts/relative_strength.py` (feed the ticker's bars + SPY's bars) for the **RS proxy**,
   **% off 52-week high**, **base depth/length**, and **breakout volume** deterministically. It
   takes TradingView's `{t,o,h,l,c,v}` dicts and IBKR-style `[t,o,h,l,c,v]` rows interchangeably —
   **paste the provider payload in as-is; never retype bars.**
4. **Chart for the report:** run `scripts/chart_data.py` on the same daily bars —
   `python scripts/chart_data.py bars.json --window 300 --marker {pivot}:Pivot:accent --js` — and
   paste the result as `CONFIG.priceChart` in the dashboard (daily candles + 50/200-day EMA +
   volume for the last **300 sessions ≈ 14 months**). It takes a TradingView `get_ohlcv` response
   as-is, or the IBKR response as-is, or `[t,o,h,l,c,v]` rows, or Polygon/Massive `/v2/aggs`
   results. **Data-sourcing note:** the
   200-day EMA is only as good as the history behind it — the 300-session window needs ~200 bars
   *before* it, i.e. **~500 daily bars in** (`get_ohlcv count=500`, or IBKR `period=TWO_YEARS`). With
   less, the 200-day line starts partway across the chart (the script says so and stamps a note
   on it); with <200 bars it is not drawn at all. Everything else on the chart works from the
   display window alone.
5. **Fundamentals** (from the ladder — TradingView first): last 2-3 quarters' EPS & sales growth
   YoY and whether it is accelerating (`get_financial_history period="fq"` + `get_earnings_history`
   for the street actual); last 3 fiscal years' EPS (`period="fy"`) + ROE/margins/debt-to-equity
   (`get_financials`) + forward estimates (`get_earnings_history.forward_estimates`); the "new"
   story (product/management/industry, IPO recency); **institutional ownership trend — not in
   TradingView, so use 13F/Form 4 or the web**; float, buybacks, management ownership.
6. **Market direction (M):** pull SPY daily bars — `get_ohlcv("AMEX:SPY", interval="1D",
   count=300)` or the IBKR equivalent — count distribution days (a close down ≥0.2% on volume
   above the prior session's) over the last ~25 sessions, and check the index against its 50- and
   200-day averages → Confirmed uptrend / Under pressure / Correction.

---

## Scoring each letter (pass / partial / fail)

Grade against the thresholds in `canslim-methodology.md`. **The grade must follow mechanically
from the threshold and the actual figure** — if the evidence you print concedes the bar was
missed ("just under 25%", "hasn't cleared the high"), the letter is not a PASS, no matter how
impressive the quarter reads. Where a threshold says **each**, every period must clear it. The
filled report audits itself on render and flags these contradictions; never ship one that does.

Suggested rubric:

- **C — Current quarterly earnings & sales.** PASS: latest-quarter EPS up ≥25% YoY (ideally
  40%+) **and** sales up ≥25% (or accelerating), growth accelerating. PARTIAL: positive but
  10-25%, or strong EPS with soft sales, or decelerating. FAIL: <10%, flat, or down. (Exclude
  one-time items.)
- **A — Annual earnings.** PASS: EPS up each of last 3 yrs at ≥25%, ROE ≥17%. PARTIAL: growth
  10-25% or one down year recovered, or ROE 12-17%. FAIL: erratic/declining, ROE <12%.
  **A company without three years of record — newly public, or freshly restructured — cannot
  exceed PARTIAL**, however good the years it has.
- **N — New + new high off a base.** PASS: a clear new product/management/industry driver **and**
  the stock breaking out to a **new high from a sound base** now (at/near pivot). PARTIAL: has
  a "new" driver but extended, or repairing a base (not at a pivot). FAIL: no new driver, or
  making new lows / wide-loose base.
  **Both halves are required for a PASS** — a genuine new driver alone is a PARTIAL at best. A
  pivot needs a sound base (≥7-8 weeks, after a ≥30% advance, handle in the upper half above the
  10-week line) **and** new high ground: a candidate pivot more than **~10% below the 52-week
  high** is a lower high with overhead supply, not a buy point. A recent local high, a three-month
  high, or the top of a spike inside a downtrend does not qualify — an earnings gap that is still
  well below the 52-week high is base *repair*, and repair is a PARTIAL with no entry price.
  **More than ~20% below the 52-week high, N fails** — that is a broken chart, not repair. A PASS
  also needs the stock no more than **~5% extended** past the pivot. Extension roughly **>25%
  above the 50-day** after a climax run puts the stock past any pivot, so **N cannot PASS** — but
  it is a **flag, not a FAIL**: N's fail rung is distance below the 52-week high, and a name
  running away from a sound base is a PARTIAL with no entry price. `rubric.cap_n` bounds N from the
  52-week-high distance alone and `rubric.extended()` reports the run-up separately, so say
  "extended, no entry" and grade PARTIAL rather than failing the letter.
- **S — Supply & demand.** PASS: breakout volume **≥40-50% above the 50-day average** (or a clear
  dry-up in the base), reasonable float, buybacks, low debt, management ownership. PARTIAL:
  institutional-grade liquidity and a constructive trend but no demand surge; mixed. FAIL: heavy
  distribution, bloated float, high debt/dilution, **or the stock below its 200-day**.
- **L — Leader or laggard.** PASS: RS clearly beating SPY (proxy well positive) **and** ranked in
  the **top half of its group**. PARTIAL: ranked in the bottom half, or leading a group that itself
  lags. FAIL: **in line with** or behind SPY — matching the index is not leadership — or near
  52-week lows. O'Neil's *buy the #1 or #2 name* is the ideal, not the pass bar: `rubric.cap_l`
  caps at PARTIAL only once the rank passes half the group, and grading to a #1-2 bar would cost
  half a point on most names either skill passes.
- **I — Institutional sponsorship.** PASS: quality funds and the trend **verified as rising**,
  with room left to add. PARTIAL: adequate ownership whose trend you could not verify, flat
  sponsorship, or over-owned — **a high ownership level alone is a PARTIAL**. FAIL: little/no
  sponsorship, or funds distributing.
- **M — Market direction.** PASS: confirmed uptrend, few distribution days, broad leadership.
  PARTIAL: uptrend under pressure — **4-5 or more distribution days** in ~25 sessions, narrowing
  leadership, or an index slipping under its 50-day. FAIL: confirmed correction/downtrend. (M is
  market-wide context, not stock-specific.)

### The total is always out of 7

**One point per letter, seven letters, maximum 7.00.** Rough read: **6.0-7.0** leader in a strong
tape · **4.5-5.5** qualifies, buyable when N gives a pivot · **3.5-4.0** watch · **under 3.5** pass
on it. The bands summarise; the C/A/L + N gate decides the label. `pass` = 1, `partial` = 0.5, `fail` = 0,
summed across exactly C-A-N-S-L-I-M. The report computes this itself and renders `{tally} / 7`, so
a typed figure cannot contradict the rows above it; the self-audit flags a `scoreText` that
disagrees with the letters or that states any denominator other than 7. Never rescale — no /70,
no percentages, no dropping M to score out of 6.

**C, A and L weigh more in the verdict, not in the arithmetic.** They were the most predictive
traits, so they gate the label rather than the number: BUY-RANGE requires C, A and L to pass with
a valid N, and no total is high enough to earn it without them. A 5.5/7 built on a failing C is a
WATCH or an AVOID, not a near-miss buy — which is precisely why the score is a summary of the
seven rows and never the thing that decides the verdict.

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

### Shared files: the two classes
`parity-manifest.json` groups the files shared with `can-slim-recommend` into two classes, and
conflating them caused a real regression.

**`verbatim`** (`scripts/rubric.py`) carries no local additions on either side, so a byte
difference IS drift. The sister's `sector_screen.py` imports it rather than restating its numbers —
restating them is how "10% below the 52-week high" came to mean N<=partial in one skill and N=fail
in the other.

**`substance`** (`references/canslim-methodology.md`, `scripts/relative_strength.py`) is not
byte-identical and is not meant to be: each side adds its own material (the sister has a "Modern
refinements" methodology section and `--asof` point-in-time truncation). Demanding equal bytes there
does not detect drift, it manufactures it — the only way to go green is to delete the other side's
work, which is what sister commit `6e281fb` did to both of its additions. Port the CHANGE, not the
file. The sister's `scripts/check_parity.py` checks these by behaviour instead: every canonical rung
paragraph must appear verbatim in its copy, and both `relative_strength` copies are run over shared
series and must return identical numbers.
