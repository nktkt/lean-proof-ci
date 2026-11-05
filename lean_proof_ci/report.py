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
            detail = cell(f['message'])
            for path in f.get("paths", {}).values():
                detail += "<br>" + cell(" → ".join(path))
            lines.append(f"| {cell(f['declaration'])} | {cell(f['code'])} | {detail} |")
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


def review_markdown(report: dict[str, Any]) -> str:
    """Keep mechanical inspection and correspondence-review status visibly separate."""
    rows = report.get("reviews", [])
    current = sum(row["status"] == "current" for row in rows)
    lines = [f"# ProofReview: {report['status'].upper()}", "",
             f"Machine audit: **{report['mechanical_status'].upper()}** · "
             f"Correspondence reviews current: **{current}/{len(rows)}**", "",
             "Review coverage is limited to the items in the review manifest.", ""]
    if report.get("error"):
        lines += ["## Operational error", "", "<pre>" + html.escape(report["error"]) + "</pre>", ""]
    for finding in report.get("findings", []):
        lines += [f"- {cell(finding['declaration'])}: {cell(finding['message'])}"]
    if report.get("findings"):
        lines.append("")
    for item in report.get("orphaned", []):
        lines += [f"- Orphaned record: {cell(item)}. Restore its mapping or explicitly retire its records in version control.", ""]
    if rows:
        lines += ["| Item | Declaration | Review status |", "| --- | --- | --- |"]
        for row in rows:
            lines.append(f"| {cell(row['id'])} | {cell(row['declaration'])} | {row['status'].upper()} |")
        lines.append("")
    for row in rows:
        d = row["declaration_info"]
        lines += [f"## {cell(row['title'])} — {row['status'].upper()}", "",
                  f"Item: {cell(row['id'])} · Source: {cell(row['source'])}", ""]
        if row["reference"]:
            lines += [f"Reference: {cell(row['reference'])}", ""]
        lines += ["| Informal source | Lean statement |", "| --- | --- |",
                  f"| {cell(row['source_text'])} | {cell(d['statement'] if d else 'Declaration missing')} |", ""]
        if d:
            lines += [f"Declaration: {cell(d['name'])} · {cell(d['module'])} : {cell(d['line'])}", "",
                      f"Axioms: {cell(', '.join(d['axioms']) or 'none')}", ""]
        previous = row["previous_review"]
        if previous:
            lines += [f"Latest record: **{cell(previous['reviewer'])}**, {cell(previous['reviewed_at'])}", "",
                      f"Review note: {cell(previous['note'])}", ""]
        else:
            lines += ["No correspondence review has been recorded.", ""]
        for change in row["changes"]:
            lines += [f"- **{cell(change['code'])}**: {cell(change['message'])}"]
        if row["changes"]:
            lines.append("")
        if row["dependencies"]:
            lines += ["### Referenced declarations in the audit scope", "",
                      "| Declaration | Kind | Type | Module : line |", "| --- | --- | --- | --- |"]
            for dependency in row["dependencies"]:
                lines.append(f"| {cell(dependency['name'])} | {cell(dependency['kind'])} | "
                             f"{cell(dependency['statement'])} | {cell(dependency['module'])} : {cell(dependency['line'])} |")
            lines += ["", "Inspect these definitions in the Lean source as part of the correspondence review.", ""]
        if row["evidence"]:
            lines += [f"Source SHA-256: {row['evidence']['source_hash']}", ""]
    lines += ["A current record means the recorded evidence has not changed. It does not prove that the informal "
              "source and Lean statement are equivalent, or authenticate the reviewer. External definitions "
              "are covered only by the pinned toolchain/dependency manifest; include relevant local roots in the audit.", ""]
    return "\n".join(lines)
