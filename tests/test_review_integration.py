"""Fresh Lean inspections drive every acceptance and stale-review decision."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/basic"


class ReviewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("lake") is None:
            raise RuntimeError("Install elan/Lean before running the integration tests")
        cls.shared = tempfile.TemporaryDirectory(prefix="proof-review-tests-")
        cls.clean = Path(cls.shared.name) / "clean"
        shutil.copytree(EXAMPLE, cls.clean, ignore=shutil.ignore_patterns(".lake", ".proof-ci", "lean-proof-review.json"))
        # Pin a real, fully inspected synthetic review for each test's starting point.
        for item in ("twice", "add-zero", "add-comm"):
            result = subprocess.run([sys.executable, "-m", "lean_proof_ci", "accept", str(cls.clean),
                                     "--item", item, "--reviewer", "Automated test fixture",
                                     "--note", "Synthetic correspondence review used only for tests."],
                                    cwd=ROOT, capture_output=True, text=True, timeout=240)
            if result.returncode:
                cls.shared.cleanup()
                raise RuntimeError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="proof-review-case-")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        shutil.copytree(self.clean, self.project, ignore=shutil.ignore_patterns(".proof-ci"))
        self.source = self.project / "Demo/Basic.lean"
        self.ledger = self.project / "lean-proof-review.json"

    def invoke(self, *args, command="review", expected=0):
        result = subprocess.run([sys.executable, "-m", "lean_proof_ci", command, str(self.project), *args],
                                cwd=ROOT, capture_output=True, text=True, timeout=240)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads((self.project / ".proof-ci/review.json").read_text())

    def test_pending_then_fresh_record_then_source_stale_then_rereview(self):
        self.ledger.unlink()
        report = self.invoke(expected=1)
        self.assertEqual(report["mechanical_status"], "pass")
        self.assertTrue(all(row["status"] == "pending" for row in report["reviews"]))
        for item in ("twice", "add-zero", "add-comm"):
            self.invoke("--item", item, "--reviewer", "Test fixture", "--note", "Checked quantifiers and conclusion.", command="accept")
        self.assertEqual(self.invoke()["status"], "pass")
        source = self.project / "specs/twice.md"
        source.write_text(source.read_text() + "Changed specification.\n")
        self.assertEqual(self.invoke(expected=1)["reviews"][0]["status"], "stale")
        self.invoke("--item", "twice", "--reviewer", "Second fixture", "--note", "Rechecked updated source.", command="accept")
        self.assertEqual(len(json.loads(self.ledger.read_text())["records"]), 4)
        self.assertEqual(self.invoke()["status"], "pass")

    def test_definition_change_only_expires_related_review(self):
        self.source.write_text(self.source.read_text().replace("def twice (n : Nat) : Nat := n + n", "def twice (n : Nat) : Nat := n + n + 0"))
        report = self.invoke(expected=1)
        self.assertEqual(report["mechanical_status"], "pass")
        self.assertEqual([row["status"] for row in report["reviews"]], ["stale", "current", "current"])
        self.assertIn({"code": "definition_changed", "declaration": "Demo.twice", "message": "Demo.twice: definition changed"},
                      report["reviews"][0]["changes"])

    def test_added_assumption_expires_a_valid_theorem(self):
        self.source.write_text(self.source.read_text().replace("theorem add_zero (n : Nat)", "theorem add_zero (n : Nat) (_h : n = 0)"))
        report = self.invoke(expected=1)
        self.assertEqual(report["mechanical_status"], "pass")
        self.assertEqual(report["reviews"][1]["changes"][0]["code"], "statement_changed")

    def test_transitive_definition_is_inferred_from_real_lean_expressions(self):
        self.source.write_text(self.source.read_text().replace(
            "def twice (n : Nat) : Nat := n + n",
            "def helper (n : Nat) : Nat := n + n\ndef twice (n : Nat) : Nat := helper n"))
        self.invoke("--item", "twice", "--reviewer", "Test fixture", "--note", "Reviewed the helper definition.", command="accept")
        self.source.write_text(self.source.read_text().replace(
            "def helper (n : Nat) : Nat := n + n", "def helper (n : Nat) : Nat := n + n + 0"))
        report = self.invoke(expected=1)
        self.assertEqual([row["status"] for row in report["reviews"]], ["stale", "current", "current"])
        self.assertIn({"code": "definition_changed", "declaration": "Demo.helper", "message": "Demo.helper: definition changed"},
                      report["reviews"][0]["changes"])

    def test_proof_edit_and_binder_rename_preserve_review(self):
        self.source.write_text(self.source.read_text().replace(
            "theorem add_zero (n : Nat) : n + 0 = n := by\n  rfl",
            "theorem add_zero (x : Nat) : x + 0 = x := by\n  exact Nat.add_zero x"))
        report = self.invoke()
        self.assertTrue(all(row["status"] == "current" for row in report["reviews"]))

    def test_unrelated_definition_does_not_expire_reviews(self):
        self.source.write_text(self.source.read_text() + "\ndef unrelated : Nat := 37\n")
        self.assertEqual(self.invoke()["status"], "pass")

    def test_sorry_cannot_be_accepted_and_leaves_history_unchanged(self):
        original = self.ledger.read_bytes()
        self.source.write_text(self.source.read_text() + "\ntheorem unfinished : False := by sorry\n")
        report = self.invoke("--item", "twice", "--reviewer", "Test fixture", "--note", "Must not record.",
                             command="accept", expected=1)
        self.assertEqual(report["mechanical_status"], "fail")
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_removed_target_and_unknown_item_are_not_accepted(self):
        original = self.ledger.read_bytes()
        report = self.invoke("--item", "typo", "--reviewer", "Test fixture", "--note", "Must not record.",
                             command="accept", expected=2)
        self.assertIn("Unknown review item", report["error"])
        self.source.write_text(self.source.read_text().replace("theorem add_zero (n : Nat) : n + 0 = n := by\n  rfl", ""))
        self.assertEqual(self.invoke(expected=1)["reviews"][1]["status"], "missing")
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_build_failure_replaces_success_and_cannot_record(self):
        self.invoke()
        original = self.ledger.read_bytes()
        self.source.write_text(self.source.read_text() + "\ntheorem illTyped : False := True.intro\n")
        report = self.invoke("--item", "twice", "--reviewer", "Test fixture", "--note", "Must not record.",
                             command="accept", expected=2)
        self.assertEqual(report["status"], "error")
        self.assertIsNone(report["snapshot"])
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_missing_source_is_an_error_not_an_empty_specification(self):
        (self.project / "specs/twice.md").unlink()
        report = self.invoke(expected=2)
        self.assertIn("Cannot read review source", report["error"])
