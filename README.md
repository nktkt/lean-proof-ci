# Lean Proof CI

A small CLI for auditing Lean declarations and reviewing changes to their statements.
It checks compiled declarations, including transitive axiom dependencies, and produces
JSON and Markdown reports. Python 3.11+, with no Python runtime dependencies.

[日本語のクイックスタート](README.ja.md) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)

## Try the MVP

Install [elan](https://github.com/leanprover/elan#installation), then run from this checkout:

```sh
python3 -m lean_proof_ci check examples/basic \
  --baseline examples/basic/lean-proof-ci.baseline.json
```

The example pins **Lean 4.29.1**, the tested version. Elan will download it if necessary.
It imports only Lean's bundled libraries: no Mathlib download and no AI service are needed.
The `lake` command must be on `PATH`.

Expected output:

```text
PASS: 4 declarations, 0 policy violations, 0 baseline changes
```

Reports appear in `examples/basic/.proof-ci/report.json` and `report.md`.
Try the intentionally broken project as well:

```sh
python3 -m lean_proof_ci check examples/broken
```

It builds in Lean but this audit **exits 1**: a theorem depends on `sorryAx`, and
another theorem depends on an unapproved axiom. This is an expected failure.

To install the command locally, inside a Python virtual environment:

```sh
python3 -m pip install .
lean-proof-ci --help
```

The project is not published to PyPI; install from this checkout or its built wheel.

## What fails CI?

| Condition | Result |
| --- | --- |
| Direct or transitive dependency on `sorryAx` | Failure; cannot be allowlisted |
| Dependency on an axiom outside the configured allowlist | Failure |
| Changed theorem type, including added assumptions | Failure against a baseline |
| Changed definition/opaque body | Failure against a baseline |
| Removed declaration, changed kind/module, or changed axiom footprint | Failure against a baseline |
| Changed roots, axiom policy, Lean version, or dependency manifest | Failure against a baseline |
| New declaration with an acceptable axiom footprint | Informational change; passes |
| Proof-only edit with the same statement and axiom footprint | Passes |
| Binder variable renamed, or formatting/comments changed | Passes when compiled structure is unchanged |
| Build/inspection failure, timeout, invalid baseline, or empty audit | Operational error; never passes |

Exit codes: **0** pass, **1** policy or baseline failure, **2** operational/configuration error.
The JSON report has `status`, `snapshot`, `findings`, and `changes`; operational errors
also include `error`. Each change records whether it fails CI. A check without
`--baseline` audits axioms only and explicitly reports that comparison was not requested.

## Audit your project

Create `lean-proof-ci.toml` alongside its `lakefile.toml` or `lakefile.lean`:

```toml
modules = ["MyLibrary"]
allowed_axioms = ["propext", "Classical.choice", "Quot.sound"]
timeout_seconds = 180
```

`modules` specifies **import roots**, not declaration namespaces. A root `MyLibrary`
audits declarations in `MyLibrary` and its *imported* `MyLibrary.*` modules. A theorem
declared in an unrelated namespace inside those files is still audited. Include other
roots explicitly if their declarations should also be inventoried. Unimported files
are outside the audit; keep your library's root imports complete.

Every selected declaration is inventoried, including definitions, axioms, private
declarations, and compiler-generated declarations visible in the imported environment.
Axiom dependencies are followed through **all imported modules**, including dependencies
outside the inventory scope. This catches an external helper containing `sorry` when a
selected declaration uses that helper.

For a new project, establish a baseline after reviewing the statements and axiom policy:

```sh
lean-proof-ci snapshot /path/to/project
lean-proof-ci check /path/to/project \
  --baseline /path/to/project/lean-proof-ci.baseline.json
```

`snapshot` writes `lean-proof-ci.baseline.json` only after the axiom audit passes.
Commit the baseline, the config, `lean-toolchain`, and `lake-manifest.json`.
Run `snapshot` again only when a reviewer intentionally accepts the reported changes.
Do not regenerate the baseline automatically in the same CI job that checks it.

Optional arguments:

```sh
lean-proof-ci check /path/to/project \
  --module MyLibrary --module OtherRoot \
  --baseline reviewed.json \
  --timeout 300 \
  --json artifacts/audit.json \
  --markdown artifacts/audit.md
```

Explicit file paths are relative to the current working directory. Default config,
snapshot, and report paths are relative to the project. `--module` replaces configured
roots. Timeout applies separately to the build command and inspection command.

## GitHub Actions

This repository includes [.github/workflows/ci.yml](.github/workflows/ci.yml): it installs
the package, runs real Lean integration tests, compares the example to its committed
baseline, and uploads reports even when an audit fails. The Markdown report is also
attached to the job summary. No credentials for external proof services are needed.

For another repository, install this tool from a reviewed release wheel or a pinned Git
commit, set up its pinned Lean toolchain, then run:

```sh
lean-proof-ci check . --baseline lean-proof-ci.baseline.json
```

Treat baseline/config changes as reviewable code changes. For untrusted pull requests,
use ordinary `pull_request` workflows without secrets or write tokens. Lake and Lean can
execute project code; this tool is not a sandbox. A PR that can change the checker,
workflow, and its own baseline can change what the check means.

## How it works

1. Build the selected roots with `lake build +Root ...` using the project's toolchain.
2. Run a temporary Lean inspector through `lake env lean`.
3. Inspect the compiled environment and use Lean's `collectAxioms` for each declaration.
4. Hash a structured encoding of the elaborated type. Strip binder names and source
   metadata, while retaining binders, implicitness, constants, universe levels, and arguments.
5. Track definition/opaque bodies separately, then compare with the reviewed snapshot.

The inspector is bundled in the Python wheel. It adds no dependency to your Lake project.
Temporary files are removed after inspection; normal Lake build outputs remain.

## Scope and limitations

- Tested on Lean **4.29.1**, macOS, with Python 3.14; CI is configured for Linux/Python 3.11.
  Other Lean versions may need an inspector update. Version mismatches against a baseline
  are surfaced for review rather than silently ignored.
- This is an audit of Lean's compiled environment, not an independent kernel checker or
  proof of agreement with an English/Japanese specification. It trusts the Lean toolchain,
  build artifacts, imports, and execution environment. It does not recheck cached `.olean`
  files with an independent checker.
- `native_decide`-style compiler-trust axioms are rejected by the default three-axiom
  policy when they appear in the dependency footprint.
- An anonymous `example` does not necessarily persist as a declaration and is not covered.
  Neither are unimported source files or unused declarations outside the configured roots.
- Fingerprints compare structure, not logical equivalence. Equivalent reformulations or
  changes to compiler-generated/private names can require review. Public theorem proof
  bodies themselves are not fingerprinted.
- Definition bodies within the selected scope are tracked. Changes inside external
  libraries are only signaled by their manifest/toolchain changes and affected axiom
  footprints; editing an untracked path dependency may evade this comparison. Include
  relevant local dependency roots and keep external dependencies pinned.
- Import roots currently use ASCII dotted identifiers; declaration names and report
  statements may contain Unicode. Large Mathlib/Stacks projects have not been benchmarked.

See Lean's [axiom documentation](https://lean-lang.org/doc/reference/latest/Axioms/) for
the meaning of the allowlist. Existing tools such as
[lean-action](https://github.com/leanprover/lean-action) already provide axiom-audit and
independent checking integrations. This MVP focuses on a portable CLI, explicit module
scope, and reviewable statement/definition snapshots alongside the axiom audit.

## Develop

```sh
python3 -m unittest discover -v
uv build
```

Tests invoke a real Lean compiler. A missing compiler fails the suite instead of skipping
its main checks. See [CONTRIBUTING.md](CONTRIBUTING.md) for the expected review workflow.
