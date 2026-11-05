"""End-to-end tests against a real, pinned Lean compiler, not mocked grep output."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/basic"


class LeanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("lake") is None:
            raise RuntimeError("Install elan/Lean before running the integration tests")
        cls.shared = tempfile.TemporaryDirectory(prefix="proof-ci-tests-")
        cls.clean = Path(cls.shared.name) / "clean"
        shutil.copytree(EXAMPLE, cls.clean, ignore=shutil.ignore_patterns(".lake", ".proof-ci", "lean-proof-ci.baseline.json"))
        result = subprocess.run(
            [sys.executable, "-m", "lean_proof_ci", "snapshot", str(cls.clean)],
            cwd=ROOT, text=True, capture_output=True, timeout=240,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.baseline = cls.clean / "lean-proof-ci.baseline.json"

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="proof-ci-case-")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        shutil.copytree(self.clean, self.project, ignore=shutil.ignore_patterns(".proof-ci"))
        self.source = self.project / "Demo/Basic.lean"

    def invoke(self, *args, expected=0, command="check"):
        result = subprocess.run(
            [sys.executable, "-m", "lean_proof_ci", command, str(self.project), *args],
            cwd=ROOT, text=True, capture_output=True, timeout=240,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        report = json.loads((self.project / ".proof-ci/report.json").read_text())
        self.assertEqual(report["status"], {0: "pass", 1: "fail", 2: "error"}[expected])
        return report

    def append(self, source):
        self.source.write_text(self.source.read_text() + "\n" + source + "\n")

    def compared(self, expected=0):
        return self.invoke("--baseline", str(self.baseline), expected=expected)

    def test_clean_project_has_inventory_and_no_changes(self):
        report = self.compared()
        self.assertEqual(len(report["snapshot"]["declarations"]), 4)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["snapshot"]["modules"], ["Demo", "Demo.Basic"])

    def test_direct_sorry_fails_and_cannot_be_snapshotted(self):
        self.append("theorem broken : False := by sorry")
        report = self.invoke(expected=1)
        self.assertIn("incomplete_proof", [f["code"] for f in report["findings"]])
        target = self.project / "should-not-exist.json"
        self.invoke("--output", str(target), command="snapshot", expected=1)
        self.assertFalse(target.exists())

    def test_transitive_sorry_crosses_modules_and_namespaces(self):
        (self.project / "Demo/Helper.lean").write_text("namespace Unrelated\ntheorem helper : False := by sorry\nend Unrelated\n")
        self.source.write_text("import Demo.Helper\n" + self.source.read_text())
        self.append("theorem consumer : False := Unrelated.helper")
        report = self.invoke(expected=1)
        affected = {f["declaration"] for f in report["findings"] if f["code"] == "incomplete_proof"}
        self.assertTrue({"Unrelated.helper", "consumer"} <= affected)
        consumer = next(f for f in report["findings"] if f["declaration"] == "consumer")
        self.assertEqual(consumer["paths"]["sorryAx"], ["consumer", "Unrelated.helper", "sorryAx"])

    def test_private_theorem_and_unused_definition_are_audited(self):
        self.append("private theorem hidden : False := by sorry\ndef unfinishedValue : Nat := by sorry")
        report = self.invoke(expected=1)
        affected = {f["declaration"] for f in report["findings"]}
        self.assertIn("unfinishedValue", affected)
        self.assertTrue(any("hidden" in name for name in affected))

    def test_custom_axiom_and_its_consumer_fail(self):
        self.append("axiom pretend : False\ntheorem arbitrary : 1 = 2 := False.elim pretend")
        report = self.invoke(expected=1)
        affected = {f["declaration"] for f in report["findings"] if f["code"] == "unapproved_axiom"}
        self.assertTrue({"pretend", "arbitrary"} <= affected)
        consumer = next(f for f in report["findings"] if f["declaration"] == "arbitrary")
        self.assertEqual(consumer["paths"]["pretend"], ["arbitrary", "pretend"])

    def test_rewritten_working_baseline_does_not_hide_change_from_git(self):
        def git(*args):
            return subprocess.run(
                ["git", "-c", "user.name=Proof CI tests", "-c", "user.email=tests@example.invalid",
                 "-c", "commit.gpgsign=false", *args],
                cwd=self.project, capture_output=True, text=True, check=True,
            )
        git("init", "-q", "-b", "main")
        git("add", "lean-proof-ci.baseline.json")
        git("commit", "-qm", "Reviewed baseline")
        self.source.write_text(self.source.read_text().replace("theorem add_zero (n : Nat)", "theorem add_zero (n : Nat) (_h : n = 0)"))
        self.invoke(command="snapshot")
        self.invoke("--baseline", str(self.project / "lean-proof-ci.baseline.json"))
        report = self.invoke("--baseline-ref", "HEAD", expected=1)
        self.assertIn("statement_changed", [c["code"] for c in report["changes"]])
        self.assertEqual(report["baseline_source"]["commit"], git("rev-parse", "HEAD").stdout.strip())

    def test_axioms_inside_axiom_types_are_not_hidden(self):
        self.append("axiom seed : Nat\naxiom witness : seed = seed\ntheorem useWitness : seed = seed := witness")
        (self.project / "lean-proof-ci.toml").write_text('modules = ["Demo"]\nallowed_axioms = ["witness"]\n')
        report = self.invoke(expected=1)
        finding = next(f for f in report["findings"] if f["declaration"] == "useWitness")
        self.assertEqual(finding["axioms"], ["seed"])

    def test_standard_axiom_is_reported_and_allowed(self):
        self.append("theorem propEquality (p q : Prop) (h : p ↔ q) : p = q := propext h")
        report = self.invoke()
        d = next(d for d in report["snapshot"]["declarations"] if d["name"] == "propEquality")
        self.assertEqual(d["axioms"], ["propext"])

    def test_native_decide_requires_explicit_trust(self):
        self.source.write_text("import Lean\n" + self.source.read_text())
        self.append("theorem nativeAnswer : (List.range 10).length = 10 := by native_decide")
        report = self.invoke(expected=1)
        affected = [f for f in report["findings"] if f["declaration"] == "nativeAnswer"]
        self.assertTrue(affected)
        self.assertEqual(affected[0]["code"], "unapproved_axiom")

    def test_weaker_statement_fails_even_with_a_valid_proof(self):
        self.source.write_text(self.source.read_text().replace("theorem add_zero (n : Nat)", "theorem add_zero (n : Nat) (_h : n = 0)"))
        report = self.compared(expected=1)
        self.assertIn("statement_changed", [c["code"] for c in report["changes"]])
        self.assertEqual(report["findings"], [])

    def test_proof_only_changes_and_binder_renaming_pass(self):
        self.source.write_text(self.source.read_text().replace(
            "theorem add_zero (n : Nat) : n + 0 = n := by\n  rfl",
            "theorem add_zero (x : Nat) : x + 0 = x := by\n  exact Nat.add_zero x"))
        self.assertEqual(self.compared()["changes"], [])

    def test_definition_body_change_requires_review(self):
        self.source.write_text(self.source.read_text().replace("def twice (n : Nat) : Nat := n + n", "def twice (n : Nat) : Nat := n + n + 0"))
        report = self.compared(expected=1)
        self.assertIn("definition_changed", [c["code"] for c in report["changes"]])

    def test_deleted_declaration_fails(self):
        self.source.write_text(self.source.read_text().replace("theorem add_zero (n : Nat) : n + 0 = n := by\n  rfl", ""))
        report = self.compared(expected=1)
        self.assertIn("declaration_removed", [c["code"] for c in report["changes"]])

    def test_new_valid_theorem_is_informational(self):
        self.append("theorem newFact : True := True.intro")
        report = self.compared()
        self.assertEqual([c["code"] for c in report["changes"]], ["declaration_added"])

    def test_build_error_replaces_previous_success_report(self):
        self.invoke()
        self.append("theorem illTyped : False := True.intro")
        report = self.invoke(expected=2)
        self.assertIsNone(report["snapshot"])
        self.assertIn("Command failed", report["error"])

    def test_empty_audit_is_an_error(self):
        (self.project / "Demo.lean").write_text("-- No imported project declarations.\n")
        report = self.invoke(expected=2)
        self.assertIn("No declarations", report["error"])

    def test_missing_module_is_an_error(self):
        report = self.invoke("--module", "MissingModule", expected=2)
        self.assertIn("Command failed", report["error"])
