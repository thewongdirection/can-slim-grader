#!/usr/bin/env python3
"""
Regression tests for scripts/tv_throttle.py.

Run them all with:  python3 -m unittest discover -s tests -v
Pure standard library (no pytest), like the scripts themselves. Time is injected as an explicit
`now` everywhere the throttle reasons about the window, so these tests never sleep.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import tv_throttle as tv  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "tv_throttle.py")


class FamilyMapping(unittest.TestCase):
    def test_observed_families(self):
        # The two families with direct evidence: the scanner refused while the chart answered.
        self.assertEqual(tv.family_of("get_symbol_data"), "scanner")
        self.assertEqual(tv.family_of("get_quote"), "scanner")
        self.assertEqual(tv.family_of("get_ohlcv"), "chart")

    def test_accepts_fully_qualified_mcp_name(self):
        self.assertEqual(tv.family_of("mcp__Trading_View__get_ohlcv"), "chart")
        self.assertEqual(tv.family_of("  mcp__Trading_View__run_screener "), "scanner")

    def test_unknown_tool_falls_to_other(self):
        self.assertEqual(tv.family_of("some_new_tool"), "other")
        self.assertEqual(tv.family_of(None), "other")

    def test_every_mapped_family_is_a_real_lane(self):
        for fam in tv.TOOL_FAMILY.values():
            self.assertIn(fam, tv.FAMILIES)


class Ceiling(unittest.TestCase):
    def test_never_reaches_the_hard_bound(self):
        # The whole point of the change: strictly below 100 requests/minute, whatever is believed.
        for believed in (60, 100, 1000, 10 ** 6):
            eff = tv.effective_rpm({"believed_rpm": believed})
            self.assertLess(eff, tv.ABSOLUTE_MAX_RPM,
                            "believed=%s produced %s" % (believed, eff))

    def test_spends_only_the_safety_share(self):
        self.assertEqual(tv.effective_rpm({"believed_rpm": 50}), int(50 * tv.SAFETY))

    def test_default_global_budget_is_below_a_hundred(self):
        st = tv.blank_state()
        self.assertLess(tv.effective_rpm(st["global"]), tv.ABSOLUTE_MAX_RPM)

    def test_tiny_limit_still_admits_one_call(self):
        self.assertEqual(tv.effective_rpm({"believed_rpm": 1}), 1)


class Window(unittest.TestCase):
    def setUp(self):
        self.st = tv.blank_state()

    def test_admits_up_to_the_budget_then_makes_the_caller_wait(self):
        now = 1000.0
        eff = tv.effective_rpm(self.st["families"]["chart"])
        for _ in range(eff):
            self.assertEqual(tv.wait_seconds(self.st, "chart", 1, now), 0)
            tv.record_calls(self.st, "chart", 1, now)
        self.assertGreater(tv.wait_seconds(self.st, "chart", 1, now), 0)

    def test_slots_free_up_when_the_window_rolls(self):
        now = 1000.0
        eff = tv.effective_rpm(self.st["families"]["chart"])
        tv.record_calls(self.st, "chart", eff, now)
        self.assertGreater(tv.wait_seconds(self.st, "chart", 1, now), 0)
        self.assertEqual(tv.wait_seconds(self.st, "chart", 1, now + tv.WINDOW + 0.1), 0)

    def test_wait_is_the_time_until_the_right_call_expires(self):
        now = 1000.0
        eff = tv.effective_rpm(self.st["families"]["chart"])
        for i in range(eff):
            tv.record_calls(self.st, "chart", 1, now + i)
        # The oldest call expires one window after it was made.
        self.assertAlmostEqual(tv.wait_seconds(self.st, "chart", 1, now + eff),
                               now + tv.WINDOW - (now + eff), places=3)

    def test_batch_larger_than_one_window_never_fits(self):
        self.assertIsNone(tv.wait_seconds(self.st, "chart", tv.ABSOLUTE_MAX_RPM + 1, 1000.0))

    def test_global_cap_binds_across_families(self):
        now = 1000.0
        budget = tv.effective_rpm(self.st["global"])
        spent = 0
        for fam in ("chart", "fundamentals", "news", "other"):
            per = tv.effective_rpm(self.st["families"][fam])
            tv.record_calls(self.st, fam, per, now)
            spent += per
        self.assertGreater(spent, budget, "test needs family budgets that can exceed the global")
        # A family with room left is still held back by the connector-wide cap.
        self.assertGreater(tv.wait_seconds(self.st, "scanner", 1, now), 0)
        self.assertLessEqual(len(self.st["global"]["calls"]), spent)

    def test_pruning_drops_calls_older_than_the_window(self):
        lane = self.st["families"]["chart"]
        lane["calls"] = [100.0, 200.0, 300.0]
        tv.prune(lane, 300.0 + tv.WINDOW + 1)
        self.assertEqual(lane["calls"], [])


class Detection(unittest.TestCase):
    def test_servers_own_flag_is_authoritative(self):
        # The exact shape observed from the connector.
        payload = ('{"success":false,"error":"HTTPStatusError(\\"Client error \'403 Forbidden\' '
                   'for url \'https://scanner.tradingview.com/america/scan\'\\")",'
                   '"rate_limited":true}')
        limited, retry, reason = tv.detect_limit(payload)
        self.assertTrue(limited)
        self.assertIn("rate_limited", reason)
        self.assertIsNone(retry)

    def test_success_is_not_a_refusal(self):
        limited, _, _ = tv.detect_limit('{"success":true,"symbol":"NASDAQ:AAPL","count":3}')
        self.assertFalse(limited)

    def test_explicit_false_flag_beats_incidental_text(self):
        limited, _, _ = tv.detect_limit(
            '{"success":false,"rate_limited":false,"error":"no rate limit info here"}')
        self.assertFalse(limited)

    def test_plain_error_does_not_teach_a_smaller_ceiling(self):
        # A bad symbol must not slow the whole run down.
        limited, _, _ = tv.detect_limit(
            '{"success":false,"error":"Quote not available for `NASDAQ:NOPE`."}')
        self.assertFalse(limited)

    def test_text_fallbacks(self):
        for text in ('{"error":"429 Too Many Requests"}',
                     '{"error":"rate limit exceeded"}',
                     '{"error":"request was throttled"}',
                     'HTTP 429'):
            self.assertTrue(tv.detect_limit(text)[0], text)

    def test_retry_after_is_read_from_field_or_text(self):
        self.assertEqual(tv.detect_limit('{"rate_limited":true,"retry_after":45}')[1], 45.0)
        self.assertEqual(tv.detect_limit('{"rate_limited":true,"Retry-After":"12"}')[1], 12.0)
        self.assertEqual(tv.detect_limit('{"error":"429; retry after 7 seconds"}')[1], 7.0)

    def test_dict_input_is_accepted(self):
        self.assertTrue(tv.detect_limit({"success": False, "rate_limited": True})[0])

    def test_non_json_text_does_not_crash(self):
        self.assertFalse(tv.detect_limit("<html>502 Bad Gateway</html>")[0])


class Adaptation(unittest.TestCase):
    def setUp(self):
        self.st = tv.blank_state()

    def test_refusal_halves_the_believed_limit(self):
        before = self.st["families"]["scanner"]["believed_rpm"]
        tv.note_limited(self.st, "scanner", 1000.0)
        self.assertEqual(self.st["families"]["scanner"]["believed_rpm"], before // 2)

    def test_refusal_only_touches_the_refused_family(self):
        # The chart service kept answering while the scanner refused - one family's refusal must
        # not stall the rest of the run.
        tv.note_limited(self.st, "scanner", 1000.0)
        self.assertEqual(self.st["families"]["chart"]["believed_rpm"], tv.DEFAULT_FAMILY_RPM)
        self.assertEqual(self.st["global"]["believed_rpm"], tv.DEFAULT_GLOBAL_RPM)
        self.assertEqual(self.st["global"]["cooldown_until"], 0.0)
        self.assertEqual(tv.wait_seconds(self.st, "chart", 1, 1000.0), 0)

    def test_cooldown_blocks_the_refused_family(self):
        tv.note_limited(self.st, "scanner", 1000.0)
        self.assertGreater(tv.wait_seconds(self.st, "scanner", 1, 1000.0), 0)
        self.assertEqual(
            tv.wait_seconds(self.st, "scanner", 1, 1000.0 + tv.BACKOFF_SECONDS[0] + 1), 0)

    def test_repeated_refusals_back_off_further(self):
        seen = []
        for i in range(len(tv.BACKOFF_SECONDS) + 2):
            seen.append(round(tv.note_limited(self.st, "scanner", 1000.0 + i)))
        self.assertEqual(seen[0], tv.BACKOFF_SECONDS[0])
        self.assertTrue(all(b <= max(tv.BACKOFF_SECONDS) + 1 for b in seen))
        self.assertGreaterEqual(seen[2], seen[1])

    def test_server_retry_after_wins_over_the_default_backoff(self):
        cooldown = tv.note_limited(self.st, "scanner", 1000.0, retry_after=5)
        self.assertAlmostEqual(cooldown, 5.0, places=3)

    def test_backoff_floors_at_min_rpm(self):
        for i in range(20):
            tv.note_limited(self.st, "scanner", 1000.0 + i)
        self.assertEqual(self.st["families"]["scanner"]["believed_rpm"], tv.MIN_RPM)

    def test_recovery_needs_both_clean_calls_and_quiet_time(self):
        tv.note_limited(self.st, "scanner", 1000.0)
        halved = self.st["families"]["scanner"]["believed_rpm"]
        # Clean calls, but too soon after the refusal: no recovery.
        tv.record_calls(self.st, "scanner", tv.RECOVERY_CLEAN_CALLS, 1001.0)
        self.assertEqual(self.st["families"]["scanner"]["believed_rpm"], halved)
        # Quiet long enough, and clean: the limit creeps back.
        quiet = 1000.0 + tv.RECOVERY_QUIET_SECONDS + 1
        tv.record_calls(self.st, "scanner", tv.RECOVERY_CLEAN_CALLS, quiet)
        self.assertEqual(self.st["families"]["scanner"]["believed_rpm"],
                         halved + tv.RECOVERY_STEP)

    def test_recovery_never_exceeds_the_default_ceiling(self):
        lane = self.st["families"]["scanner"]
        lane["limit_events"] = 1
        lane["believed_rpm"] = tv.DEFAULT_FAMILY_RPM
        lane["clean_calls"] = tv.RECOVERY_CLEAN_CALLS
        lane["last_limited"] = 0.0
        tv.maybe_recover(self.st, "scanner", 10 ** 6)
        self.assertEqual(lane["believed_rpm"], tv.DEFAULT_FAMILY_RPM)

    def test_a_clean_family_never_recovers_past_its_start(self):
        tv.record_calls(self.st, "chart", tv.RECOVERY_CLEAN_CALLS, 1000.0)
        self.assertEqual(self.st["families"]["chart"]["believed_rpm"], tv.DEFAULT_FAMILY_RPM)


class CleanCredit(unittest.TestCase):
    def test_note_clean_credits_recovery_without_spending_budget(self):
        st = tv.blank_state()
        tv.note_clean(st, "chart", 5, 1000.0)
        self.assertEqual(st["families"]["chart"]["calls"], [])
        self.assertEqual(st["families"]["chart"]["clean_calls"], 5)

    def test_clean_credit_alone_can_drive_recovery(self):
        st = tv.blank_state()
        tv.note_limited(st, "scanner", 1000.0)
        halved = st["families"]["scanner"]["believed_rpm"]
        tv.note_clean(st, "scanner", tv.RECOVERY_CLEAN_CALLS,
                      1000.0 + tv.RECOVERY_QUIET_SECONDS + 1)
        self.assertEqual(st["families"]["scanner"]["believed_rpm"], halved + tv.RECOVERY_STEP)


class StateFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "nested", "state.json")

    def test_save_reports_failure_instead_of_raising(self):
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = tv.save_state(os.path.join(blocker, "s.json"), tv.blank_state())
        self.assertFalse(ok)
        self.assertIn("WARNING", err.getvalue())

    def test_round_trip(self):
        st = tv.blank_state()
        tv.note_limited(st, "scanner", 1000.0)
        self.assertTrue(tv.save_state(self.path, st))
        back = tv.load_state(self.path)
        self.assertEqual(back["families"]["scanner"]["believed_rpm"],
                         st["families"]["scanner"]["believed_rpm"])

    def test_missing_file_yields_defaults(self):
        self.assertEqual(tv.load_state(os.path.join(self.dir, "nope.json")),
                         tv.blank_state())

    def test_corrupt_file_yields_defaults_instead_of_crashing(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(tv.load_state(self.path)["global"]["believed_rpm"],
                         tv.DEFAULT_GLOBAL_RPM)

    def test_old_version_is_discarded(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 0, "families": {}}, f)
        self.assertEqual(tv.load_state(self.path), tv.blank_state())

    def test_garbage_values_are_coerced_rather_than_crashing(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": tv.STATE_VERSION, "families": {"scanner": {
                "believed_rpm": "lots", "cooldown_until": None, "clean_calls": [],
                "calls": [1.0, "nope", True, 3.0]}}}, f)
        st = tv.load_state(self.path)
        lane = st["families"]["scanner"]
        self.assertEqual(lane["believed_rpm"], tv.DEFAULT_FAMILY_RPM)
        self.assertEqual(lane["calls"], [1.0, 3.0])
        self.assertEqual(tv.wait_seconds(st, "scanner", 1, 1000.0), 0)

    def test_a_state_file_cannot_raise_the_ceiling_past_the_hard_bound(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": tv.STATE_VERSION,
                       "families": {"scanner": {"believed_rpm": 100000}}}, f)
        lane = tv.load_state(self.path)["families"]["scanner"]
        self.assertEqual(lane["believed_rpm"], tv.ABSOLUTE_MAX_RPM)
        self.assertLess(tv.effective_rpm(lane), tv.ABSOLUTE_MAX_RPM)

    def test_partial_state_is_backfilled(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": tv.STATE_VERSION,
                       "families": {"scanner": {"believed_rpm": 12}}}, f)
        st = tv.load_state(self.path)
        self.assertEqual(st["families"]["scanner"]["believed_rpm"], 12)
        self.assertEqual(st["families"]["chart"]["believed_rpm"], tv.DEFAULT_FAMILY_RPM)
        self.assertEqual(st["families"]["scanner"]["calls"], [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "state.json")

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, SCRIPT, "--state", self.state] + list(args),
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_status_reports_a_budget_below_the_hard_bound(self):
        rc, out, _ = self.run_cli("--json", "status")
        self.assertEqual(rc, 0)
        snap = json.loads(out)
        self.assertLess(snap["global"]["effective_rpm"], 100)
        for fam in snap["families"]:
            self.assertLess(fam["effective_rpm"], 100)

    def test_check_says_go_then_wait(self):
        rc, _, _ = self.run_cli("check", "--tool", "get_ohlcv")
        self.assertEqual(rc, 0)
        eff = tv.effective_rpm(tv.blank_state()["families"]["chart"])
        self.run_cli("record", "--tool", "get_ohlcv", "--calls", str(eff))
        rc, out, _ = self.run_cli("check", "--tool", "get_ohlcv")
        self.assertEqual(rc, 3)
        self.assertIn("WAIT", out)

    def test_oversized_batch_is_rejected_rather_than_waited_on(self):
        rc, out, _ = self.run_cli("check", "--tool", "get_ohlcv", "--calls", "500")
        self.assertEqual(rc, 4)
        self.assertIn("split", out)

    def test_observe_learns_from_a_refusal(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state", self.state, "--json",
             "observe", "--tool", "get_symbol_data", "-"],
            input='{"success":false,"rate_limited":true}', capture_output=True, text=True)
        self.assertEqual(proc.returncode, 3)
        res = json.loads(proc.stdout)
        self.assertTrue(res["rate_limited"])
        self.assertGreater(res["cooldown_s"], 0)
        # ...and the healthy family is untouched.
        rc, _, _ = self.run_cli("check", "--tool", "get_ohlcv")
        self.assertEqual(rc, 0)

    def test_observe_does_not_double_count_what_wait_already_spent(self):
        # The documented loop is `wait` -> call -> `observe`. If observe re-counted the call the
        # real budget would quietly halve.
        self.run_cli("wait", "--tool", "get_ohlcv")
        subprocess.run([sys.executable, SCRIPT, "--state", self.state,
                        "observe", "--tool", "get_ohlcv", "-"],
                       input='{"success":true}', capture_output=True, text=True)
        _, out, _ = self.run_cli("--json", "status")
        lane = [f for f in json.loads(out)["families"] if f["lane"] == "chart"][0]
        self.assertEqual(lane["used_last_60s"], 1)

    def test_observe_record_spends_a_call_made_without_wait(self):
        subprocess.run([sys.executable, SCRIPT, "--state", self.state,
                        "observe", "--tool", "get_ohlcv", "--record", "-"],
                       input='{"success":true}', capture_output=True, text=True)
        _, out, _ = self.run_cli("--json", "status")
        lane = [f for f in json.loads(out)["families"] if f["lane"] == "chart"][0]
        self.assertEqual(lane["used_last_60s"], 1)

    def test_unwritable_state_warns_instead_of_crashing(self):
        # A regular file where a directory has to be - unwritable even for root, unlike chmod.
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state", os.path.join(blocker, "state.json"),
             "check", "--tool", "get_ohlcv"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("WARNING", proc.stderr)
        self.assertIn("TV_THROTTLE_STATE", proc.stderr)

    def test_observe_accepts_a_response_file(self):
        p = os.path.join(self.dir, "resp.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"success":true}')
        rc, out, _ = self.run_cli("observe", "--tool", "get_ohlcv", p)
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)

    def test_set_limit_clamps_to_the_hard_bound(self):
        rc, out, _ = self.run_cli("--json", "set-limit", "--family", "scanner",
                                  "--per-minute", "5000", "--source", "test")
        self.assertEqual(rc, 0)
        res = json.loads(out)
        self.assertEqual(res["believed_rpm"], tv.ABSOLUTE_MAX_RPM)
        lane = [f for f in res["status"]["families"] if f["lane"] == "scanner"][0]
        self.assertLess(lane["effective_rpm"], tv.ABSOLUTE_MAX_RPM)
        self.assertEqual(lane["source"], "test")

    def test_set_limit_rejects_nonsense(self):
        rc, _, err = self.run_cli("set-limit", "--family", "scanner", "--per-minute", "0")
        self.assertNotEqual(rc, 0)
        self.assertIn("per-minute", err)
        rc, _, err = self.run_cli("set-limit", "--family", "nope", "--per-minute", "10")
        self.assertNotEqual(rc, 0)

    def test_unknown_family_is_rejected(self):
        rc, _, err = self.run_cli("check", "--family", "nope")
        self.assertNotEqual(rc, 0)
        self.assertIn("unknown family", err)

    def test_wait_gives_up_instead_of_blocking_the_run(self):
        eff = tv.effective_rpm(tv.blank_state()["families"]["chart"])
        self.run_cli("record", "--tool", "get_ohlcv", "--calls", str(eff))
        rc, out, _ = self.run_cli("wait", "--tool", "get_ohlcv", "--max-wait", "0")
        self.assertEqual(rc, 3)
        self.assertIn("ladder", out)

    def test_wait_records_by_default(self):
        rc, _, _ = self.run_cli("wait", "--tool", "get_ohlcv")
        self.assertEqual(rc, 0)
        _, out, _ = self.run_cli("--json", "status")
        lane = [f for f in json.loads(out)["families"] if f["lane"] == "chart"][0]
        self.assertEqual(lane["used_last_60s"], 1)

    def test_plan_estimates_a_batch(self):
        rc, out, _ = self.run_cli("--json", "plan", "--calls", "8", "--tool", "get_ohlcv")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["estimated_seconds"], 0.0)
        rc, out, _ = self.run_cli("--json", "plan", "--calls", "90", "--tool", "get_ohlcv")
        self.assertGreater(json.loads(out)["estimated_seconds"], 0)

    def test_reset_forgets_the_learned_ceiling(self):
        self.run_cli("limited", "--tool", "get_quote")
        self.run_cli("reset")
        _, out, _ = self.run_cli("--json", "status")
        lane = [f for f in json.loads(out)["families"] if f["lane"] == "scanner"][0]
        self.assertEqual(lane["believed_rpm"], tv.DEFAULT_FAMILY_RPM)
        self.assertEqual(lane["cooldown_s"], 0)


if __name__ == "__main__":
    unittest.main()
