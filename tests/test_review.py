"""Review evidence, malformed inputs, and report/source separation."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from lean_proof_ci.cli import main
from lean_proof_ci.core import AuditError, digest, expression_dependencies
from lean_proof_ci.report import review_markdown, write_json
from lean_proof_ci.review import evaluate_reviews, load_items, load_ledger, new_record

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/basic"


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        shutil.copytree(EXAMPLE / "specs", self.project / "specs")
        for name in ("lean-proof-review.toml", "lean-proof-ci.toml"):
            shutil.copyfile(EXAMPLE / name, self.project / name)
        self.manifest = self.project / "lean-proof-review.toml"
        self.ledger_path = self.project / "lean-proof-review.json"
        self.items = load_items(self.project, self.manifest)
        self.snapshot = json.loads((EXAMPLE / "lean-proof-ci.baseline.json").read_text())
        self.graph = {d["name"]: [] for d in self.snapshot["declarations"]}
        self.graph["Demo.twice_eq_add"] = ["Demo.twice"]
        self.ledger = {"schema_version": 1, "records": []}

    def evaluate(self):
        return evaluate_reviews(self.project, self.items, self.snapshot, self.graph, self.ledger)

    def accept_all(self):
        rows, _ = self.evaluate()
        self.ledger["records"] = [new_record(row, "Automated test fixture", "Synthetic review for a test.") for row in rows]

    def test_source_changes_expire_only_their_own_review(self):
        self.accept_all()
        self.assertTrue(all(row["status"] == "current" for row in self.evaluate()[0]))
        source = self.project / "specs/twice.md"
        source.write_text(source.read_text() + "追加の仮定を検討する。\n")
        rows, _ = self.evaluate()
        self.assertEqual([row["status"] for row in rows], ["stale", "current", "current"])
        self.assertEqual(rows[0]["changes"][0]["code"], "source_changed")

    def test_transitive_definitions_and_cycles_are_tracked(self):
        twice = next(d for d in self.snapshot["declarations"] if d["name"] == "Demo.twice")
        helper = {**twice, "name": "Demo.helper"}
        self.snapshot["declarations"].append(helper)
        self.graph["Demo.twice"] = ["Demo.helper"]
        self.graph["Demo.helper"] = ["Demo.twice", "External.untracked"]
        self.accept_all()
        helper["definition_hash"] = digest("new body")
        rows, _ = self.evaluate()
        self.assertEqual([row["status"] for row in rows], ["stale", "current", "current"])
        self.assertEqual(rows[0]["changes"], [{"code": "definition_changed", "declaration": "Demo.helper",
                                              "message": "Demo.helper: definition changed"}])

    def test_missing_graph_data_is_an_error_not_a_partial_review(self):
        del self.graph["Demo.twice"]
        with self.assertRaisesRegex(AuditError, "Missing inspection dependency"):
            self.evaluate()

    def test_mapping_and_environment_changes_expire_reviews(self):
        self.accept_all()
        self.manifest.write_text(self.manifest.read_text().replace("/ twice / v1", "/ twice / v2"))
        self.items = load_items(self.project, self.manifest)
        self.assertEqual(self.evaluate()[0][0]["changes"][0]["code"], "mapping_changed")
        self.snapshot["dependency_manifest_hash"] = digest("new lockfile")
        rows, _ = self.evaluate()
        self.assertTrue(all(row["status"] == "stale" for row in rows))
        self.assertIn("environment_changed", [c["code"] for c in rows[1]["changes"]])

    def test_new_constructor_invalidates_records_even_if_existing_types_stay_equal(self):
        self.accept_all()
        d = self.snapshot["declarations"][0]
        self.snapshot["declarations"].append({**d, "name": "Demo.Color.blue", "kind": "constructor"})
        self.graph["Demo.Color.blue"] = []
        self.assertTrue(all(row["status"] == "stale" for row in self.evaluate()[0]))

    def test_latest_record_wins_and_old_evidence_does_not_auto_approve_a_revert(self):
        self.accept_all()
        original = (self.project / "specs/twice.md").read_bytes()
        (self.project / "specs/twice.md").write_bytes(original + b"\nChanged source.\n")
        self.ledger["records"].append(new_record(self.evaluate()[0][0], "Second test fixture", "Review the revised text."))
        (self.project / "specs/twice.md").write_bytes(original)
        self.assertEqual(self.evaluate()[0][0]["status"], "stale")

    def test_missing_declaration_and_removed_mapping_cannot_silently_pass(self):
        self.accept_all()
        self.snapshot["declarations"] = [d for d in self.snapshot["declarations"] if d["name"] != "Demo.twice_eq_add"]
        row = self.evaluate()[0][0]
        self.assertEqual(row["status"], "missing")
        with self.assertRaisesRegex(AuditError, "missing declaration"):
            new_record(row, "Test", "Cannot accept a missing target.")
        self.items = self.items[1:]
        self.assertEqual(self.evaluate()[1], ["twice"])

    def test_ledger_round_trip_and_corrupt_evidence_rejected(self):
        self.accept_all()
        write_json(self.ledger_path, self.ledger)
        self.assertEqual(load_ledger(self.ledger_path), self.ledger)
        self.ledger["records"][0]["evidence"]["source_hash"] = digest("different")
        write_json(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(AuditError, "fingerprint does not match"):
            load_ledger(self.ledger_path)

    def test_malformed_ledger_cannot_be_treated_as_empty(self):
        for value in ([], {"schema_version": True, "records": []}, {"schema_version": 1, "records": [None]}):
            with self.subTest(value=value):
                write_json(self.ledger_path, value)
                with self.assertRaises(AuditError):
                    load_ledger(self.ledger_path)
        self.accept_all()
        for field, value in (("reviewer", " "), ("note", ""), ("reviewed_at", "2026-01-01"),
                             ("item_id", "wrong")):
            ledger = copy.deepcopy(self.ledger)
            ledger["records"][0][field] = value
            write_json(self.ledger_path, ledger)
            with self.subTest(field=field), self.assertRaises(AuditError):
                load_ledger(self.ledger_path)

    def test_manifest_typos_duplicates_and_escaping_paths_fail(self):
        valid = self.manifest.read_text()
        for content in (valid.replace("source =", "soruce =", 1),
                        valid.replace('id = "add-zero"', 'id = "twice"'),
                        valid.replace('"specs/twice.md"', '"../outside.md"'),
                        valid.replace('"specs/twice.md"', '".proof-ci/review.md"'),
                        "schema_version = 1\nitems = []\n"):
            self.manifest.write_text(content)
            with self.subTest(content=content), self.assertRaises(AuditError):
                load_items(self.project, self.manifest)

    def test_symlink_source_cannot_escape_the_project(self):
        (self.project / "specs/link.md").symlink_to(self.project.parent / "outside.md")
        self.manifest.write_text(self.manifest.read_text().replace("specs/twice.md", "specs/link.md"))
        with self.assertRaisesRegex(AuditError, "inside the project"):
            load_items(self.project, self.manifest)

    def test_report_paths_cannot_overwrite_source_or_ledger(self):
        source = self.project / "specs/twice.md"
        original = source.read_bytes()
        for args in (["review", "--markdown", str(source)],
                     ["review", "--json", str(self.manifest)],
                     ["accept", "--ledger", str(source), "--item", "twice", "--reviewer", "Test", "--note", "Test"]):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main([args[0], str(self.project), *args[1:]])
            self.assertEqual(code, 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((self.project / ".proof-ci/review.json").exists())

    def test_invalid_manifest_replaces_default_success_report(self):
        path = self.project / ".proof-ci/review.json"
        write_json(path, {"status": "pass"})
        self.manifest.write_text("invalid toml [")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["review", str(self.project)]), 2)
        self.assertEqual(json.loads(path.read_text())["status"], "error")

    def test_expression_references_include_projections_but_not_literal_text(self):
        expression = ["app", ["const", "Demo.twice", []], ["proj", "Demo.Box", 0, ["bvar", 0]]]
        self.assertEqual(expression_dependencies(expression, ["string", "Demo.not_a_reference"]), ["Demo.Box", "Demo.twice"])

    def test_report_shows_separate_verdicts_and_escapes_source_and_notes(self):
        (self.project / "specs/twice.md").write_text('<script>alert(1)</script> | `text`\n')
        self.accept_all()
        self.ledger["records"][0]["note"] = "<img src=x>"
        rows, orphaned = self.evaluate()
        report = {"status": "pass", "mechanical_status": "pass", "reviews": rows, "orphaned": orphaned, "findings": []}
        rendered = review_markdown(report)
        self.assertIn("Machine audit: **PASS**", rendered)
        self.assertIn("Correspondence reviews current: **3/3**", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("Demo.twice", rendered)
