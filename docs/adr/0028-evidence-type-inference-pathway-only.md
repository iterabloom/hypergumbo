<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0028: Edge.evidence_type Names the Inference Pathway

- Status: Draft
- Date: 2026-05-02
- Supersedes: —
- Superseded by: —
- Related: ADR-0024 (the axis-declaration template instantiated here), ADR-0023 (the worked example whose four-phase migration shape this ADR mirrors and whose Open Question #2 explicitly anticipated this ADR), ADR-0015 (Dataflow Access Modes — the precedent for sibling fields and `meta` keys carrying per-edge structured metadata), tracker item `WI-turin-pajuk-vahuk-vaput-damoj-livif-vadob-gitob` (the deep audit whose verdict produced this ADR; the cluster taxonomy in §3 below is that audit's output), ADR-0027 (the sibling axis declaration for `Symbol.kind` surfaced by the same Adjacent Concept Sweep)

## Context

Hypergumbo's `Edge` dataclass (`packages/hypergumbo-core/src/hypergumbo_core/ir.py:395`) defines `evidence_type` as a free-form `str` with default `"ast_call_direct"` and the docstring "Type of evidence (e.g., ast_call_direct)". The documented purpose of the field is to record **how the analyzer concluded this edge exists** — the inference pathway from source code to recovered relationship. There is no enum, no canonical vocabulary, and no governing principle constraining what an `evidence_type` value should encode. Each analyzer, linker, and inference rule invents its label in isolation.

The 2026-04-30 Adjacent Concept Sweep (the periodic audit-playbook §5 sweep) flagged this field as a confirmed leak. The follow-on deep audit at `WI-turin-pajuk` returned an inventory of **210 distinct `evidence_type` values** in production code (lowercase identifier-shaped string literals at `evidence_type=` sites under `packages/`, excluding tests) and clustered them along three axes that are silently sharing one field. The audit's verdict was DEPRECATE: full ADR-0023-shape migration warranted. This confirms ADR-0023's Open Question #2 prediction ("Probably needs its own ADR").

This is the **largest concept-axis migration on the project's roadmap**: ~140 production files have `evidence_type` literals, and the producer-rename Phase 3 will likely take 7-12 days plus bakeoff validation per cluster. ADR-0028 instantiates [ADR-0024](0024-axis-declaration-template.md)'s four-part axis-declaration template for `Edge.evidence_type` and lays out the migration shape; per-cluster verdicts (CANONICAL / FOLD / DEPRECATE-NO-FOLD per ADR-0024 §"Family-audit verdict methodology") are deferred to audit-findings documents at `docs/audits/<NN>-<topic>.md`.

### The three axes that are being conflated

Cluster taxonomy from the WI-turin-pajuk audit (210 values; sub-counts approximate):

- **Cluster A — Inference pathway (~75-90 values; the documented purpose of the field).** Values that genuinely answer *"how did the analyzer conclude this edge exists?"*: `ast_call_direct`, `ast_import`, `ast_extends`, `ast_new`, `ast_attribute`, `ast_implements`, `ast_decorator`, `ast_annotation`, `ast_call_static`, `ast_call_type_inferred`, `ast_method_this`, `ast_method_inferred`, `ast_type_ref`, `tree_sitter`, `naming_convention`, `scip_occurrence_ref`, `function_call`, `method_call`, `import_statement`, `import_declaration`, `include`, `require`, `constructor_call`, `object_creation`, `function_reference`, `function_reference_arg`, `callable_reference`, `function_pointer`, `function_pointer_arg`, `dispatch_table_initializer`, `dispatch_table_reference`, `struct_field_reference`, `object_field_reference`, `module_attribute_reference`, `method_reference`, `function_application`, `typed_receiver_call`, `typed_field_call`, `receiver_call`, `bare_method_call`, `pipe_call`, `ambiguous_method_call`, `method_call_type_inferred`, `method_call_field_chain`, `stdlib_method_call`, `method_call_recovery`, `span_overlap`, `canonical_name`, `alias_resolution`, `import_resolution`, `hg_annotation`, `type_hierarchy`, `behaviour_callback`, `closure_wrapper`, `build_target_main`, `import_to_manifest`, `bridging_header_import`, … This is the canonical seed (after the apex/peer collapse described in Cluster D below).

- **Cluster B — Resolution status smuggled in (~6+ values).** Every `*_unresolved` / `unresolved_*` variant doubles as a resolution-status flag squeezed into the inference label. `ast_annotation` vs `ast_annotation_unresolved` answers BOTH "inference pathway" AND "did we resolve the target?" in the same field. Values: `unresolved_external_call`, `unresolved_method_call`, `ast_annotation_unresolved`, `ast_decorator_unresolved`, `luajit_ffi_unresolved`, `grpc_unresolved_resolution`. **This is the canonical Test 4 (mechanism vs category) failure: a flag dimension squeezed into a label.**

- **Cluster C — Framework-specific dispatch conventions (~30+ values).** A third axis: which framework-detection pattern fired. Values: `django_orm_dispatch`, `airflow_framework_dispatch`, `jackson_bean_dispatch`, `kafka_streams_dispatch`, `otp_genserver_dispatch`, `rust_trait_dispatch`, `go_cobra_dispatch`, `dispatch_pattern`, `nestjs_module_registration`, `controller_routes`, `router_routes`, `middleware_chain`, `ruby_delegate`, `activerecord_association`, `orm_accessor_pattern`, `phoenix_event_match`, `resolver_field_match`, `resolver_type_match`, `graphql_operation_match`, `openapi_path_match`, `openapi_operation_id_match`, `vue_event_handler`, `vue_component_import`, `yjs_crdt_pattern`, `implicit_convention`, `registry_dispatch`, `job_enqueue`, `signal_receiver_match`, `http_url_match`, `table_name_match`, `crypto_api_pattern`, `subprocess_cli_match`, `tauri_invoke`, `tauri_emit_listen`, `event_name_match`, `specta_wrapper_import`, `jni_naming_convention`, `abi_name_match`. These name **how the framework dispatched / which detection pattern recognized it**, not the analyzer's inference pathway in the Cluster-A sense.

- **Cluster D — Apex/peer overloads (Test 2 fires).** `function_call` (38 occurrences), `method_call` (6), `function_application` (3), `local_call`, `remote_call`, `typed_receiver_call`, `ambiguous_method_call`, `bare_method_call`, `method_call_type_inferred`, `method_call_field_chain`, `pipe_call`, `receiver_call`, `typed_field_call`, … — multiple "flavors" of *a call happened* in the same field, distinguished only by an inference-path detail that should be a `meta` key. `function_call` is sometimes apex (the generic top type), sometimes peer of `method_call` — the same string playing both roles depending on the analyzer.

### Four leakage tests (cluster-level)

- **Test 1 (Property derivability).** Cluster B values (resolution status) are derivable from a separate boolean — *is the dst symbol resolved?* — easily a sibling `Edge` field. Cluster C values (framework conventions) duplicate information that `Edge.meta["framework"]` / `meta["protocol"]` already carries (or could carry, with negligible producer change). **Massive leakage.**

- **Test 2 (Apex/peer overloading).** `function_call` vs `method_call` vs `function_application` vs `typed_receiver_call` vs `ambiguous_method_call` — same fundamental relationship (a call), split across N variants by inference-path detail. The apex (`function_call` or arguably `ast_call`) coexists with the specialized peers, and the analyzer-emit-site decides which to use. **Fires.**

- **Test 3 (Construct vs relationship).** Cluster A members like `ast_extends` name a syntactic construct ("an extends keyword was lexed and matched"); Cluster B members like `unresolved` name a *resolution outcome* (a property of the dst, not of the inference); Cluster C members like `django_orm_dispatch` name a *detection pattern firing on producer side*. Three different categories of question. **Fires.**

- **Test 4 (Mechanism vs category).** Cluster C — `django_orm_dispatch` is a mechanism (HOW the framework dispatched); the analyzer's category-question is just "a call edge with framework context." The mechanism deserves a `meta` key, not a labeled-value of the inference axis. **Fires.**

### Why this matters now

The ADR-0023 lesson scales here: hardcoded `evidence_type` enumerations in consumers (taint analysis, IO boundary detection, slice membership, ranking weights) drift in both directions silently — missing emitted values when a new framework adds its own `*_dispatch` variant, and including never-emitted values when a producer is renamed without coordinating consumer cleanup. The fact that ADR-0023 §1 already documented one such cross-field leak (`unresolved_external_call` in `taint.TAINT_CALL_EDGE_TYPES` was an `evidence_type` value being filtered against `edge_type`, a membership test that never matched at runtime) is the canonical proof: 210 distinct `evidence_type` values + zero typing discipline + ~140 producer files = the conditions for that bug class to recur indefinitely.

This ADR is the structural fix. Catching the pattern at 210 values is significantly cheaper than catching it at 300+; new analyzers (especially every framework-detection linker) add Cluster-C values at a steady rate.

## Decision

Adopt the following typing rule for the `Edge.evidence_type` field:

> **`Edge.evidence_type` names the inference pathway by which the analyzer concluded this edge exists. The dst-resolution status moves to a sibling `Edge` field. Framework-specific dispatch conventions move to `Edge.meta`. Apex/peer call variants collapse to a canonical `ast_call` plus a `meta["call_construct"]` distinguishing key.**

Three operational corollaries:

1. **No new `evidence_type` value may encode a non-inference axis.** If a proposed new value would record resolution status, framework convention, or call-construct flavor, the right answer is to use the canonical inference label and route the additional fact to its proper home (sibling field for resolution; `meta` for framework / construct).

2. **Resolution status promotes to a sibling field per ADR-0024 §"Fold-residue discipline" rule 3.** Cluster B's six values fail the recurrence-promotion threshold (≥3 distinct axis values OR ≥2 producer modules emit it) trivially: resolution status is mentioned in 6 inference-axis values across at least 4 producer modules. The right home is `Edge.is_resolved: bool` (or, if downstream needs richer states, `Edge.resolution_status: Literal["resolved", "unresolved", "stub"]`). The choice between bool and enum is delegated to the cluster-B audit-findings doc.

3. **Framework-specific conventions move to `Edge.meta["framework_dispatch"]` (or `meta["detection_pattern"]`).** Cluster C's ~37 values fold to canonical inference labels plus structured meta — the same shape ADR-0026 / audit-findings 0002 used for IPC mechanisms and ADR-0025 / audit-findings 0001 used for dispatch-and-publish patterns on `Edge.edge_type`.

Following [ADR-0024](0024-axis-declaration-template.md)'s four-part template:

### 1. Axis name

The **evidence-type axis** (with sections `inference_pathway`, `endpoint_shape` for deprecation candidates, and `pending_classification` for the long-tail audit). The axis name appears in:

- Module-level constants in the registry (`AXIS_INFERENCE_PATHWAY`, `AXIS_ENDPOINT_SHAPE`, `AXIS_PENDING`).
- The by-axis view's section headings (`docs/concept-axes.md`).
- This ADR's title and first paragraph.

The `endpoint_shape` section name is reused from ADR-0023 and ADR-0027 even though Cluster B's leakage shape is "resolution-status leakage" rather than "endpoint leakage" in the strict sense. The name composes; the reuse is a feature. (An alternative — `endpoint_shape_or_resolution_status_or_framework_convention` — would be clearer at the cost of section-vocabulary fragmentation across axes.)

### 2. Axiom

> **`Edge.evidence_type` names the inference pathway by which the analyzer concluded this edge exists. Properties of the dst's resolvedness, of the framework's dispatch convention, or of the call-construct's surface form are queried from siblings (`Edge.is_resolved`) or `Edge.meta`, not smuggled into the evidence label.**

Properties of this axiom:

- **Falsifiable** — given a candidate value, the axiom either accepts it or rejects it. `ast_annotation_unresolved` is rejected (the `_unresolved` suffix encodes resolution status, which lives on the sibling field). `django_orm_dispatch` is rejected (the `django_orm` portion encodes a framework-specific detection pattern, which lives in `meta`). `ast_call_direct` is accepted (a pure inference pathway).
- **One sentence long** (with a dependent clause; arguably two semicolons but one sentence-shape).
- **Distinguishes the canonical section from the rest** — Cluster A members are `inference_pathway`; Cluster B / C members are `endpoint_shape` deprecation candidates; Cluster D's apex collapses to canonical and the peers fold via `meta["call_construct"]`.

### 3. Consumer pattern

The accessor function shape that consumers use to query the axis:

```python
from hypergumbo_core.evidence_types import (
    AXIS_INFERENCE_PATHWAY, all_evidence_type_names, evidence_types_on_axis,
)

# Instead of: _AST_DERIVED_INFERENCE = {"ast_call_direct", "ast_import", "ast_extends"}
canonical_inference = evidence_types_on_axis(AXIS_INFERENCE_PATHWAY)
```

Consumers needing "all inference-pathway-shaped evidence types" call `evidence_types_on_axis(AXIS_INFERENCE_PATHWAY)` instead of maintaining a local set. The accessor signature mirrors `edge_types_on_axis` and `symbol_kinds_on_axis` exactly:

- `evidence_types_on_axis(axis: str) -> tuple[EvidenceTypeSpec, ...]` — canonical form.
- Values returned are typed objects (`EvidenceTypeSpec` carrying name + axis + description), not raw strings.
- The accessor is colocated with the registry module at `packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py`.

Consumers that need to combine `evidence_type` with the new sibling field and meta query the composed shape:

```python
def is_unresolved_orm_call(edge: Edge) -> bool:
    return (
        edge.evidence_type == "ast_call_direct"
        and not edge.is_resolved
        and edge.meta.get("framework_dispatch", "").endswith("_orm_dispatch")
    )
```

This is the (evidence_type, is_resolved, meta) shape per ADR-0023 §6 Phase 2's pattern. Consumer migration in Phase 2 below replaces hardcoded evidence-type sets with this query pattern.

### 4. Enforcement

Two-layered, mirroring ADR-0023 and ADR-0027:

- **Static (in-tree).** The shared AST walker `hypergumbo_core.axis_drift.find_drift` (parameterized per `WI-pilam-jukus`) scans the package source tree for module-level sets whose target name contains a configurable substring filter. The `Edge.evidence_type` instantiation uses `name_filter="EVIDENCE_TYPE"` plus the new `EVIDENCE_TYPES` registry. Surfaces in two places: a property test in `test_evidence_types.py` (CI gate) and a CLI script `scripts/check-evidence-type-drift` (pre-commit gate). The wrapper is a one-liner per ADR-0024 §4:

  ```python
  from hypergumbo_core.axis_drift import find_drift
  from hypergumbo_core.evidence_types import all_evidence_type_names

  def find_evidence_type_drift(repo_root: Path) -> list[str]:
      return find_drift(
          repo_root,
          name_filter="EVIDENCE_TYPE",
          registry_names=all_evidence_type_names(),
      )
  ```

  **Note on filter false-positives.** `EVIDENCE_TYPE` as a substring filter may match consumer-side sets named `*EDGE_TYPE*` or `*EDGE_EVIDENCE*` if any future name ambiguity creeps in. The `axis_drift.find_drift` helper supports a `name_filter_excludes` parameter (or equivalent — to be confirmed during Phase 1 implementation) so the wrapper can exclude `EDGE_TYPE` from the match if necessary. The actual `name_filter` choice is a Phase 1 decision — `EVIDENCE_TYPE` reads cleanly today; a Phase 1 sweep of the codebase confirms.

- **Runtime corpus-based (planned).** A coverage check analogous to ADR-0023 §3: partition emitted edges by `(src.kind, dst.kind, src.language, dst.language)` and assert that within each partition, `evidence_type` is consistent with the (kind, edge_type, framework) context — i.e., a `dispatches_to` edge from a Django view to an ORM model should have `evidence_type` from a small allow-list of inference labels regardless of which producer linker emitted it. The implementation reuses the `runtime_coherence` harness from ADR-0023 §3 once Phase 1 lands.

For the static check, the existing `axis_drift.find_drift` surface already supports the parameterization; this ADR adds no new helper, only the registry, the wrapper, and the new sibling field.

## Detailed analysis: per-cluster fold targets

Per ADR-0024 §"Family-audit verdict methodology" + the audit-findings filing convention recorded in this ADR's tracker discussion (WI-pilit, 2026-05-02), the per-value CANONICAL / FOLD / DEPRECATE-NO-FOLD verdicts with structured `diagnostic_test` and `rationale` are filed as audit-findings documents at `docs/audits/<NN>-<topic>.md`, NOT as embedded verdict tables in this ADR.

The high-level cluster taxonomy and fold targets are this section's responsibility:

- **Cluster A (~75-90 values; canonical seed).** All values keep their existing form on the `inference_pathway` section. Per-value rationale (no deprecations expected; some apex/peer overloads in Cluster D fold across) goes into the cluster-A audit-findings doc.

- **Cluster B (~6 values; resolution-status fold).** Each value folds to its sibling-stripped form on the `inference_pathway` section + `Edge.is_resolved=False`:
  - `unresolved_external_call` → `ast_call_direct` (or producer-specific equivalent) + `is_resolved=False`.
  - `unresolved_method_call` → `method_call` (or post-collapse `ast_call` + `meta["call_construct"]="method"`) + `is_resolved=False`.
  - `ast_annotation_unresolved` → `ast_annotation` + `is_resolved=False`.
  - `ast_decorator_unresolved` → `ast_decorator` + `is_resolved=False`.
  - `luajit_ffi_unresolved` → an inference-pathway value naming the FFI inference (`luajit_ffi_lookup` or similar) + `is_resolved=False`.
  - `grpc_unresolved_resolution` → an inference-pathway value naming the gRPC stub-resolution attempt (`grpc_stub_resolution`) + `is_resolved=False`.

  The exact canonical-form choice for the `_unresolved` strip per row goes in the cluster-B audit-findings doc. **Sibling-field design** (boolean vs. enum) is the ADR-0028-internal decision recorded here per ADR-0024 §"Fold-residue discipline" rule 3 (recurrence-promotion to dedicated field): see the design call-out below.

- **Cluster C (~37 values; framework-convention fold).** Each value folds to a canonical inference label + `meta["framework_dispatch"]=<value>` (or `meta["detection_pattern"]=<value>`, depending on per-row semantics; the cluster-C audit-findings doc decides per row). Worked examples:
  - `django_orm_dispatch` → `ast_call_direct` (or `dispatches_to_inferred`) + `meta["framework_dispatch"]="django_orm"`.
  - `kafka_streams_dispatch` → an inference-pathway value naming how the dispatch was inferred + `meta["framework_dispatch"]="kafka_streams"`.
  - `nestjs_module_registration` → `ast_decorator` + `meta["framework_dispatch"]="nestjs_module_registration"`.
  - `phoenix_event_match` → `naming_convention` + `meta["detection_pattern"]="phoenix_event_match"`.

  After the fold, `meta["framework_dispatch"]` would have ~30 distinct values across ~30 producer modules — a heavy meta key that may itself trip ADR-0024 §"Fold-residue discipline" rule 3's recurrence-promotion threshold. The cluster-C audit-findings doc records the per-row mapping; the question of whether `framework_dispatch` should subsequently promote to a dedicated `Edge.framework: str | None` field is filed as a Phase-3 follow-on decision (after producer migration ships and meta-key emission stabilizes).

- **Cluster D (~13 values; apex/peer collapse).** All call-construct flavors collapse to a canonical apex (`ast_call`) plus `meta["call_construct"]` taking one of a small enumerated set of values:
  - `function_call` → `ast_call` + `meta["call_construct"]="function"`.
  - `method_call` → `ast_call` + `meta["call_construct"]="method"`.
  - `function_application` → `ast_call` + `meta["call_construct"]="application"`.
  - `typed_receiver_call`, `bare_method_call`, `pipe_call`, `receiver_call`, `typed_field_call` → `ast_call` + `meta["call_construct"]` distinguishing each.
  - `ambiguous_method_call`, `method_call_type_inferred`, `method_call_field_chain`, `method_call_recovery`, `stdlib_method_call` → `ast_call` + `meta["call_construct"]="method"` + per-row `meta["resolution_quality"]` or similar.

  The choice of apex name (`ast_call` vs `function_call` as the canonical apex) follows ADR-0023's heuristic ("most-frequent emitter wins"); per the WI-turin-pajuk audit, `function_call` is the high-frequency emitter (38 occurrences). The cluster-D audit-findings doc decides apex selection and the per-row `call_construct` enumeration; the apex name may end up `ast_call` if the audit prefers it for symmetry with `ast_*` companions.

Estimated emit-site distribution: ~140 production files have `evidence_type` literals. Cluster A is ~110 of those; Cluster B ~6 producer modules; Cluster C ~30 producer modules; Cluster D ~25 producer modules. Total Phase 3 producer edits substantially larger than ADR-0023's, hence the larger Phase 3 wall-clock estimate (7-12 days vs ADR-0023's 3-5).

### Sibling-field design call-out (`Edge.is_resolved`)

Per ADR-0024 §"Fold-residue discipline" rule 3, Cluster B's "resolution status" property meets the recurrence-promotion threshold (≥3 distinct axis values; ≥2 producer modules). The fold target is therefore a dedicated `Edge` field, not a `meta` key.

Two options for the field shape, decided here:

- **Option 1 — `Edge.is_resolved: bool` (default `True`).** Producer marks `is_resolved=False` whenever the dst symbol couldn't be resolved at analysis time. Simplest possible API. Captures the binary distinction Cluster B's `_unresolved` values already encode.

- **Option 2 — `Edge.resolution_status: Literal["resolved", "unresolved", "stub", "ambiguous"]`.** Richer state. `stub` for gRPC-stub edges where the dst is known to exist but its symbol record isn't in scope; `ambiguous` for edges where multiple candidate dsts exist and the analyzer didn't pick one.

**Decision: Option 1 (`Edge.is_resolved: bool`).** Rationale:
- The Cluster B values empirically encode a binary distinction (resolved vs not); the richer states in Option 2 are speculative.
- Bool is JSON-stable across schema versions; enum changes require coordinated bumps.
- If a producer subsequently needs to record stub or ambiguous state, the existing `meta` is the right home for that distinction (per ADR-0024 §"Fold-residue discipline" rule 3 — promote *only* when the recurrence threshold fires; speculative fields rot).

The bool defaults to `True` because the **dominant case is resolved**: ~90% of edges have well-determined dsts. Producers explicitly set `is_resolved=False` only in the unresolved case, minimizing producer-side churn.

Schema impact: `SCHEMA_VERSION` minor bump in Phase 1 (additive — old consumers ignore the new field; new consumers rely on the default). Phase 4 schema bump removes the deprecated `*_unresolved` evidence types but leaves the field shape stable.

## Migration

Mirrors ADR-0023's four-phase shape; consumers can keep working throughout. JSON output stability is treated as additive deprecation in Phase 1, hard rename in Phase 4.

### Phase 1 — registry + sibling field + drift linter

Mirrors ADR-0023 Phase 1 but with one extra artifact (the new sibling field). Land:

1. The registry module at `packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py` with `AXIS_*` constants, `EvidenceTypeSpec` frozen dataclass, the `EVIDENCE_TYPES` tuple-of-specs (seeded with Cluster A on `inference_pathway`, Clusters B / C on `endpoint_shape`, Cluster D apex+peers split between `inference_pathway` and `endpoint_shape` per the audit), and the accessors.
2. The `Edge.is_resolved: bool = True` field on the `Edge` dataclass at `ir.py:368-410` (the field block where `evidence_type` lives), plus serialization round-trips in `to_dict` / `from_dict`. Schema regeneration adds the new key.
3. The drift-coherence linter at `scripts/check-evidence-type-drift`, wiring into `.githooks/pre-commit`.
4. The property test in `tests/test_evidence_types.py`: registry invariants + drift-detection test + `Edge.is_resolved` round-trip + `is_resolved` defaults to `True` invariant.
5. By-axis view extension: add the `Edge.evidence_type` axis's three sections to `scripts/generate-concept-axes`'s `_SECTIONS` table; regenerate `docs/concept-axes.md`.
6. JSON Schema integration: extend `scripts/generate-schema` to emit `x-axis-of-values` for the `Edge.evidence_type` enum.

Estimated effort: ~1.5-2 days. Adds the sibling field and registers it on the schema; producer / consumer migrations don't ship in this phase.

### Phase 2 — migrate consumers

Update consumers to query the canonical (evidence_type, is_resolved, meta) shape rather than hardcoded `*_unresolved` set membership. Known consumer surfaces (audit during Phase 1):

- `packages/hypergumbo-core/src/hypergumbo_core/taint.py` — already had cross-field bug (`unresolved_external_call` in `TAINT_CALL_EDGE_TYPES`, removed alongside ADR-0023 Phase 1).
- `packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py` — IO boundary detection may filter on resolution status.
- `packages/hypergumbo-core/src/hypergumbo_core/slice.py` — slice membership rules likely filter on resolution status.
- `packages/hypergumbo-core/src/hypergumbo_core/ranking.py` — centrality weights may discount unresolved edges.
- The fundamental-concept audit's existing inventory at `~/hypergumbo_lab_notebook/concept-audit-sweep_2026-04-30.md` lists additional consumer sites surfaced by the audit walk.

Each consumer migration is reversible: the old `*_unresolved` set semantics still produce the right answer with the new (`is_resolved=False`) query shape, plus the consumer can additionally see all inference-pathway values uniformly.

Estimated effort: ~1-2 days, comparable to ADR-0023 Phase 2.

### Phase 3 — unify producers

Sweep the producer sites that emit deprecation-list `evidence_type` values. Migration order, lowest-risk-first:

1. **Cluster B** (resolution-status fold; ~6 values, ~6 producer modules): purely additive on consumer side (`is_resolved=False` default fail-safe is correct for any consumer that hasn't migrated). Smallest blast radius — the natural first subset.
2. **Cluster D** (apex/peer collapse; ~13 values, ~25 producer modules): per-language sub-PRs, each with its own `awaits_bakeoff_validation` tag. The apex `ast_call` (or `function_call`) was already in the registry, so consumer-side enumerations don't need to change.
3. **Cluster C** (framework-convention fold; ~37 values, ~30 producer modules): largest single subset; the heaviest Phase 3 work. Per-framework sub-PRs (django, kafka, phoenix, nestjs, …) each ship as their own `awaits_bakeoff_validation` PR. Estimated 5-8 days alone.

Per producer subset:
1. Replace the specialized `evidence_type` with the canonical equivalent.
2. Set `is_resolved=False` (Cluster B) or populate `meta["framework_dispatch"]` / `meta["detection_pattern"]` / `meta["call_construct"]` (Clusters C / D) as appropriate.
3. Re-run the drift property test; the offender set strictly shrinks each migration.

Estimated effort: ~7-12 days plus per-subset bakeoff validation. This is the largest concept-axis migration on the project's roadmap.

### Phase 4 — schema bump and deprecation removal

Mirrors ADR-0023 Phase 4's split shape, scaled up for the larger value count:

- **Phase 4a (additive).** Each `endpoint_shape` value (Cluster B / C / D peers) gets an `x-deprecated` annotation in `docs/schema.json`. Values stay valid in the enum during the deprecation window. `SCHEMA_VERSION` minor bump. Per-value migration guidance lives in the registry's `EvidenceTypeSpec.description`.

- **Phase 4b (hard-fail; piecewise).** Remove deprecated values from `EVIDENCE_TYPES` once the corresponding Phase 3 producer subset's `awaits_bakeoff_validation` tag clears. Schema enum values disappear for removed entries piecewise, mirroring the ADR-0023 §6 Phase 4b "many-small-ships" shape. Each Phase 4b sub-ship gets its own `SCHEMA_VERSION` minor bump (likely 5-7 sub-ships across the family clusters).

Phase 4b prerequisites match ADR-0023's: bakeoff validation must clear for the corresponding Phase 3 subset before its 4b ships. Given the producer-migration scope, expect the Phase 4 work to span 4-8 weeks even in the optimistic case.

Estimated effort: Phase 4a ~1 day; per-subset Phase 4b ~0.5 day each, gated on bakeoff validation cycle time.

### Total

~10-15 days of focused producer / consumer / registry work, plus a multi-week Phase 4b schedule gated on per-cluster bakeoff validation. The total wall-clock from ADR landing to all-`endpoint_shape`-values-removed is likely 6-10 weeks. Migration interleaves with feature work; only Phase 1 needs to land before other linker changes to prevent further accumulation.

## Consequences

### Positive

- **Cuts the `Edge.evidence_type` vocabulary** from 210 to a manageable canonical set (~75 inference-pathway values after apex/peer collapse), making the IR easier to reason about.
- **Eliminates the cross-field leak structurally.** ADR-0023 §1 documented an `evidence_type` value (`unresolved_external_call`) silently filtered against `edge_type`, a membership test that never matched. The drift linter blocks recurrence at the registry level: the value can't appear in two unrelated registries by accident.
- **Cleaner consumer queries.** `(evidence_type, is_resolved, meta)` is the natural query shape for "what kind of inference produced this edge, did it resolve, and what framework context surrounds it?" — three distinct questions, three distinct surfaces, no string-prefix gymnastics.
- **Promotes resolution status to a first-class field.** The `is_resolved` boolean is queryable independently of evidence type; ranking / slice / sketch consumers gain a clean filter that was previously buried in label-string membership tests.
- **Reuses ADR-0024's tooling.** Registry shape, `axis_drift.find_drift`, by-axis view aggregator, JSON Schema extension all transfer one-for-one. The implementation is mostly fill-in-the-template plus the new sibling field.
- **Aligns with the sibling axis ADR.** ADR-0027 (Symbol.kind) and ADR-0028 (Edge.evidence_type) both ship axis declarations on core dataclass fields; the audit-findings convention captures their per-cluster verdicts uniformly.

### Negative

- **Largest concept-axis migration on the roadmap.** ~140 production files, ~10-15 days of focused producer work, ~6-10 weeks total wall-clock to schema-stable steady state. Migration cost is non-trivial.
- **JSON output changes** for ~135 deprecated evidence-type strings, breaking external consumers that filter by them. Mitigated by the additive Phase 4a deprecation window (and by the fact that the relevant external consumers — `verify-claims`, downstream audit tooling — are in the same monorepo and migrate together).
- **`Edge.meta` carries more keys.** `meta["framework_dispatch"]`, `meta["detection_pattern"]`, `meta["call_construct"]` are net-new keys after fold. The recurrence-promotion threshold in ADR-0024 §"Fold-residue discipline" rule 3 will fire on `framework_dispatch` (~30 producers, ~30 distinct values) — Phase 3 follow-on work likely promotes it to a dedicated `Edge.framework: str | None` field.
- **Schema version churn.** Phase 4b ships ~5-7 minor `SCHEMA_VERSION` bumps as per-cluster sub-ships clear bakeoff validation. Standard cost, but real.
- **Bakeoff revalidation cost.** Each per-cluster sub-PR needs `awaits_bakeoff_validation` discipline. Cumulative bakeoff cost is significant.

### Risks

- **Cluster C is the long tail.** ~37 framework-specific values across many frameworks (Django, Kafka, Phoenix, NestJS, Vue, OpenAPI, gRPC, Tauri, JNI, …). Per-framework sub-PRs let migration interleave with feature work, but the cumulative producer churn is substantial. Mitigation: schedule Cluster C as several Phase 3 sub-PRs over multiple weeks rather than as a single large change.
- **`framework_dispatch` meta-key promotion.** If Phase 3 reveals that `meta["framework_dispatch"]` is consistently emitted across many producers and consumed by many consumers, the recurrence-promotion threshold fires and a dedicated `Edge.framework` field is the right answer. This is a follow-on ADR (or follow-on §6 amendment to this ADR), not in scope here. Mitigation: track the meta-key emission count during Phase 3; file the follow-on once the threshold trips.
- **Apex selection ambiguity in Cluster D.** Choice between `ast_call` (symmetry with `ast_*` companions) and `function_call` (most-frequent emitter heuristic) is a coin flip. The cluster-D audit-findings doc decides; either choice is recoverable but the wrong choice creates churn during Phase 3.
- **Cluster B fold-target ambiguity.** Some `_unresolved` values fold to a clear canonical (`ast_annotation_unresolved` → `ast_annotation`); others (`grpc_unresolved_resolution`, `luajit_ffi_unresolved`) imply a producer-specific inference-path label that may not yet exist in Cluster A. The cluster-B audit-findings doc may identify ~2-3 new canonical inference labels to add.
- **Producer-migration coordination.** ~25-30 producer modules touch Cluster D (call-construct fold). The fold pattern is uniform but the per-language emit sites are diverse. Mitigation: per-language sub-PRs; smart-test ensures each PR's coverage is intact.

## Alternatives considered

### A. Status quo — no axis declaration

Keep `evidence_type` free-form, accept the proliferation. Cost: silent bugs ship (the cross-field leak ADR-0023 §1 already documented); review burden grows; new framework analyzers reintroduce the pattern; the Adjacent Concept Sweep cycle re-flags this field every quarter without resolution. Rejected: the audit verdict was DEPRECATE.

### B. Multi-axis structured evidence (`(pathway, framework?, resolution?, construct?)`)

Replace `evidence_type: str` with a tuple or structured object encoding multiple axes simultaneously. More expressive, but heavier migration cost and over-engineering relative to the actual problem — most leakage cleanly resolves to "use the canonical pathway and route the additional fact to its proper home (sibling field for resolution; meta for framework / construct)." Same trade-off ADR-0023 §"Alternatives B" rejected. Rejected as over-design.

### C. ADR-0023-shape relabel + sibling field (this ADR)

Define the principle, deprecate the leakage, enforce via property test plus drift linter, promote the recurring resolution-status property to a dedicated `is_resolved` field. Lowest migration cost compatible with the recurrence-promotion threshold; aligns with ADR-0024's template; mirrors ADR-0027's structure for the sibling-axis case. **Recommended.**

### D. Bundle with ADR-0027 (Symbol.kind)

Both ADRs were flagged by the same Adjacent Concept Sweep; both have DEPRECATE verdicts. A combined ADR could amortize template instantiation work. Rejected: the two axes are independent (no shared producer files; no shared consumer surfaces beyond ranking weight tables that already separate them); a single bundled ADR would be harder to amend without touching unrelated material (the same reason ADR-0024 §"Alternatives B" rejected one mega-ADR per host dataclass). Each axis ships as its own ADR, with this ADR's sibling-field design reused if `Symbol.kind` later needs an analogous fold (it doesn't appear to per ADR-0027's analysis).

### E. Defer until ADR-0027 (Symbol.kind) lands

Sequence the migrations: ship ADR-0027's Phase 1 first, then ADR-0028's Phase 1. Rejected: the two ADRs share no implementation work in Phase 1 (the registries are separate modules; the drift linter is parameterized); blocking each on the other adds latency without reducing overall effort. The sibling axis ADRs can ship in parallel.

## Open questions

1. **Exact apex name in Cluster D.** `ast_call` or `function_call`? Decision deferred to the cluster-D audit-findings doc. Either choice is recoverable.

2. **`framework_dispatch` meta-key promotion.** Will `meta["framework_dispatch"]` itself trip ADR-0024 §"Fold-residue discipline" rule 3's recurrence-promotion threshold during or after Phase 3? Tracking: monitor the meta-key emission count during Phase 3; file a follow-on tracker item if the threshold fires (likely 2-3 weeks into Phase 3 wall-clock).

3. **Cluster B canonical-form additions.** Some `_unresolved` values may need new Cluster A canonical inference labels (`grpc_stub_resolution`, `luajit_ffi_lookup`, …). Decision deferred to the cluster-B audit-findings doc, which lists the new canonical labels per row.

4. **`is_resolved` semantics for stub edges.** Some gRPC / FFI edges have a known-existing dst whose symbol record is out of scope (the dst is in a generated stub the analyzer doesn't see). Should these be `is_resolved=True` or `is_resolved=False`? Empirically the simpler answer is `is_resolved=False` because the dst is unrecovered for the analysis pass; consumers can disambiguate via `meta["resolution_method"]="stub"` if needed. Deferred to the cluster-B audit-findings doc; the chosen rule applies uniformly to all 6 Cluster B values.

5. **Coordination with ADR-0027.** Both ADRs depend on `axis_drift.find_drift` and the audit-findings filing convention. Phasing: Phase 1 of either can land independently (no merge-conflict surface beyond `concept-axes.md` regeneration); subsequent phases proceed independently per ADR. Tracked at the parent items WI-dahim and WI-pilit.

6. **`evidence_type` default.** The current default is `"ast_call_direct"`. Should it become `None` to force callers to provide a value? Rejected: the default shipped two years ago and downstream consumers depend on it; changing it is a breaking change unrelated to the axis declaration. Stays as-is in this ADR; possibly revisited in a future cleanup ADR.

## Related

- **Surfaced by**: WI-turin-pajuk-vahuk-vaput-damoj-livif-vadob-gitob — the deep audit whose verdict produced this ADR.
- **Anticipated by**: ADR-0023 Open Question #2 — explicitly noted that `evidence_type` "probably needs its own ADR" once `Edge.edge_type`'s axis declaration landed.
- **Generalized template**: [ADR-0024](0024-axis-declaration-template.md) — the four-part axis-declaration template instantiated here, including the §"Fold-residue discipline" rule that operationalizes the `is_resolved` sibling-field promotion.
- **Worked example**: [ADR-0023](0023-edge-type-relationship-not-endpoints.md) — the four-phase migration shape mirrored here.
- **Pattern precedent**: ADR-0015 (Dataflow Access Modes) — `meta` field for relationship metadata; the same precedent for `meta["framework_dispatch"]` here.
- **Sibling axis ADR**: [ADR-0027](0027-symbol-kind-language-construct-only.md) (Symbol.kind axis declaration) — independent axis surfaced by the same Adjacent Concept Sweep; can ship in parallel.
- **Audit playbook**: [Fundamental Concept Audit](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md).
- **Audit-findings convention**: [`docs/audits/README.md`](../audits/README.md) — per-cluster verdict outputs file there; bucket boundary at [`docs/adr/README.md`](README.md).
- **Future tracker items**: After this ADR lands, file separate items for each migration phase (registry + sibling field + linter, consumer migration, producer migration per cluster, schema bump). Same chain pattern as ADR-0023's WI-sahab-fatoz / WI-mokam-jalig / WI-vomoj-suhaz.
