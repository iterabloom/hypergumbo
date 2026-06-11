<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0033: Spec-vs-Data Validator Stage

- Status: **Accepted**
- Date: 2026-05-31
- Supersedes: —
- Superseded by: —
- Related: ADR-0024 (Axis Declaration Template — the static-AST validator whose runtime counterpart this ADR introduces), ADR-0027 (Symbol.kind axis), ADR-0028 (Edge.evidence_type axis), ADR-0031 (Symbol.language reshape — its "Enforcement" §explicitly defers the runtime drift gate to this ADR), ADR-0032 (canonical_name / fingerprint reshape — same deferral); tracker items INV-sugat (super-META "no spec-vs-data validator stage exists in the pipeline" — closed by this ADR), INV-luhur (META: AnalysisRun + behavior-map meta-layer writers have no validator), INV-fabov (META: command-line and configuration values silently accepted without validation), INV-numat (META: vocabulary fields mix axes); WI-rolol (structural-fix trial whose sub-tasks A and B fold into this ADR's Phase 3 implementation per the campaign plan).

> **Amendment (2026-06-11):** Cross-field coherence invariant (a) — `Edge.dst_ref ↔ Edge.dst` per the `make_unresolved_edge` docstring — is re-anchored by ADR-0037 ruling 2 (2026-06-10 design interview, ADRs 0035–0042, PR #4181): `dst_ref` becomes unconditionally derived at edge finalization, so the invariant's enforcement point moves from producer stamping to the single finalization verdict, joined by ADR-0037's new FK predicate (`is_resolved=True ⇒ dst ∈ nodes`). The invariant survives; only its producer-side anchor is retired.

## Context

### The architectural absence

The pipeline at `packages/hypergumbo-core/src/hypergumbo_core/cli.py` runs ordered passes (file index → analyzers → linkers → fingerprint post-pass → supply-chain classification → boundary synthesis → route promotion → tier filtering → metrics/ranking → write artifact) and produces a behavior-map dict that is serialized to disk. **Each pass is a writer; no pass is a reader.** Nothing reads the emitted Symbols / Edges / AnalysisRuns and checks them against the schema axioms, axis catalogs, or producer contracts.

The consequences are visible across multiple META umbrellas:

- **INV-numat** (vocabulary fields mix axes) — fields are populated with values from multiple unrelated axes because nothing enforces axis conformance at emit time.
- **INV-luhur** (AnalysisRun writer contract) — 10+ members describe writers populating fields with defaults, constants, or stale data because no test gates the writer-side contract.
- **INV-fabov** (silent input validation) — verify-claims falls through to `confirmed` for malformed inputs because no validator catches the "the data didn't match any constraint" case.
- **INV-kovob / INV-fogum** (identifier formats) — `canonical_name` carries three different values, `fingerprint` two different formats, because nothing scans the emit corpus for format drift.

ADR-0031 §"Enforcement" calls the gap out by name:

> **Runtime / corpus check — known gap.** The catalog-derived axes don't have a runtime drift linter today (the static AST walker is the wrong shape for catalog-derived values). This ADR doesn't fix that gap; a future ADR addressing INV-sugat's "no spec-vs-data validator stage" super-META is the right home for the runtime check.

ADR-0032 §"What this does NOT address" makes the same deferral. The id-construction discipline lab-notebook posture says explicitly that its reviewer-time checklist is "a manual version of what a validator stage would do automatically."

### The shape that already exists

The static-AST side of validation already exists: `multi_value_field_axis.py` + `scripts/check-multi-value-field-axis-declaration` + `tests/test_multi_value_field_axis.py::test_live_tree_passes` enforce that every `str`-typed dataclass field carries an `# axis: <category>` annotation. This is the *source-level* enforcement of ADR-0024's discipline.

What's missing is the *runtime / corpus-level* counterpart: scan the emitted IR and verify that values produced by all the upstream passes conform to the same axes the static linter enforces. The static walker can't do this — it inspects source AST; it doesn't see runtime emissions.

## Decision

Introduce a **spec-vs-data validator stage** as a post-pass in the orchestrator. The validator reads the live emitted IR (Symbols, Edges, AnalysisRuns) at the end of the pipeline, runs a set of validation classes against it, and writes a structured `validation_report` section into the behavior-map artifact.

### Pipeline position

The validator stage runs after **all enrichment passes** have populated the IR (including fingerprint stamping, supply-chain classification, route promotion, tier filtering, boundary synthesis) and **before** the optional compact transformation reshapes the output. This placement lets the validator see the final form of every Symbol and Edge that will appear in the artifact.

Concretely, the validator stage drops in immediately before `del all_symbols / del all_edges` in `run_behavior_map` (`cli.py`), reading those live dataclass instances rather than the serialized dict form. The `validation_report` is then merged into the `behavior_map` dict before the write step.

### Validator classes

Four validation classes, each responsible for one structural concern. They land progressively in separate PRs; this ADR is the architectural contract.

1. **Axis-conformance** — for every `str` (and `Optional[str]` / `Literal[str, ...]`) field on a core dataclass (`Symbol`, `Edge`, `AnalysisRun`, sub-records) that carries a `# axis: <category>` annotation, verify that emitted values are in the catalog ∪ `{None}` for Optional fields. The four ADR-0024 categories interpret differently at runtime:
   - **Known-axis-name** (`edge-type` / `symbol-kind` / `evidence-type` / `language` / etc.): values must be in the catalog accessor's `all_known_*()` set or `None`.
   - **`identity`**: values must be unique per record (within the appropriate scope — per-pass, per-language, etc.).
   - **`bounded-enum`**: values must be in the small fixed list documented in the dataclass docstring.
   - **`free-text`**: no value check; the justification was the gate at source-write time.
2. **Writer-contract** — for each `(producer-class, axis-tagged field)` pair, verify that records from that producer populate the field. This generalizes the four sub-patterns in INV-luhur's description (schema-declares-no-writer; default-only initializer; same-name-two-definitions; writer-writes-constant) into a check class. Each pass declares which fields it populates; the validator checks records emitted by that pass against its declaration.
3. **Cross-field coherence** — for documented field-pair invariants, verify that emitted records honor them. Initial invariants: (a) `Edge.dst_ref ↔ Edge.dst` coherence per `make_unresolved_edge` docstring; (b) `Symbol.language is None ↔ Symbol.protocol_origin is not None` for synthetic stand-ins (per ADR-0031 Class B); (c) `Symbol.display_label` populated on synthetic stand-ins only, not on real-source declarations (per ADR-0032). New invariants are added by appending to a declared list.
4. **Verdict-enum completeness** — for verdict-emitting code paths, verify that an `inconclusive` (or equivalent) branch exists for missing-data / malformed-input / broken-binary cases. The first instance is `ClaimVerdict` in `verify_claims.py`, which today falls through to `"confirmed"` for "no constraint matched" (INV-bitig P0). Generalized as a class because any future verdict-emitting subcommand has the same risk shape.

### Output format

The validator emits structured violation records. A single dataclass, surfaced into the artifact:

```python
@dataclass
class ValidationViolation:
    severity: str        # axis: bounded-enum {"error", "warning", "info"}
    validator_class: str # axis: bounded-enum {"axis_conformance", "writer_contract", "cross_field", "verdict_enum"}
    axis: Optional[str]  # axis: free-text — the axis name (when validator_class == "axis_conformance")
    field: Optional[str] # axis: free-text — the dataclass field name (when applicable)
    record_id: Optional[str]  # axis: free-text — Symbol.id / Edge.id / AnalysisRun.execution_id
    observed: Optional[str]   # axis: free-text — the offending value (stringified)
    expected: Optional[str]   # axis: free-text — short description of what was expected
    message: str         # axis: free-text — human-readable description for review
```

The `validation_report` section in the behavior-map artifact is a JSON object:

```json
{
  "validation_report": {
    "schema_version": "0.1",
    "violations": [
      {
        "severity": "error",
        "validator_class": "axis_conformance",
        "axis": "language",
        "field": "Symbol.language",
        "record_id": "...",
        "observed": "objective-c",
        "expected": "value in language catalog (catalog includes 'objc' not 'objective-c')",
        "message": "..."
      }
    ],
    "violations_by_class": {
      "axis_conformance": 12,
      "writer_contract": 0,
      "cross_field": 0,
      "verdict_enum": 0
    }
  }
}
```

### Default failure behavior

The validator does **not** fail the `hypergumbo run` command by default. Violations are:

1. Written into the artifact's `validation_report` section.
2. Summarized to stderr (`"[warn] N axis-conformance violations; see validation_report in <artifact>"`).
3. CI-gated by a separate test (`tests/test_validation_report_empty.py`) that runs the self-analysis corpus and fails when `validation_report.violations` is non-empty.

The split between "always emit; never fail run" and "CI gate fails when non-empty" gives the validator a soft introduction: users see violations as informational warnings, the CI catches new violations as regressions, and the self-analysis dogfooding workflow becomes the engine that drives the violation count to zero.

### Severity convention

- **`error`** — schema axiom violated. Field carries a value outside its declared axis. Must be fixed.
- **`warning`** — soft-invariant violated (e.g., writer-contract for a field that's documented optional). Should be fixed; CI gate may treat as informational depending on the test configuration.
- **`info`** — diagnostic / metric (e.g., a count that's high but not a defect). Never CI-gates.

The initial validator classes emit only `error` and `warning`; `info` is reserved for future diagnostic classes.

### Catalog interpretation under `language=None`

ADR-0031 relaxes `Symbol.language: str → Optional[str]`. The axis-conformance validator MUST accept `None` for Optional axis-tagged fields, treating `catalog ∪ {None}` as the legal value set. The same rule applies prospectively to any other `Optional[str]` field carrying an axis annotation.

For `bounded-enum` and `identity` categories, `None` legality follows the field's `Optional` declaration in the dataclass.

## Migration plan

Implementation is staged across the phases of the campaign captured in the lab-notebook plan file:

- **Phase 0 (this ADR + scaffolding)** — Land this ADR + the stub `spec_validator.py` module (returns `[]`) + the pipeline wire-up + the smoke test. No validator class is turned on; the artifact gains an empty `validation_report` section.
- **Phase 3 (validator classes turn on, one per PR)** — Land the four validator classes in dedicated PRs. After each, the self-analysis run's `validation_report` shows real violation counts; the CI gate `test_validation_report_empty.py` fails until each class's violations are reduced to zero through downstream cleanup.
- **Phase 5 (ID-format validator class)** — A fifth validator class for ID-format conformance, codifying the lab-notebook ID-construction discipline as mechanical enforcement (per ADR-0034).
- **Phase 6 (cleanup tail)** — Per-emitter fixes driven by the validator's report until the self-analysis corpus is clean.

## Consequences

### Positive

- **Closes INV-sugat super-META.** The architectural gap is named, designed, and given a single home (this ADR + the `spec_validator` module). Future tracker items reference this ADR's runtime checks rather than describing the absence in prose.
- **Provides the runtime drift gate that ADR-0031 / ADR-0032 / ADR-0034 each defer to.** Each future axis decision can declare its runtime check by adding a validator class entry or extending an existing one.
- **WI-rolol's sub-tasks become Phase 3 PRs.** Sub-task A (`ClaimVerdict.inconclusive`) is Phase 3 PR4 (verdict-enum validator). Sub-task B (AnalysisRun writer-contract test) is Phase 3 PR2 (writer-contract validator).
- **Generalizes the WI-mafik / WI-huzuv / WI-nigah retrofit pattern.** The cross-field coherence validator turns `module_hint ↔ dst_ref ↔ dst` from a manual per-analyzer migration into an enforced invariant.
- **The CI gate prevents regressions without blocking work.** Land-then-fix is supported; the gate runs against the corpus after a release, not against every PR independently.

### Negative

- **Two new modules + a new dataclass.** `spec_validator.py` joins `multi_value_field_axis.py` as a second validator. The new `ValidationViolation` dataclass is added to the IR-adjacent module set.
- **One new pipeline pass.** The validator runs over every Symbol and Edge; on large repos (100k+ Symbols), this adds time. The validator must be efficient — the axis-conformance check is O(symbols) with constant-time catalog lookups; the cross-field coherence check is similarly O(records). No O(N²) checks are added.
- **Artifact schema grows.** The behavior-map artifact gains a `validation_report` top-level section. Consumers that didn't expect it must ignore unknown keys (most JSON consumers already do). The SCHEMA_VERSION carrying ADR-0033 increments accordingly (combined with ADR-0031 + ADR-0032 at the 0.12.0 bump per the campaign plan).
- **`validation_report` itself is data the validator could in principle check.** Recursion is bounded: the validator does NOT validate its own output (no second-pass run). This is acceptable because the validator's emit code is small, statically-checked, and not corpus-driven.

### Neutral / acknowledged

- **The static-AST validator (`multi_value_field_axis.py`) remains.** It enforces source-level discipline at PR review; the runtime validator catches what slipped through. The two are complementary, not redundant.
- **The validator does not replace per-pass property tests.** Existing tests (e.g., `test_protocol_origins.py` checks the catalog shape; `test_axis_meta_keys.py` checks registry membership for meta keys) continue to run. The validator adds *corpus-level* coverage on top of *unit-level* coverage.

## Alternatives considered

1. **No validator stage; per-finding tracker items continue.** Rejected: leaves INV-sugat permanently open and each future axis decision (ADR-0031, ADR-0032, hypothetical ADR-0035+) carries its own deferred-enforcement footnote indefinitely.
2. **Lift the static-AST walker to corpus-level via post-pass AST analysis.** Rejected: the static walker uses Python AST against source files; it can't inspect runtime emissions because runtime values aren't accessible from source AST. Different mechanism, same name.
3. **One validator class per axis decision.** Rejected: the four classes proposed here are *axes-of-concern*, not axes-of-data; mapping every axis decision to its own class would multiply the validator surface and create coordination friction. The current decomposition keeps the validator surface small while letting each class accumulate value-axis-specific checks internally.
4. **Hard-fail by default.** Rejected per user direction in the campaign plan: warn + report is the chosen default; CI gate is the failure surface.

## References

- ADR-0024 (Axis Declaration Template): the static-AST validator pattern this ADR's runtime counterpart mirrors.
- ADR-0031 / ADR-0032 / ADR-0034: each defers a runtime drift check to this ADR. Phase 3 PR1 (axis-conformance) and Phase 5 PR1 (ID-format) implement those checks.
- Tracker INV-sugat: super-META closed by this ADR's adoption. The seven constituents (INV-fabov, INV-numat, INV-kurup, INV-luhur, INV-nanon, INV-dubam, INV-tazaj) each gain a "covered-by-validator-class-X" annotation as Phase 3 PRs land.
- Tracker WI-rolol: structural-fix trial whose sub-tasks A (ClaimVerdict.inconclusive) and B (AnalysisRun writer-contract test) become Phase 3 PR4 and Phase 3 PR2 respectively.
- Lab-notebook campaign plan: `~/.claude/plans/happy-swimming-sketch.md` (approved 2026-05-31).
