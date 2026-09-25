<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0050: The I/O-Boundary Axis

Status: Accepted
Date: 2026-09-01
Related: [ADR-0024](0024-axis-declaration-template.md) (the template this
instantiates), [ADR-0016](0016-io-boundary-analysis.md) (which introduced the
vocabulary), [ADR-0049](0049-deferred-crossings-are-disclosed-not-minted.md)
(whose ruling 1 supplies the axiom, and whose `net_listen` requires the
`deferred_crossing` section), [ADR-0023](0023-edge-type-relationship-not-endpoints.md)
/ [ADR-0027](0027-symbol-kind-language-construct-only.md) /
[ADR-0028](0028-evidence-type-inference-pathway-only.md) (the three prior axes
this one is modelled on).

## Context

ADR-0016 §1 defined "a controlled vocabulary for system boundary types" and
both that ADR and `taint.py` call it *hypergumbo's canonical I/O-boundary risk
taxonomy*. It was never declared as an axis. Concretely, at the time of
writing it had none of ADR-0024's four artifacts — no axiom, no registry
module, no consumer helper, no drift linter — and `io-boundary` was absent from
`multi_value_field_axis._known_axes()`, so no field could even name it.

Three facts make that a defect rather than a missing formality.

**Six consumers branch on the values.** `AUTO_SOURCE_LABEL_MAP` mints taint
sources from three of them; `OPAQUE_BOUNDARIES` withholds confirmations on one;
`_DISCLOSED_ONLY_BOUNDARIES` excludes three from the headline;
`DEFERRED_CROSSING_SHADOWS` maps one to another; `verify-claims` validates a
claim's `constraint.boundary` against `KNOWN_IO_BOUNDARIES`; and the CLI's
`--io-boundary` filter rejects unknown values. A vocabulary that only ever gets
displayed can be free text. This one decides verdicts.

**The cost has been measured twice, not theorised.**

- *INV-tutar.* `env_read` carried two readings — ambient configuration and
  secret material — and `AUTO_SOURCE_LABEL_MAP` read the wrong one. 134 of the
  195 shipped `env_read` rows were host *description* or user identity
  (`runtime.GOOS`, `os.uname`, `navigator.platform`, `pwd.getpwnam`), which is
  why host-secret claims carried 48 of 85 adjudicated flows at 22.9% precision,
  the weakest family in measurement 0001. The catalogues were already
  distorting themselves to cope: `python.yaml` deliberately withheld `getpid`
  and `cpu_count` because rowing them "would manufacture false sources," while
  `go.yaml` rowed `GOOS` and `Getwd`. One value, two membership rules, two
  shipped files.
- *WI-johuk.* The server-launch question was derived four times over five
  months and ruled zero times, because there was nowhere a ruling could live.
  The only governing text was a *test docstring*. Four independent censuses of
  the affected rows produced four different numbers (37 / 52 / 63 / 75) —
  there was no membership rule to count against.

**The documentation had already drifted.** ADR-0016's vocabulary table
documents nine values. Ten more are live: `env_write`, `db_read`, `db_write`,
`process_send`, `logging`, `browser_storage_read`, `browser_storage_write`,
`net_listen`, `external_potential`, `command_launch`. A "controlled vocabulary"
table describing less than half of its vocabulary is the drift an axis exists
to prevent.

The code had also begun asking for this by name. `OPAQUE_BOUNDARIES`'s comment
reads: *"If a future boundary is added to `CATALOG_BOUNDARY_TYPES` whose meaning
is 'control left this process', it belongs here too, and the axis-conformance
tests are what will ask."* There were no axis-conformance tests.

## Decision

Declare the **io-boundary axis** with ADR-0024's four artifacts. The registry
is `hypergumbo_core.io_boundary_types`; the linter is
`scripts/check-io-boundary-drift`; the property tests are
`tests/test_io_boundary_types.py`; `io-boundary` is wired into `_known_axes()`.

### 1. Axiom

> A boundary value names **what data crosses the process boundary at this call
> site, in which direction** — not what the program is thereby arranged to do
> later.

Taken verbatim from ADR-0049 ruling 1. Adopting the existing sentence rather
than writing a new one keeps ADR-0049 the value-level ruling and makes this the
axis that ruling lives on; two differently-worded axioms for one vocabulary
would be the same "one fact, two homes" shape the vocabulary already paid for.

The axiom is falsifiable in ADR-0024's sense. Its second clause is what makes
`net_listen` a disclosure rather than a crossing, and its first clause is the
sentence `env_read` failed when INV-tutar found most of its rows were host
description.

### 2. Sections

Four axes partition the twenty values.

| Axis | Meaning | Values |
|---|---|---|
| `data_crossing` | The canonical section. Data crosses the process boundary at this call site, in a named direction. | `fs_read`, `fs_write`, `net_send`, `net_recv`, `ipc_recv`, `ipc_send`, `env_read`, `env_write`, `host_info_read`, `db_read`, `db_write`, `process_send`, `logging`, `browser_storage_read`, `browser_storage_write` |
| `opacity` | Control left this process. The classification is correct and the analysis cannot see past it, so it does not license "I looked and found nothing." | `subprocess`, `command_launch` |
| `deferred_crossing` | The call *arranges* a crossing it does not itself perform (ADR-0049). | `net_listen`, `db_compose` |
| `speculative` | Synthesised uncertainty; declarable by no catalogue. | `external_potential` |

`deferred_crossing` is a section rather than a violation, and that is the
subtle part: `net_listen` (and since WI-fasap its database twin `db_compose`,
the composed-but-unevaluated query) is *precisely* what the axiom's second
clause excludes from `data_crossing`. ADR-0049 created it deliberately, so the axis
must have somewhere to put it. A value that the axiom rejects and the project
intends is a section; a value that the axiom rejects and nobody defends is a
deprecation candidate.

### 3. Two properties are per-value, not sections

Both cut across the partition, which per ADR-0024's fold-residue discipline is
the signal to put them on the spec:

- **`catalog_declarable`** — whether an `io_primitives/*.yaml` may declare the
  value. `_parse_catalog` iterates exactly the declarable names, so a
  non-declarable value can only ever be producer-stamped and a declarable one
  can only ever arrive through a catalogue. This is the channel split that
  `OPAQUE_BOUNDARIES` (catalogue) versus `PRODUCER_OPAQUE_BOUNDARIES`
  (producer) encodes, and deriving both from this one field makes
  `io_boundary.py`'s comment — *"each set is reachable through exactly one
  channel"* — executable instead of a sentence someone has to keep true.
- **`counts_in_headline`** — whether the value is part of the curated surface
  reported as `total_io_edges`. `subprocess` is the value that proves this
  cannot be an axis query: it is *opaque and counted*, while
  `external_potential`, `command_launch`, `net_listen` and `db_compose` are
  disclosed and excluded. An axis-derived rule would need exactly one special case.

### 4. The registry is the single source of truth

`io_boundary.py` no longer writes its five vocabulary constants by hand.
`CATALOG_BOUNDARY_TYPES`, `KNOWN_IO_BOUNDARIES`, `OPAQUE_BOUNDARIES`,
`PRODUCER_OPAQUE_BOUNDARIES` and `_DISCLOSED_ONLY_BOUNDARIES` are each derived.
Property tests pin the pre-refactor membership — and, for
`CATALOG_BOUNDARY_TYPES`, the pre-refactor *order*, since `_parse_catalog`
iterates it and several sites resolve a doubly-declared primitive by
first-declared-wins.

## Consequences

### Positive

- A per-value ruling now has a home. INV-tutar's `env_read` split and
  WI-johuk's server-launch question were both expensive because there was
  nowhere to record an answer; the next one is an audit against a written
  sentence.
- The channel split and the headline split are enforced rather than commented.
- ADR-0016's nine-of-nineteen table is superseded as the vocabulary's home by
  a registry that cannot fall behind the code, because the code reads it.

### Negative, and stated plainly

**The drift linter collects nothing on the live tree.** The shared AST walker
in `axis_drift` matches module-level string-literal sets whose name contains
the filter. After §4's refactor, `io_boundary.py` has none left — they are
calls into the registry now. So `scripts/check-io-boundary-drift` exits 0 over
an *empty collection*, and it would exit 0 identically if it were broken.

That is a weaker guarantee than the other three axes get, and it is disclosed
rather than papered over:

- `test_the_drift_scanner_actually_fires` builds a synthetic tree containing
  the ADR-0023 silent-bug shape and asserts the scanner reports it, so a broken
  scanner and a clean tree are distinguishable.
- The vocabulary's *real* consumers are dicts and derived frozensets, which the
  set-walker structurally cannot see. All four — `AUTO_SOURCE_LABEL_MAP`,
  `DEFERRED_CROSSING_SHADOWS`, `_READ_TARGET_KIND_BOUNDARY` and its write-direction twin `_WRITE_TARGET_KIND_BOUNDARY` (WI-suhug) — get explicit
  registry-membership assertions instead. A name-filtered scan that silently
  missed them would be worse than no gate, because it would look like coverage.

Widening the shared walker to reach dict keys, dict values and bare tuples
would serve all four axes and is filed separately (it changes machinery three
other registries depend on, so it does not belong in this PR).

### Open question

`logging` is the one `data_crossing` value naming a **purpose** rather than a
medium, and it overlaps `ipc_send` on stdout: go's `fmt.Println` and haskell's
`Prelude.putStrLn` are catalogued `logging` while `stdout.write` is catalogued
`ipc_send`. 46 rows across 12 catalogues are involved. This ADR does **not**
settle it and moves no row — it records `logging` as the first candidate for a
per-value audit under ADR-0024's family-audit methodology, whose output would
file as `docs/audits/<NN>-logging-family.md`. Having somewhere to ask the
question is the point of the axis; answering it here would be exactly the
undisciplined move the axis exists to prevent.
