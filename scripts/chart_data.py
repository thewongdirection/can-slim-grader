#!/usr/bin/env python3
"""
chart_data.py - build the `priceChart` block for the CAN SLIM dashboard.

Turns daily OHLCV bars into the exact object `assets/evaluation_template.html` expects:
daily candles for the display window (default the last ~3 months = 63 sessions) plus the
50- and 200-period moving averages and the 50-day average volume.

WHY A SCRIPT: the 200-day EMA needs ~200 sessions of history BEFORE the first visible
candle. Feed this the longest daily series you can pull (>= 1 year, ideally 2); it computes
the averages across the whole series and then slices only the display window, so CONFIG
stays small and the moving averages are correctly seeded rather than restarted at the left
edge of the chart.

INPUT (auto-detected, file argument or stdin):
  1. IBKR `get_price_history` response - parallel arrays:
       {"time":[...], "open":[...], "high":[...], "low":[...], "close":[...], "volume":[...]}
  2. Row bars, the same shape scripts/relative_strength.py takes:
       [[t,o,h,l,c,v], ...]   or {"daily":[...]} / {"bars":[...]} / {"candidates":[{"daily":[...]}]}
  3. Polygon / Massive `/v2/aggs` - {"results":[{"t":ms,"o":..,"h":..,"l":..,"c":..,"v":..}, ...]}
  Timestamps may be epoch seconds, epoch millis, or an ISO string; only the date is kept.

OUTPUT: JSON on stdout - paste it as CONFIG.priceChart in the filled dashboard.

Usage:
  python chart_data.py nvda_daily.json                     # last 63 sessions, 50/200 EMA
  python chart_data.py nvda_daily.json --window 63 --js    # ready-to-paste "priceChart: {...},"
  python chart_data.py bars.json --type sma                # 50/200 simple MAs instead
  python chart_data.py bars.json --marker 178:Pivot:accent --marker 164:"Stop -8%":fail
  cat bars.json | python chart_data.py --out chart.json

Pure standard library.
"""
import argparse
import datetime as _dt
import json
import sys

MONTH_SESSIONS = 21  # ~21 trading sessions per month; 3 months ~= 63


# --------------------------------------------------------------------------- loading

def _date(t):
    """Normalize a bar timestamp to YYYY-MM-DD. Accepts epoch s/ms or an ISO string."""
    if isinstance(t, str):
        return t[:10]
    if isinstance(t, (int, float)):
        secs = t / 1000.0 if t > 1e11 else float(t)  # millis vs seconds
        return _dt.datetime.utcfromtimestamp(secs).strftime("%Y-%m-%d")
    return ""


def _rows_from_columns(d):
    """IBKR-style parallel arrays."""
    t = d.get("time") or d.get("t") or []
    o, h, l, c = d.get("open", []), d.get("high", []), d.get("low", []), d.get("close", [])
    v = d.get("volume", [])
    n = min(len(t), len(o), len(h), len(l), len(c))
    return [[_date(t[i]), o[i], h[i], l[i], c[i], (v[i] if i < len(v) else 0)] for i in range(n)]


def _rows_from_list(items):
    """Row bars [t,o,h,l,c,v] or Polygon-style dicts {t,o,h,l,c,v}."""
    rows = []
    for b in items:
        if isinstance(b, dict):
            rows.append([_date(b.get("t") or b.get("date") or b.get("d")),
                         b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v", 0)])
        elif isinstance(b, (list, tuple)) and len(b) >= 5:
            rows.append([_date(b[0]), b[1], b[2], b[3], b[4], (b[5] if len(b) > 5 else 0)])
    return rows


def load_rows(data):
    """Accept any of the documented input shapes; return [[date,o,h,l,c,v], ...] oldest first."""
    if isinstance(data, list):
        rows = _rows_from_list(data)
    elif isinstance(data, dict):
        if "open" in data and "close" in data:
            rows = _rows_from_columns(data)
        elif isinstance(data.get("results"), list):
            rows = _rows_from_list(data["results"])
        elif isinstance(data.get("daily"), list):
            rows = _rows_from_list(data["daily"])
        elif isinstance(data.get("bars"), list):
            rows = _rows_from_list(data["bars"])
        elif isinstance(data.get("candidates"), list) and data["candidates"]:
            rows = _rows_from_list(data["candidates"][0].get("daily", []))
        else:
            raise SystemExit("chart_data: unrecognized input shape; see the docstring.")
    else:
        raise SystemExit("chart_data: input must be a JSON object or array.")

    clean = []
    for r in rows:
        try:
            o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        except (TypeError, ValueError):
            continue  # skip holidays / null bars
        vol = 0.0
        try:
            vol = float(r[5] or 0)
        except (TypeError, ValueError):
            pass
        clean.append([r[0], o, h, l, c, vol])
    if not clean:
        raise SystemExit("chart_data: no usable bars found in the input.")
    if len(clean) > 1 and clean[0][0] and clean[-1][0] and clean[0][0] > clean[-1][0]:
        clean.reverse()  # newest-first inputs -> oldest first
    return clean


# --------------------------------------------------------------------------- averages

def ema(values, n):
    """Exponential MA seeded with the SMA of the first n values. None until seeded."""
    out = [None] * len(values)
    if len(values) < n or n < 1:
        return out
    e = sum(values[:n]) / n
    out[n - 1] = e
    k = 2.0 / (n + 1)
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def sma(values, n):
    out = [None] * len(values)
    if len(values) < n or n < 1:
        return out
    run = sum(values[:n])
    out[n - 1] = run / n
    for i in range(n, len(values)):
        run += values[i] - values[i - n]
        out[i] = run / n
    return out


def avg_volume(vols, n=50):
    if len(vols) < n:
        return round(sum(vols) / len(vols)) if vols else None
    return round(sum(vols[-n:]) / n)


# --------------------------------------------------------------------------- output

def _px(x, dp):
    return None if x is None else round(float(x), dp)


def build(rows, window, ma_type, periods, markers, label):
    closes = [r[4] for r in rows]
    fn = sma if ma_type == "sma" else ema
    fast, slow = periods
    ma_fast, ma_slow = fn(closes, fast), fn(closes, slow)

    dp = 4 if max(closes) < 1 else 2  # sub-dollar names need more precision
    start = max(0, len(rows) - window)
    win = rows[start:]

    out = {
        "windowLabel": label or "last %d sessions" % len(win),
        "bars": [[r[0], _px(r[1], dp), _px(r[2], dp), _px(r[3], dp), _px(r[4], dp), int(r[5])] for r in win],
        "ema50": [_px(x, dp) for x in ma_fast[start:]],
        "ema200": [_px(x, dp) for x in ma_slow[start:]],
        "avgVol": avg_volume([r[5] for r in rows], 50),
        "markers": markers,
    }
    if ma_type == "sma":
        out["emaLabels"] = ["%d SMA" % fast, "%d SMA" % slow]
    elif (fast, slow) != (50, 200):
        out["emaLabels"] = ["%d EMA" % fast, "%d EMA" % slow]

    warnings = []
    history = start  # bars available before the first visible candle
    if len(rows) < slow:
        warnings.append("only %d daily bars supplied - the %d-period average never seeds and will "
                        "not be drawn; pull at least %d." % (len(rows), slow, slow + window))
    elif history < slow:
        warnings.append("only %d bars precede the display window, so the %d-period average is still "
                        "converging at the left edge; pull ~%d bars (2 years) for a fully settled line."
                        % (history, slow, slow + window))
    if len(win) < window:
        warnings.append("input has %d bars; the window was trimmed to that." % len(win))
    if warnings:
        out["note"] = "Chart data: " + " ".join(warnings)
    return out, warnings


def parse_marker(s):
    """'178:Pivot:accent' -> {"price":178.0,"label":"Pivot","tone":"accent"}"""
    parts = s.split(":")
    if not parts or not parts[0]:
        raise argparse.ArgumentTypeError("marker must look like PRICE[:LABEL[:TONE]]")
    try:
        price = float(parts[0])
    except ValueError:
        raise argparse.ArgumentTypeError("marker price %r is not a number" % parts[0])
    tone = parts[2] if len(parts) > 2 and parts[2] else "accent"
    if tone not in ("accent", "pass", "fail", "partial"):
        raise argparse.ArgumentTypeError("marker tone must be accent|pass|fail|partial")
    return {"price": price, "label": (parts[1] if len(parts) > 1 else ""), "tone": tone}


def main():
    ap = argparse.ArgumentParser(description="Build the dashboard's priceChart block from daily OHLCV bars.")
    ap.add_argument("input", nargs="?", help="JSON file of daily bars (default: stdin)")
    ap.add_argument("--window", type=int, default=3 * MONTH_SESSIONS,
                    help="sessions to display (default 63 ~= 3 months)")
    ap.add_argument("--months", type=int, help="display window in months (overrides --window)")
    ap.add_argument("--type", choices=("ema", "sma"), default="ema", help="moving-average type (default ema)")
    ap.add_argument("--periods", default="50,200", help="fast,slow MA periods (default 50,200)")
    ap.add_argument("--marker", action="append", type=parse_marker, default=[],
                    metavar="PRICE[:LABEL[:TONE]]", help="horizontal line, e.g. 178:Pivot:accent (repeatable)")
    ap.add_argument("--label", help='window label shown in the report (default "last N sessions")')
    ap.add_argument("--js", action="store_true", help='emit "priceChart: {...}," ready to paste into CONFIG')
    ap.add_argument("--out", help="write to this file instead of stdout")
    args = ap.parse_args()

    try:
        periods = tuple(int(p) for p in args.periods.split(","))
        if len(periods) != 2 or periods[0] < 1 or periods[1] < 1:
            raise ValueError
    except ValueError:
        raise SystemExit("chart_data: --periods wants two positive integers, e.g. 50,200")

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    window = args.months * MONTH_SESSIONS if args.months else args.window
    label = args.label or ("last %d months" % args.months if args.months else "last 3 months")
    rows = load_rows(data)
    chart, warnings = build(rows, max(5, window), args.type, periods, args.marker, label)

    text = json.dumps(chart, indent=2)
    if args.js:
        text = "priceChart: " + text + ","
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        sys.stderr.write("chart_data: wrote %s (%d candles from %d bars)\n"
                         % (args.out, len(chart["bars"]), len(rows)))
    else:
        sys.stdout.write(text + "\n")
    for w in warnings:
        sys.stderr.write("chart_data: WARNING - %s\n" % w)


if __name__ == "__main__":
    main()
