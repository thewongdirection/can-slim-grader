#!/usr/bin/env python3
"""The docs' source-sourcing policy for `securities-filings-lookup`, pinned.

WHY THIS EXISTS. This skill's source ladder puts SEC filings above every vendor for C and A, and
`securities-filings-lookup` is the only thing in the kit that reaches them - so it is part of the
data sourcing, not a companion nicety, and a reader who does not install it grades C/A off
vendor-derived fields without being told. That instruction lives only in prose, and prose drifts.

It also pins the opposite direction, which is the sharper half. The same skill is keyed on the
TICKER'S OWN CIK, so pointing it at I returns the 13Fs that company FILED - what it owns - rather
than who owns it. NVDA's CIK carries eleven 13F-HRs listing Coherent, CoreWeave, Intel, Nebius,
Nokia and Synopsys. That is a *plausible wrong answer*, which is worse than an empty one, and the
docs said to use it for I until this was fixed. A sentence that offers it as an I source again
must fail a test rather than quietly misgrade a letter.

Offline, standard library, no pytest.
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = "SKILL.md"
GUIDE = os.path.join("references", "data-and-scoring-guide.md")
NAME = "securities-filings-lookup"
REPO = "https://github.com/thewongdirection/" + NAME

# Terms that mean "this sentence is talking about where I comes from".
I_SOURCING = re.compile(r"13F|Form 4|sponsorship|institutional ownership|grade \*?\*?I\*?\*?\b",
                        re.I)
# The skill may be named alongside them only to rule it out - and the negation has to sit NEXT to
# the mention, not merely somewhere in the same sentence. Mutation-testing caught that: restoring
# the old "take it from 13F/Form 4 (FMP form13F, securities-filings-lookup)" line passed a
# sentence-wide check, because "TradingView cannot answer" earlier in the same sentence satisfied
# it. The window is what makes this test able to fail.
NEGATION = re.compile(r"\bnot\b|\bcannot\b|\bcan't\b|\bnever\b|\binstead\b|\bwrong\b", re.I)
NEAR = 48  # characters either side of the mention


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    """Markdown wraps at ~100 columns and gets reflowed; assert against the words, not the lines."""
    return " ".join(text.split())


def sentences(text):
    """Claim-sized chunks of Markdown prose, one assertion at a time.

    A table ROW is its own claim. Table rows carry no full stops, so flattening a table into one
    chunk puts every row in the same blob - which let the C/A row's legitimate mention of the
    filings skill sit beside the I row and mask exactly the pairing this module tests for.
    """
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.split("\n")
        rows = [ln for ln in lines if ln.lstrip().startswith("|")]
        out.extend(flat(r) for r in rows)
        rest = flat("\n".join(ln for ln in lines if not ln.lstrip().startswith("|")))
        out.extend(s for s in re.split(r"(?<=[.!?])\s+", rest) if s)
    return out


class FilingsLookupIsPartOfTheDataSourcing(unittest.TestCase):
    """It is how the top of the C/A ladder is reached, so the docs have to say to install it."""

    @classmethod
    def setUpClass(cls):
        cls.skill, cls.guide = flat(read(SKILL)), flat(read(GUIDE))

    def test_skill_md_calls_it_data_sourcing_rather_than_an_optional_companion(self):
        self.assertRegex(self.skill, NAME + r"` is part of this skill's data sourcing")
        self.assertRegex(self.skill, r"part of this skill's data sourcing, not an optional")

    def test_the_guide_says_the_same_and_gives_the_repo_to_install_from(self):
        self.assertRegex(self.guide, r"### `" + NAME + r"` is part of this skill's data sourcing")
        self.assertIn(REPO, self.guide)
        self.assertIn(REPO, self.skill)

    def test_it_is_named_the_primary_source_for_C_and_A(self):
        for doc in (self.skill, self.guide):
            self.assertRegex(doc, r"primary source for\s+\*?\*?C\s*(?:/|and)\s*\*?\*?A")

    def test_a_missing_install_is_reported_rather_than_silently_absorbed(self):
        # The failure mode this guards: grading C/A off a vendor field and saying nothing.
        self.assertRegex(self.skill, r"if it is\s+missing, say so")
        self.assertRegex(self.guide, r"If it is not installed, say so")
        self.assertRegex(self.guide, r"vendor-derived")


class FilingsLookupIsNeverOfferedAsAnISource(unittest.TestCase):
    """Keyed on the ticker's OWN CIK, it answers "what does this company own", not "who owns it"."""

    @classmethod
    def setUpClass(cls):
        cls.raw = {SKILL: read(SKILL), GUIDE: read(GUIDE)}          # sentences() needs the lines
        cls.docs = dict((k, flat(v)) for k, v in cls.raw.items())   # regexes want the words

    def test_no_sentence_pairs_it_with_13F_or_sponsorship_without_ruling_it_out(self):
        for rel, text in self.raw.items():
            for s in sentences(text):
                if NAME not in s or not I_SOURCING.search(s):
                    continue
                for m in re.finditer(re.escape(NAME), s):
                    window = s[max(0, m.start() - NEAR):m.end() + NEAR]
                    self.assertRegex(
                        window, NEGATION,
                        "%s names %s as a source in an I-sourcing sentence: %r"
                        % (rel, NAME, s))

    def test_the_trap_is_explained_with_its_evidence(self):
        guide = self.docs[GUIDE]
        self.assertRegex(guide, r"cannot grade I")
        self.assertRegex(guide, r"own\*?\*? CIK")          # the mechanism
        self.assertRegex(guide, r"NVDA'?s CIK carries eleven 13F-HRs")   # the checked example
        self.assertRegex(guide, r"not who owns NVIDIA")    # the direction that matters

    def test_I_is_sourced_by_aggregating_across_filers(self):
        for rel, text in self.docs.items():
            self.assertRegex(text, r"[Aa]ggregat\w+ across (?:every )?filer",
                             "%s does not say I is an aggregation across filers" % rel)


class TheLadderDoesNotContradictItself(unittest.TestCase):
    """The table put filings first for C/A while the numbered fallbacks ranked the skill 7th."""

    @classmethod
    def setUpClass(cls):
        cls.guide = read(GUIDE)

    def test_the_table_still_puts_sec_filings_first_for_C_and_A(self):
        row = re.search(r"^\|\s*\*\*C, A\*\*.*$", self.guide, re.M)
        self.assertIsNotNone(row, "the C/A row of the source-priority table has moved")
        first = row.group(0).split("|")[2]
        self.assertIn("SEC filings", first)
        self.assertIn(NAME, first)

    def test_it_is_not_also_ranked_among_the_fallbacks(self):
        body = self.guide[self.guide.index("the fallbacks below the top three:"):]
        numbered = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", body, re.M)
        self.assertTrue(numbered, "the numbered fallback ladder has moved")
        for entry in numbered:
            self.assertNotIn(NAME, entry,
                             "%s is ranked as fallback %r while the table puts it first"
                             % (NAME, entry))


if __name__ == "__main__":
    unittest.main()
