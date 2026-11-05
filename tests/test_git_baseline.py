"""Read real Git objects, including when the working baseline has been replaced."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from lean_proof_ci.core import AuditError
from lean_proof_ci.git_baseline import load_git_baseline
from tests.test_core import example_snapshot


class GitBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="proof-ci-git-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.project = self.repo / "projects" / "library with spaces"
        self.project.mkdir(parents=True)
        self.path = self.project / "lean-proof-ci.baseline.json"
        self.original = example_snapshot()
        self.path.write_text(json.dumps(self.original))
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-qm", "Reviewed baseline")
        self.sha = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        result = subprocess.run(
            ["git", "-c", "user.name=Proof CI tests", "-c", "user.email=tests@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            cwd=self.repo, text=True, capture_output=True, check=True,
        )
        return result.stdout

    def test_working_tree_changes_do_not_change_the_git_baseline(self):
        self.path.write_text("invalid replacement")
        snapshot, source = load_git_baseline(self.project, "HEAD")
        self.assertEqual(snapshot, self.original)
        self.assertEqual(source["commit"], self.sha)
        self.assertEqual(source["path"], "projects/library with spaces/lean-proof-ci.baseline.json")
        self.assertEqual(self.path.read_text(), "invalid replacement")

    def test_explicit_repository_path(self):
        snapshot, _ = load_git_baseline(self.repo, self.sha, self.path.relative_to(self.repo).as_posix())
        self.assertEqual(snapshot, self.original)

    def test_unknown_ref_and_missing_historical_file_fail(self):
        with self.assertRaises(AuditError):
            load_git_baseline(self.project, "missing-branch")
        with self.assertRaises(AuditError):
            load_git_baseline(self.project, "HEAD", "not-in-the-commit.json")

    def test_path_traversal_and_option_like_refs_are_rejected(self):
        for path in ("../baseline.json", "/tmp/baseline.json", "bad\npath", "C:\\baseline.json", "."):
            with self.subTest(path=path), self.assertRaises(AuditError):
                load_git_baseline(self.project, "HEAD", path)
        with self.assertRaises(AuditError):
            load_git_baseline(self.project, "--help")

    def test_malformed_committed_snapshot_fails(self):
        self.path.write_text('{"schema_version": 999}')
        self.git("add", ".")
        self.git("commit", "-qm", "Invalid baseline")
        with self.assertRaises(AuditError):
            load_git_baseline(self.project, "HEAD")
