#!/usr/bin/env python3
"""
Regression tests for scripts/html_to_pdf.py.

Run them all with:  python3 -m unittest discover -s tests -v
Pure standard library, and no browser: these pin the file:// URL construction, which is where a
wrong answer is silent - the engine renders its own error page into the PDF and still exits 0, so
the run produces a plausible-looking file with none of the report in it.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import html_to_pdf as htp  # noqa: E402


class FileUrl(unittest.TestCase):
    def test_posix_path_gets_exactly_three_slashes(self):
        # "file:///" + an already-absolute POSIX path produced file:////home/... - four.
        url = htp._file_url("/home/user/report.html")
        self.assertTrue(url.startswith("file:///"), url)
        self.assertFalse(url.startswith("file:////"), url)
        self.assertEqual(url, "file:///home/user/report.html")

    def test_a_hash_in_the_path_is_encoded_not_treated_as_a_fragment(self):
        # The failure that matters: unencoded, everything after # is a fragment, the browser asks
        # for a file that does not exist and renders its own error page into the PDF.
        url = htp._file_url("/tmp/Reports #2/NVDA.html")
        self.assertIn("%23", url)
        self.assertNotIn("#", url)

    def test_a_question_mark_is_encoded_not_treated_as_a_query(self):
        url = htp._file_url("/tmp/a?b/NVDA.html")
        self.assertIn("%3F", url)
        self.assertNotIn("?", url)

    def test_spaces_are_percent_encoded(self):
        self.assertEqual(htp._file_url("/home/user/My Reports/NVDA canslim.html"),
                         "file:///home/user/My%20Reports/NVDA%20canslim.html")

    def test_relative_paths_are_resolved_against_the_cwd(self):
        url = htp._file_url("NVDA-canslim.html")
        self.assertEqual(url, Path(os.path.abspath("NVDA-canslim.html")).as_uri())
        self.assertIn(Path(os.getcwd()).as_uri(), url)

    def test_round_trips_back_to_the_original_path(self):
        for p in ("/tmp/plain.html", "/tmp/with space.html", "/tmp/with#hash.html",
                  "/tmp/with?query.html", "/tmp/a+b/c%d.html"):
            with self.subTest(p=p):
                self.assertEqual(os.path.abspath(p), url2path(htp._file_url(p)))

    def test_both_engines_build_the_url_the_same_way(self):
        # via_chrome and via_playwright each built the URL inline and drifted apart trivially;
        # they now share one helper. Pin that neither open-codes it again.
        import inspect
        for fn in (htp.via_chrome, htp.via_playwright):
            src = inspect.getsource(fn)
            self.assertIn("_file_url(", src, fn.__name__)
            self.assertNotIn('"file:///"', src, fn.__name__)


def url2path(url):
    from urllib.parse import urlparse, unquote
    return unquote(urlparse(url).path)


if __name__ == "__main__":
    unittest.main()
