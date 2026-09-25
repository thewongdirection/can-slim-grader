#!/usr/bin/env python3
"""
rubric.py - the CAN SLIM letter thresholds, as code, so the pair can be tested for agreement.

WHY THIS EXISTS. `can-slim-grader` and `can-slim-recommend` are one methodology aimed at two
questions, and for years the only thing keeping their rules in step was prose in two repos. Prose
drifts silently: a September-2026 comparison found the shared methodology carrying DIFFERENT score
bands on each side (3-4 watch here, 3.5-4.0 there), which is the same file disagreeing with itself
about whether a 3.0 is a watch or a pass. This module is the executable statement of the shared
thresholds, and `tests/test_rubric_parity.py` runs 100 real tickers through it and through the
sister's own `sector_screen.py` to prove they still agree.

WHAT THIS IS NOT. It does not grade C or A - those need a filings pull, not a screener row - and it
does not replace the judgement in the letters' `read`. It pins the parts that are arithmetic: the
thresholds, the weights, the bands.

THE TWO SKILLS ARE NOT SUPPOSED TO PRODUCE THE SAME NUMBER, and that is the subtlety this module
exists to keep straight. `can-slim-recommend` computes a CEILING - the best a name could score once
C and A are pulled - to decide what is worth grading. `can-slim-grader` computes the ACTUAL grade.
A ceiling is optimistic by construction. What must match is the THRESHOLDS applied to the same
input, which is exactly what `cap_*` below returns and what the parity test compares.

Pure standard library.
"""

WEIGHT = {"pass": 1.0, "partial": 0.5, "fail": 0.0}

# Shared thresholds. Every number here is quoted in references/canslim-methodology.md and is
# mirrored by can-slim-recommend/scripts/sector_screen.py's DEFAULTS.
PIVOT_BAND_PCT = 10.0     # beyond this below the 52-week high there is no pivot, so N <= partial
N_FAIL_PCT = 20.0         # beyond this, N fails outright - a lower high with overhead supply
                          # (= 2 x PIVOT_BAND_PCT; the sister derives it that way)
TRIAGE_DROP_PCT = 25.0    # a screener stops considering a name at all (triage only, NOT a letter)
EXTENDED_VS_EMA50_PCT = 25.0  # further above the 50-day than this is extended, past any pivot
THIN_VOL = 0.8            # relative volume under this is drying up under the price -> S fails
MIN_RS = 0.0              # RS must beat the benchmark at all, or the name is a laggard -> L fails
# The method's price and liquidity floors. A screener DROPS a name that misses these, because it is
# choosing among thousands. A single-ticker grade cannot drop the name someone asked about, so the
# same rule lands on S instead: below these a stock is not institutionally ownable, which is exactly
# what S measures. Same judgement, different place to put it - see canslim-methodology.md.
MIN_PRICE = 15.0          # Nasdaq floor; the methodology prefers NYSE >=20 and $30+ bases
MIN_DOLLAR_VOL = 20e6     # institutions need to be able to get in and out

# Rough read of the total. A summary, never the decision: the C/A/L + N gate decides the label.
BANDS = ((6.0, "leader in a strong tape"), (4.5, "qualifies, buyable when N gives a pivot"),
         (3.5, "watch - needs the market or a letter to improve"), (0.0, "pass on it"))
QUALIFY_THRESHOLD = 4.5   # the screener's recommendation cut, out of 7


def pct_off_high(close, high52):
    """Percent below the 52-week high, negative below it. None when either input is missing."""
    if close is None or high52 in (None, 0):
        return None
    return (close - high52) / high52 * 100.0


def pct_vs(close, level):
    if close is None or level in (None, 0):
        return None
    return (close - level) / level * 100.0


def cap_n(off_high_pct):
    """N's ceiling from distance below the 52-week high alone.

    Within the pivot band a pivot is possible, so N stays open. Past it there is no pivot, so N
    cannot exceed partial. Past twice it the chart is broken, not repairing, and N fails.
    """
    if off_high_pct is None:
        return "pass", "off-high unknown - N not bounded by price position"
    if off_high_pct < -N_FAIL_PCT:
        return "fail", ("%.0f%% below the 52-week high - no new-high ground at all"
                        % abs(off_high_pct))
    if off_high_pct < -PIVOT_BAND_PCT:
        return "partial", "%.0f%% below the 52-week high, so there is no pivot" % abs(off_high_pct)
    return "pass", "within the pivot band of the 52-week high"


def cap_s(rel_volume, vs_ema200_pct=None, close=None, dollar_vol=None):
    """S's ceiling: institutional ownability first, then the accumulation footprint.

    Price and liquidity come first because they are disqualifying rather than merely weak - a $4
    stock on $2M a day cannot be accumulated by a fund at all, so no volume pattern rescues S.
    """
    if close is not None and close < MIN_PRICE:
        return "fail", ("$%.2f is under the $%.0f price floor - not institutionally ownable"
                        % (close, MIN_PRICE))
    if dollar_vol is not None and dollar_vol < MIN_DOLLAR_VOL:
        return "fail", ("$%.1fM a day is too thin for institutional sponsorship"
                        % (dollar_vol / 1e6))
    if vs_ema200_pct is not None and vs_ema200_pct < 0:
        return "fail", "below the 200-day - downtrend, not accumulation"
    if rel_volume is None:
        return "pass", "relative volume unknown - S not bounded by volume"
    if rel_volume < THIN_VOL:
        return "fail", "relative volume %.2fx - volume drying up under the price" % rel_volume
    if rel_volume < 1.0:
        return "partial", "relative volume %.2fx - under its own norm, no accumulation" % rel_volume
    return "pass", "relative volume %.2fx - at or above its own norm" % rel_volume


def cap_l(rs_vs_bench_pts, sector_rank=None, sector_count=None):
    """L's ceiling. In line with the benchmark is not leadership; leading a laggard group is not
    leadership either."""
    if rs_vs_bench_pts is not None and rs_vs_bench_pts <= MIN_RS:
        return "fail", "performance lags the benchmark - laggard, not leader"
    if sector_rank and sector_count and sector_rank > sector_count / 2.0:
        return "partial", ("sector ranks #%d of %d - leader of a laggard group"
                           % (sector_rank, sector_count))
    return "pass", "outperforming the benchmark"


def extended(vs_ema50_pct):
    """True when price is further above the 50-day than a pivot entry allows."""
    return vs_ema50_pct is not None and vs_ema50_pct > EXTENDED_VS_EMA50_PCT


def triage_drop(off_high_pct):
    """A screener's own cut - distinct from N's grade, and deliberately looser."""
    return off_high_pct is not None and off_high_pct < -TRIAGE_DROP_PCT


def total(caps):
    """Sum seven letters at pass 1 / partial 0.5 / fail 0, to the nearest half point."""
    missing = [k for k in "CANSLIM" if k not in caps]
    if missing:
        raise ValueError("total() needs all seven letters; missing %s" % ",".join(missing))
    return round(sum(WEIGHT[caps[k]] for k in "CANSLIM") * 2) / 2.0


def band(score):
    for floor, text in BANDS:
        if score >= floor:
            return text
    return BANDS[-1][1]


def score_row(row, bench_perf=None, window="Perf.6M", sector_rank=None, sector_count=None):
    """Apply the shared thresholds to one screener-shaped row. Returns metrics + letter ceilings."""
    close = row.get("close")
    oh = pct_off_high(close, row.get("price_52_week_high"))
    perf = row.get(window)
    rs = (perf - bench_perf) if (perf is not None and bench_perf is not None) else None
    v50, v200 = pct_vs(close, row.get("EMA50")), pct_vs(close, row.get("EMA200"))
    avgvol = row.get("average_volume_10d_calc")
    dollar_vol = close * avgvol if (close is not None and avgvol is not None) else None
    n, n_why = cap_n(oh)
    s, s_why = cap_s(row.get("relative_volume_10d_calc"), v200, close, dollar_vol)
    l, l_why = cap_l(rs, sector_rank, sector_count)
    return {
        "symbol": row.get("symbol") or row.get("name"),
        "off_high_pct": oh, "rs_vs_bench_pts": rs, "avg_dollar_volume": dollar_vol,
        "vs_ema50_pct": v50, "vs_ema200_pct": v200,
        "caps": {"N": n, "S": s, "L": l},
        "why": {"N": n_why, "S": s_why, "L": l_why},
        "extended": extended(v50),
        "triage_drop": triage_drop(oh),
    }
