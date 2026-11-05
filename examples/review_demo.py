"""An isolated, automated walkthrough; it never records a real human review."""

from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    output = ROOT / ".proof-ci"
    output.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="review-demo-", dir=output))
    project = directory / "project"
    shutil.copytree(ROOT / "examples/basic", project,
                    ignore=shutil.ignore_patterns(".lake", ".proof-ci", "lean-proof-review.json"))

    def invoke(command: str, *args: str, expected: int) -> dict:
        result = subprocess.run([sys.executable, "-m", "lean_proof_ci", command, str(project), *args],
                                cwd=ROOT, capture_output=True, text=True, timeout=240)
        if result.returncode != expected:
            raise RuntimeError(result.stdout + result.stderr)
        print(result.stdout, end="")
        return json.loads((project / ".proof-ci/review.json").read_text(encoding="utf-8"))

    print("1/3: Leanの検査は通りますが、原文との対応は未レビューです。", flush=True)
    invoke("review", expected=1)
    print("\n2/3: デモ用の確認記録を作ります（人間によるレビューではありません）。", flush=True)
    for item in ("twice", "add-zero", "add-comm"):
        invoke("accept", "--item", item, "--reviewer", "Demo automation (not a human review)",
               "--note", "Synthetic record to demonstrate evidence invalidation; not a semantic endorsement.", expected=0)
    invoke("review", expected=0)

    print("\n3/3: twiceの定義を変え、関係する1件だけが再レビューになることを確認します。", flush=True)
    source = project / "Demo/Basic.lean"
    original = source.read_text(encoding="utf-8")
    before = "def twice (n : Nat) : Nat := n + n"
    after = "def twice (n : Nat) : Nat := n + n + 0"
    if before not in original:
        raise RuntimeError("The demo's Lean definition changed; update this walkthrough.")
    source.write_text(original.replace(before, after), encoding="utf-8")
    report = invoke("review", expected=1)
    states = {row["id"]: row["status"] for row in report["reviews"]}
    if report["mechanical_status"] != "pass" or states != {"twice": "stale", "add-zero": "current", "add-comm": "current"}:
        raise RuntimeError(f"Unexpected demo result: {states}")
    print(f"\nデモ完了。レポート: {project / '.proof-ci/review.md'}")
    print(f"デモのコピー: {project}")
    print("元のサンプルと実際のレビュー記録は変更していません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
