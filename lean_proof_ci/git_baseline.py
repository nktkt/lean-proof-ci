"""Read a reviewed snapshot from an immutable Git object without checking it out."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .core import AuditError, run, validate_snapshot


def load_git_baseline(
    project: Path, revision: str, path: str | None = None, timeout: int = 30,
) -> tuple[dict[str, Any], dict[str, str]]:
    root = Path(run(["git", "rev-parse", "--show-toplevel"], project, timeout).strip()).resolve()
    if not project.is_relative_to(root):
        raise AuditError("The project must be inside its Git working tree")
    if not revision or "\x00" in revision:
        raise AuditError("--baseline-ref must name a Git commit, branch, or tag")
    commit = run(["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"], root, timeout).strip()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
        raise AuditError("Git did not resolve the baseline ref to a commit")
    selected = path if path is not None else (project.relative_to(root) / "lean-proof-ci.baseline.json").as_posix()
    parsed = PurePosixPath(selected)
    if (not selected or parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() == "."
            or any(c in selected for c in ("\x00", "\\", ":", "\n", "\r"))):
        raise AuditError("--baseline-path must be a relative, POSIX repository path without '..'")
    selected = parsed.as_posix()
    text = run(["git", "cat-file", "blob", f"{commit}:{selected}"], root, timeout)
    try:
        snapshot = json.loads(text)
    except ValueError as exc:
        raise AuditError(f"Invalid baseline JSON at {commit}:{selected}: {exc}") from exc
    validate_snapshot(snapshot)
    return snapshot, {"kind": "git", "ref": revision, "commit": commit, "path": selected}
