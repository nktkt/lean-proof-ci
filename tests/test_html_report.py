from html.parser import HTMLParser
import unittest

from lean_proof_ci.html_report import html_report, statement_diff
from tests.test_core import example_snapshot


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.ids.extend(value for key, value in attrs if key == "id")


class HtmlReportTests(unittest.TestCase):
    def test_untrusted_statements_are_text_not_markup(self):
        snapshot = example_snapshot()
        attack = '</pre><script>alert(1)</script><img src=x onerror=alert(1)> | "'
        snapshot["declarations"][0]["statement"] = attack
        result = html_report({"status": "pass", "snapshot": snapshot})
        parser = Tags()
        parser.feed(result)
        self.assertEqual(parser.tags.count("script"), 1)  # The report's own filter script only.
        self.assertNotIn("img", parser.tags)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", result)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertIn("Axiom audit only", result)

    def test_statement_diff_marks_added_assumptions(self):
        before, after = statement_diff("∀ n, n = n", "∀ n, n = 0 → n = n")
        self.assertIn("<ins>", after)
        self.assertNotIn("<script>", ''.join(statement_diff("True", "<script>x</script>")))
        self.assertEqual(before, "∀ n, n = n")

    def test_error_report_does_not_claim_a_passing_audit(self):
        result = html_report({"status": "error", "snapshot": None, "error": "bad <input>"})
        self.assertIn("Inspection failed", result)
        self.assertIn("bad &lt;input&gt;", result)
        self.assertNotIn("No violations of the configured policy were found", result)

    def test_dependency_paths_are_visible(self):
        result = html_report({"status": "fail", "snapshot": example_snapshot(), "findings": [{
            "code": "incomplete_proof", "declaration": "test", "message": "Depends on sorryAx",
            "paths": {"sorryAx": ["test", "helper", "sorryAx"]},
        }]})
        self.assertIn("Dependency path", result)
        self.assertIn("<code>helper</code>", result)

    def test_same_pretty_statement_explains_structural_change(self):
        result = html_report({"status": "fail", "snapshot": example_snapshot(), "changes": [{
            "code": "statement_changed", "declaration": "test", "before": "True", "after": "True",
            "before_hash": "old", "after_hash": "new", "fails": True,
        }]})
        self.assertIn("pretty-printed statement is unchanged", result)
        self.assertIn("Before: old", result)
