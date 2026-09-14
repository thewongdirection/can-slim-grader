#!/usr/bin/env python3
"""
self_update.py - make sure a run uses the newest published version of this skill.

A skill that refuses to grade a stock on stale prices should not grade one on stale rules
either. This checks the source repo before the run starts and, with --apply, installs the
newer version in place - so the thresholds, the scoring rubric and the guardrails actually
being followed are the current ones.

  python scripts/self_update.py            # check only: is there a newer version upstream?
  python scripts/self_update.py --apply    # check, and install it if there is
  python scripts/self_update.py --json     # same, machine-readable on stdout

It handles every way this skill gets installed:

  git clone       - compares HEAD against the branch tip and fast-forwards. Never rebases,
                    never touches a dirty or diverged worktree. (A check fetches the branch
                    into the object store when the commit is not already there; nothing in
                    the working tree changes until --apply.)
  unpacked files  - no .git (a zip, a plugin directory): compares every file against the
                    branch's archive and rewrites only the ones that differ, drops the ones
                    an earlier run installed that upstream has since retired, and records
                    the commit and file list in .skill-version so the next check is one
                    API call.
  vendored        - committed inside a LARGER repo (dotfiles, a monorepo of skills): read
                    only. Rewriting it would clobber somebody's version-controlled files,
                    so it reports where the update belongs and changes nothing.

The last line of output is STATUS: <one of>

  current           this copy already matches upstream - carry on
  updated           a newer version was installed - RE-READ SKILL.md and references/ before
                    continuing, because what is in context is the old copy
  update-available  newer version found, check-only mode (no --apply)
  blocked           newer version found but not installable here (local edits, diverged
                    history, a read-only or vendored install, or a write that failed
                    partway) - carry on with this copy and say so
  unknown           could not reach the repo - carry on with this copy and say so

Exit codes match: 0 current/updated, 10 update-available, 20 blocked, 30 unknown.

This is never fatal to a run. A grade from a slightly older copy beats no grade at all, as
long as the report says which copy produced it.

Pure standard library.
"""
import argparse
import datetime as _dt
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "thewongdirection/can-slim-grader"
BRANCH = "main"
STAMP = ".skill-version"
TIMEOUT = 12.0
# The whole skill is ~200 KB. Anything near this ceiling is not our archive.
MAX_BYTES = 32 * 1024 * 1024

# Never compared, never written: local state, caches, build litter.
IGNORE_DIRS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"})
IGNORE_NAMES = frozenset({STAMP, ".DS_Store"})
IGNORE_SUFFIXES = (".pyc", ".pyo")

# The file that identifies a skill tree, and so the wrapper directory inside a GitHub archive.
MARKER = "SKILL.md"

# Staging directories are made with mkdtemp, so the prefix is only how we recognise our own.
# Only ones older than this are swept: a fresher one belongs to a run still using it.
STAGE_PREFIX = "can-slim-grader-upstream-"
STAGE_TTL = 3600.0

# Everything a fetch can fail with, including a body that is not the archive we asked for
# (a proxy interstitial, an HTML error page, a truncated transfer).
NET_ERRORS = (OSError, ValueError, http.client.HTTPException, zipfile.BadZipFile)

# A git object id: 40 hex, or 64 in a sha256 repository.
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

STATUS_EXIT = {"current": 0, "updated": 0, "update-available": 10, "blocked": 20, "unknown": 30}


# --------------------------------------------------------------------------- paths

def safe_rel(name):
    """Archive path -> repo-relative path, or None if it is unusable or escapes the root."""
    rel = name.replace("\\", "/").strip("/")
    if not rel or os.path.isabs(name) or ":" in rel.split("/")[0]:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return rel


def ignored(rel):
    parts = rel.split("/")
    if any(p in IGNORE_DIRS for p in parts):
        return True
    return parts[-1] in IGNORE_NAMES or parts[-1].endswith(IGNORE_SUFFIXES)


def valid_sha(text):
    """Guard against an HTML error page or a proxy banner being taken for a commit id."""
    return bool(text) and bool(SHA_RE.match(text.strip()))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- git

def git(root, *args, timeout=TIMEOUT):
    """(returncode, stdout) - returncode None when git itself could not run."""
    try:
        p = subprocess.run(["git", "-C", root] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, (p.stdout or "").strip()


def enclosing_repo(root):
    """Nearest directory at or above root holding a .git, or None.

    Walked by hand rather than asked of git, so vendoring is still detected when git is missing.
    """
    path = os.path.realpath(root)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def git_state(root):
    """Describe the checkout, or None when this install is under no git repository at all.

    Three shapes matter, and the two unusable ones must still return a state: falling through
    to None would send version-controlled files down the file-by-file path and overwrite
    committed work.

      usable            root is the top of its own clone - fast-forward it
      usable=False      root has a .git that git cannot read - do nothing
      vendored          root sits inside a LARGER repo (dotfiles, a monorepo of skills) - the
                        files are committed somewhere we have no business rewriting
    """
    top = enclosing_repo(root)
    if top is None:
        return None
    if top != os.path.realpath(root):
        return {"head": None, "dirty": None, "branch": None, "usable": False,
                "vendored": True, "toplevel": top}
    rc, head = git(root, "rev-parse", "HEAD")
    if rc != 0 or not valid_sha(head):
        return {"head": None, "dirty": None, "branch": None, "usable": False, "vendored": False}
    # Tracked changes only: a report or a bars.json left in the directory is not a reason to
    # refuse the update for ever, and git itself refuses a fast-forward that would clobber one.
    _, porcelain = git(root, "status", "--porcelain", "--untracked-files=no")
    rc_b, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return {"head": head, "dirty": bool(porcelain), "usable": True, "vendored": False,
            "branch": branch if rc_b == 0 else "?"}


def is_ancestor(root, older, newer):
    rc, _ = git(root, "merge-base", "--is-ancestor", older, newer)
    return rc == 0


GITHUB_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def is_github_slug(repo):
    """True for owner/name. A URL, an scp-style remote or a local path is not one."""
    return bool(GITHUB_SLUG.match(repo)) and not os.path.isdir(repo)


def repo_url(repo):
    """owner/name -> its GitHub URL. Anything else (a URL, a mirror path) is used verbatim."""
    return "https://github.com/%s" % repo if is_github_slug(repo) else repo


# --------------------------------------------------------------------------- network

def ssl_context():
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(var)
        if path and os.path.exists(path):
            try:
                return ssl.create_default_context(cafile=path)
            except OSError:
                pass
    return ssl.create_default_context()


def http_get(url, timeout, accept="*/*", limit=MAX_BYTES):
    req = urllib.request.Request(url, headers={
        "User-Agent": "can-slim-grader-self-update",
        "Accept": accept,
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        body = r.read(limit + 1)
    if len(body) > limit:
        raise ValueError("response exceeds %d bytes" % limit)
    return body


def remote_sha(repo, branch, timeout, fetch=http_get):
    """Branch tip. The API first (one small call), then ls-remote (no rate limit)."""
    if is_github_slug(repo):
        url = "https://api.github.com/repos/%s/commits/%s" % (repo, urllib.parse.quote(branch))
        try:
            sha = fetch(url, timeout, accept="application/vnd.github.sha",
                        limit=4096).decode("utf-8", "replace").strip()
            if valid_sha(sha):
                return sha, "github api"
        except NET_ERRORS:
            pass
    try:
        p = subprocess.run(["git", "ls-remote", repo_url(repo), branch],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode == 0 and p.stdout.split():
            sha = p.stdout.split()[0]
            if valid_sha(sha):
                return sha, "git ls-remote"
    except (OSError, subprocess.SubprocessError):
        pass
    return None, None


def fetch_tree(repo, branch, timeout, fetch=http_get):
    """The branch's files as {repo-relative path: bytes}, top-level archive dir stripped."""
    url = "https://codeload.github.com/%s/zip/refs/heads/%s" % (repo, urllib.parse.quote(branch))
    return read_zip(fetch(url, timeout))


def read_zip(blob):
    """Archive bytes -> {repo-relative path: contents}, with the wrapper directory removed."""
    files = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        members = [(safe_rel(i.filename), i) for i in z.infolist() if not i.is_dir()]
        members = [(rel, i) for rel, i in members if rel]
        # GitHub wraps everything in <repo>-<ref>/. Recognise that wrapper by what it holds -
        # the skill's own SKILL.md - rather than by "everything shares a first component",
        # which is also true of an unwrapped archive whose files all sit in one subdirectory.
        names = {rel for rel, _ in members}
        tops = {rel.split("/")[0] for rel, _ in members}
        strip = len(tops) == 1 and "%s/%s" % (next(iter(tops)), MARKER) in names
        for rel, info in members:
            if strip:
                rel = rel.split("/", 1)[1] if "/" in rel else ""
            if not rel or ignored(rel):
                continue
            files[rel] = z.read(info)
    return files


# --------------------------------------------------------------------------- compare / write

def diff_tree(root, remote):
    """(changed, new, extra) - what upstream would rewrite, add, and does not know about."""
    changed, new = [], []
    for rel in sorted(remote):
        path = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(path):
            new.append(rel)
        elif sha256_file(path) != hashlib.sha256(remote[rel]).hexdigest():
            changed.append(rel)
    return changed, new, sorted(local_only(root, remote))


def local_only(root, remote):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for name in names:
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if not ignored(rel) and rel not in remote:
                yield rel


def superseded(root, remote, installed):
    """Files an earlier run installed that upstream has since deleted or renamed.

    Only paths the stamp says we put there: leaving them would keep a retired reference file in
    `references/`, which step 0 then tells the run to read. Everything else in the directory -
    a report, a bars.json, a local note - is none of our business and is never removed.
    """
    stale = []
    for rel in sorted(set(installed or ())):
        if rel in remote or ignored(rel) or safe_rel(rel) != rel:
            continue
        if os.path.isfile(os.path.join(root, *rel.split("/"))):
            stale.append(rel)
    return stale


def remove_tree(root, rels):
    """Delete each path and any directory it leaves empty. (removed, failed)."""
    removed, failed = [], []
    for rel in rels:
        path = os.path.join(root, *rel.split("/"))
        try:
            os.remove(path)
            removed.append(rel)
        except OSError as exc:
            failed.append((rel, str(exc)))
            continue
        parent = os.path.dirname(path)
        while os.path.realpath(parent) != os.path.realpath(root):
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
    return removed, failed


def write_tree(root, remote, rels):
    """Rewrite each path atomically, keeping the existing file mode. (written, failed)."""
    written, failed = [], []
    for rel in rels:
        dest = os.path.join(root, *rel.split("/"))
        tmp = None
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            mode = os.stat(dest).st_mode & 0o7777 if os.path.exists(dest) else None
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".self-update-")
            with os.fdopen(fd, "wb") as f:
                f.write(remote[rel])
            if mode is not None:
                os.chmod(tmp, mode)
            os.replace(tmp, dest)
            tmp = None
            written.append(rel)
        except OSError as exc:
            failed.append((rel, str(exc)))
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return written, failed


def stage_tree(remote):
    """Unpack upstream into a private directory, for a run that cannot update itself in place.

    The run is told to read this directory's SKILL.md and references/ and follow them, which
    makes it a trust boundary: nothing anyone else planted may end up inside it. So the path is
    created by mkdtemp - unguessable, mode 0700, ours - and never a fixed name in shared /tmp
    that a local attacker could pre-create as a symlink or a sticky directory. Copies left by
    earlier runs are cleared first, since this runs before every grade.

    Returns None when the temp directory is unusable: staging is a courtesy, not a step the run
    depends on.
    """
    clear_old_stages()
    try:
        staged = tempfile.mkdtemp(prefix=STAGE_PREFIX)
        for rel, blob in remote.items():
            path = os.path.join(staged, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(blob)
        return staged
    except OSError:
        return None


def clear_old_stages():
    """Remove staging directories this user left behind on earlier runs.

    Only ones we could have made ourselves: a real directory (never a symlink), owned by this
    user, still carrying mkdtemp's private mode. Anything else in /tmp wearing the same prefix
    is somebody else's and is left untouched. And only ones older than STAGE_TTL - a directory
    written minutes ago belongs to a run that was just told to read from it.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return
    tmp = tempfile.gettempdir()
    try:
        names = os.listdir(tmp)
    except OSError:
        return
    cutoff = time.time() - STAGE_TTL
    for name in names:
        if not name.startswith(STAGE_PREFIX):
            continue
        path = os.path.join(tmp, name)
        try:
            info = os.lstat(path)
            if (stat.S_ISDIR(info.st_mode) and info.st_uid == getuid()
                    and not info.st_mode & 0o077 and info.st_mtime < cutoff):
                shutil.rmtree(path)
        except OSError:
            pass


def read_stamp(root):
    try:
        with open(os.path.join(root, STAMP), encoding="utf-8") as f:
            stamp = json.load(f)
        return stamp if isinstance(stamp, dict) else None
    except (OSError, ValueError):
        return None


def write_stamp(root, repo, branch, sha, files=None):
    if not sha:
        return False
    payload = {
        "repo": repo,
        "branch": branch,
        "commit": sha,
        "checked": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Written by scripts/self_update.py; records the upstream commit this copy matches.",
        "files": sorted(files) if files else [],
    }
    try:
        with open(os.path.join(root, STAMP), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        return True
    except OSError:
        return False


def stamp_matches(stamp, repo, branch, sha):
    return bool(sha and stamp and stamp.get("commit") == sha
                and stamp.get("repo") == repo and stamp.get("branch") == branch)


def nearest_existing_dir(path):
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path or os.sep


def unwritable(root, rels):
    """First (rel, reason) that could not be written - checked before anything is touched.

    The test is the *directory*, not the file: write_tree creates a temp file beside the target
    and renames it over the top, so a read-only file in a writable directory is replaceable and
    a mode-644 file in a read-only directory is not.
    """
    for rel in rels:
        parent = nearest_existing_dir(os.path.dirname(os.path.join(root, *rel.split("/"))))
        if not os.access(parent, os.W_OK | os.X_OK):
            return rel, "%s is not writable" % parent
    return None


# --------------------------------------------------------------------------- the check

def check(root, repo=REPO, branch=BRANCH, timeout=TIMEOUT, apply_update=False,
          stamp=True, fetch=http_get):
    """Compare this install against the repo, update it when asked, and report what happened."""
    state = git_state(root)
    res = {
        "root": root,
        "repo": repo,
        "branch": branch,
        "mode": _mode(state),
        "status": "unknown",
        "detail": "",
        "local": state["head"] if state else None,
        "upstream": None,
        "source": None,
        "dirty": state["dirty"] if state else None,
        "behind": None,
        "changed": [],
        "new": [],
        "removed": [],
        "extra": [],
        "staged": None,
    }
    res["upstream"], res["source"] = remote_sha(repo, branch, timeout, fetch=fetch)
    if state and state.get("vendored"):
        # Committed inside somebody else's repo: read-only, and say where the update belongs.
        _check_files(res, root, timeout, apply_update, stamp, fetch, writable=False)
    elif state:
        _check_git(res, root, state, timeout, apply_update)
    else:
        _check_files(res, root, timeout, apply_update, stamp, fetch)
    return res


def _mode(state):
    if not state:
        return "unpacked files"
    return "vendored in %s" % state["toplevel"] if state.get("vendored") else "git clone"


def _check_git(res, root, state, timeout, apply_update):
    sha, branch = res["upstream"], res["branch"]
    if not state["usable"]:
        res["detail"] = ("this is a git checkout but git could not read it (no git on PATH, or a "
                         "broken .git) - refusing to rewrite a clone file by file; install git "
                         "or re-install the skill by hand")
        return
    if not sha:
        res["detail"] = "could not reach %s - offline, or the repo is unreachable" % res["repo"]
        return
    if sha == state["head"]:
        res["status"] = "current"
        res["detail"] = "HEAD is the %s tip" % branch
        return
    # Ahead or behind cannot be told apart without the commit itself. Fetch it only when it is
    # not already in the object store, and keep to the caller's timeout rather than a longer one
    # of our own: step 0 promises not to stall a grade.
    if git(root, "cat-file", "-e", "%s^{commit}" % sha)[0] != 0:
        rc, _ = git(root, "fetch", "--quiet", repo_url(res["repo"]), branch, timeout=timeout)
        if rc != 0:
            res["detail"] = ("upstream is at %s but this clone could not fetch it, so ahead or "
                             "behind cannot be told apart" % sha[:7])
            return
    if is_ancestor(root, sha, state["head"]):
        res["status"] = "current"
        res["detail"] = "this clone already contains the %s tip" % branch
        return
    if not is_ancestor(root, state["head"], sha):
        res["status"] = "blocked"
        res["detail"] = ("local history has diverged from %s - merge or re-clone by hand" % branch)
        return
    _, count = git(root, "rev-list", "--count", "%s..%s" % (state["head"], sha))
    res["behind"] = int(count) if count.isdigit() else None
    if not apply_update:
        res["status"] = "update-available"
        res["detail"] = "a newer version exists upstream; re-run with --apply to install it"
        return
    if state["dirty"]:
        res["status"] = "blocked"
        res["detail"] = "the working tree has uncommitted changes - not fast-forwarding over them"
        return
    rc, _ = git(root, "merge", "--ff-only", sha, timeout=timeout)
    if rc != 0:
        res["status"] = "blocked"
        res["detail"] = "fast-forward to %s failed" % sha[:7]
        return
    res["status"] = "updated"
    res["local"] = sha
    res["detail"] = "fast-forwarded to %s" % sha[:7]


def _check_files(res, root, timeout, apply_update, stamp, fetch, writable=True):
    repo, branch, sha = res["repo"], res["branch"], res["upstream"]
    recorded = read_stamp(root)
    if writable and stamp_matches(recorded, repo, branch, sha):
        res["status"] = "current"
        res["local"] = sha
        res["detail"] = "%s records this copy at the %s tip" % (STAMP, branch)
        return
    if recorded:
        res["local"] = recorded.get("commit")
    if not is_github_slug(repo):
        # The tip may well be known (ls-remote answered); it is installing it that is impossible.
        res["status"] = "blocked" if sha else "unknown"
        res["detail"] = ("%s is not a GitHub owner/name, and an unpacked install can only be "
                         "refreshed from a GitHub archive - re-install by hand" % repo)
        return
    try:
        remote = fetch_tree(repo, branch, timeout, fetch=fetch)
    except NET_ERRORS as exc:
        res["detail"] = "could not download %s@%s (%s)" % (repo, branch, exc)
        return
    if not remote:
        res["detail"] = "the %s archive came back with no usable files" % branch
        return
    res["changed"], res["new"], res["extra"] = diff_tree(root, remote)
    res["removed"] = superseded(root, remote, (recorded or {}).get("files"))
    pending = res["changed"] + res["new"]
    if not pending and not res["removed"]:
        res["status"] = "current"
        res["detail"] = "every file matches %s@%s" % (repo, branch)
        if stamp and writable:
            write_stamp(root, repo, branch, sha, remote)
        return
    if not writable:
        res["status"] = "blocked"
        res["staged"] = stage_tree(remote)
        res["detail"] = ("this skill is committed inside the repo at %s - updating it here would "
                         "rewrite version-controlled files, so nothing was changed; pull the "
                         "newer version in that repo instead%s"
                         % (res["mode"].split(" in ", 1)[-1], _staged_note(res["staged"])))
        return
    if not apply_update:
        res["status"] = "update-available"
        res["detail"] = ("%d file(s) differ from upstream%s; re-run with --apply to install"
                         % (len(pending),
                            " and %d are retired upstream" % len(res["removed"])
                            if res["removed"] else ""))
        return
    blocker = unwritable(root, pending)
    if blocker:
        res["status"] = "blocked"
        res["staged"] = stage_tree(remote)
        res["detail"] = ("read-only install (%s) - nothing was changed%s"
                         % (blocker[1], _staged_note(res["staged"])))
        return
    written, failed = write_tree(root, remote, pending)
    if failed:
        res["status"] = "blocked"
        res["staged"] = stage_tree(remote)
        res["detail"] = ("wrote %d of %d file(s) then failed on %s (%s) - this install is now "
                         "part-updated and needs a re-run or a re-install%s"
                         % (len(written), len(pending), failed[0][0], failed[0][1],
                            _staged_note(res["staged"])))
        return
    # Only ever the files an earlier run installed that upstream has since dropped.
    res["removed"], _ = remove_tree(root, res["removed"])
    res["status"] = "updated"
    res["local"] = sha
    res["detail"] = ("installed %d file(s) from %s@%s%s"
                     % (len(written), repo, branch,
                        ", removed %d retired upstream" % len(res["removed"])
                        if res["removed"] else ""))
    if stamp:
        write_stamp(root, repo, branch, sha, remote)


def _staged_note(staged):
    return ("; the upstream copy is unpacked at %s - read its SKILL.md and references/ and "
            "follow those" % staged) if staged else ""


# --------------------------------------------------------------------------- output

def _short(sha):
    return sha[:7] if sha else "unknown"


def _listing(rels, limit=12):
    shown = ", ".join(rels[:limit])
    return shown + (" (+%d more)" % (len(rels) - limit) if len(rels) > limit else "")


def render(res):
    where = res["mode"]
    if res["dirty"] is not None:
        where += ", %s" % ("uncommitted changes" if res["dirty"] else "clean")
    lines = [
        "can-slim-grader self-update",
        "  install   : %s (%s)" % (res["root"], where),
        "  local     : %s" % _short(res["local"]),
        "  upstream  : %s@%s -> %s%s" % (res["repo"], res["branch"], _short(res["upstream"]),
                                         " (%s)" % res["source"] if res["source"] else ""),
    ]
    if res["behind"]:
        lines.append("  behind    : %d commit(s)" % res["behind"])
    for label, rels in (("changed", res["changed"]), ("new", res["new"]),
                        ("removed", res["removed"])):
        if rels:
            lines.append("  %-10s: %s" % (label, _listing(rels)))
    if res["detail"]:
        lines.append("  detail    : %s" % res["detail"])
    if res["status"] == "updated":
        lines.append("  next      : re-read SKILL.md and references/ - the copy in context is stale")
    lines.append("STATUS: %s" % res["status"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="install the newer version when there is one (default: report only)")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    ap.add_argument("--root", default=ROOT, help="skill directory to check (default: this one)")
    ap.add_argument("--repo", default=REPO, help="owner/name of the source repo")
    ap.add_argument("--branch", default=BRANCH, help="branch to track (default: %s)" % BRANCH)
    ap.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds per network call")
    ap.add_argument("--no-stamp", action="store_true",
                    help="do not write %s when the copy is confirmed current" % STAMP)
    args = ap.parse_args(argv)

    res = check(os.path.abspath(args.root), repo=args.repo, branch=args.branch,
                timeout=args.timeout, apply_update=args.apply, stamp=not args.no_stamp)
    print(json.dumps(res, indent=2) if args.json else render(res))
    return STATUS_EXIT.get(res["status"], 30)


if __name__ == "__main__":
    sys.exit(main())
