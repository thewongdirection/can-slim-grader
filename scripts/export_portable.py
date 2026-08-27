#!/usr/bin/env python3
"""
export_portable.py - bundle this skill into ONE self-contained Markdown file that a
non-Claude model (Gemini, ChatGPT, a local model, ...) can be handed as context.

The skill is written for Claude Code: it names MCP connectors, `ToolSearch`, and a file
layout. None of that exists on another host, but almost all of the content is portable -
the methodology, the thresholds, the scoring rubric, the freshness rules, the two pure-
stdlib scripts and the self-auditing HTML report. So the bundle inlines every file
verbatim and puts a HOST NOTES preamble in front that says what the host has to supply
and how to read the Claude-specific parts.

Verbatim matters: the bundle is generated from the real files, so regenerating after a
change keeps the portable copy honest instead of letting it drift into a stale fork.

  python scripts/export_portable.py                       # -> can-slim-grader.portable.md
  python scripts/export_portable.py --out /tmp/bundle.md
  python scripts/export_portable.py --zip                 # also write can-slim-grader.zip

Pure standard library.
"""
import argparse
import datetime as _dt
import hashlib
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Order matters: rules first, then the data guide, then the machinery.
INCLUDE = [
    ("SKILL.md", "The skill itself - activation, workflow, guardrails"),
    ("references/canslim-methodology.md", "The CAN SLIM rule set every grade is measured against"),
    ("references/data-and-scoring-guide.md", "What data to gather, from where, and the pass/partial/fail rubric"),
    ("scripts/relative_strength.py", "RS proxy, % off 52-week high, base metrics, breakout volume"),
    ("scripts/chart_data.py", "Builds the report's candlestick/EMA/volume block from daily bars"),
    ("scripts/html_to_pdf.py", "Renders the filled HTML to an A4 PDF (optional - engine-dependent)"),
    ("assets/evaluation_template.html", "The report itself: fill CONFIG, it renders and audits itself"),
]
# Repo housekeeping that means nothing outside this git repo.
OMITTED = ["scripts/check_parity.py", "parity-manifest.json", "README.md"]

PREAMBLE = '''# can-slim-grader - portable bundle

Grade ONE stock ticker against the CAN SLIM growth-investing model and return a
letter-by-letter (C-A-N-S-L-I-M) scorecard with a BUY-RANGE / WATCH / AVOID verdict, as a
self-auditing HTML report (A4 PDF when the host can print it).

This is a **single-file export of a Claude skill**, for use with any other model that can hold
it in context. Everything below is inlined verbatim from the source repo:
<https://github.com/thewongdirection/can-slim-grader>

---

## HOST NOTES - read this first if you are not Claude Code

The skill was written for Claude Code with MCP data connectors. The *method* is
host-independent; only the plumbing changes. Read the rest of this file with these
substitutions in mind.

### 1. What your host has to provide

| Need | Used for | If you don't have it |
|---|---|---|
| **Daily OHLCV bars, >=500 of them**, for the ticker | the chart (300 shown) + RS + breakout volume | Fewer bars still work; the 200-day EMA just starts partway across the chart and the script says so |
| **Daily bars for a benchmark** (SPY or equivalent) | the relative-strength proxy, and market direction (M) | M cannot be graded properly - say so rather than guessing |
| **Weekly bars, ~2 years** | base depth/length for N | Base metrics are skipped |
| **52-week high/low** | % off high, the pivot test in N | Derive from the daily bars you pulled |
| **Quarterly EPS & revenue with YoY, last ~8 quarters** | C, and whether growth is accelerating | Grade C from the company's own earnings releases |
| **Annual EPS/revenue, last ~5 years; ROE, margins, debt** | A | Same - annual reports / filings |
| **Institutional ownership** | I | Mark I as unavailable in `dataStatus` and say so; do not invent it |
| **A Python 3 runtime** | the two scripts below (pure stdlib, no pip installs) | Do the arithmetic yourself, but state that you did |
| **Ability to write a file** | the HTML report | Output the same scorecard as text, and say the report was not produced |
| **A PDF engine** (headless Chrome, WeasyPrint, wkhtmltopdf) | the A4 PDF | Hand over the HTML instead and say why |

Any market-data API works - the scripts take bars as either `[timestamp, open, high, low,
close, volume]` rows or `{"t":..,"o":..,"h":..,"l":..,"c":..,"v":..}` dicts, so most providers'
output can be piped in unedited. **Never retype bars by hand**; that is where a grade quietly
acquires a typo.

### 2. Claude-specific things you will read below, and what they mean for you

- **"MCP", "connector", "`ToolSearch`", "deferred tools"** - Claude Code's way of loading data
  tools. Substitute whatever data source you have; the table above lists what is actually needed.
- **Named connectors** (`Trading_View`, `Interactive_Brokers_IBKR`, `Massive_Market_Data`, `FMP`,
  Daloopa, bigdata.com, LSEG) - a preference ladder, not a requirement. Keep the *ordering
  principle*: audited/structured financial data beats generic web search, and say which source
  each figure came from.
- **`securities-filings-lookup`, `ibkr-review-ticker`, `can-slim-recommend`** - sibling Claude
  skills. Ignore the references; go to the filings or the web directly.
- **File paths** (`scripts/...`, `assets/...`, `references/...`) - the sections of this file.
- **The parity section at the end of SKILL.md** - housekeeping for keeping two sibling repos in
  sync. Irrelevant here; skip it.

### 3. The parts that are NOT optional

These are what make the output trustworthy rather than a plausible-sounding opinion:

1. **Every grade follows mechanically from the threshold and the actual figure printed beside
   it.** If the evidence concedes the bar was missed, the letter is not a PASS. A strong story is
   never a promotion.
2. **The report audits itself on render** and prints a red banner listing any contradiction
   (grade vs evidence, score arithmetic, a pivot that is not in new-high ground, a stop that is
   not 7-8%, an undated figure). Never ship a report showing that banner - fix the grade or the
   evidence, not the check.
3. **Pull fresh every run; when you cannot, say so in the report.** Carried-over figures are
   allowed, dated and flagged (`dataStatus`, amber "Data notice"); undated or silently stale ones
   are not.
4. **Decision support, not advice.** No personalized buy/sell directives, no order placement, and
   every read is paired with the 7-8% loss-cutting rule.

### 4. Quickest path to a first run

1. Load this whole file as context / system instructions.
2. Ask for a ticker: *"grade NVDA against CAN SLIM"*.
3. The model should: assess market direction (M) -> pull the ticker's bars and fundamentals ->
   run `relative_strength.py` and `chart_data.py` -> fill `CONFIG` in the HTML template ->
   check the self-audit banner is absent -> hand over the PDF (or the HTML).

'''


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build(root):
    stamp = _dt.date.today().isoformat()
    out = [PREAMBLE, "---\n"]
    out.append("## Contents of this bundle\n")
    for rel, why in INCLUDE:
        out.append("- **`%s`** - %s" % (rel, why))
    out.append("\nOmitted (repo housekeeping, meaningless outside the git repo): %s\n"
               % ", ".join("`%s`" % o for o in OMITTED))
    out.append("---\n")

    for rel, why in INCLUDE:
        path = os.path.join(root, rel)
        body = open(path, "r", encoding="utf-8").read()
        lang = {".md": "markdown", ".py": "python", ".html": "html"}.get(os.path.splitext(rel)[1], "")
        out.append("# FILE: `%s`\n" % rel)
        out.append("> %s\n" % why)
        out.append("````%s" % lang)
        out.append(body.rstrip("\n"))
        out.append("````\n")
        out.append("---\n")

    files_line = ", ".join("%s (%s)" % (rel, sha(open(os.path.join(root, rel), encoding="utf-8").read()))
                           for rel, _ in INCLUDE)
    out.append("_Bundled %s from https://github.com/thewongdirection/can-slim-grader - "
               "regenerate with `python scripts/export_portable.py` after any change._\n\n"
               "_Source digests: %s_\n" % (stamp, files_line))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Bundle the skill into one portable Markdown file.")
    ap.add_argument("--out", default=os.path.join(ROOT, "can-slim-grader.portable.md"))
    ap.add_argument("--zip", action="store_true", help="also write a zip of the raw skill files")
    args = ap.parse_args()

    text = build(ROOT)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote %s (%d bytes, %d files inlined)" % (args.out, len(text), len(INCLUDE)))

    if args.zip:
        zpath = os.path.splitext(args.out)[0].replace(".portable", "") + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, _ in INCLUDE:
                z.write(os.path.join(ROOT, rel), os.path.join("can-slim-grader", rel))
            z.writestr("can-slim-grader/PORTABLE-README.md", PREAMBLE)
        print("wrote %s" % zpath)


if __name__ == "__main__":
    main()
