"""Machine-readable reports and a GitHub-job-summary-friendly Markdown view."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(text)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def cell(value: Any) -> str:
    if value is None:
        return "—"
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return html.escape(value).replace("|", "\\|").replace("`", "&#96;").replace("\n", "<br>")


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# Lean Proof CI: {report['status'].upper()}", ""]
    snapshot = report.get("snapshot")
    if snapshot is not None:
        lines += [f"Lean {cell(snapshot['lean_version'])} · {len(snapshot['declarations'])} declarations · "
                  f"roots: {cell(', '.join(snapshot['roots']))}", "",
                  "Baseline comparison: " + (cell(report["baseline"]) if report.get("baseline") else "not requested"), ""]
    if report.get("error"):
        lines += ["## Operational error", "", "<pre>" + html.escape(report["error"]) + "</pre>", ""]
    findings = report.get("findings", [])
    if findings:
        lines += ["## Policy violations", "", "| Declaration | Rule | Detail |", "| --- | --- | --- |"]
        for f in findings:
            lines.append(f"| {cell(f['declaration'])} | {cell(f['code'])} | {cell(f['message'])} |")
        lines.append("")
    changes = report.get("changes", [])
    if changes:
        lines += ["## Changes from baseline", "", "| Declaration | Change | Before | After | Fails CI |", "| --- | --- | --- | --- | --- |"]
        for c in changes:
            lines.append(f"| {cell(c['declaration'])} | {cell(c['code'])} | {cell(c['before'])} | {cell(c['after'])} | {'yes' if c['fails'] else 'no'} |")
        lines.append("")
    if snapshot is not None:
        lines += ["## Declaration inventory", "", "| Declaration | Module : line | Kind | Statement | Axioms |", "| --- | --- | --- | --- | --- |"]
        for d in snapshot["declarations"]:
            location = d["module"] + (f" : {d['line']}" if d.get("line") else "")
            lines.append(f"| {cell(d['name'])} | {cell(location)} | {cell(d['kind'])} | {cell(d['statement'])} | {cell(', '.join(d['axioms']) or 'none')} |")
        lines += ["", "This audits compiled declarations under the configured axiom policy. It does not establish that a statement matches an informal specification.", ""]
    return "\n".join(lines)
