"""CLI orchestration for ProofReview; ordinary audit/snapshot behavior stays intact."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from .core import AuditError, audit, inspect_project, load_config
from .report import review_markdown, write_json, write_text
from .review import evaluate_reviews, load_items, load_ledger, new_record, nonempty, source_path


def add_review_commands(commands: Any) -> None:
    for name, help_text in (("review", "Check source-to-Lean review records and show changed evidence"),
                            ("accept", "Record a correspondence review for the freshly inspected evidence")):
        c = commands.add_parser(name, help=help_text)
        c.add_argument("project", nargs="?", type=Path, default=Path("."), help="Lake project directory (default: .)")
        c.add_argument("--config", type=Path, help="Audit config (default: PROJECT/lean-proof-ci.toml)")
        c.add_argument("--module", action="append", help="Import root; repeat to override configured modules")
        c.add_argument("--timeout", type=int, help="Timeout in seconds per Lake/Lean command")
        c.add_argument("--manifest", type=Path, help="Mappings (default: PROJECT/lean-proof-review.toml)")
        c.add_argument("--ledger", type=Path, help="Review history (default: PROJECT/lean-proof-review.json)")
        c.add_argument("--json", type=Path, help="JSON report (default: PROJECT/.proof-ci/review.json)")
        c.add_argument("--markdown", type=Path, help="Markdown report (default: PROJECT/.proof-ci/review.md)")
        if name == "accept":
            c.add_argument("--item", required=True, help="Manifest item ID to record as reviewed")
            c.add_argument("--reviewer", required=True, help="Reviewer name; a self-reported label, not an authenticated identity")
            c.add_argument("--note", required=True, help="What was compared, including the assumptions and conclusion")


def guard_outputs(outputs: list[Path], protected: list[Path], project: Path) -> None:
    resolved = [path.resolve() for path in outputs]
    inputs = {path.resolve() for path in protected}
    if len(set(resolved)) != len(resolved):
        raise AuditError("Review output paths must differ")
    for path in resolved:
        inside_build = path.is_relative_to(project) and ".lake" in path.relative_to(project).parts
        if path in inputs or path.suffix == ".lean" or inside_build:
            raise AuditError(f"Review output must not overwrite a source, configuration, or build artifact: {path}")


def run_review(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    manifest = args.manifest or project / "lean-proof-review.toml"
    ledger_path = args.ledger or project / "lean-proof-review.json"
    json_path = args.json or project / ".proof-ci/review.json"
    md_path = args.markdown or project / ".proof-ci/review.md"
    report = {"schema_version": 1, "status": "error", "mechanical_status": "error", "snapshot": None,
              "manifest": str(manifest), "ledger": str(ledger_path), "findings": [], "reviews": [], "orphaned": []}
    paths_safe = False
    recorded = None
    exit_code = 2
    try:
        protected = [manifest, project / "lean-proof-ci.baseline.json", project / "lake-manifest.json",
                     project / "lean-proof-ci.toml", project / "lean-toolchain", project / "lakefile.toml",
                     project / "lakefile.lean"]
        if args.config is not None:
            protected.append(args.config)
        # These checks also apply when an invalid manifest prevents discovering its sources.
        guard_outputs([json_path, md_path], [*protected, ledger_path], project)
        default_reports = (json_path.resolve() == (project / ".proof-ci/review.json").resolve()
                           and md_path.resolve() == (project / ".proof-ci/review.md").resolve())
        # Refresh reserved reports on malformed input, without overwriting unknown sources.
        paths_safe = default_reports
        if args.command == "accept":
            paths_safe = False
            guard_outputs([ledger_path, json_path, md_path], protected, project)
            paths_safe = default_reports
            nonempty(args.reviewer, "reviewer")
            nonempty(args.note, "note")
        if not project.is_dir():
            raise AuditError(f"Project directory does not exist: {project}")
        items = load_items(project, manifest)
        protected.extend(source_path(project, item.source) for item in items)
        paths_safe = False
        guard_outputs([json_path, md_path], [*protected, ledger_path], project)
        if args.command == "accept":
            guard_outputs([ledger_path, json_path, md_path], protected, project)
        paths_safe = True
        if args.command == "accept" and args.item not in {item.id for item in items}:
            raise AuditError(f"Unknown review item: {args.item}")
        config = load_config(project, args.config, args.module, args.timeout)
        ledger = load_ledger(ledger_path)
        graph: dict[str, list[str]] = {}
        snapshot = inspect_project(project, config, dependency_graph=graph)
        report["snapshot"] = snapshot
        report["findings"] = audit(snapshot)
        report["mechanical_status"] = "fail" if report["findings"] else "pass"
        rows, orphaned = evaluate_reviews(project, items, snapshot, graph, ledger)
        if args.command == "accept" and not report["findings"]:
            row = next(row for row in rows if row["id"] == args.item)
            record = new_record(row, args.reviewer, args.note)
            ledger["records"].append(record)
            write_json(ledger_path, ledger)
            recorded = record["item_id"]
            rows, orphaned = evaluate_reviews(project, items, snapshot, graph, ledger)
        report["reviews"], report["orphaned"] = rows, orphaned
        failed = bool(report["findings"] or orphaned) or any(row["status"] != "current" for row in rows)
        report["status"] = "fail" if failed else "pass"
        # `accept` acknowledges a successful record operation; `review` is the CI gate.
        exit_code = 0 if recorded is not None else (1 if failed else 0)
    except (AuditError, OSError, UnicodeError) as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        exit_code = 2
    if paths_safe:
        try:
            write_json(json_path, report)
            write_text(md_path, review_markdown(report))
        except OSError as exc:
            print(f"ERROR: Cannot write review reports: {exc}", file=sys.stderr)
            return 2
    if recorded is not None:
        print(f"Recorded review: {recorded} -> {ledger_path}")
    current = sum(row["status"] == "current" for row in report["reviews"])
    print(f"{report['status'].upper()}: machine audit {report['mechanical_status']}; "
          f"{current}/{len(report['reviews'])} reviews current")
    for row in report["reviews"]:
        print(f"  {row['status'].upper()}: {row['id']} -> {row['declaration']}")
        for change in row["changes"]:
            print(f"    {change['code']}: {change['message']}")
    for finding in report["findings"]:
        print(f"  {finding['code']}: {finding['declaration']}: {finding['message']}")
    for item in report["orphaned"]:
        print(f"  ORPHANED: {item} has records but no manifest mapping")
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    if paths_safe:
        print(f"Reports: {json_path}, {md_path}")
    return exit_code
