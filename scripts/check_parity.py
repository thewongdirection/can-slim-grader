#!/usr/bin/env python3
"""
check_parity.py - flag drift in the files this skill shares verbatim with `can-slim-recommend`.

The two skills are one methodology pointed at two questions, so a change to a shared file here is
a change owed to the sister repo. This records a hash per shared file in parity-manifest.json and
tells you when the working copy no longer matches what was last synced.

  python scripts/check_parity.py            # report drift; exit 1 if any
  python scripts/check_parity.py --update   # record the current hashes as synced

WHAT THIS CANNOT DO: it compares bytes in THIS repo against the last recorded sync. It does not
read the sister repo, and it cannot see a material change that lives somewhere other than a shared
file - a threshold reworded in SKILL.md, a new freshness rule, a changed pivot definition. Those
are the common case and they are yours to notice. See "Keep can-slim-recommend at parity" in
SKILL.md for what counts as material.

Pure standard library.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "parity-manifest.json")
SISTER = "https://github.com/thewongdirection/can-slim-recommend"
SHARED = ["references/canslim-methodology.md", "scripts/relative_strength.py"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def current():
    out = {}
    for rel in SHARED:
        p = os.path.join(ROOT, rel)
        out[rel] = sha256(p) if os.path.exists(p) else None
    return out


def load():
    if not os.path.exists(MANIFEST):
        return {}
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f).get("files", {})


def main():
    ap = argparse.ArgumentParser(description="Check the files shared verbatim with can-slim-recommend.")
    ap.add_argument("--update", action="store_true",
                    help="record the current hashes as synced (do this in the same commit as the port)")
    args = ap.parse_args()

    now, was = current(), load()

    if args.update:
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump({"sister": SISTER,
                       "note": "sha256 of the files shared verbatim with can-slim-recommend. Update "
                               "ONLY in the same commit that ports the change to the sister repo.",
                       "files": now}, f, indent=2)
            f.write("\n")
        print("check_parity: recorded %d shared file(s) as synced with %s" % (len(now), SISTER))
        return 0

    missing = [r for r, h in now.items() if h is None]
    drifted = [r for r, h in now.items() if h is not None and was.get(r) != h]

    for rel in missing:
        print("check_parity: MISSING - %s is gone; the sister skill expects it." % rel)
    for rel in drifted:
        print("check_parity: DRIFT - %s changed since the last recorded sync." % rel)

    if not missing and not drifted:
        print("check_parity: OK - %d shared file(s) match the last recorded sync." % len(now))
        print("check_parity: byte-level only. A material change to a threshold, the freshness rule, "
              "or the pivot definition still has to be ported by hand - see SKILL.md.")
        return 0

    print("\ncheck_parity: port the change to %s, then re-run with --update in the SAME commit.\n"
          "If you cannot reach the sister repo, say so in your reply and the commit message and "
          "leave this un-updated so the drift stays visible." % SISTER)
    return 1


if __name__ == "__main__":
    sys.exit(main())
