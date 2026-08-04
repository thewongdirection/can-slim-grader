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


def manifest():
    if not os.path.exists(MANIFEST):
        return {}
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


def load():
    return manifest().get("files", {})


def pending():
    """Material changes already landed here that still owe a port to the sister skill."""
    return manifest().get("pending_port", [])


def main():
    ap = argparse.ArgumentParser(description="Check the files shared verbatim with can-slim-recommend.")
    ap.add_argument("--update", action="store_true",
                    help="record the current hashes as synced (do this in the same commit as the port)")
    ap.add_argument("--clear-pending", action="store_true",
                    help="drop the pending_port debt after porting those changes to the sister repo")
    args = ap.parse_args()

    now, was, owed = current(), load(), pending()

    if args.update or args.clear_pending:
        m = manifest()
        out = {"sister": SISTER,
               "note": "sha256 of the files shared verbatim with can-slim-recommend. Update ONLY in "
                       "the same commit that ports the change to the sister repo.",
               "files": now if args.update else m.get("files", now),
               "pending_port": [] if args.clear_pending else owed}
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
        if args.update:
            print("check_parity: recorded %d shared file(s) as synced with %s" % (len(now), SISTER))
        if args.clear_pending:
            print("check_parity: cleared %d pending port(s)." % len(owed))
        return 0

    missing = [r for r, h in now.items() if h is None]
    drifted = [r for r, h in now.items() if h is not None and was.get(r) != h]

    for rel in missing:
        print("check_parity: MISSING - %s is gone; the sister skill expects it." % rel)
    for rel in drifted:
        print("check_parity: DRIFT - %s changed since the last recorded sync." % rel)

    for p in owed:
        print("check_parity: PENDING PORT - %s (%s)\n    %s"
              % (p.get("commit", "?"), p.get("summary", ""), p.get("why", "")))

    if not missing and not drifted and not owed:
        print("check_parity: OK - %d shared file(s) match the last recorded sync." % len(now))
        print("check_parity: byte-level only. A material change to a threshold, the freshness rule, "
              "or the pivot definition still has to be ported by hand - see SKILL.md.")
        return 0

    if owed and not missing and not drifted:
        print("\ncheck_parity: shared files match, but %d material change(s) landed here still owe a "
              "port to\n%s. Port them, then re-run with --clear-pending." % (len(owed), SISTER))
        return 1

    print("\ncheck_parity: port the change to %s, then re-run with --update in the SAME commit.\n"
          "If you cannot reach the sister repo, say so in your reply and the commit message and "
          "leave this un-updated so the drift stays visible." % SISTER)
    return 1


if __name__ == "__main__":
    sys.exit(main())
