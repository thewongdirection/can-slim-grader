#!/usr/bin/env python3
"""The shipped docs' structure, pinned: the workflow's numbering, and the update step that is gone.

WHY THIS EXISTS. SKILL.md is a procedure, and a procedure that points at a step that is not there
is a trap the run walks into at grade time. The data guide once said "(step 0)" after a renumber
and nothing noticed. So the numbering and every "step N" reference are checked here.

It also pins the removal of the skill's self-update pre-flight. This skill is distributed through
a marketplace, which owns updates; a skill that also rewrites its own files from a repo is two
updaters fighting over one directory. The feature came in on a branch that may still exist, and
a merge of it would put the step, the script and its guardrail back without a conflict to warn
anyone - so that fails a test here instead.

Offline, standard library, no pytest.
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import export_portable  # noqa: E402

SKILL = "SKILL.md"
GUIDE = os.path.join("references", "data-and-scoring-guide.md")

# The workflow's own headings: "### 3 — Resolve the ticker".
STEP_HEADING = re.compile(r"^### (\d+)\b", re.M)
# A prose pointer at one: "step 2", "Steps 1-2", "steps 1 and 4". Every number in the list is
# read, not just the first. \s rather than a literal space, so a non-breaking one cannot hide it.
STEP_REF = re.compile(
    r"\b[Ss]teps?\s+(\d+[a-z]?(?:\s*(?:-|\u2013|\u2014|,|&|and|or)\s*\d+[a-z]?)*)")
# What an item inside a step looks like when it is mistaken for a step: "1b".
LETTERED = re.compile(r"\d[a-z]\b")

# What the removed feature left behind: its name, its stamp file, and the phrases of the step and
# its guardrail ("run the newest published version", "Newest rules every run", the STATUS: table,
# the --apply flag). Anchored on those, so an unrelated "update" in the prose is not a hit; a
# merge that rewords the step still trips on its status table or its flag.
SELF_UPDATE = re.compile(
    r"self[_ -]?updat|\.skill-version|update-available|newest published|newest rules"
    r"|--apply\b|\bSTATUS: (?:current|updated|blocked|unknown)", re.I)
# Where shipped files live. Walked explicitly rather than from the root, so a generated
# can-slim-grader.portable.md or a local scratch file cannot fail the test - and tests/ is left
# out because this module has to spell the pattern it forbids.
SHIPPED_DIRS = ("references", "scripts", "assets")
SHIPPED_TOP_LEVEL = ("README.md", "SKILL.md", ".gitignore", "parity-manifest.json")


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def shipped_files():
    for rel in SHIPPED_TOP_LEVEL:
        yield rel
    for top in SHIPPED_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(names):
                if not name.endswith((".pyc", ".pyo")):
                    yield os.path.relpath(os.path.join(base, name), ROOT)


class WorkflowNumbering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = read(SKILL)
        cls.steps = [int(n) for n in STEP_HEADING.findall(cls.skill)]
        cls.docs = {SKILL: cls.skill, GUIDE: read(GUIDE),
                    "the portable bundle's HOST NOTES": export_portable.PREAMBLE}

    def test_steps_run_from_one_without_a_gap_or_a_repeat(self):
        self.assertTrue(self.steps, "SKILL.md has no '### N' workflow headings")
        self.assertEqual(self.steps, list(range(1, len(self.steps) + 1)))

    def test_every_step_reference_names_a_step_that_exists(self):
        for where, text in self.docs.items():
            for m in STEP_REF.finditer(text):
                for n in re.findall(r"\d+", m.group(1)):
                    self.assertIn(int(n), self.steps,
                                  "%s points at %r, but the workflow has steps %s"
                                  % (where, m.group(0), self.steps))

    def test_an_item_inside_a_step_is_not_called_a_step(self):
        # "step 1b" read as a sub-step of the freshness step when it meant an item of step 8.
        for where, text in self.docs.items():
            for m in STEP_REF.finditer(text):
                self.assertIsNone(LETTERED.search(m.group(1)),
                                  "%s says %r: steps have no lettered sub-steps - call it an "
                                  "'item' of the step it lives in" % (where, m.group(0)))


class SelfUpdateIsNotShipped(unittest.TestCase):
    def test_the_script_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "scripts", "self_update.py")))

    def test_nothing_shipped_refers_to_it(self):
        for rel in shipped_files():
            with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    self.assertIsNone(SELF_UPDATE.search(line),
                                      "%s:%d still refers to the removed self-update: %r"
                                      % (rel, n, line.strip()[:100]))

    def test_the_scan_covers_the_files_the_feature_lived_in(self):
        # A walk that silently found nothing would turn the test above into a pass for free.
        found = set(shipped_files())
        for rel in (SKILL, "README.md", ".gitignore", GUIDE,
                    os.path.join("scripts", "export_portable.py"),
                    os.path.join("scripts", "check_skill.py")):
            self.assertIn(rel, found)


if __name__ == "__main__":
    unittest.main()
