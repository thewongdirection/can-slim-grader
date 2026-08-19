#!/usr/bin/env python3
"""
relative_strength.py — turn IBKR price-history bars into the technical CAN SLIM metrics.

Computes, deterministically (so every run doesn't re-derive it by eyeballing bars):
  - Relative-strength proxy vs. a benchmark (SPY/QQQ) over 3/6/12 months, and a blended
    RS score weighted toward recent performance.
  - % off the 52-week high (N wants near new highs; reject near new lows).
  - Most-recent base depth & length (weekly bars) and a wide/loose flag.
  - Latest-day breakout volume vs. average (want >= +40-50% on a breakout).

INPUT: a JSON file (or stdin) shaped like:
{
  "benchmark": {"symbol": "SPY", "daily": [[t, o, h, l, c, v], ...]},
  "candidates": [
     {"symbol": "NVDA",
      "daily":  [[t,o,h,l,c,v], ...],   # ~6-12 months of ONE_DAY bars, oldest first
      "weekly": [[t,o,h,l,c,v], ...]},  # ~1-2 years of ONE_WEEK bars, oldest first
     ...
  ]
}
Each bar is [timestamp, open, high, low, close, volume] OR the dict form {t, o, h, l, c, v}
that TradingView's `get_ohlcv` and Polygon/Massive `/v2/aggs` return - both are accepted and
normalized on the way in, so provider output can be dropped straight into this file without
being reshaped by hand. `t` may be any monotonic value; only ordering is used. Missing
`weekly` disables base metrics for that name.

OUTPUT: JSON to stdout — per-candidate metrics plus a candidate-set RS rank (1 = strongest).

Usage:
  python relative_strength.py bars.json
  cat bars.json | python relative_strength.py
Pure standard library. If IBKR returns bars in a different shape, adapt the loader; the
math below only needs ordered (close, volume) series.
"""
import json
import sys


def as_row(bar):
    """Normalize one bar to [t, o, h, l, c, v].

    Providers disagree about the shape: IBKR and this file's own format use positional rows,
    while TradingView's `get_ohlcv` and Polygon/Massive `/v2/aggs` return {t, o, h, l, c, v}
    dicts. Accepting both here is what lets a run paste provider output in unedited - and
    hand-retyping bars is exactly where a grade quietly acquires a typo.
    """
    if isinstance(bar, dict):
        t = bar.get("t", bar.get("time", bar.get("date", bar.get("d"))))
        return [t, bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"), bar.get("v", 0)]
    return bar


def normalize(bars):
    return [as_row(b) for b in (bars or []) if b]


def closes(bars):
    return [float(b[4]) for b in bars if b and b[4] is not None]


def volumes(bars):
    return [float(b[5]) for b in bars if b and b[5] is not None]


def ret_over(series, lookback, tol=0.9):
    """Return fractional price change over the last `lookback` bars (e.g. ~63=3mo daily).

    If the series is a little shorter than `lookback` (e.g. IBKR's ONE_YEAR returns ~251
    daily bars but the 12-month window wants 252+1), clamp to the oldest available bar as
    long as we still have >= `tol` of the requested window. Below that, return None rather
    than pass off, say, 3 months of data as a 12-month return."""
    n = len(series)
    if n < 2:
        return None
    lb = lookback if n > lookback else n - 1
    if lb < lookback * tol or series[-lb - 1] == 0:
        return None
    return series[-1] / series[-lb - 1] - 1.0


def rs_proxy(cand_daily, bench_daily):
    """3/6/12-month relative return vs benchmark + a recency-weighted blend."""
    c, b = closes(cand_daily), closes(bench_daily)
    windows = {"3m": 63, "6m": 126, "12m": 252}
    rel = {}
    for name, lb in windows.items():
        cr, br = ret_over(c, lb), ret_over(b, lb)
        rel[name] = None if cr is None or br is None else round(cr - br, 4)
    # Blend: weight recent more (relative-strength ratings weight the most recent quarter). Skip missing.
    weights = {"3m": 0.40, "6m": 0.35, "12m": 0.25}
    num = sum(weights[k] * rel[k] for k in weights if rel[k] is not None)
    den = sum(weights[k] for k in weights if rel[k] is not None)
    blend = round(num / den, 4) if den else None
    return rel, blend


def pct_off_52w_high(daily):
    c = closes(daily)
    if not c:
        return None
    window = c[-252:] if len(c) >= 252 else c
    hi = max(window)
    if hi == 0:
        return None
    return round((hi - c[-1]) / hi, 4)  # 0.0 = at new high; 0.20 = 20% below


def base_metrics(weekly):
    """Rough most-recent consolidation: scan back from the last bar for the local peak,
    then the trough after it, to estimate depth% and length(weeks). Heuristic, not a
    full pattern classifier — use with the pattern rules in canslim-methodology.md."""
    c = closes(weekly)
    if len(c) < 6:
        return None
    last = c[-1]
    # find highest close in the trailing ~65 weeks that precedes current price action
    look = c[-65:] if len(c) >= 65 else c
    peak_idx = max(range(len(look)), key=lambda i: look[i])
    peak = look[peak_idx]
    trough = min(look[peak_idx:]) if peak_idx < len(look) else last
    depth = round((peak - trough) / peak, 4) if peak else None
    length_weeks = len(look) - peak_idx
    near_high = round((peak - last) / peak, 4) if peak else None
    # wide/loose warning: base deeper than ~35% is suspect (bull-market context)
    wide_loose = depth is not None and depth > 0.35
    return {
        "base_depth_pct": depth,
        "base_length_weeks": length_weeks,
        "pct_below_base_peak": near_high,
        "wide_and_loose_flag": wide_loose,
    }


def breakout_volume(daily, avg_window=50):
    v = volumes(daily)
    if len(v) < avg_window + 1:
        return None
    avg = sum(v[-avg_window - 1:-1]) / avg_window
    if avg == 0:
        return None
    latest = v[-1]
    return round(latest / avg - 1.0, 4)  # 0.5 = +50% above average


def analyze(data):
    bench = normalize(data.get("benchmark", {}).get("daily", []))
    out = []
    for cand in data.get("candidates", []):
        daily = normalize(cand.get("daily", []))
        weekly = normalize(cand.get("weekly", []))
        rel, blend = rs_proxy(daily, bench) if bench and daily else ({}, None)
        out.append({
            "symbol": cand.get("symbol"),
            "rs_relative_return": rel,
            "rs_blended": blend,
            "pct_off_52w_high": pct_off_52w_high(daily),
            "breakout_vol_vs_avg": breakout_volume(daily),
            "base": base_metrics(weekly) if weekly else None,
        })
    # rank by blended RS (strongest first); None sorts last
    ranked = sorted(out, key=lambda r: (r["rs_blended"] is None, -(r["rs_blended"] or 0)))
    for i, r in enumerate(ranked, 1):
        r["rs_rank"] = i
    return {"candidates": ranked, "count": len(ranked)}


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)
    json.dump(analyze(data), sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
