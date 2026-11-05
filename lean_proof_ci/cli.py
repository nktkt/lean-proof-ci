"""Command line interface; 0 = pass, 1 = audit failure, 2 = operational error."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .core import AuditError, audit, compare, inspect_project, load_config, load_snapshot
from .report import markdown, write_json, write_text


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
        if name == "check":
            c.add_argument("--baseline", type=Path, help="Reviewed snapshot; a missing/invalid file is an error")
        else:
            c.add_argument("--output", "-o", type=Path, help="Snapshot (default: PROJECT/lean-proof-ci.baseline.json)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    project = args.project.resolve()
    json_path = args.json or project / ".proof-ci/report.json"
    md_path = args.markdown or project / ".proof-ci/report.md"
    baseline_path = getattr(args, "baseline", None)
    output_path = getattr(args, "output", None) or project / "lean-proof-ci.baseline.json"
    report = {"schema_version": 1, "status": "error", "snapshot": None,
              "baseline": str(baseline_path) if baseline_path else None, "findings": [], "changes": []}
    exit_code = 2
    paths_safe = False
    try:
        outputs = [json_path.resolve(), md_path.resolve()]
        if len(set(outputs)) != 2:
            raise AuditError("JSON and Markdown report paths must differ")
        protected = [p.resolve() for p in (baseline_path, args.config) if p is not None]
        protected += [project / "lean-proof-ci.toml", project / "lean-toolchain", project / "lakefile.toml", project / "lakefile.lean"]
        if args.command == "snapshot":
            protected.append(output_path.resolve())
        if any(p in protected for p in outputs):
            raise AuditError("Report paths must not overwrite the baseline or project configuration")
        paths_safe = True
        if not project.is_dir():
            raise AuditError(f"Project directory does not exist: {project}")
        config = load_config(project, args.config, args.module, args.timeout)
        baseline = load_snapshot(baseline_path) if baseline_path else None
        snapshot = inspect_project(project, config)
        report["snapshot"] = snapshot
        report["findings"] = audit(snapshot)
        if baseline is not None:
            report["changes"] = compare(snapshot, baseline)
        failed = bool(report["findings"]) or any(c["fails"] for c in report["changes"])
        report["status"] = "fail" if failed else "pass"
        exit_code = 1 if failed else 0
        if args.command == "snapshot" and not failed:
            if output_path.resolve() in protected[:-1]:
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
        except OSError as exc:
            print(f"ERROR: Cannot write reports: {exc}", file=sys.stderr)
            return 2
    snapshot = report.get("snapshot")
    count = len(snapshot["declarations"]) if snapshot else 0
    print(f"{report['status'].upper()}: {count} declarations, {len(report['findings'])} policy violations, "
          f"{len(report['changes'])} baseline changes")
    for f in report["findings"]:
        print(f"  {f['code']}: {f['declaration']}: {f['message']}")
    for c in report["changes"]:
        print(f"  {c['code']}: {c['declaration'] or '(project)'}")
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    if paths_safe:
        print(f"Reports: {json_path}, {md_path}")
    return exit_code
