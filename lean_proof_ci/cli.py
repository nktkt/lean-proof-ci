"""Command line interface; 0 = pass, 1 = audit failure, 2 = operational error."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .core import AuditError, audit, compare, inspect_project, load_config, load_snapshot
from .git_baseline import load_git_baseline
from .html_report import html_report
from .report import markdown, write_json, write_text
from .review_cli import add_review_commands, run_review


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit Lean axiom dependencies and declaration changes.")
    p.add_argument("--version", action="version", version=__version__)
    commands = p.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "Audit a project, optionally comparing a baseline"),
                            ("snapshot", "Write a reviewed baseline after a clean axiom audit")):
        c = commands.add_parser(name, help=help_text)
        c.add_argument("project", nargs="?", type=Path, default=Path("."), help="Lake project directory (default: .)")
        c.add_argument("--config", type=Path, help="Config file (default: PROJECT/lean-proof-ci.toml)")
        c.add_argument("--module", action="append", help="Import root; repeat to override configured modules")
        c.add_argument("--timeout", type=int, help="Timeout in seconds per Lake/Lean command")
        c.add_argument("--json", type=Path, help="JSON report (default: PROJECT/.proof-ci/report.json)")
        c.add_argument("--markdown", type=Path, help="Markdown report (default: PROJECT/.proof-ci/report.md)")
        c.add_argument("--html", type=Path, help="Searchable offline report (default: PROJECT/.proof-ci/report.html)")
        if name == "check":
            baseline = c.add_mutually_exclusive_group()
            baseline.add_argument("--baseline", type=Path, help="Reviewed snapshot; a missing/invalid file is an error")
            baseline.add_argument("--baseline-ref", help="Read the reviewed baseline from a Git commit, branch, or tag")
            c.add_argument("--baseline-path", help="Baseline path inside the Git repository (requires --baseline-ref)")
        else:
            c.add_argument("--output", "-o", type=Path, help="Snapshot (default: PROJECT/lean-proof-ci.baseline.json)")
    add_review_commands(commands)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command in ("review", "accept"):
        return run_review(args)
    project = args.project.resolve()
    json_path = args.json or project / ".proof-ci/report.json"
    md_path = args.markdown or project / ".proof-ci/report.md"
    html_path = args.html or project / ".proof-ci/report.html"
    baseline_path = getattr(args, "baseline", None)
    baseline_ref = getattr(args, "baseline_ref", None)
    git_path = getattr(args, "baseline_path", None)
    output_path = getattr(args, "output", None) or project / "lean-proof-ci.baseline.json"
    report = {"schema_version": 1, "status": "error", "snapshot": None,
              "baseline": str(baseline_path) if baseline_path else None, "baseline_source": None,
              "findings": [], "changes": []}
    exit_code = 2
    paths_safe = False
    try:
        outputs = [p.resolve() for p in (json_path, md_path, html_path)]
        if len(set(outputs)) != 3:
            raise AuditError("JSON, Markdown, and HTML report paths must differ")
        inputs = [baseline_path, args.config, project / "lean-proof-ci.baseline.json"]
        inputs += [project / p for p in ("lean-proof-ci.toml", "lean-toolchain", "lakefile.toml", "lakefile.lean")]
        protected = [p.resolve() for p in inputs if p is not None]
        if args.command == "snapshot":
            protected.append(output_path.resolve())
        if any(p in protected for p in outputs):
            raise AuditError("Report paths must not overwrite the baseline or project configuration")
        paths_safe = True
        if not project.is_dir():
            raise AuditError(f"Project directory does not exist: {project}")
        if git_path is not None and baseline_ref is None:
            raise AuditError("--baseline-path requires --baseline-ref")
        config = load_config(project, args.config, args.module, args.timeout)
        baseline = None
        if baseline_ref is not None:
            baseline, source = load_git_baseline(project, baseline_ref, git_path)
            report["baseline_source"] = source
            report["baseline"] = f"{source['commit']}:{source['path']}"
        elif baseline_path is not None:
            baseline = load_snapshot(baseline_path)
            report["baseline_source"] = {"kind": "file", "path": str(baseline_path)}
        snapshot = inspect_project(project, config)
        report["snapshot"] = snapshot
        report["findings"] = audit(snapshot)
        if baseline is not None:
            report["changes"] = compare(snapshot, baseline)
        failed = bool(report["findings"]) or any(c["fails"] for c in report["changes"])
        report["status"] = "fail" if failed else "pass"
        exit_code = 1 if failed else 0
        if args.command == "snapshot" and not failed:
            snapshot_inputs = [p.resolve() for p in inputs if p is not None and p != project / "lean-proof-ci.baseline.json"]
            if output_path.resolve() in snapshot_inputs:
                raise AuditError("Snapshot path must not overwrite project configuration")
            write_json(output_path, snapshot)
            print(f"Snapshot written: {output_path}")
    except (AuditError, OSError, UnicodeError) as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        exit_code = 2
    # Do not write anything when output paths collide with protected inputs.
    if paths_safe:
        try:
            write_json(json_path, report)
            write_text(md_path, markdown(report))
            write_text(html_path, html_report(report))
        except OSError as exc:
            print(f"ERROR: Cannot write reports: {exc}", file=sys.stderr)
            return 2
    snapshot = report.get("snapshot")
    count = len(snapshot["declarations"]) if snapshot else 0
    print(f"{report['status'].upper()}: {count} declarations, {len(report['findings'])} policy violations, "
          f"{len(report['changes'])} baseline changes")
    for f in report["findings"]:
        print(f"  {f['code']}: {f['declaration']}: {f['message']}")
        for path in f.get("paths", {}).values():
            print("    " + " -> ".join(path))
    for c in report["changes"]:
        print(f"  {c['code']}: {c['declaration'] or '(project)'}")
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    if paths_safe:
        print(f"Reports: {json_path}, {md_path}, {html_path}")
    return exit_code
