#!/usr/bin/env python3
"""Regression tests for scripts/export_portable.py.

The exporter keeps two hand-written lists - what it inlines, and what it says it left out - and a
sentence in the bundle telling the reader which is which. Nothing ties either list to the
repository, so a script that is deleted or renamed leaves a line in the bundle describing a file
that does not exist, and a script that is added is dropped without anyone deciding to. These tests
tie the lists to the tree.

Offline, standard library, no pytest.
"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import export_portable as ep  # noqa: E402

INCLUDED = [rel for rel, _ in ep.INCLUDE]
# The exporter is the one script that is neither bundled nor "omitted": it is what builds the bundle.
EXPORTER = "scripts/export_portable.py"


def exists(rel):
    return os.path.exists(os.path.join(ROOT, *rel.rstrip("/").split("/")))


class FileLists(unittest.TestCase):
    def test_every_included_file_exists(self):
        for rel in INCLUDED:
            self.assertTrue(exists(rel), "INCLUDE names %s, which is not in the repo" % rel)

    def test_every_omitted_path_exists(self):
        for rel in ep.OMITTED:
            self.assertTrue(exists(rel), "OMITTED names %s, which is not in the repo - the "
                                         "bundle would tell its reader about a file that "
                                         "was never there" % rel)

    def test_nothing_is_both_included_and_omitted(self):
        self.assertEqual(set(INCLUDED) & set(ep.OMITTED), set())

    def test_every_script_is_either_bundled_or_deliberately_left_out(self):
        scripts = sorted("scripts/" + n for n in os.listdir(os.path.join(ROOT, "scripts"))
                         if n.endswith(".py"))
        decided = set(INCLUDED) | set(ep.OMITTED) | {EXPORTER}
        undecided = [s for s in scripts if s not in decided]
        self.assertEqual(undecided, [], "new script(s) neither in INCLUDE nor OMITTED: add each "
                                        "to one list on purpose")


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = ep.build(ROOT)

    def test_each_included_file_is_inlined_under_its_own_header(self):
        for rel in INCLUDED:
            self.assertIn("# FILE: `%s`\n" % rel, self.text)

    def test_the_omitted_paths_are_listed_for_the_reader(self):
        listing = next(ln for ln in self.text.splitlines() if ln.startswith("Omitted"))
        for rel in ep.OMITTED:
            self.assertIn("`%s`" % rel, listing)

    def test_an_omitted_script_is_not_inlined(self):
        for rel in ep.OMITTED:
            self.assertNotIn("# FILE: `%s`" % rel, self.text)


class Zip(unittest.TestCase):
    def test_the_zip_holds_the_bundled_files_and_nothing_omitted(self):
        tmp = tempfile.mkdtemp(prefix="export-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "bundle.portable.md")
        with mock.patch.object(sys, "argv", ["export_portable.py", "--out", out, "--zip"]), \
                mock.patch("sys.stdout"):
            ep.main()
        with zipfile.ZipFile(os.path.join(tmp, "bundle.zip")) as z:
            names = set(z.namelist())
        self.assertEqual(names, {"can-slim-grader/" + rel for rel in INCLUDED}
                         | {"can-slim-grader/PORTABLE-README.md"})


if __name__ == "__main__":
    unittest.main()
