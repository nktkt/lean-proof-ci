import copy
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import sys
import tempfile
import unittest

from lean_proof_ci.cli import main
from lean_proof_ci.core import AuditError, compare, digest, load_config, load_snapshot, run, validate_snapshot
from lean_proof_ci.report import markdown


def example_snapshot():
    return {
        "schema_version": 1, "checker_version": "0.1.0", "toolchain": "leanprover/lean4:v4.29.1",
        "lean_version": "4.29.1", "roots": ["Demo"], "modules": ["Demo"],
        "allowed_axioms": ["propext"], "dependency_manifest_hash": None,
        "declarations": [{"name": "test", "module": "Demo", "kind": "theorem", "statement": "True",
                          "statement_hash": digest("True"), "definition_hash": None, "axioms": [], "line": 1}],
    }


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_scope_policy_toolchain_and_dependency_changes_fail(self):
        baseline = example_snapshot()
        for field, value, code in [
            ("roots", ["Other"], "scope_changed"),
            ("allowed_axioms", ["pretend"], "policy_changed"),
            ("toolchain", "nightly", "toolchain_changed"),
            ("dependency_manifest_hash", digest("updated"), "dependencies_changed"),
        ]:
            with self.subTest(field=field):
                current = copy.deepcopy(baseline)
                current[field] = value
                changes = compare(current, baseline)
                self.assertEqual(changes[0]["code"], code)
                self.assertTrue(changes[0]["fails"])

    def test_axiom_footprint_changes_require_review(self):
        baseline = example_snapshot()
        current = copy.deepcopy(baseline)
        current["declarations"][0]["axioms"] = ["propext"]
        self.assertEqual(compare(current, baseline)[0]["code"], "axioms_changed")

    def test_invalid_configuration_is_rejected(self):
        for text in ['modules = []', 'modules = ["Demo;evil"]', 'modules = ["Demo"]\nallowed_axioms = ["sorryAx"]',
                     'modules = ["Demo"]\ntimeout_seconds = 0', 'modules = ["Demo"]\nmoduels = ["Other"]']:
            with self.subTest(text=text):
                (self.root / "lean-proof-ci.toml").write_text(text)
                with self.assertRaises(AuditError):
                    load_config(self.root, None, None, None)

    def test_invalid_baseline_is_rejected(self):
        path = self.root / "baseline.json"
        path.write_text('{"schema_version": 999}')
        with self.assertRaises(AuditError):
            load_snapshot(path)
        snapshot = example_snapshot()
        del snapshot["declarations"][0]["definition_hash"]
        with self.assertRaises(AuditError):
            validate_snapshot(snapshot)
        snapshot = example_snapshot()
        snapshot["declarations"] *= 2
        with self.assertRaises(AuditError):
            validate_snapshot(snapshot)

    def test_optional_paths_preserve_old_snapshot_compatibility(self):
        snapshot = example_snapshot()
        validate_snapshot(snapshot)
        snapshot["declarations"][0]["axiom_paths"] = {"sorryAx": ["wrong", "sorryAx"]}
        with self.assertRaises(AuditError):
            validate_snapshot(snapshot)

    def test_html_output_cannot_overwrite_a_baseline(self):
        path = self.root / "lean-proof-ci.baseline.json"
        path.write_text("keep me")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["check", str(self.root), "--html", str(path)])
        self.assertEqual(code, 2)
        self.assertEqual(path.read_text(), "keep me")

    def test_missing_baseline_is_an_operational_error_with_report(self):
        (self.root / "lean-proof-ci.toml").write_text('modules = ["Demo"]')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["check", str(self.root), "--baseline", str(self.root / "missing.json")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads((self.root / ".proof-ci/report.json").read_text())["status"], "error")

    def test_colliding_report_cannot_destroy_baseline(self):
        path = self.root / "baseline.json"
        path.write_text("keep me")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["check", str(self.root), "--baseline", str(path), "--json", str(path)])
        self.assertEqual(code, 2)
        self.assertEqual(path.read_text(), "keep me")

    def test_failed_subprocess_is_not_a_pass(self):
        with self.assertRaisesRegex(AuditError, "Command failed \\(7\\)"):
            run([sys.executable, "-c", "raise SystemExit(7)"], self.root, 5)

    def test_timeout_is_not_a_pass(self):
        with self.assertRaisesRegex(AuditError, "timed out"):
            run([sys.executable, "-c", "import time; time.sleep(60)"], self.root, 1)

    def test_report_escapes_lean_names_and_statements(self):
        snapshot = example_snapshot()
        snapshot["declarations"][0]["statement"] = '<script>bad</script> | `code`\nnext'
        report = {"status": "pass", "snapshot": snapshot, "findings": [], "changes": []}
        rendered = markdown(report)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("\\|", rendered)
