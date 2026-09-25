#!/usr/bin/env python3
"""
Regression tests for scripts/chart_data.py, focused on the daily/weekly interval presets.

Run with:  python3 -m unittest discover -s tests
Pure standard library. The weekly preset exists so a run covering several tickers can still show a
correctly-seeded long-term average; these pin that it moves the window, BOTH averages, the volume
average, the labels and the staleness rule together - a half-applied preset is the failure that
would ship a weekly chart labelled as a daily one.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chart_data as cd  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "chart_data.py")


def bars(n, step_days=1, start=100.0):
    """n synthetic rows [t,o,h,l,c,v] ending today, so staleness is never incidentally tripped."""
    today = dt.date.today()
    out = []
    for i in range(n):
        d = today - dt.timedelta(days=step_days * (n - 1 - i))
        c = start + i
        out.append([d.strftime("%Y-%m-%d"), c - 0.5, c + 1.0, c - 1.0, c, 1000 + i])
    return out


class Presets(unittest.TestCase):
    def test_the_two_intervals_are_fully_specified(self):
        keys = {"window", "periods", "vol_window", "stale_after", "unit", "ma_unit", "pull_hint"}
        for name, spec in cd.INTERVALS.items():
            self.assertEqual(set(spec), keys, name)

    def test_daily_defaults_are_unchanged(self):
        d = cd.INTERVALS["daily"]
        self.assertEqual((d["window"], d["periods"], d["vol_window"], d["stale_after"]),
                         (300, (50, 200), 50, 4))

    def test_weekly_is_the_canonical_10_40(self):
        # 40 weeks ~= 200 sessions: the weekly chart measures the same thing at lower resolution.
        w = cd.INTERVALS["weekly"]
        self.assertEqual(w["periods"], (10, 40))
        self.assertEqual(w["window"], 150)
        self.assertEqual(w["vol_window"], 10)

    def test_weekly_tolerates_a_bar_days_old(self):
        # A weekly bar is routinely several days old; the daily 4-day rule would warn every run.
        self.assertGreater(cd.INTERVALS["weekly"]["stale_after"],
                           cd.INTERVALS["daily"]["stale_after"])
        self.assertGreaterEqual(cd.INTERVALS["weekly"]["stale_after"], 7)


class Build(unittest.TestCase):
    def test_daily_labels_and_volume_window(self):
        out, _ = cd.build(bars(400), 300, "ema", (50, 200), [], None)
        self.assertTrue(out["windowLabel"].endswith("sessions"), out["windowLabel"])
        self.assertEqual(out["avgVolLabel"], "50-day average")
        self.assertNotIn("emaLabels", out)      # 50/200 daily is the default, needs no relabelling

    def test_weekly_labels_name_their_unit(self):
        out, _ = cd.build(bars(200, step_days=7), 150, "ema", (10, 40), [], None,
                          interval="weekly")
        self.assertTrue(out["windowLabel"].endswith("weeks"), out["windowLabel"])
        self.assertEqual(out["emaLabels"], ["10-week EMA", "40-week EMA"])
        self.assertEqual(out["avgVolLabel"], "10-week average")

    def test_weekly_volume_average_is_ten_bars_not_fifty(self):
        # Mislabelling a 50-week average as a 10-week one would silently change what the dashed
        # line means, and the report quotes that line.
        rows = bars(200, step_days=7)
        weekly, _ = cd.build(rows, 150, "ema", (10, 40), [], None, interval="weekly")
        daily, _ = cd.build(rows, 150, "ema", (50, 200), [], None, interval="daily")
        self.assertEqual(weekly["avgVol"], cd.avg_volume([r[5] for r in rows], 10))
        self.assertEqual(daily["avgVol"], cd.avg_volume([r[5] for r in rows], 50))
        self.assertNotEqual(weekly["avgVol"], daily["avgVol"])

    def test_weekly_seeds_its_slow_average_on_far_fewer_bars(self):
        # The whole point: ~200 weekly bars span the chart where ~500 daily bars would be needed.
        out, warns = cd.build(bars(200, step_days=7), 150, "ema", (10, 40), [], None,
                              interval="weekly")
        self.assertEqual(len(out["bars"]), 150)
        self.assertTrue(all(x is not None for x in out["ema200"]),
                        "the 40-week line should span the whole window")
        self.assertFalse([w for w in warns if "starts partway" in w], warns)

    def test_a_thin_weekly_series_still_warns(self):
        _, warns = cd.build(bars(60, step_days=7), 150, "ema", (10, 40), [], None,
                            interval="weekly")
        self.assertTrue([w for w in warns if "partway" in w or "never seeds" in w], warns)

    def test_the_seed_warning_names_the_right_pull(self):
        _, warns = cd.build(bars(60, step_days=7), 150, "ema", (10, 40), [], None,
                            interval="weekly")
        self.assertTrue([w for w in warns if '1W' in w], warns)
        _, dwarns = cd.build(bars(60), 300, "ema", (50, 200), [], None)
        self.assertTrue([w for w in dwarns if '1D' in w], dwarns)

    def test_daily_output_shape_is_unchanged_by_the_new_argument(self):
        rows = bars(400)
        a, _ = cd.build(rows, 300, "ema", (50, 200), [], None)
        b, _ = cd.build(rows, 300, "ema", (50, 200), [], None, interval="daily")
        self.assertEqual(a, b)


class Cli(unittest.TestCase):
    def run_cli(self, payload, *args):
        p = subprocess.run([sys.executable, SCRIPT] + list(args),
                           input=json.dumps({"bars": payload}), capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def test_interval_weekly_sets_the_whole_preset(self):
        rc, out, _ = self.run_cli(bars(200, step_days=7), "--interval", "weekly")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(len(d["bars"]), 150)
        self.assertEqual(d["emaLabels"], ["10-week EMA", "40-week EMA"])
        self.assertEqual(d["avgVolLabel"], "10-week average")

    def test_default_is_still_daily(self):
        rc, out, _ = self.run_cli(bars(400))
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(len(d["bars"]), 300)
        self.assertEqual(d["avgVolLabel"], "50-day average")

    def test_explicit_flags_still_override_the_preset(self):
        rc, out, _ = self.run_cli(bars(200, step_days=7), "--interval", "weekly",
                                  "--window", "80", "--periods", "5,20", "--vol-window", "4")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(len(d["bars"]), 80)
        self.assertEqual(d["emaLabels"], ["5-week EMA", "20-week EMA"])
        self.assertEqual(d["avgVolLabel"], "4-week average")

    def test_months_is_refused_for_weekly(self):
        # --months counts trading sessions; silently applying it to weeks would be a 5x error.
        rc, _, err = self.run_cli(bars(200, step_days=7), "--interval", "weekly", "--months", "6")
        self.assertNotEqual(rc, 0)
        self.assertIn("only applies to --interval daily", err)

    def test_unknown_interval_is_rejected(self):
        rc, _, err = self.run_cli(bars(50), "--interval", "monthly")
        self.assertNotEqual(rc, 0)
        self.assertIn("invalid choice", err)


if __name__ == "__main__":
    unittest.main()
