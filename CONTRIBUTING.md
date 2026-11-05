# Contributing

Use Python 3.11+ and the example's pinned Lean toolchain. Install the package in a
virtual environment with `python3 -m pip install -e .`, or run it from the checkout
with `python3 -m lean_proof_ci`.

Before submitting a change, run:

```sh
python3 -m unittest discover -v
python3 -m lean_proof_ci check examples/basic \
  --baseline examples/basic/lean-proof-ci.baseline.json
uv build
```

For changes to axiom collection or expression fingerprints, add an end-to-end test
with a real Lean example that distinguishes the intended behavior. Do not replace
these with source-string matching. Changes that require a snapshot-format migration
must increment its schema version and document the migration.

Never turn an inspector/build error into a successful audit. Preserve the distinction
between an audit failure (exit 1) and an operational/configuration failure (exit 2).

Review the example's snapshot diff if updating it. CI must compare against the checked-in
baseline rather than regenerate it. Version support claims should identify the Lean
versions actually tested. The broken example is intentionally expected to fail.

All contributions are provided under the repository's MIT license.
