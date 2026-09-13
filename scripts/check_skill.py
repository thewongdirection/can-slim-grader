#!/usr/bin/env python3
"""
check_skill.py - verify SKILL.md is importable as a skill, here and in other tools.

A skill that fails to load is not a subtle bug: the whole thing silently does not exist. The
rules below are the ones an importer actually enforces (frontmatter shape, the name grammar, the
1024-character description cap) plus the ones that quietly corrupt a file in transit - angle
brackets that a Markdown or HTML renderer eats as tags, CRLF, tabs, a BOM.

  python scripts/check_skill.py              # check this skill; exit 1 on any ERROR
  python scripts/check_skill.py --root DIR   # check the skill in DIR
  python scripts/check_skill.py --strict     # treat warnings as errors too

Pure standard library, so it runs anywhere the skill does.
"""
import argparse
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = "SKILL.md"

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Allowed by the skill spec. Anything else risks being rejected on import.
KNOWN_KEYS = {"name", "description", "license", "allowed-tools", "metadata", "version"}
MAX_NAME = 64
MAX_DESCRIPTION = 1024
# Guidance, not a hard cap: past this a skill is better off pushing detail into references/.
SOFT_MAX_WORDS = 5000
SOFT_MAX_LINES = 500

# Anything that reads as a markup tag: <div>, <TICKER>, </p>. Use {placeholder} instead.
TAG_RE = re.compile(r"<[/!?a-zA-Z][^>\n]{0,120}>")
# Repo-relative paths the document points at, inside backticks.
PATH_RE = re.compile(r"`((?:references|scripts|assets|tests)/[A-Za-z0-9._/-]+)`")


def load_frontmatter(text):
    """(mapping, body, error). A deliberately small YAML reader: flat keys, folded blocks."""
    if not text.startswith("---\n"):
        return {}, text, "does not begin with a '---' frontmatter delimiter"
    end = text.find("\n---\n", 3)
    if end == -1:
        return {}, text, "frontmatter is never closed with '---'"
    block, body = text[4:end + 1], text[end + 5:]

    data, key = {}, None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t":
            if key is None:
                return data, body, "indented line before any key: %r" % line[:40]
            data[key] += (" " if data[key] else "") + line.strip()
            continue
        if ":" not in line:
            return data, body, "line is neither a key nor a continuation: %r" % line[:40]
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        data[key] = "" if value in (">-", ">", "|", "|-", "") else value
    return data, body, None


def check(root, skill_name=None):
    """[(level, message)] - level is 'ERROR' or 'WARN'."""
    out = []
    path = os.path.join(root, SKILL)
    if not os.path.isfile(path):
        return [("ERROR", "%s is missing - a skill is a directory with %s at its root"
                 % (SKILL, SKILL))]

    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [("ERROR", "%s is not valid UTF-8 (%s)" % (SKILL, exc))]

    out += _check_encoding(text)
    data, body, err = load_frontmatter(text)
    if err:
        out.append(("ERROR", "frontmatter: %s" % err))
        return out
    out += _check_frontmatter(data, skill_name or os.path.basename(os.path.realpath(root)))
    out += _check_body(root, body, text)
    return out


def _check_encoding(text):
    out = []
    if text.startswith("﻿"):
        out.append(("ERROR", "file starts with a byte-order mark - strip it"))
    if "\r" in text:
        out.append(("ERROR", "file has CRLF line endings - convert to LF"))
    for n, line in enumerate(text.splitlines(), 1):
        if "\t" in line:
            out.append(("ERROR", "line %d contains a tab - YAML forbids them for indentation" % n))
            break
    bad = {c for c in text if unicodedata.category(c) == "Cc" and c != "\n"}
    if bad:
        out.append(("ERROR", "file contains control characters: %s"
                    % ", ".join("U+%04X" % ord(c) for c in sorted(bad))))
    return out


def _check_frontmatter(data, dirname):
    out = []
    name = data.get("name", "")
    if not name:
        out.append(("ERROR", "frontmatter has no 'name'"))
    else:
        if not NAME_RE.match(name):
            out.append(("ERROR", "name %r must be lowercase letters, digits and single hyphens"
                        % name))
        if len(name) > MAX_NAME:
            out.append(("ERROR", "name is %d characters; the limit is %d" % (len(name), MAX_NAME)))
        if dirname and name != dirname:
            out.append(("WARN", "name %r does not match the directory %r - some loaders key on "
                        "the directory" % (name, dirname)))

    description = data.get("description", "")
    if not description:
        out.append(("ERROR", "frontmatter has no 'description' - it is what makes the skill "
                    "trigger at all"))
    elif len(description) > MAX_DESCRIPTION:
        out.append(("ERROR", "description is %d characters; the limit is %d - summarise it"
                    % (len(description), MAX_DESCRIPTION)))

    for key in sorted(set(data) - KNOWN_KEYS):
        out.append(("WARN", "unrecognised frontmatter key %r - importers may reject it "
                    "(known: %s)" % (key, ", ".join(sorted(KNOWN_KEYS)))))
    return out


def _check_body(root, body, text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for tag in TAG_RE.findall(line):
            out.append(("ERROR", "line %d has %s, which reads as a markup tag - write a "
                        "{placeholder} instead" % (n, tag)))

    if body.count("```") % 2:
        out.append(("ERROR", "an unclosed ``` code fence"))

    for rel in sorted(set(PATH_RE.findall(body))):
        target = os.path.join(root, *rel.split("/"))
        if not os.path.exists(target) and not os.path.exists(target.rstrip("/")):
            out.append(("ERROR", "refers to %s, which does not exist" % rel))

    words, lines = len(body.split()), len(body.splitlines())
    if words > SOFT_MAX_WORDS:
        out.append(("WARN", "body is %d words (guidance: under %d) - move detail into references/"
                    % (words, SOFT_MAX_WORDS)))
    if lines > SOFT_MAX_LINES:
        out.append(("WARN", "body is %d lines (guidance: under %d)" % (lines, SOFT_MAX_LINES)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--root", default=ROOT, help="skill directory (default: this one)")
    ap.add_argument("--name", help="expected skill name (default: the directory name)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures too")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    findings = check(root, args.name)
    errors = [m for level, m in findings if level == "ERROR"]
    warnings = [m for level, m in findings if level == "WARN"]

    for level, message in findings:
        print("check_skill: %-5s %s" % (level, message))
    if not findings:
        print("check_skill: OK - %s conforms (frontmatter, name, description cap, no markup "
              "tags, references resolve)" % SKILL)
    elif not errors:
        print("check_skill: no errors; %d warning(s)" % len(warnings))

    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
