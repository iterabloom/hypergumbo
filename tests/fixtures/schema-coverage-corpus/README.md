<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# schema-coverage-corpus (WI-luzuh)

Curated fixture corpus for `scripts/check-schema-coverage`. Each file
here exists to **trigger a specific producer code path** in hypergumbo
that would otherwise go unexercised — and therefore unprotected from
schema-drift regressions — by self-analysis alone.

## What this corpus is

Files in this tree need only be **parse-able by the relevant
analyzer**. They do not need to be:

- runnable or import-able,
- a complete project,
- syntactically valid across the whole tree,
- meaningful business logic.

They need to be a minimum reproducer for whatever `Symbol.kind`,
`Edge.type`, or `Edge.meta.evidence_type` value the corresponding
producer emits.

## How this corpus is used

`scripts/check-schema-coverage` runs hypergumbo against this directory,
extracts the set of registry values that appeared in the output, and
asserts that no canonical registry value is missing beyond the
baseline at `.ci/schema-coverage-baseline.json`. The baseline can only
shrink (the ratchet): every PR that covers a new value can drop it
from the baseline; future PRs that break that coverage fail the gate.

## How to add a new fixture

1. Identify the missing registry value (Symbol kind, edge type, or
   evidence type).
2. Find the producer (`grep -rn '<value>' packages/`) and look at the
   test fixtures already used in its unit tests.
3. Add the smallest file under the appropriate language subdir that
   triggers the producer.
4. Run `./scripts/check-schema-coverage` locally. The newly-covered
   value will show up in the "newly covered" report.
5. Run `./scripts/check-schema-coverage --update-baseline` to shrink
   the baseline.
6. Commit the fixture file AND the baseline file together.

## What's intentionally NOT here

- **No package manifests** (`Cargo.toml`, `go.mod`, `package.json`)
  unless required to trigger a producer. Manifest analyzers run
  independent of source-file analyzers, so a fixture that wants to
  cover Rust source-language constructs doesn't need a `Cargo.toml`.
- **No nested projects.** This is one flat language-bucketed
  corpus, not a fake polyglot monorepo.
- **No real names.** Symbols like `MyTrait`, `FooStruct` are fine —
  the contract is registry-value coverage, not realism.

See WI-luzuh (`scripts/tracker show WI-luzuh`) for the design
background and `~/hypergumbo_lab_notebook/notebookjournal_05182026_1511.md`
for the measurement that motivated the corpus.
