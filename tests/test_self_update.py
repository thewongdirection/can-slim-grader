#!/usr/bin/env python3
"""
Regression tests for scripts/self_update.py.

No network: the HTTP fetcher is injected, and the git cases run against real local
repositories created in a temp directory.

  python -m unittest discover -s tests -v
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import self_update as su  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40
GIT_ID = ["-c", "user.email=test@example.com", "-c", "user.name=test", "-c", "commit.gpgsign=false"]


def make_zip(files, wrapper="can-slim-grader-main"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for rel, body in files.items():
            z.writestr("%s/%s" % (wrapper, rel) if wrapper else rel, body)
    return buf.getvalue()


def fake_fetch(files, sha=SHA, wrapper="can-slim-grader-main"):
    blob = make_zip(files, wrapper)

    def fetch(url, timeout, accept="*/*", limit=None):
        return sha.encode() if "api.github.com" in url else blob

    return fetch


def write(root, rel, body):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def read(root, rel):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="self-update-test-")
        self.addCleanup(shutil.rmtree, self.root, True)


class PathRules(unittest.TestCase):
    def test_safe_rel_keeps_ordinary_paths(self):
        self.assertEqual(su.safe_rel("scripts/chart_data.py"), "scripts/chart_data.py")
        self.assertEqual(su.safe_rel("a\\b.md"), "a/b.md")

    def test_safe_rel_rejects_escapes_and_junk(self):
        for bad in ("../outside.md", "a/../../b", "/etc/passwd", "", "/", "C:/x/y", "a//b"):
            self.assertIsNone(su.safe_rel(bad), bad)

    def test_ignored_covers_local_state_only(self):
        for rel in (".git/config", "scripts/__pycache__/x.pyc", "scripts/a.pyc",
                    su.STAMP, ".DS_Store"):
            self.assertTrue(su.ignored(rel), rel)
        for rel in ("SKILL.md", "assets/evaluation_template.html", "scripts/chart_data.py"):
            self.assertFalse(su.ignored(rel), rel)


class ArchiveReading(unittest.TestCase):
    def test_strips_single_wrapper_directory(self):
        files = su.read_zip(make_zip({"SKILL.md": "x", "scripts/a.py": "y"}))
        self.assertEqual(sorted(files), ["SKILL.md", "scripts/a.py"])

    def test_keeps_layout_when_there_is_no_wrapper(self):
        files = su.read_zip(make_zip({"SKILL.md": "x", "scripts/a.py": "y"}, wrapper=None))
        self.assertEqual(sorted(files), ["SKILL.md", "scripts/a.py"])

    def test_drops_ignored_and_traversing_members(self):
        blob = make_zip({"SKILL.md": "x", ".git/config": "no", "scripts/a.pyc": "no",
                         "../escape.md": "no"})
        self.assertEqual(sorted(su.read_zip(blob)), ["SKILL.md"])


class TreeDiff(TempRoot):
    def test_reports_changed_new_and_extra(self):
        write(self.root, "SKILL.md", "old")
        write(self.root, "keep.md", "same")
        write(self.root, "mine.md", "local only")
        remote = {"SKILL.md": b"new", "keep.md": b"same", "scripts/a.py": b"added"}
        changed, new, extra = su.diff_tree(self.root, remote)
        self.assertEqual(changed, ["SKILL.md"])
        self.assertEqual(new, ["scripts/a.py"])
        self.assertEqual(extra, ["mine.md"])

    def test_ignores_local_state_when_listing_extras(self):
        write(self.root, "SKILL.md", "same")
        write(self.root, su.STAMP, "{}")
        write(self.root, "scripts/__pycache__/x.pyc", "junk")
        _, _, extra = su.diff_tree(self.root, {"SKILL.md": b"same"})
        self.assertEqual(extra, [])


class Writing(TempRoot):
    def test_writes_atomically_and_keeps_the_file_mode(self):
        path = write(self.root, "scripts/a.py", "old")
        os.chmod(path, 0o750)
        written, failed = su.write_tree(self.root, {"scripts/a.py": b"new", "new.md": b"hi"},
                                        ["scripts/a.py", "new.md"])
        self.assertEqual(written, ["scripts/a.py", "new.md"])
        self.assertEqual(failed, [])
        self.assertEqual(read(self.root, "scripts/a.py"), "new")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o750)
        litter = [n for n in os.listdir(os.path.join(self.root, "scripts"))
                  if n.startswith(".self-update-")]
        self.assertEqual(litter, [])

    def test_reports_failures_without_raising(self):
        with mock.patch.object(su.os, "replace", side_effect=OSError("read-only")):
            written, failed = su.write_tree(self.root, {"a.md": b"x"}, ["a.md"])
        self.assertEqual(written, [])
        self.assertEqual(len(failed), 1)
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".self-update-")], [])

    def test_unwritable_names_the_first_blocker(self):
        write(self.root, "a.md", "x")
        with mock.patch.object(su.os, "access", return_value=False):
            blocker = su.unwritable(self.root, ["a.md"])
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker[0], "a.md")
        self.assertIsNone(su.unwritable(self.root, ["a.md"]))


class Stamp(TempRoot):
    def test_roundtrip_and_matching(self):
        self.assertIsNone(su.read_stamp(self.root))
        self.assertTrue(su.write_stamp(self.root, "o/r", "main", SHA))
        stamp = su.read_stamp(self.root)
        self.assertEqual(stamp["commit"], SHA)
        self.assertTrue(su.stamp_matches(stamp, "o/r", "main", SHA))
        self.assertFalse(su.stamp_matches(stamp, "o/r", "main", OTHER))
        self.assertFalse(su.stamp_matches(stamp, "other/repo", "main", SHA))
        self.assertFalse(su.stamp_matches(stamp, "o/r", "dev", SHA))
        self.assertFalse(su.stamp_matches(stamp, "o/r", "main", None))

    def test_unreadable_stamp_is_not_fatal(self):
        write(self.root, su.STAMP, "not json")
        self.assertIsNone(su.read_stamp(self.root))

    def test_no_stamp_without_a_known_commit(self):
        self.assertFalse(su.write_stamp(self.root, "o/r", "main", None))


class FilesMode(TempRoot):
    def check(self, remote, **kw):
        kw.setdefault("fetch", fake_fetch(remote))
        return su.check(self.root, repo="o/r", branch="main", timeout=1, **kw)

    def test_identical_copy_is_current_and_gets_stamped(self):
        write(self.root, "SKILL.md", "same")
        res = self.check({"SKILL.md": "same"})
        self.assertEqual(res["status"], "current")
        self.assertEqual(su.read_stamp(self.root)["commit"], SHA)

    def test_stamp_short_circuits_the_download(self):
        su.write_stamp(self.root, "o/r", "main", SHA)

        def fetch(url, timeout, accept="*/*", limit=None):
            if "codeload" in url:
                raise AssertionError("should not download when the stamp matches")
            return SHA.encode()

        res = su.check(self.root, repo="o/r", branch="main", timeout=1, fetch=fetch)
        self.assertEqual(res["status"], "current")

    def test_difference_is_reported_but_not_applied_by_default(self):
        write(self.root, "SKILL.md", "old")
        res = self.check({"SKILL.md": "new", "scripts/a.py": "added"})
        self.assertEqual(res["status"], "update-available")
        self.assertEqual(res["changed"], ["SKILL.md"])
        self.assertEqual(res["new"], ["scripts/a.py"])
        self.assertEqual(read(self.root, "SKILL.md"), "old")
        self.assertIsNone(su.read_stamp(self.root))

    def test_apply_installs_and_records_the_commit(self):
        write(self.root, "SKILL.md", "old")
        res = self.check({"SKILL.md": "new", "scripts/a.py": "added"}, apply_update=True)
        self.assertEqual(res["status"], "updated")
        self.assertEqual(read(self.root, "SKILL.md"), "new")
        self.assertEqual(read(self.root, "scripts/a.py"), "added")
        self.assertEqual(su.read_stamp(self.root)["commit"], SHA)

    def test_apply_leaves_local_only_files_alone(self):
        write(self.root, "SKILL.md", "old")
        write(self.root, "NVDA-canslim.html", "a report")
        self.check({"SKILL.md": "new"}, apply_update=True)
        self.assertEqual(read(self.root, "NVDA-canslim.html"), "a report")

    def test_read_only_install_changes_nothing_and_stages_upstream(self):
        write(self.root, "SKILL.md", "old")
        with mock.patch.object(su.os, "access", return_value=False):
            res = self.check({"SKILL.md": "new"}, apply_update=True)
        self.assertEqual(res["status"], "blocked")
        self.assertEqual(read(self.root, "SKILL.md"), "old")
        self.assertTrue(os.path.isfile(os.path.join(res["staged"], "SKILL.md")))
        shutil.rmtree(res["staged"], True)

    def test_offline_is_unknown_not_a_failure(self):
        write(self.root, "SKILL.md", "old")

        def fetch(url, timeout, accept="*/*", limit=None):
            raise urllib.error.URLError("no route to host")

        with mock.patch.object(su, "remote_sha", return_value=(None, None)):
            res = su.check(self.root, repo="o/r", branch="main", timeout=1, fetch=fetch,
                           apply_update=True)
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(read(self.root, "SKILL.md"), "old")

    def test_non_github_source_cannot_refresh_an_unpacked_install(self):
        write(self.root, "SKILL.md", "old")
        with mock.patch.object(su, "remote_sha", return_value=(None, None)):
            res = su.check(self.root, repo="https://example.invalid/x.git", branch="main",
                           timeout=1, apply_update=True)
        self.assertEqual(res["status"], "unknown")
        self.assertIn("GitHub", res["detail"])


class GitMode(unittest.TestCase):
    """Real local repositories - no network, no GitHub."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="self-update-git-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.upstream = os.path.join(self.tmp, "upstream.git")
        self.author = os.path.join(self.tmp, "author")
        self.clone = os.path.join(self.tmp, "clone")
        self.git(None, "init", "--bare", "-q", self.upstream)
        self.git(self.upstream, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git(None, "init", "-q", self.author)
        self.git(self.author, "symbolic-ref", "HEAD", "refs/heads/main")
        self.commit(self.author, "SKILL.md", "v1")
        self.git(self.author, "push", "-q", self.upstream, "main")
        self.git(None, "clone", "-q", self.upstream, self.clone)

    def git(self, cwd, *args):
        p = subprocess.run(["git"] + GIT_ID + list(args), cwd=cwd,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, "git %s: %s" % (" ".join(args), p.stderr))
        return p.stdout.strip()

    def commit(self, repo, rel, body):
        write(repo, rel, body)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "update %s" % rel)

    def publish(self, body="v2"):
        self.commit(self.author, "SKILL.md", body)
        self.git(self.author, "push", "-q", self.upstream, "main")

    def check(self, **kw):
        return su.check(self.clone, repo=self.upstream, branch="main", timeout=30, **kw)

    def test_up_to_date_clone(self):
        res = self.check()
        self.assertEqual(res["status"], "current")
        self.assertEqual(res["mode"], "git clone")

    def test_behind_is_reported_then_fast_forwarded(self):
        self.publish()
        res = self.check()
        self.assertEqual(res["status"], "update-available")
        self.assertEqual(res["behind"], 1)
        self.assertEqual(read(self.clone, "SKILL.md"), "v1")

        res = self.check(apply_update=True)
        self.assertEqual(res["status"], "updated")
        self.assertEqual(read(self.clone, "SKILL.md"), "v2")
        self.assertEqual(res["local"], res["upstream"])

    def test_uncommitted_changes_block_the_fast_forward(self):
        self.publish()
        write(self.clone, "SKILL.md", "my local edit")
        res = self.check(apply_update=True)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("uncommitted", res["detail"])
        self.assertEqual(read(self.clone, "SKILL.md"), "my local edit")

    def test_diverged_history_is_blocked_not_merged(self):
        self.publish()
        self.commit(self.clone, "SKILL.md", "a different v2")
        res = self.check(apply_update=True)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("diverged", res["detail"])
        self.assertEqual(read(self.clone, "SKILL.md"), "a different v2")

    def test_clone_ahead_of_upstream_counts_as_current(self):
        self.commit(self.clone, "SKILL.md", "v1 plus work in progress")
        res = self.check(apply_update=True)
        self.assertEqual(res["status"], "current")
        self.assertEqual(read(self.clone, "SKILL.md"), "v1 plus work in progress")

    def test_untracked_working_files_do_not_block_the_fast_forward(self):
        self.publish()
        write(self.clone, "NVDA-canslim.html", "a report from an earlier run")
        write(self.clone, "bars.json", "[]")
        res = self.check(apply_update=True)
        self.assertEqual(res["status"], "updated")
        self.assertEqual(read(self.clone, "SKILL.md"), "v2")
        self.assertEqual(read(self.clone, "NVDA-canslim.html"), "a report from an earlier run")

    def test_no_stamp_is_written_for_a_git_install(self):
        self.publish()
        self.check(apply_update=True)
        self.assertFalse(os.path.exists(os.path.join(self.clone, su.STAMP)))


class Reporting(unittest.TestCase):
    def result(self, **kw):
        res = {"root": "/skill", "repo": "o/r", "branch": "main", "mode": "git clone",
               "status": "current", "detail": "", "local": SHA, "upstream": SHA,
               "source": "github api", "dirty": False, "behind": None, "changed": [],
               "new": [], "extra": [], "staged": None}
        res.update(kw)
        return res

    def test_status_is_the_last_line(self):
        self.assertTrue(su.render(self.result()).endswith("STATUS: current"))

    def test_an_update_tells_the_caller_to_re_read_the_skill(self):
        self.assertIn("re-read SKILL.md", su.render(self.result(status="updated")))

    def test_current_does_not(self):
        self.assertNotIn("re-read SKILL.md", su.render(self.result()))

    def test_long_file_lists_are_truncated(self):
        out = su.render(self.result(status="update-available",
                                    changed=["f%02d.md" % i for i in range(20)]))
        self.assertIn("(+8 more)", out)

    def test_exit_codes_follow_the_status(self):
        for status, code in su.STATUS_EXIT.items():
            with mock.patch.object(su, "check", return_value=self.result(status=status)):
                with mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(su.main([]), code, status)

    def test_json_mode_emits_the_status(self):
        buf = io.StringIO()
        with mock.patch.object(su, "check", return_value=self.result(status="updated")):
            with mock.patch("sys.stdout", buf):
                su.main(["--json"])
        self.assertIn('"status": "updated"', buf.getvalue())


class Robustness(TempRoot):
    """The cases that must not crash a grade, and must not rewrite what they should not."""

    def test_an_archive_that_is_not_a_zip_is_unknown_not_a_traceback(self):
        write(self.root, "SKILL.md", "old")

        def fetch(url, timeout, accept="*/*", limit=None):
            return SHA.encode() if "api.github.com" in url else b"<html>proxy says no</html>"

        res = su.check(self.root, repo="o/r", branch="main", timeout=1, fetch=fetch,
                       apply_update=True)
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(read(self.root, "SKILL.md"), "old")

    def test_a_git_checkout_git_cannot_read_is_never_rewritten_file_by_file(self):
        write(self.root, "SKILL.md", "local work")
        write(self.root, ".git", "not a real git directory")
        res = su.check(self.root, repo="o/r", branch="main", timeout=1,
                       fetch=fake_fetch({"SKILL.md": "upstream"}), apply_update=True)
        self.assertEqual(res["mode"], "git clone")
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(read(self.root, "SKILL.md"), "local work")

    def test_writability_is_judged_on_the_directory_not_the_file(self):
        path = write(self.root, "a.md", "x")
        os.chmod(path, 0o444)
        self.addCleanup(os.chmod, path, 0o644)
        # A read-only file in a writable directory is replaceable: temp file, then rename.
        self.assertIsNone(su.unwritable(self.root, ["a.md"]))

        real_access = os.access

        def no_dir_write(target, mode):
            return False if os.path.isdir(target) else real_access(target, mode)

        with mock.patch.object(su.os, "access", side_effect=no_dir_write):
            blocker = su.unwritable(self.root, ["a.md"])
        self.assertIsNotNone(blocker)
        self.assertIn(self.root, blocker[1])

    def test_a_body_that_is_not_a_commit_id_is_refused(self):
        for bad in ("<html>404</html>", "", "zzz", "a" * 39, "A" * 40):
            self.assertFalse(su.valid_sha(bad), bad)
        for good in ("a" * 40, "0123456789abcdef" * 4):
            self.assertTrue(su.valid_sha(good), good)

    def test_a_junk_api_body_does_not_become_the_upstream_commit(self):
        def fetch(url, timeout, accept="*/*", limit=None):
            return b"<html>rate limited</html>"

        with mock.patch.object(su.subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(su.remote_sha("o/r", "main", 1, fetch=fetch), (None, None))


class Staging(unittest.TestCase):
    """The staged copy is a directory the run is told to trust, so nobody else may reach it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="self-update-tmpdir-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.object(su.tempfile, "gettempdir", return_value=self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def test_each_run_gets_its_own_private_unguessable_directory(self):
        first = su.stage_tree({"SKILL.md": b"v1"})
        self.assertEqual(read(first, "SKILL.md"), "v1")
        self.assertEqual(os.stat(first).st_mode & 0o777, 0o700)
        self.assertTrue(os.path.basename(first).startswith(su.STAGE_PREFIX))
        second = su.stage_tree({"SKILL.md": b"v2"})
        self.assertNotEqual(first, second)
        self.assertEqual(read(second, "SKILL.md"), "v2")

    def test_our_own_earlier_copies_are_cleared_rather_than_piling_up(self):
        paths = [su.stage_tree({"SKILL.md": b"v%d" % i}) for i in range(3)]
        self.assertEqual([p for p in paths[:-1] if os.path.exists(p)], [])
        self.assertTrue(os.path.isdir(paths[-1]))

    def test_a_symlink_wearing_our_prefix_is_never_followed_or_removed(self):
        victim = os.path.join(self.tmp, "victim")
        os.makedirs(victim)
        write(victim, "keep.md", "important")
        link = os.path.join(self.tmp, su.STAGE_PREFIX + "planted")
        os.symlink(victim, link)

        staged = su.stage_tree({"SKILL.md": b"upstream"})

        self.assertNotEqual(staged, link)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(read(victim, "keep.md"), "important")
        self.assertFalse(os.path.exists(os.path.join(victim, "SKILL.md")))

    def test_a_directory_someone_else_can_write_is_left_alone(self):
        planted = os.path.join(self.tmp, su.STAGE_PREFIX + "shared")
        os.makedirs(planted, mode=0o777)
        os.chmod(planted, 0o777)
        write(planted, "references.md", "not ours")

        staged = su.stage_tree({"SKILL.md": b"upstream"})

        self.assertNotEqual(staged, planted)
        self.assertTrue(os.path.isdir(planted))
        self.assertEqual(read(planted, "references.md"), "not ours")

    def test_staging_is_a_courtesy_not_a_dependency(self):
        with mock.patch.object(su.os, "makedirs", side_effect=OSError("read-only /tmp")):
            self.assertIsNone(su.stage_tree({"a/b.md": b"x"}))
        self.assertEqual(su._staged_note(None), "")
        self.assertIn("/tmp/x", su._staged_note("/tmp/x"))

    def test_a_reachable_but_uninstallable_source_is_blocked_not_unknown(self):
        root = tempfile.mkdtemp(prefix="self-update-test-")
        self.addCleanup(shutil.rmtree, root, True)
        write(root, "SKILL.md", "old")
        with mock.patch.object(su, "remote_sha", return_value=(SHA, "git ls-remote")):
            res = su.check(root, repo="https://example.invalid/x.git", branch="main",
                           timeout=1, apply_update=True)
        self.assertEqual(res["status"], "blocked")
        self.assertEqual(read(root, "SKILL.md"), "old")


if __name__ == "__main__":
    unittest.main()
