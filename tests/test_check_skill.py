#!/usr/bin/env python3
"""
Regression tests for scripts/check_skill.py.

Each case builds a throwaway skill directory and asserts the checker either passes it or names
the one thing wrong with it - a checker that only ever says OK is worse than none.

  python -m unittest discover -s tests -v
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import check_skill as cs  # noqa: E402

GOOD = """---
name: demo-skill
description: >-
  Do a specific thing when the user asks for that thing, and say what it returns.
---

# demo-skill

Body text. See `references/guide.md` and run `scripts/tool.py`.
"""


class SkillDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="check-skill-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "demo-skill")
        os.makedirs(os.path.join(self.root, "references"))
        os.makedirs(os.path.join(self.root, "scripts"))
        for rel in ("references/guide.md", "scripts/tool.py"):
            with open(os.path.join(self.root, *rel.split("/")), "w", encoding="utf-8") as f:
                f.write("x")
        self.write(GOOD)

    def write(self, text, newline="\n"):
        with open(os.path.join(self.root, "SKILL.md"), "w", encoding="utf-8", newline=newline) as f:
            f.write(text)

    def findings(self, level=None):
        out = cs.check(self.root)
        return [m for lv, m in out if level is None or lv == level]

    def assertClean(self):
        self.assertEqual(cs.check(self.root), [])

    def assertError(self, fragment):
        errors = self.findings("ERROR")
        self.assertTrue(any(fragment in m for m in errors), "%r not in %s" % (fragment, errors))

    def assertWarn(self, fragment):
        warns = self.findings("WARN")
        self.assertTrue(any(fragment in m for m in warns), "%r not in %s" % (fragment, warns))


class Frontmatter(SkillDir):
    def test_a_conforming_skill_passes(self):
        self.assertClean()

    def test_a_missing_file_is_reported(self):
        os.remove(os.path.join(self.root, "SKILL.md"))
        self.assertError("SKILL.md is missing")

    def test_no_frontmatter(self):
        self.write("# demo-skill\n\nno frontmatter here\n")
        self.assertError("does not begin with a '---'")

    def test_unclosed_frontmatter(self):
        self.write("---\nname: demo-skill\ndescription: x\n\n# body\n")
        self.assertError("never closed")

    def test_a_name_that_is_not_a_slug(self):
        for bad in ("Demo_Skill", "demo skill", "demo--skill", "-demo", "DEMO"):
            self.write(GOOD.replace("name: demo-skill", "name: %s" % bad))
            self.assertError("must be lowercase letters")

    def test_a_name_over_the_length_cap(self):
        self.write(GOOD.replace("name: demo-skill", "name: %s" % ("a" * 65)))
        self.assertError("the limit is 64")

    def test_a_name_that_does_not_match_the_directory(self):
        self.write(GOOD.replace("name: demo-skill", "name: other-skill"))
        self.assertWarn("does not match the directory")

    def test_a_missing_description(self):
        self.write("---\nname: demo-skill\n---\n\n# body\n")
        self.assertError("no 'description'")

    def test_a_description_over_the_cap(self):
        long = "word " * 300
        self.write(GOOD.replace("Do a specific thing when the user asks for that thing, and say "
                                "what it returns.", long))
        self.assertError("the limit is 1024")

    def test_a_folded_description_is_measured_as_one_string(self):
        folded = GOOD.replace(
            "  Do a specific thing when the user asks for that thing, and say what it returns.\n",
            "  Do a specific thing when the user asks for it.\n  A second line of the same value.\n")
        self.write(folded)
        data, _, err = cs.load_frontmatter(folded)
        self.assertIsNone(err)
        self.assertEqual(data["description"],
                         "Do a specific thing when the user asks for it. "
                         "A second line of the same value.")
        self.assertClean()

    def test_an_unrecognised_key(self):
        self.write(GOOD.replace("name: demo-skill", "name: demo-skill\nauthor: someone"))
        self.assertWarn("unrecognised frontmatter key")

    def test_the_documented_optional_keys_are_accepted(self):
        self.write(GOOD.replace("name: demo-skill", "name: demo-skill\nlicense: MIT"))
        self.assertClean()


class Body(SkillDir):
    def test_an_angle_bracket_placeholder_is_an_error(self):
        self.write(GOOD.replace("Body text.", "Write it to <TICKER>-report.html."))
        self.assertError("reads as a markup tag")

    def test_a_real_html_tag_is_an_error_too(self):
        self.write(GOOD.replace("Body text.", 'Set <html lang="en"> on the root.'))
        self.assertError("reads as a markup tag")

    def test_a_brace_placeholder_is_fine(self):
        self.write(GOOD.replace("Body text.", "Write it to {TICKER}-report.html."))
        self.assertClean()

    def test_a_comparison_operator_is_not_a_tag(self):
        self.write(GOOD.replace("Body text.", "Grade it when EPS growth >= 25% and debt < 1.0."))
        self.assertClean()

    def test_an_unclosed_code_fence(self):
        self.write(GOOD + "\n```bash\npython scripts/tool.py\n")
        self.assertError("unclosed ``` code fence")

    def test_a_reference_that_does_not_exist(self):
        self.write(GOOD.replace("`references/guide.md`", "`references/missing.md`"))
        self.assertError("refers to references/missing.md")

    def test_a_long_body_is_a_warning_not_an_error(self):
        self.write(GOOD + "\n" + "filler words here and there. " * 1200)
        self.assertWarn("guidance: under 5000")
        self.assertEqual(self.findings("ERROR"), [])


class Encoding(SkillDir):
    def test_crlf_line_endings(self):
        self.write(GOOD, newline="\r\n")
        self.assertError("CRLF")

    def test_a_byte_order_mark(self):
        self.write("﻿" + GOOD)
        self.assertError("byte-order mark")

    def test_a_tab(self):
        self.write(GOOD.replace("  Do a specific", "\tDo a specific"))
        self.assertError("contains a tab")

    def test_a_control_character(self):
        self.write(GOOD.replace("Body text.", "Body\x07text."))
        self.assertError("control characters")

    def test_invalid_utf8_is_reported_not_raised(self):
        with open(os.path.join(self.root, "SKILL.md"), "wb") as f:
            f.write(b"---\nname: demo-skill\ndescription: x\n---\n\xff\xfe body\n")
        self.assertError("not valid UTF-8")


class Cli(SkillDir):
    def run_main(self, argv):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = cs.main(argv)
        return code, buf.getvalue()

    def test_a_clean_skill_exits_zero(self):
        code, out = self.run_main(["--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_an_error_exits_one(self):
        self.write(GOOD.replace("Body text.", "Write it to <TICKER>.html."))
        code, out = self.run_main(["--root", self.root])
        self.assertEqual(code, 1)
        self.assertIn("ERROR", out)

    def test_a_warning_passes_unless_strict(self):
        self.write(GOOD.replace("name: demo-skill", "name: other-skill"))
        self.assertEqual(self.run_main(["--root", self.root])[0], 0)
        self.assertEqual(self.run_main(["--root", self.root, "--strict"])[0], 1)

    def test_the_expected_name_can_be_given_explicitly(self):
        self.write(GOOD.replace("name: demo-skill", "name: other-skill"))
        code, _ = self.run_main(["--root", self.root, "--name", "other-skill", "--strict"])
        self.assertEqual(code, 0)


class ThisSkill(unittest.TestCase):
    """The repository's own SKILL.md has to pass, not just the fixtures."""

    def test_the_shipped_skill_conforms(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        errors = [m for level, m in cs.check(root) if level == "ERROR"]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
