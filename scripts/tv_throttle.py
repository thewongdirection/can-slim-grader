#!/usr/bin/env python3
"""
tv_throttle.py - pace this skill's TradingView calls so a run never trips the provider's limit.

WHY THIS EXISTS. TradingView publishes no rate limit for the endpoints behind the `Trading_View`
MCP, and it does not answer with a budget either: a successful response carries no
`limit`/`remaining`/`reset` field, so there is nothing to read a quota off. What it does do is
refuse - the MCP server hands back `{"success": false, "rate_limited": true, ...}` (seen wrapping
an HTTP 403 from `scanner.tradingview.com`) - and a refused call is a letter this skill cannot
grade. So the limit is discovered, not looked up: assume a conservative ceiling, stay under it,
and let an actual refusal teach the throttle what the real ceiling is.

THE CEILING. Never more than ABSOLUTE_MAX_RPM (100) requests/minute, and because the budget is
spent at SAFETY (80%) of whatever limit is believed, the throttle targets a number strictly below
that - 80/min globally out of the box. If a limit is ever discovered (advertised by the server,
documented by TradingView, or told to us by the user), record it with `set-limit` and every
budget re-derives from it.

PER FAMILY, NOT GLOBAL. The limit is enforced per upstream endpoint, not across the connector:
in an observed check `get_symbol_data` and `get_quote` were both refused (they hit
`scanner.tradingview.com`) while `get_ohlcv` answered normally in the same second (it hits the
chart service). So each family carries its own window and its own learned ceiling, under a shared
global cap.

ADAPTATION (AIMD - the standard additive-increase/multiplicative-decrease rule).
  - Refused  -> halve that family's believed limit (floor MIN_RPM) and cool the family down for
                the server's `retry_after`, else an escalating 30s/60s/120s/300s backoff.
  - Clean    -> after RECOVERY_CLEAN_CALLS good calls and RECOVERY_QUIET_SECONDS without a
                refusal, add RECOVERY_STEP back, never above the believed cap.
The learned ceiling is kept in a state file so the next run starts where this one left off.

USAGE (every TradingView call in a run goes through this):
  python scripts/tv_throttle.py wait --tool get_ohlcv        # block for a slot, then spend it
  python scripts/tv_throttle.py observe --tool get_ohlcv -   # feed the response back; it learns
        # `wait` spends the call, `observe` only learns from it - pair them and nothing is
        # double-counted. Observing a call made WITHOUT `wait` first needs --record.
  python scripts/tv_throttle.py status --json                # budget + cooldowns for the report
  python scripts/tv_throttle.py plan --calls 8 --tool get_ohlcv
  python scripts/tv_throttle.py set-limit --family scanner --per-minute 60 --source "TV support"
  python scripts/tv_throttle.py reset

Exit codes: 0 = go / clean, 3 = must wait (check) or rate-limited (observe), 4 = the request can
never fit one window. Pure standard library.
"""
import argparse
import json
import os
import re
import sys
import time

# --- the budget ------------------------------------------------------------------------------

ABSOLUTE_MAX_RPM = 100   # hard bound: this skill never issues 100 calls in any 60s window.
SAFETY = 0.8             # spend only this share of whatever limit is believed.
MIN_RPM = 5              # backoff floor - below this, waiting beats retrying.
WINDOW = 60.0            # the rate window, in seconds.

DEFAULT_FAMILY_RPM = 60  # believed per-family limit until something teaches us otherwise.
DEFAULT_GLOBAL_RPM = 100 # believed connector-wide limit; SAFETY keeps the spend under it.

BACKOFF_SECONDS = (30, 60, 120, 300)  # nth consecutive refusal -> cooldown.
RECOVERY_CLEAN_CALLS = 20             # clean calls before the limit creeps back up.
RECOVERY_QUIET_SECONDS = 120          # ...and this long since the last refusal.
RECOVERY_STEP = 5                     # rpm added per recovery.

STATE_VERSION = 1

# Which upstream endpoint each tool hits. `scanner` and `chart` are observed (see the docstring);
# the rest are grouped by best inference, which costs nothing: an unknown tool lands in `other`,
# and the global cap holds whatever the grouping gets wrong. A family that turns out to be wrong
# corrects itself the first time one of its tools is refused.
TOOL_FAMILY = {
    # scanner.tradingview.com - observed refusing with {"rate_limited": true}
    "get_quote": "scanner",
    "get_quotes_batch": "scanner",
    "get_symbol_data": "scanner",
    "run_screener": "scanner",
    "search_symbols": "scanner",
    "get_technicals": "scanner",
    "get_technicals_rating": "scanner",
    "get_full_technicals": "scanner",
    "filter_by_indicator": "scanner",
    "compare_symbols_tool": "scanner",
    "rank_symbol_setups": "scanner",
    "analyze_sector_tool": "scanner",
    # chart service - observed answering while the scanner was refusing
    "get_ohlcv": "chart",
    "analyze_multi_timeframe": "chart",
    "analyze_multi_timeframe_batch": "chart",
    "analyze_smc_tool": "chart",
    "analyze_swing_tool": "chart",
    "analyze_structure_batch": "chart",
    "compute_levels_batch": "chart",
    "calculate_correlation_tool": "chart",
    "event_study": "chart",
    # fundamentals / calendars
    "get_financials": "fundamentals",
    "get_financial_history": "fundamentals",
    "get_earnings_history": "fundamentals",
    "get_earnings_calendar": "fundamentals",
    "get_dividends_calendar": "fundamentals",
    "get_economic_calendar": "fundamentals",
    "get_forecasts": "fundamentals",
    # news / documents
    "get_news": "news",
    "get_news_story": "news",
    "get_documents": "news",
    "get_document_view": "news",
    "web_search": "news",
}
FAMILIES = ("scanner", "chart", "fundamentals", "news", "other")


def family_of(tool):
    """Map a TradingView tool name to its rate-limit family."""
    if not tool:
        return "other"
    name = tool.strip()
    # Accept the fully-qualified MCP name as well as the bare tool name.
    for prefix in ("mcp__Trading_View__", "Trading_View.", "Trading_View:"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return TOOL_FAMILY.get(name, "other")


# --- state -----------------------------------------------------------------------------------

def state_path(explicit=None):
    """Where the learned ceiling lives, so it survives between runs."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get("TV_THROTTLE_STATE")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "can-slim", "tv_throttle.json")


def _num(value, fallback):
    """A number from the state file, or `fallback` if it is not one (bools are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value)


def _blank_lane(rpm):
    return {"believed_rpm": rpm, "source": "default", "calls": [], "cooldown_until": 0.0,
            "consecutive_limits": 0, "clean_calls": 0, "last_limited": 0.0, "limit_events": 0}


def blank_state():
    st = {"version": STATE_VERSION,
          "global": _blank_lane(DEFAULT_GLOBAL_RPM),
          "families": {f: _blank_lane(DEFAULT_FAMILY_RPM) for f in FAMILIES}}
    return st


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return blank_state()
    if not isinstance(st, dict) or st.get("version") != STATE_VERSION:
        return blank_state()
    st.setdefault("global", _blank_lane(DEFAULT_GLOBAL_RPM))
    fams = st.setdefault("families", {})
    for f in FAMILIES:
        fams.setdefault(f, _blank_lane(DEFAULT_FAMILY_RPM))
    # The state file is persistent and hand-editable, so nothing read back is trusted: coerce
    # every number and drop what will not coerce. A corrupt ledger must cost a slower run, never
    # a crashed grade.
    for lane in [st["global"]] + list(fams.values()):
        default = _blank_lane(DEFAULT_FAMILY_RPM)
        for k, v in default.items():
            lane.setdefault(k, v)
        lane["calls"] = sorted(_num(t, None) for t in lane.get("calls", [])
                               if _num(t, None) is not None)
        for k in ("believed_rpm", "cooldown_until", "last_limited", "consecutive_limits",
                  "clean_calls", "limit_events"):
            lane[k] = _num(lane.get(k), default[k])
        lane["believed_rpm"] = max(1, min(int(lane["believed_rpm"]), ABSOLUTE_MAX_RPM))
    return st


def save_state(path, st):
    """Atomic write - two runs pacing at once must not read a half-written ledger.

    A state file that cannot be written is loud but not fatal: the pacing of THIS command still
    holds, but nothing carries to the next one, so the run is effectively unthrottled. Say so on
    stderr rather than crashing a grade over a read-only directory.
    """
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        sys.stderr.write(
            "tv_throttle: WARNING - cannot write %s (%s). Pacing will NOT carry to the next "
            "call; set --state or $TV_THROTTLE_STATE to a writable path.\n" % (path, exc))
        return False


class _Lock(object):
    """Best-effort cross-process lock so concurrent pacers share one ledger."""

    def __init__(self, path):
        self.path = path + ".lock"
        self.fh = None

    def __enter__(self):
        try:
            import fcntl
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            self.fh = open(self.path, "w")
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            self.fh = None  # no flock (or no write access) - single-process pacing still works.
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.fh.close()
            self.fh = None
        return False


# --- the throttle ----------------------------------------------------------------------------

def effective_rpm(lane):
    """Calls/minute this lane may actually spend: SAFETY of what is believed, never >= the cap."""
    believed = min(float(lane.get("believed_rpm", DEFAULT_FAMILY_RPM)), float(ABSOLUTE_MAX_RPM))
    return max(1, min(int(believed * SAFETY), ABSOLUTE_MAX_RPM - 1))


def prune(lane, now):
    lane["calls"] = sorted(t for t in lane["calls"] if now - t < WINDOW)
    return lane["calls"]


def lane_wait(lane, n, now):
    """Seconds until `n` calls fit this lane. 0 = go now, None = never fits one window."""
    cool = max(0.0, float(lane.get("cooldown_until", 0.0)) - now)
    times = prune(lane, now)
    eff = effective_rpm(lane)
    if n > eff:
        return None
    free = eff - len(times)
    if free >= n:
        return cool
    # The (n - free)-th oldest call is the one whose expiry frees the last slot needed.
    k = n - free
    return max(cool, times[k - 1] + WINDOW - now)


def lanes_for(st, family):
    return [st["families"][family], st["global"]]


def wait_seconds(st, family, n, now):
    """Seconds until `n` calls fit BOTH the family lane and the global lane."""
    waits = [lane_wait(lane, n, now) for lane in lanes_for(st, family)]
    if any(w is None for w in waits):
        return None
    return max(waits)


def note_clean(st, family, n, now):
    """Credit n calls that came back clean - recovery accounting only, no window spend."""
    lane = st["families"][family]
    lane["clean_calls"] = int(lane.get("clean_calls", 0)) + n
    maybe_recover(st, family, now)


def record_calls(st, family, n, now):
    """Spend n calls from the family lane and the global cap, and credit them as clean."""
    for lane in lanes_for(st, family):
        prune(lane, now)
        lane["calls"].extend([now] * n)
        lane["calls"] = sorted(lane["calls"])[-(2 * ABSOLUTE_MAX_RPM):]
    note_clean(st, family, n, now)


def note_limited(st, family, now, retry_after=None):
    """A call was refused: halve THAT family's believed limit and cool that family down.

    Only the refused family adapts. The limit is enforced per endpoint - the chart service kept
    answering while the scanner was refusing - so cooling the whole connector down on one
    family's refusal would stall a run against an endpoint that is perfectly healthy. The global
    lane stays a fixed cap, not a learner: it exists to hold the whole run below ABSOLUTE_MAX_RPM.
    """
    lane = st["families"][family]
    lane["consecutive_limits"] = int(lane.get("consecutive_limits", 0)) + 1
    lane["limit_events"] = int(lane.get("limit_events", 0)) + 1
    lane["clean_calls"] = 0
    lane["last_limited"] = now
    believed = float(lane.get("believed_rpm", DEFAULT_FAMILY_RPM))
    lane["believed_rpm"] = max(MIN_RPM, int(believed / 2))
    lane["source"] = "observed"
    idx = min(lane["consecutive_limits"], len(BACKOFF_SECONDS)) - 1
    backoff = float(retry_after) if retry_after else float(BACKOFF_SECONDS[idx])
    lane["cooldown_until"] = max(float(lane.get("cooldown_until", 0.0)), now + backoff)
    return lane["cooldown_until"] - now


def maybe_recover(st, family, now):
    """Creep a refused family's believed limit back up once it has been clean for a while."""
    lane = st["families"][family]
    if lane.get("limit_events", 0) == 0:
        return
    if lane.get("clean_calls", 0) < RECOVERY_CLEAN_CALLS:
        return
    if now - float(lane.get("last_limited", 0.0)) < RECOVERY_QUIET_SECONDS:
        return
    raised = min(DEFAULT_FAMILY_RPM, int(lane.get("believed_rpm", MIN_RPM)) + RECOVERY_STEP)
    if raised > lane["believed_rpm"]:
        lane["believed_rpm"] = raised
    lane["clean_calls"] = 0
    lane["consecutive_limits"] = 0


# --- reading a limit off a response ----------------------------------------------------------

_LIMIT_TEXT = re.compile(
    r"rate[\s_-]?limit|too\s+many\s+requests|\b429\b|quota\s+exceeded|throttl", re.I)
_RETRY_TEXT = re.compile(r"retry[\s_-]?after\D{0,10}(\d+(?:\.\d+)?)", re.I)


def detect_limit(payload):
    """Read a TradingView tool response and say whether it was refused for rate.

    Returns (limited, retry_after, reason). The server's own `rate_limited` flag is
    authoritative; the text patterns are the fallback for shapes it does not set it on. A plain
    error WITHOUT any of those signals is a failure but not a throttle - it must not teach the
    throttle a smaller ceiling, or one bad symbol would slow the whole run down.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    obj = payload if isinstance(payload, dict) else None
    if obj is None:
        try:
            parsed = json.loads(text)
            obj = parsed if isinstance(parsed, dict) else None
        except ValueError:
            obj = None

    retry = None
    if obj:
        for key in ("retry_after", "retryAfter", "Retry-After", "retry_after_seconds"):
            val = obj.get(key)
            if isinstance(val, (int, float)):
                retry = float(val)
                break
            if isinstance(val, str) and val.strip().replace(".", "", 1).isdigit():
                retry = float(val.strip())
                break
    if retry is None:
        m = _RETRY_TEXT.search(text)
        if m:
            retry = float(m.group(1))

    if obj is not None and obj.get("rate_limited") is True:
        return True, retry, "server set rate_limited=true"
    if obj is not None and obj.get("rate_limited") is False:
        return False, retry, "server set rate_limited=false"
    if obj is not None and obj.get("success") is True:
        return False, retry, "success"
    m = _LIMIT_TEXT.search(text)
    if m:
        return True, retry, "response text matched %r" % m.group(0)
    return False, retry, "no rate-limit signal"


# --- reporting -------------------------------------------------------------------------------

def snapshot(st, now):
    def lane_view(name, lane):
        prune(lane, now)
        eff = effective_rpm(lane)
        return {"lane": name,
                "believed_rpm": int(lane.get("believed_rpm", 0)),
                "effective_rpm": eff,
                "used_last_60s": len(lane["calls"]),
                "free_now": max(0, eff - len(lane["calls"])),
                "source": lane.get("source", "default"),
                "cooldown_s": round(max(0.0, float(lane.get("cooldown_until", 0.0)) - now), 1),
                "limit_events": int(lane.get("limit_events", 0))}
    return {"absolute_max_rpm": ABSOLUTE_MAX_RPM,
            "safety": SAFETY,
            "global": lane_view("global", st["global"]),
            "families": [lane_view(f, st["families"][f]) for f in FAMILIES]}


def print_status(snap):
    print("tv_throttle: hard bound %d req/min; spending %d%% of each believed limit."
          % (snap["absolute_max_rpm"], int(snap["safety"] * 100)))
    rows = [snap["global"]] + snap["families"]
    print("  %-13s %9s %9s %7s %7s  %-9s %s"
          % ("lane", "believed", "effective", "used", "free", "source", "cooldown"))
    for r in rows:
        print("  %-13s %9d %9d %7d %7d  %-9s %s"
              % (r["lane"], r["believed_rpm"], r["effective_rpm"], r["used_last_60s"],
                 r["free_now"], r["source"],
                 ("%.0fs" % r["cooldown_s"]) if r["cooldown_s"] > 0 else "-"))


# --- commands --------------------------------------------------------------------------------

def _resolve_family(args):
    if getattr(args, "family", None):
        fam = args.family.strip().lower()
        if fam not in FAMILIES:
            raise SystemExit("tv_throttle: unknown family %r (want one of %s)"
                             % (args.family, ", ".join(FAMILIES)))
        return fam
    return family_of(getattr(args, "tool", None))


def _emit(args, payload, human):
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human)


def cmd_status(args, path):
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        snap = snapshot(st, now)
        save_state(path, st)
    if args.json:
        print(json.dumps(snap, indent=2, sort_keys=True))
    else:
        print_status(snap)
    return 0


def cmd_check(args, path):
    fam = _resolve_family(args)
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        wait = wait_seconds(st, fam, args.calls, now)
        save_state(path, st)
    if wait is None:
        _emit(args, {"family": fam, "calls": args.calls, "fits": False},
              "tv_throttle: %d call(s) cannot fit one 60s window for %s - split the batch."
              % (args.calls, fam))
        return 4
    go = wait <= 0
    _emit(args, {"family": fam, "calls": args.calls, "fits": True, "go": go,
                 "wait_s": round(wait, 2)},
          "tv_throttle: GO (%s)" % fam if go
          else "tv_throttle: WAIT %.1fs before %d %s call(s)" % (wait, args.calls, fam))
    return 0 if go else 3


def cmd_wait(args, path):
    fam = _resolve_family(args)
    slept = 0.0
    while True:
        with _Lock(path):
            st = load_state(path)
            now = time.time()
            wait = wait_seconds(st, fam, args.calls, now)
            if wait is None:
                save_state(path, st)
                _emit(args, {"family": fam, "calls": args.calls, "fits": False},
                      "tv_throttle: %d call(s) cannot fit one 60s window for %s - split the batch."
                      % (args.calls, fam))
                return 4
            if wait <= 0:
                if args.record:
                    record_calls(st, fam, args.calls, now)
                save_state(path, st)
                _emit(args, {"family": fam, "calls": args.calls, "waited_s": round(slept, 2),
                             "recorded": bool(args.record)},
                      "tv_throttle: GO (%s)%s" % (fam, " after %.1fs" % slept if slept else ""))
                return 0
            save_state(path, st)
        if args.max_wait is not None and slept + wait > args.max_wait:
            _emit(args, {"family": fam, "calls": args.calls, "waited_s": round(slept, 2),
                         "wait_s": round(wait, 2), "gave_up": True},
                  "tv_throttle: would need %.1fs more (max-wait %.0fs) - fall down the source "
                  "ladder instead of blocking the run." % (wait, args.max_wait))
            return 3
        step = min(wait, 5.0)
        time.sleep(step)
        slept += step


def cmd_record(args, path):
    fam = _resolve_family(args)
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        record_calls(st, fam, args.calls, now)
        snap = snapshot(st, now)
        save_state(path, st)
    _emit(args, {"family": fam, "recorded": args.calls, "status": snap},
          "tv_throttle: recorded %d %s call(s)." % (args.calls, fam))
    return 0


def cmd_limited(args, path):
    fam = _resolve_family(args)
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        cooldown = note_limited(st, fam, now, args.retry_after)
        snap = snapshot(st, now)
        save_state(path, st)
    _emit(args, {"family": fam, "cooldown_s": round(cooldown, 1), "status": snap},
          "tv_throttle: %s refused - believed limit halved to %d/min, cooling down %.0fs."
          % (fam, snap["families"][FAMILIES.index(fam)]["believed_rpm"], cooldown))
    return 3


def cmd_observe(args, path):
    """Feed a tool response in; the throttle decides whether it was a refusal and adapts."""
    fam = _resolve_family(args)
    if args.rate_limited:
        limited, retry, reason = True, args.retry_after, "caller passed --rate-limited"
    else:
        src = args.response
        if src in (None, "-"):
            raw = sys.stdin.read()
        else:
            with open(src, "r", encoding="utf-8") as f:
                raw = f.read()
        limited, retry, reason = detect_limit(raw)
        if args.retry_after:
            retry = args.retry_after
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        if limited:
            cooldown = note_limited(st, fam, now, retry)
        else:
            cooldown = 0.0
            # `wait` already spent this call from the window; observing it must not spend it
            # twice, or the real budget quietly halves. --record covers a call made without
            # `wait` first.
            (record_calls if args.record else note_clean)(st, fam, 1, now)
        snap = snapshot(st, now)
        save_state(path, st)
    _emit(args, {"family": fam, "rate_limited": limited, "reason": reason,
                 "retry_after": retry, "cooldown_s": round(cooldown, 1), "status": snap},
          ("tv_throttle: RATE-LIMITED on %s (%s) - backing off %.0fs. Do not fabricate the "
           "figure; retry after the cooldown, then fall down the source ladder."
           % (fam, reason, cooldown)) if limited
          else "tv_throttle: %s call OK (%s)." % (fam, reason))
    return 3 if limited else 0


def cmd_plan(args, path):
    """How long `--calls` calls will take at the current budget, before making any of them."""
    fam = _resolve_family(args)
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        snap = snapshot(st, now)
        save_state(path, st)
    eff = min(snap["families"][FAMILIES.index(fam)]["effective_rpm"],
              snap["global"]["effective_rpm"])
    free = min(snap["families"][FAMILIES.index(fam)]["free_now"], snap["global"]["free_now"])
    n = args.calls
    if n <= free:
        seconds = 0.0
    else:
        # Each window past the first admits `eff` more calls.
        seconds = WINDOW * ((n - free + eff - 1) // eff)
    _emit(args, {"family": fam, "calls": n, "effective_rpm": eff, "free_now": free,
                 "estimated_seconds": round(seconds, 1)},
          "tv_throttle: %d %s call(s) at %d/min - %d can go now, ~%.0fs to finish."
          % (n, fam, eff, free, seconds))
    return 0


def cmd_set_limit(args, path):
    """Adopt a limit that has actually been discovered (advertised, documented, or told to us)."""
    fam = args.family.strip().lower() if args.family else None
    if fam and fam not in FAMILIES and fam != "global":
        raise SystemExit("tv_throttle: unknown family %r" % args.family)
    rpm = int(args.per_minute)
    if rpm < 1:
        raise SystemExit("tv_throttle: --per-minute must be >= 1")
    capped = min(rpm, ABSOLUTE_MAX_RPM)
    with _Lock(path):
        st = load_state(path)
        now = time.time()
        targets = [st["global"]] if fam == "global" else (
            [st["families"][fam]] if fam else [st["global"]] + list(st["families"].values()))
        for lane in targets:
            lane["believed_rpm"] = capped
            lane["source"] = args.source or "manual"
        snap = snapshot(st, now)
        save_state(path, st)
    note = "" if capped == rpm else " (clamped to the %d/min hard bound)" % ABSOLUTE_MAX_RPM
    _emit(args, {"family": fam or "all", "believed_rpm": capped, "source": args.source or "manual",
                 "status": snap},
          "tv_throttle: %s limit set to %d/min%s - spending %d%% of it."
          % (fam or "every lane", capped, note, int(SAFETY * 100)))
    return 0


def cmd_reset(args, path):
    with _Lock(path):
        st = load_state(path)
        if args.family:
            fam = args.family.strip().lower()
            if fam == "global":
                st["global"] = _blank_lane(DEFAULT_GLOBAL_RPM)
            elif fam in FAMILIES:
                st["families"][fam] = _blank_lane(DEFAULT_FAMILY_RPM)
            else:
                raise SystemExit("tv_throttle: unknown family %r" % args.family)
        else:
            st = blank_state()
        save_state(path, st)
    print("tv_throttle: reset %s." % (args.family or "every lane"))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        description="Pace TradingView calls under a discovered rate limit (hard bound %d/min)."
                    % ABSOLUTE_MAX_RPM)
    ap.add_argument("--state", help="state file (default $TV_THROTTLE_STATE or "
                                    "$XDG_STATE_HOME/can-slim/tv_throttle.json)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_target(p):
        p.add_argument("--tool", help="TradingView tool name, e.g. get_ohlcv")
        p.add_argument("--family", help="or the family directly: %s" % ", ".join(FAMILIES))
        return p

    with_target(sub.add_parser("check", help="can N calls go now? (non-blocking)")).add_argument(
        "--calls", type=int, default=1)
    w = with_target(sub.add_parser("wait", help="block until N calls fit, then record them"))
    w.add_argument("--calls", type=int, default=1)
    w.add_argument("--max-wait", type=float, default=90.0,
                   help="give up after this many seconds and use the source ladder (default 90)")
    w.add_argument("--no-record", dest="record", action="store_false", default=True,
                   help="do not count the call(s) yet")
    with_target(sub.add_parser("record", help="count calls already made")).add_argument(
        "--calls", type=int, default=1)
    lim = with_target(sub.add_parser("limited", help="report a refusal; halve and cool down"))
    lim.add_argument("--retry-after", type=float, help="seconds the server asked for")
    obs = with_target(sub.add_parser("observe", help="feed a tool response in; adapt from it"))
    obs.add_argument("response", nargs="?", default="-",
                     help="file with the JSON response, or - for stdin")
    obs.add_argument("--rate-limited", action="store_true",
                     help="skip detection; treat it as a refusal")
    obs.add_argument("--retry-after", type=float)
    obs.add_argument("--record", action="store_true",
                     help="also spend the call from the budget (only if `wait` did not already)")
    with_target(sub.add_parser("plan", help="estimate the time for a batch")).add_argument(
        "--calls", type=int, required=True)
    sl = sub.add_parser("set-limit", help="record a limit that was actually discovered")
    sl.add_argument("--family", help="a family, 'global', or omit for every lane")
    sl.add_argument("--per-minute", type=int, required=True)
    sl.add_argument("--source", help="where the number came from")
    sub.add_parser("status", help="show the budget")
    r = sub.add_parser("reset", help="forget the learned ceiling")
    r.add_argument("--family")
    return ap


COMMANDS = {"status": cmd_status, "check": cmd_check, "wait": cmd_wait, "record": cmd_record,
            "limited": cmd_limited, "observe": cmd_observe, "plan": cmd_plan,
            "set-limit": cmd_set_limit, "reset": cmd_reset}


def main(argv=None):
    args = build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args, state_path(args.state))


if __name__ == "__main__":
    sys.exit(main())
