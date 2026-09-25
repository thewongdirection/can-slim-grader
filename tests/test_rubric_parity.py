#!/usr/bin/env python3
"""
Parity between can-slim-grader and can-slim-recommend, over 100 real tickers.

WHAT THIS GUARDS. The two skills are one methodology aimed at two questions, and the only thing
that had been keeping their rules in step was prose in two repositories. Prose drifts silently: a
2026-09 comparison found the SHARED methodology file carrying different score bands on each side,
so the same 3.0 total read "watch" in one skill and "pass on it" in the other. This test makes that
class of drift fail a build instead of reaching a report.

HOW. tests/fixtures/screener_100.json holds 100 real TradingView screener rows. Every row is graded
by scripts/rubric.py (this repo's executable statement of the shared thresholds). When the sister
repo is also on disk, the SAME rows are graded by ITS OWN sector_screen.py and the letter ceilings
are required to match row for row - a live differential test against the other implementation, not
against a copy of it.

Point the sister check at a clone with:  CANSLIM_SISTER=/path/to/can-slim-recommend
Without it, the fixture-only assertions still run, so this is never silently a no-op: the test
reports which mode it ran in.

RUN IT ON EVERY UPGRADE. It is an ordinary unittest, so `python3 -m unittest discover -s tests`
covers it, and that command is the repo's own pre-commit gate.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rubric  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "screener_100.json")

# A sister clone, if one is on disk. Checked in this order; the env var wins.
SISTER_CANDIDATES = [
    os.environ.get("CANSLIM_SISTER"),
    "/home/user/thewongdirection/can-slim-recommend",
    os.path.join(os.path.dirname(ROOT), "can-slim-recommend"),
]


def load_rows():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)["rows"]


def find_sister():
    for p in SISTER_CANDIDATES:
        if p and os.path.isfile(os.path.join(p, "scripts", "sector_screen.py")):
            return p
    return None


class Fixture(unittest.TestCase):
    def test_the_fixture_is_a_hundred_real_rows(self):
        rows = load_rows()
        self.assertEqual(len(rows), 100)
        # Real data, not placeholders: prices and 52-week highs must actually be there.
        priced = [r for r in rows if r.get("close") and r.get("price_52_week_high")]
        self.assertGreaterEqual(len(priced), 90, "fixture has too few usable rows to be a test")

    def test_the_fixture_carries_no_grades(self):
        # If expected grades were baked in, this test would be checking its own homework.
        blob = json.dumps(load_rows())
        for word in ('"pass"', '"partial"', '"fail"', '"caps"'):
            self.assertNotIn(word, blob, "the fixture must hold inputs only, not verdicts")


class Thresholds(unittest.TestCase):
    """The numbers themselves, pinned so a future edit cannot move one side quietly."""

    def test_n_fail_is_twice_the_pivot_band(self):
        self.assertEqual(rubric.N_FAIL_PCT, 2 * rubric.PIVOT_BAND_PCT)

    def test_triage_is_looser_than_the_letter(self):
        # Dropping a name from a screen and failing its N are different decisions.
        self.assertGreater(rubric.TRIAGE_DROP_PCT, rubric.N_FAIL_PCT)

    def test_weights_and_bands(self):
        self.assertEqual(rubric.WEIGHT, {"pass": 1.0, "partial": 0.5, "fail": 0.0})
        self.assertEqual(rubric.total(dict.fromkeys("CANSLIM", "pass")), 7.0)
        self.assertEqual(rubric.total(dict.fromkeys("CANSLIM", "partial")), 3.5)
        self.assertEqual(rubric.total(dict.fromkeys("CANSLIM", "fail")), 0.0)

    def test_the_band_seam_is_at_three_and_a_half(self):
        # The exact drift that was found: 3.0 must not read as "watch".
        self.assertIn("pass on it", rubric.band(3.0))
        self.assertIn("watch", rubric.band(3.5))
        self.assertIn("qualifies", rubric.band(4.5))

    def test_total_demands_all_seven_letters(self):
        with self.assertRaises(ValueError):
            rubric.total({"C": "pass"})


class Rubric(unittest.TestCase):
    def test_every_row_grades_without_raising(self):
        for r in load_rows():
            out = rubric.score_row(r, bench_perf=19.92)   # SPY Perf.6M on the fixture's date
            self.assertEqual(set(out["caps"]), {"N", "S", "L"})
            for k, v in out["caps"].items():
                self.assertIn(v, rubric.WEIGHT, "%s %s" % (out["symbol"], k))

    def test_n_tracks_distance_below_the_high_monotonically(self):
        order = {"pass": 2, "partial": 1, "fail": 0}
        prev = 2
        for off in (0, -5, -9.9, -10.1, -15, -19.9, -20.1, -40):
            cur = order[rubric.cap_n(off)[0]]
            self.assertLessEqual(cur, prev, "N got kinder at %.1f%% off the high" % off)
            prev = cur

    def test_the_price_and_liquidity_floors_cap_s(self):
        # Found by this very test: the sister DROPS a sub-$15 name, this repo had no price rule at
        # all, so the same stock was refused by one skill and graded by the other.
        self.assertEqual(rubric.cap_s(1.5, 10.0, close=4.0)[0], "fail")
        self.assertEqual(rubric.cap_s(1.5, 10.0, close=40.0, dollar_vol=2e6)[0], "fail")
        self.assertEqual(rubric.cap_s(1.5, 10.0, close=40.0, dollar_vol=500e6)[0], "pass")

    def test_the_floors_outrank_a_pretty_volume_pattern(self):
        # A $4 stock cannot be accumulated by a fund however good its relative volume looks.
        grade, why = rubric.cap_s(3.0, 50.0, close=4.0, dollar_vol=900e6)
        self.assertEqual(grade, "fail")
        self.assertIn("price floor", why)

    def test_missing_inputs_never_invent_a_failure(self):
        # A blank field is not evidence of a breach; it must leave the letter unbounded.
        self.assertEqual(rubric.cap_n(None)[0], "pass")
        self.assertEqual(rubric.cap_s(None)[0], "pass")
        self.assertEqual(rubric.cap_l(None)[0], "pass")


class SisterParity(unittest.TestCase):
    """The differential test: this repo's thresholds vs the sister's own implementation."""

    @classmethod
    def setUpClass(cls):
        cls.sister = find_sister()
        if not cls.sister:
            return
        sys.path.insert(0, os.path.join(cls.sister, "scripts"))
        import sector_screen                      # noqa: E402
        cls.ss = sector_screen

    def setUp(self):
        if not self.sister:
            self.skipTest("no can-slim-recommend clone found; set CANSLIM_SISTER to enable "
                          "the live differential test (fixture assertions still ran)")

    def test_the_shared_constants_match(self):
        d = self.ss.DEFAULTS
        self.assertEqual(d["pivot_band"], rubric.PIVOT_BAND_PCT)
        self.assertEqual(d["max_off_high"], rubric.TRIAGE_DROP_PCT)
        self.assertEqual(d["thin_vol"], rubric.THIN_VOL)
        self.assertEqual(d["min_rs"], rubric.MIN_RS)
        self.assertEqual(d["threshold"], rubric.QUALIFY_THRESHOLD)
        self.assertEqual(self.ss.WEIGHT, rubric.WEIGHT)

    def test_the_two_skills_agree_on_all_one_hundred_rows(self):
        """Compare the right layers.

        The skills place two rules differently BY DESIGN: the screener disqualifies a laggard or a
        sub-200-day name at TRIAGE (it never reaches a letter), while this repo, which grades one
        named ticker someone already asked about, has to express the same rule as a letter that
        FAILS. So the comparison is in two parts:
          - a row the sister DROPS must also be refused here (some letter fails, or triage agrees);
          - among the rows the sister keeps, every letter ceiling must match exactly.
        Collapsing those into one comparison is what made an earlier version of this test report 20
        "disagreements" that were nothing of the kind.
        """
        bench, window = 19.92, "Perf.6M"
        dropped_but_not_refused, letter_conflicts = [], []
        kept = 0
        for row in load_rows():
            mine = rubric.score_row(row, bench_perf=bench, window=window)
            theirs = self.ss.score_row(dict(row), window, bench, self.ss.DEFAULTS)
            if theirs.get("triage") == "drop":
                refused = ("fail" in mine["caps"].values()) or mine["triage_drop"]
                if not refused:
                    dropped_but_not_refused.append(
                        "%s: recommend dropped it (%s) but grader caps are %s"
                        % (mine["symbol"], "; ".join(theirs.get("drop_reasons") or []),
                           mine["caps"]))
                continue
            kept += 1
            caps = (self.ss.ceiling(theirs, self.ss.DEFAULTS, {}) or {}).get("ceiling_caps") or {}
            # L's ceiling needs a sector rank the fixture has no sweep for.
            for letter in ("N", "S"):
                if letter in caps and caps[letter] != mine["caps"][letter]:
                    letter_conflicts.append(
                        "%s %s: grader=%s recommend=%s (off_high=%.1f rv=%s)"
                        % (mine["symbol"], letter, mine["caps"][letter], caps[letter],
                           mine["off_high_pct"] or 0, row.get("relative_volume_10d_calc")))
        self.assertEqual(dropped_but_not_refused, [],
                         "the sister refuses these names and this repo does not:\n  "
                         + "\n  ".join(dropped_but_not_refused[:20]))
        self.assertEqual(letter_conflicts, [],
                         "same inputs, different letter ceiling:\n  "
                         + "\n  ".join(letter_conflicts[:20]))
        self.assertGreater(kept, 0, "every fixture row was dropped - the comparison proved nothing")

    def test_the_derived_metrics_agree(self):
        for row in load_rows()[:40]:
            mine = rubric.score_row(row, bench_perf=19.92)
            theirs = self.ss.score_row(dict(row), "Perf.6M", 19.92, self.ss.DEFAULTS)
            for key in ("off_high_pct", "vs_ema50_pct", "vs_ema200_pct", "rs_vs_bench_pts"):
                a, b = mine.get(key), theirs.get(key)
                if a is None or b is None:
                    self.assertEqual(a is None, b is None, "%s %s" % (mine["symbol"], key))
                else:
                    self.assertAlmostEqual(a, b, places=6, msg="%s %s" % (mine["symbol"], key))


if __name__ == "__main__":
    unittest.main(verbosity=2)
