"""Version-bound records of a reviewer's informal-to-formal correspondence review.

These are review records, not signatures or proofs of semantic equivalence. A record
is current only for its source bytes, mapping, environment, and reachable declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tomllib
from typing import Any

from .core import AuditError, SHA256, digest, string_list

REVIEW_SCHEMA = 1
FINGERPRINT_FIELDS = ("module", "kind", "statement_hash", "definition_hash", "axioms")
ENVIRONMENT_FIELDS = ("checker_version", "toolchain", "lean_version", "roots", "modules",
                      "allowed_axioms", "dependency_manifest_hash")
STRUCTURAL_KINDS = {"inductive", "constructor", "recursor", "quotient"}


def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"{field} must be a nonempty string")
    return value


def fields(value: Any, required: set[str], optional: set[str], label: str) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise AuditError(f"Invalid {label} fields; required: {', '.join(sorted(required))}")


def hash_value(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise AuditError(f"Invalid {label} SHA-256 fingerprint")


@dataclass(frozen=True)
class ReviewItem:
    id: str
    declaration: str
    source: str
    title: str
    reference: str

    def mapping(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in ("id", "declaration", "source", "title", "reference")}


def source_path(project: Path, source: str) -> Path:
    relative = Path(source)
    path = (project / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(project.resolve()):
        raise AuditError(f"Review source must stay inside the project: {source}")
    if any(part in {".proof-ci", ".lake"} for part in path.relative_to(project.resolve()).parts):
        raise AuditError(f"Review source cannot be in a generated report/build directory: {source}")
    return path


def load_items(project: Path, path: Path) -> list[ReviewItem]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Cannot read review manifest {path}: {exc}") from exc
    fields(data, {"schema_version", "items"}, set(), "review manifest")
    if type(data["schema_version"]) is not int or data["schema_version"] != REVIEW_SCHEMA:
        raise AuditError("Unsupported review manifest schema_version")
    if not isinstance(data["items"], list) or not data["items"]:
        raise AuditError("Review manifest items must be a nonempty array of tables")
    items = []
    seen = set()
    for row in data["items"]:
        fields(row, {"id", "declaration", "source"}, {"title", "reference"}, "review item")
        for key, value in row.items():
            nonempty(value, f"review item {key}")
        if row["id"] in seen:
            raise AuditError(f"Duplicate review item id: {row['id']}")
        seen.add(row["id"])
        source_path(project, row["source"])
        items.append(ReviewItem(row["id"], row["declaration"], row["source"],
                                row.get("title", row["id"]), row.get("reference", "")))
    return items


def fingerprint(declaration: dict[str, Any]) -> dict[str, Any]:
    return {key: declaration[key] for key in FINGERPRINT_FIELDS}


def related_declarations(name: str, declarations: dict[str, dict[str, Any]],
                         graph: dict[str, list[str]]) -> list[str]:
    pending, seen = [name], set()
    while pending:
        current = pending.pop()
        if current in seen or current not in declarations:
            continue
        if current not in graph:
            raise AuditError(f"Missing inspection dependency data for {current}")
        seen.add(current)
        pending.extend(graph[current])
    return sorted(seen)


def evidence_for(item: ReviewItem, source: bytes, snapshot: dict[str, Any],
                 graph: dict[str, list[str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    declarations = {d["name"]: d for d in snapshot["declarations"]}
    names = related_declarations(item.declaration, declarations, graph)
    environment = {key: snapshot[key] for key in ENVIRONMENT_FIELDS}
    # Types alone do not capture an inductive's constructor inventory or recursor rules.
    # Conservatively invalidate all reviews when an in-scope datatype changes.
    environment["datatypes"] = {name: fingerprint(d) for name, d in declarations.items()
                                if d["kind"] in STRUCTURAL_KINDS}
    return {
        "mapping": item.mapping(),
        "source_hash": hashlib.sha256(source).hexdigest(),
        "environment_hash": digest(environment),
        "declarations": {name: fingerprint(declarations[name]) for name in names},
    }, [declarations[name] for name in names if name != item.declaration]


def validate_evidence(evidence: Any) -> None:
    fields(evidence, {"mapping", "source_hash", "environment_hash", "declarations"}, set(), "review evidence")
    mapping = evidence["mapping"]
    fields(mapping, {"id", "declaration", "source", "title", "reference"}, set(), "review mapping")
    for key, value in mapping.items():
        if key == "reference" and value == "":
            continue
        nonempty(value, f"review mapping {key}")
    for key in ("source_hash", "environment_hash"):
        hash_value(evidence[key], key)
    declarations = evidence["declarations"]
    if not isinstance(declarations, dict) or mapping["declaration"] not in declarations:
        raise AuditError("Review evidence is missing the target declaration")
    for name, d in declarations.items():
        nonempty(name, "review declaration name")
        fields(d, set(FINGERPRINT_FIELDS), set(), "review declaration")
        for key in ("module", "kind"):
            nonempty(d[key], f"review declaration {key}")
        hash_value(d["statement_hash"], "statement_hash")
        if d["definition_hash"] is not None:
            hash_value(d["definition_hash"], "definition_hash")
        string_list(d["axioms"], "review declaration axioms")


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": REVIEW_SCHEMA, "records": []}
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Cannot read review ledger {path}: {exc}") from exc
    fields(ledger, {"schema_version", "records"}, set(), "review ledger")
    if type(ledger["schema_version"]) is not int or ledger["schema_version"] != REVIEW_SCHEMA:
        raise AuditError("Unsupported review ledger schema_version")
    if not isinstance(ledger["records"], list):
        raise AuditError("Review ledger records must be an array")
    for record in ledger["records"]:
        fields(record, {"item_id", "reviewer", "note", "reviewed_at", "evidence", "evidence_hash"}, set(), "review record")
        for key in ("item_id", "reviewer", "note", "reviewed_at"):
            nonempty(record[key], f"review record {key}")
        try:
            timestamp = datetime.fromisoformat(record["reviewed_at"])
        except ValueError as exc:
            raise AuditError("Invalid review record timestamp") from exc
        if timestamp.tzinfo is None:
            raise AuditError("Review record timestamp must include a timezone")
        validate_evidence(record["evidence"])
        if record["item_id"] != record["evidence"]["mapping"]["id"]:
            raise AuditError("Review record item_id does not match its evidence")
        if record["evidence_hash"] != digest(record["evidence"]):
            raise AuditError("Review record evidence fingerprint does not match its contents")
    return ledger


def changes_since(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for key, message in (("mapping", "Source/declaration mapping changed"),
                         ("source_hash", "Informal source text changed"),
                         ("environment_hash", "Toolchain, scope, policy, dependencies, or datatype structure changed")):
        if before[key] != after[key]:
            changes.append({"code": key.removesuffix("_hash") + "_changed", "declaration": None, "message": message})
    old, new = before["declarations"], after["declarations"]
    for name in sorted(old.keys() | new.keys()):
        if name not in old or name not in new:
            changes.append({"code": "dependency_set_changed", "declaration": name,
                            "message": "Declaration entered or left the statement/definition dependency closure"})
            continue
        for key in FINGERPRINT_FIELDS:
            if old[name][key] != new[name][key]:
                changes.append({"code": key.removesuffix("_hash") + "_changed", "declaration": name,
                                "message": f"{name}: {key.removesuffix('_hash')} changed"})
    return changes


def evaluate_reviews(project: Path, items: list[ReviewItem], snapshot: dict[str, Any],
                     graph: dict[str, list[str]], ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    declarations = {d["name"]: d for d in snapshot["declarations"]}
    # Ledger order, not a caller-supplied timestamp, determines the latest record.
    latest = {record["item_id"]: record for record in ledger["records"]}
    rows = []
    for item in items:
        try:
            source = source_path(project, item.source).read_bytes()
            source_text = source.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise AuditError(f"Cannot read review source {item.source}: {exc}") from exc
        if not source_text.strip():
            raise AuditError(f"Review source is empty: {item.source}")
        previous = latest.get(item.id)
        row = {**item.mapping(), "source_text": source_text, "previous_review": previous,
               "declaration_info": declarations.get(item.declaration), "dependencies": [],
               "evidence": None, "status": "missing", "changes": []}
        if item.declaration not in declarations:
            row["changes"] = [{"code": "declaration_missing", "declaration": item.declaration,
                               "message": "Target declaration is absent from the configured audit scope"}]
        else:
            evidence, dependencies = evidence_for(item, source, snapshot, graph)
            row.update(evidence=evidence, dependencies=dependencies)
            if previous is None:
                row["status"] = "pending"
            else:
                row["changes"] = changes_since(previous["evidence"], evidence)
                row["status"] = "stale" if row["changes"] else "current"
        rows.append(row)
    orphaned = sorted(latest.keys() - {item.id for item in items})
    return rows, orphaned


def new_record(row: dict[str, Any], reviewer: str, note: str) -> dict[str, Any]:
    nonempty(reviewer, "reviewer")
    nonempty(note, "note")
    if row["evidence"] is None:
        raise AuditError("Cannot record a review for a missing declaration")
    return {"item_id": row["id"], "reviewer": reviewer.strip(), "note": note.strip(),
            "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "evidence": row["evidence"], "evidence_hash": digest(row["evidence"])}
