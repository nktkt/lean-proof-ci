# Changelog

## 0.2.0 — 2026-11-05

- Explain rejected axiom dependencies with paths through Lean's compiled kernel constants.
- Read reviewed baselines from Git commits with `--baseline-ref` and `--baseline-path`,
  recording the resolved commit and refusing a working-tree fallback.
- Generate offline HTML reports with declaration search, filters, expandable details,
  dependency paths, and highlighted statement changes.
- Provide a reusable GitHub Action with status/exit-code/report outputs and job summaries.
  Test both its passing and failing paths in CI, and use PR base baselines for comparisons.
- Preserve schema-1 baseline compatibility and existing structural fingerprints.
- Add opt-in source-to-Lean correspondence reviews through `review` and `accept`, with
  recorded evidence and stale-review detection. Mechanical and correspondence verdicts
  remain separate; records do not authenticate the reviewer or prove semantic equivalence.

## 0.1.0 — 2026-11-05

- Initial CLI, compiled axiom audit, statement/definition snapshots, JSON/Markdown reports,
  valid and broken Lean examples, and CI tests.
