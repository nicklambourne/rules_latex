"""Unit tests for `BuildState.get_git_info` + `snapshot(workspace=)`.

The footer git badge polls /status ~1×/s for the duration ticker.
Without caching, that's three `git` subprocesses per second per
connected tab. `get_git_info` caches the (branch, dirty, short_sha)
triple for ``GIT_INFO_TTL_S`` seconds. These tests pin the cache
behaviour against a real temp git repo (no mocking of subprocess —
the subprocess-out-shape contract is exactly what we want to
exercise) and confirm the non-git fallback.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.py._template_loader import load_template_module

_M = load_template_module(name="serve_web_git_test")


def _git(*args: str, cwd: Path, env: dict | None = None):
    """Run `git <args>` with the test-local identity preset so we
    don't depend on the global gitconfig."""
    full_env = os.environ.copy()
    full_env.update({
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example",
    })
    if env:
        full_env.update(env)
    subprocess.run(
        ["git", *args], cwd=cwd, env=full_env, check=True,
        capture_output=True,
    )


def _make_repo(td: Path) -> Path:
    """Init a minimal git repo at ``td`` with one signed-off commit
    on a deterministic branch name. Returns ``td`` for chaining.
    """
    _git("init", "--initial-branch=main", "-q", cwd=td)
    # Configure user info locally so commits work even when the
    # invoker has no global identity (e.g. CI sandboxes).
    _git("config", "user.email", "test@example", cwd=td)
    _git("config", "user.name", "Test", cwd=td)
    # Disable signing in case the invoker has commit.gpgsign=true
    # in their global config — the test runner doesn't have the
    # signing key.
    _git("config", "commit.gpgsign", "false", cwd=td)
    _git("config", "tag.gpgsign", "false", cwd=td)
    (td / "README").write_text("hello\n")
    _git("add", "README", cwd=td)
    _git("commit", "-m", "init", cwd=td)
    return td


class GetGitInfoTest(unittest.TestCase):
    def setUp(self):
        self.state = _M.BuildState()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = _make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_repo_reports_branch_and_sha(self):
        branch, dirty, short_sha = self.state.get_git_info(self.workspace)
        self.assertEqual(branch, "main")
        self.assertFalse(dirty)
        # Short sha is 7 chars by default; we don't pin the value
        # (commit hash depends on author/committer dates) but we
        # pin the shape.
        self.assertIsNotNone(short_sha)
        self.assertGreaterEqual(len(short_sha), 4)
        self.assertLessEqual(len(short_sha), 12)
        self.assertTrue(
            all(c in "0123456789abcdef" for c in short_sha),
            f"short_sha {short_sha!r} should be hex",
        )

    def test_dirty_repo_flagged(self):
        # Modify the tracked file; status --porcelain emits a
        # line so dirty=True.
        (self.workspace / "README").write_text("changed\n")
        branch, dirty, _ = self.state.get_git_info(self.workspace)
        self.assertEqual(branch, "main")
        self.assertTrue(dirty)

    def test_untracked_file_also_dirty(self):
        # status --porcelain shows untracked files as `?? path`,
        # which is non-empty stdout → dirty=True.
        (self.workspace / "new_file").write_text("hi\n")
        _, dirty, _ = self.state.get_git_info(self.workspace)
        self.assertTrue(dirty)

    def test_detached_head_has_no_branch_but_keeps_sha(self):
        # Check out a sha (not a branch) to put HEAD in detached
        # state. `git branch --show-current` returns empty, which
        # we normalise to None.
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace, capture_output=True, text=True, check=True,
        ).stdout.strip()
        _git("checkout", "-q", "--detach", rev, cwd=self.workspace)
        branch, dirty, short_sha = self.state.get_git_info(self.workspace)
        self.assertIsNone(branch)
        self.assertFalse(dirty)
        self.assertIsNotNone(short_sha)

    def test_non_git_workspace_returns_none_and_doesnt_raise(self):
        # A workspace that isn't a git repo at all should produce
        # the (None, False, None) fallback rather than blow up.
        with tempfile.TemporaryDirectory() as bare:
            branch, dirty, short_sha = self.state.get_git_info(Path(bare))
        self.assertIsNone(branch)
        self.assertFalse(dirty)
        self.assertIsNone(short_sha)

    def test_cache_hits_within_ttl(self):
        # Call once, then mutate the repo, then call again within
        # the TTL window — the second call should still return the
        # cached (clean) state.
        first = self.state.get_git_info(self.workspace)
        self.assertFalse(first[1], "baseline expected clean")

        (self.workspace / "README").write_text("post-cache mutation\n")
        second = self.state.get_git_info(self.workspace)

        self.assertEqual(
            first, second,
            "cache should not refresh within the TTL — the mutation "
            "made between calls shouldn't surface yet",
        )

    def test_cache_expires_after_ttl(self):
        # Drive the cache age past GIT_INFO_TTL_S by reaching into
        # the private member. Cheaper + more deterministic than
        # sleeping for 2 s in every test run. Verifies that an
        # expired cache triggers a fresh git lookup.
        self.state.get_git_info(self.workspace)
        (self.workspace / "README").write_text("post-cache mutation\n")
        # Backdate the cached "as-of" timestamp.
        self.state._git_info_at = 0.0
        _, dirty, _ = self.state.get_git_info(self.workspace)
        self.assertTrue(
            dirty,
            "expired cache should pick up the new dirty state",
        )


class SnapshotWithWorkspaceTest(unittest.TestCase):
    """The `/status` handler hands `state.snapshot(workspace=self.workspace)`
    to the client. The workspace-bearing call surfaces the same git
    triple that `get_git_info` returns, under the
    `git_branch` / `git_dirty` / `git_short_sha` keys.
    """

    def setUp(self):
        self.state = _M.BuildState()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = _make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_snapshot_without_workspace_omits_git_keys(self):
        # The default no-workspace caller (none of the SyncTeX or
        # other internal paths pass `workspace=`) shouldn't pay
        # for a git subprocess.
        snap = self.state.snapshot()
        self.assertNotIn("git_branch", snap)
        self.assertNotIn("git_dirty", snap)
        self.assertNotIn("git_short_sha", snap)

    def test_snapshot_with_workspace_includes_git_keys(self):
        snap = self.state.snapshot(workspace=self.workspace)
        self.assertIn("git_branch", snap)
        self.assertIn("git_dirty", snap)
        self.assertIn("git_short_sha", snap)
        self.assertEqual(snap["git_branch"], "main")
        self.assertFalse(snap["git_dirty"])

    def test_snapshot_preserves_existing_fields(self):
        # The git enrichment must not stomp on the keys the older
        # callers rely on (build_count, last_message, etc.).
        snap = self.state.snapshot(workspace=self.workspace)
        for key in (
            "build_count", "last_success", "last_elapsed_seconds",
            "last_finished_at", "last_message", "synctex_enabled",
        ):
            self.assertIn(key, snap, f"missing key {key} on snapshot")


if __name__ == "__main__":
    unittest.main()
