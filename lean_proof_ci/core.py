"""Build Lean modules, inspect their environment, and compare reviewed snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from . import __version__

SCHEMA_VERSION = 1
STANDARD_AXIOMS = ("propext", "Classical.choice", "Quot.sound")
MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class AuditError(Exception):
    """Operational or configuration failure, never a successful audit."""


@dataclass(frozen=True)
class Config:
    modules: tuple[str, ...]
    allowed_axioms: tuple[str, ...] = STANDARD_AXIOMS
    timeout_seconds: int = 180


def string_list(value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise AuditError(f"{field} must be an array of nonempty strings")
    if len(set(value)) != len(value) or (nonempty and not value):
        raise AuditError(f"{field} must contain unique entries" + (" and cannot be empty" if nonempty else ""))
    return value


def load_config(project: Path, config_path: Path | None, modules: list[str] | None, timeout: int | None) -> Config:
    path = config_path if config_path is not None else project / "lean-proof-ci.toml"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuditError(f"Cannot read config {path}: {exc}") from exc
    elif config_path is not None:
        raise AuditError(f"Config not found: {path}")
    unknown = set(data) - {"modules", "allowed_axioms", "timeout_seconds"}
    if unknown:
        raise AuditError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    roots = string_list(modules if modules is not None else data.get("modules", []), "modules", nonempty=True)
    if any(not MODULE_NAME.fullmatch(n) for n in roots):
        raise AuditError("Import roots must be dotted ASCII Lean module names, such as Demo.Basic")
    allowed = string_list(data.get("allowed_axioms", list(STANDARD_AXIOMS)), "allowed_axioms")
    if "sorryAx" in allowed:
        raise AuditError("sorryAx cannot be allowlisted: incomplete proofs always fail")
    seconds = timeout if timeout is not None else data.get("timeout_seconds", 180)
    if type(seconds) is not int or seconds <= 0:
        raise AuditError("timeout_seconds must be a positive integer")
    return Config(tuple(sorted(roots)), tuple(sorted(allowed)), seconds)


def run(command: list[str], project: Path, timeout: int) -> str:
    """No shell; kill the complete process group on timeout on POSIX."""
    try:
        process = subprocess.Popen(
            command, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise AuditError(f"Cannot start {command[0]}: {exc}") from exc
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        output, _ = process.communicate()
        raise AuditError(f"Command timed out after {timeout}s: {' '.join(command)}\n{output[-12000:]}") from exc
    if process.returncode != 0:
        raise AuditError(f"Command failed ({process.returncode}): {' '.join(command)}\n{output[-12000:]}")
    return output


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def expression_dependencies(*expressions: Any) -> list[str]:
    """Read constant/projection references from the inspector's structured expressions."""
    pending = list(expressions)
    names: set[str] = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, list):
            continue
        if node and node[0] in ("const", "proj"):
            names.add(node[1])
        pending.extend(child for child in node if isinstance(child, list))
    return sorted(names)


def inspect_project(project: Path, config: Config, *,
                    dependency_graph: dict[str, list[str]] | None = None) -> dict[str, Any]:
    toolchain_file = project / "lean-toolchain"
    if not toolchain_file.is_file():
        raise AuditError(f"Missing lean-toolchain in {project}; use a pinned Lake project")
    if not any((project / f"lakefile.{ext}").is_file() for ext in ("toml", "lean")):
        raise AuditError(f"Missing lakefile.toml or lakefile.lean in {project}")
    toolchain = toolchain_file.read_text(encoding="utf-8").strip()
    if not toolchain:
        raise AuditError("lean-toolchain is empty")
    # Explicit module targets avoid an empty/default Lake target giving a false success.
    run(["lake", "build", *[f"+{name}" for name in config.modules]], project, config.timeout_seconds)
    inspector = files("lean_proof_ci").joinpath("Inspector.lean").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=".lean-proof-ci-", dir=project) as tmp:
        directory = Path(tmp)
        result_file = directory / "inspection.json"
        script = directory / "Inspect.lean"
        # Only validated module names enter import syntax; JSON strings are Lean-compatible here.
        roots = "#[" + ", ".join(json.dumps(m) for m in config.modules) + "]"
        allowed = "#[" + ", ".join(json.dumps(a, ensure_ascii=False) for a in config.allowed_axioms) + "]"
        source = "import Lean\n" + "".join(f"import {m}\n" for m in config.modules)
        source += inspector + f"\nrun_cmd LeanProofCI.inspect {roots} {allowed} {json.dumps(str(result_file), ensure_ascii=False)}\n"
        script.write_text(source, encoding="utf-8")
        run(["lake", "env", "lean", str(script)], project, config.timeout_seconds)
        try:
            raw = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuditError(f"Lean inspector did not produce valid JSON: {exc}") from exc
    try:
        if raw["schema_version"] != SCHEMA_VERSION:
            raise AuditError("Unsupported Lean inspector schema")
        declarations = []
        for item in raw["declarations"]:
            declaration = {key: item[key] for key in ("name", "module", "kind", "statement", "line")}
            declaration["statement_hash"] = digest(item["type_expression"])
            declaration["definition_hash"] = digest(item["body_expression"]) if item["body_expression"] is not None else None
            declaration["axioms"] = sorted(set(item["axioms"]))
            declaration["axiom_paths"] = item["axiom_paths"]
            declarations.append(declaration)
            if dependency_graph is not None:
                dependency_graph[item["name"]] = expression_dependencies(
                    item["type_expression"], item["body_expression"])
        manifest = project / "lake-manifest.json"
        snapshot = {
            "schema_version": SCHEMA_VERSION, "checker_version": __version__,
            "toolchain": toolchain, "lean_version": raw["lean_version"],
            "roots": list(config.modules), "modules": sorted(raw["modules"]),
            "allowed_axioms": list(config.allowed_axioms),
            "dependency_manifest_hash": digest(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else None,
            "declarations": sorted(declarations, key=lambda d: d["name"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"Malformed Lean inspector output: {exc}") from exc
    validate_snapshot(snapshot)
    if not snapshot["declarations"]:
        raise AuditError("No declarations found in the selected modules; refusing an empty audit")
    return snapshot


def validate_snapshot(snapshot: Any) -> None:
    if not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != SCHEMA_VERSION:
        raise AuditError("Unsupported or missing snapshot schema_version")
    for key in ("checker_version", "toolchain", "lean_version"):
        if not isinstance(snapshot.get(key), str) or not snapshot[key]:
            raise AuditError(f"Snapshot {key} must be a nonempty string")
    for key in ("roots", "modules", "allowed_axioms"):
        string_list(snapshot.get(key), f"snapshot.{key}", nonempty=key != "allowed_axioms")
    if not set(snapshot["roots"]).issubset(snapshot["modules"]):
        raise AuditError("Snapshot is missing a requested root module")
    if "sorryAx" in snapshot["allowed_axioms"]:
        raise AuditError("Snapshot cannot allow sorryAx")
    for key in ("dependency_manifest_hash",):
        if key not in snapshot:
            raise AuditError(f"Missing snapshot {key}")
        value = snapshot.get(key)
        if value is not None and (not isinstance(value, str) or not SHA256.fullmatch(value)):
            raise AuditError(f"Invalid snapshot {key}")
    declarations = snapshot.get("declarations")
    if not isinstance(declarations, list):
        raise AuditError("Snapshot declarations must be an array")
    seen: set[str] = set()
    for d in declarations:
        if not isinstance(d, dict):
            raise AuditError("Snapshot declaration must be an object")
        for key in ("name", "module", "kind", "statement"):
            if not isinstance(d.get(key), str) or not d[key]:
                raise AuditError(f"Declaration {key} must be a nonempty string")
        if d["name"] in seen:
            raise AuditError(f"Duplicate declaration in snapshot: {d['name']}")
        seen.add(d["name"])
        if d["module"] not in snapshot["modules"]:
            raise AuditError(f"Declaration outside snapshot modules: {d['name']}")
        for key in ("statement_hash", "definition_hash"):
            if key not in d:
                raise AuditError(f"Missing {key} for {d['name']}")
            value = d.get(key)
            if key == "definition_hash" and value is None:
                continue
            if not isinstance(value, str) or not SHA256.fullmatch(value):
                raise AuditError(f"Invalid {key} for {d['name']}")
        if d.get("line") is not None and (type(d["line"]) is not int or d["line"] < 1):
            raise AuditError(f"Invalid line for {d['name']}")
        string_list(d.get("axioms"), f"{d['name']}.axioms")
        paths = d.get("axiom_paths", {})
        if not isinstance(paths, dict):
            raise AuditError(f"Invalid axiom_paths for {d['name']}")
        for axiom, path in paths.items():
            string_list(path, f"{d['name']}.axiom_paths.{axiom}", nonempty=True)
            if axiom not in d["axioms"] or path[0] != d["name"] or path[-1] != axiom:
                raise AuditError(f"Invalid axiom path endpoints for {d['name']}")


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Cannot read baseline {path}: {exc}") from exc
    validate_snapshot(snapshot)
    return snapshot


def audit(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    allowed = set(snapshot["allowed_axioms"])
    for d in snapshot["declarations"]:
        if "sorryAx" in d["axioms"]:
            findings.append({"code": "incomplete_proof", "declaration": d["name"],
                             "paths": {a: p for a, p in d.get("axiom_paths", {}).items() if a == "sorryAx"},
                             "message": "Depends on sorryAx, directly or transitively"})
        unexpected = sorted(set(d["axioms"]) - allowed - {"sorryAx"})
        if unexpected:
            findings.append({"code": "unapproved_axiom", "declaration": d["name"], "axioms": unexpected,
                             "paths": {a: p for a, p in d.get("axiom_paths", {}).items() if a in unexpected},
                             "message": "Depends on unapproved axioms: " + ", ".join(unexpected)})
    return findings


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def add(code: str, name: str | None, before: Any, after: Any, *, fails: bool = True) -> None:
        changes.append({"code": code, "declaration": name, "before": before, "after": after, "fails": fails})

    for field, code in (("roots", "scope_changed"), ("allowed_axioms", "policy_changed"),
                        ("toolchain", "toolchain_changed"), ("lean_version", "lean_version_changed"),
                        ("dependency_manifest_hash", "dependencies_changed")):
        if current[field] != baseline[field]:
            add(code, None, baseline[field], current[field])
    old = {d["name"]: d for d in baseline["declarations"]}
    new = {d["name"]: d for d in current["declarations"]}
    for name in sorted(old.keys() - new.keys()):
        add("declaration_removed", name, old[name]["statement"], None)
    for name in sorted(new.keys() - old.keys()):
        add("declaration_added", name, None, new[name]["statement"], fails=False)
    for name in sorted(old.keys() & new.keys()):
        a, b = old[name], new[name]
        if a["statement_hash"] != b["statement_hash"]:
            add("statement_changed", name, a["statement"], b["statement"])
            changes[-1].update(before_hash=a["statement_hash"], after_hash=b["statement_hash"])
        if a["kind"] != b["kind"]:
            add("kind_changed", name, a["kind"], b["kind"])
        if a["module"] != b["module"]:
            add("module_changed", name, a["module"], b["module"])
        if a["definition_hash"] != b["definition_hash"]:
            add("definition_changed", name, a["definition_hash"], b["definition_hash"])
        if a["axioms"] != b["axioms"]:
            add("axioms_changed", name, a["axioms"], b["axioms"])
    return changes
