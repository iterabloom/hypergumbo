<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0027: Symbol.kind Names the Source-Language Syntactic Construct

- Status: Accepted (Phases 1–4 complete through `SCHEMA_VERSION` 0.6.0; endpoint_shape closure shipped per Wave 8 — all 71 endpoint_shape values removed from `SYMBOL_KINDS` per audit-findings 0005/0006/0007/0009/0010/0011/0013)
- Date: 2026-05-02
- Supersedes: —
- Superseded by: —
- Related: ADR-0024 (the axis-declaration template instantiated here), ADR-0023 (the worked example whose four-phase migration shape this ADR mirrors), ADR-0014 (Generalized Symbol Identity — the dataclass this axis types one field of), ADR-0028 (the sibling-axis ADR for `Edge.evidence_type`; ADR-0028 Phase 1 retroactively re-opened this ADR's `Symbol.kind` JSON Schema enum — see the schema-impact note appended to Phase 1 step #5 below), tracker item `WI-dumiz-bikul-pitaf-gutiv-nudig-vovam-sinad-vogaj` (the deep audit whose verdict produced this ADR; the cluster taxonomy in §3 below is that audit's output), [`docs/MIGRATION-5.X-CONCEPT-AXES.md`](../MIGRATION-5.X-CONCEPT-AXES.md) (per-value rename tables for JSON consumers post-closure)

## Context

Hypergumbo's `Symbol` dataclass (`packages/hypergumbo-core/src/hypergumbo_core/ir.py:251`) defines `kind` as a free-form `str` with the docstring "Type of symbol (function, class, etc.)". There is no enum, no canonical vocabulary, and no governing principle that constrains what a `kind` value should encode. Each analyzer and linker invents its label in isolation.

The 2026-04-30 Adjacent Concept Sweep (the periodic audit-playbook §5 sweep) flagged this field as a confirmed leak. The follow-on deep audit at `WI-dumiz-bikul` returned an inventory of **192 distinct `Symbol.kind` values** in production code (lowercase identifier-shaped string literals at `kind=` sites under `packages/`, excluding tests) and clustered them into eight groups along three orthogonal axes that are silently sharing one field. The audit's verdict was DEPRECATE: full ADR-0023-shape migration warranted.

This ADR is the next-stage deliverable. It instantiates [ADR-0024](0024-axis-declaration-template.md)'s four-part axis-declaration template for `Symbol.kind` and lays out the migration shape; per-cluster verdicts (CANONICAL / FOLD / DEPRECATE-NO-FOLD per ADR-0024 §"Family-audit verdict methodology") are deferred to audit-findings documents at `docs/audits/<NN>-<topic>.md` per the convention documented in `docs/audits/README.md`.

### The three axes that are being conflated

Cluster taxonomy from the WI-dumiz-bikul audit (192 values; sub-counts approximate):

- **Cluster 27A — Pure language constructs (~30 values):** `function`, `class`, `method`, `struct`, `interface`, `enum`, `module`, `variable`, `property`, `constructor`, `namespace`, `attribute`, `field`, `trait`, `type`, `type_alias`, `alias`, `macro`, `fn`, `var`, `proc`, `procedure`, `subroutine`, `getter`, `setter`, `mixin`, `record`, `union`, `typedef`, `simple_type`, `defined_type`, `abstract`, `const`, `constant`, `instance`. These are the canonical seed: each value names a source-language syntactic construct and reads cleanly across analyzers.

- **Cluster 27B — File-shape entities (~12 values):** `file`, `library`, `dependency`, `package`, `executable`, `program`, `project`, `module_file`, `component_file`, `npm_package`, `composer_package`, `main_entry`, `bin`, `binary`, `library_export`, `export_entry`, `wasm_module`, `tsconfig`. These name *files or build-system artifacts*, which is arguably a separate axis (a Symbol-as-file is structurally different from a Symbol-as-construct).

- **Cluster 27C — Apex/peer overloads (~6 distinguishing pairs):** `function` vs `fn` (apex/peer); `variable` vs `var`; `procedure` vs `proc`; `struct` vs `structure`; `call` as apex of every `*_call` peer (see Cluster 27E). The same string sometimes plays "top type" and sometimes "language-specific short alias" — a pure naming-discipline failure that ADR-0024 §"Fold-residue discipline" rule 3's recurrence-promotion threshold catches.

- **Cluster 27D — Framework roles / dispatch participation (~25 values):** `event_publisher`, `event_subscriber`, `ipc_publisher`, `ipc_caller`, `ipc_bridge_caller`, `objc_bridge`, `crypto_producer`, `crypto_consumer`, `message_sender`, `message_handler`, `mq_publisher`, `grpc_service`, `grpc_servicer`, `grpc_stub`, `grpc_server`, `websocket_endpoint`, `dispatcher`, `graphql_resolver`, `http_client`, `graphql_client`, `route_mount`, `route`, `route_include`, `openapi_operation`, `abi_call`, `selector_ref`. These values encode the symbol's **participation in a framework pattern**, not what the symbol *is*. This is the same axis confusion ADR-0023 caught for `Edge.edge_type` (cf. its dispatch / publish / IPC / bridge family deprecations).

- **Cluster 27E — Edge labels masquerading as Symbol kinds (~10 values):** `call`, `function_call`, `subprocess_call`, `db_query`, `abi_call`, `import`, `inherit`, `reference`, `write`, `read`, `include`, `extends`, `route_mount`. These name *relationships*, not symbol categories. A `Symbol.kind == "call"` site is structurally different from an `Edge` whose `edge_type == "calls"`; putting both vocabularies in the same field forces consumers to guess which question they're asking.

- **Cluster 27F — Component / UI references (~6 values):** `component`, `component_ref`, `component_file`, `slot`, `prop`, `view`. Overlaps with the `imports_component` value ADR-0023 already deprecated for `Edge.edge_type`; `kind=component_ref` is dst-kind leakage of the same shape on the symbol side.

- **Cluster 27G — Build / config-shape (~10 values):** `test`, `work_item`, `target`, `recipe`, `env_var`, `build_arg`, `exposed_port`, `stage`, `requirement`, `editable`, `url_requirement`, `setting`, `config`, `derivation`. Arguably a separate axis (build-system or config-system entities, not language constructs).

- **Cluster 27H — Domain-specific long tail (~70+ values):** `section`, `paragraph`, `code_block`, `diagram`, `plot`, `yield`, `for_loop`, `conditional`, `declaration`, `prefix`, `base`, `query`, `entry`, `task`, `python_task`, `addtask`, `entity`, `architecture`, `error_set`, `participant`, `state`, `model`, `fragment`, `partial`, `provider`, `local`, `style_block`, `permission`, `keyframes`, `media`, `font_face`, `class_selector`, `id_selector`, `filter`, `workflow`, `subdirectory`, `workspace`, `table_array`, `link`, `pattern_rule`, `special_target`, `table`, `label`, `command`, `environment`, `binding`, `varying`, `id`, `storage`, `private`, `workgroup`, `source`, `port`, `uniform`, `output`, `input`, `value`, `pattern`, `handler`, `subscript`, `signal`, … The mixture includes domain-vocabulary nouns (graph / DB / template / config / shader), control-flow constructs (`for_loop`, `conditional`), and per-language synthetic kinds. A per-value audit will sort these (see Migration §3 below); the four leakage tests below catch the family-level structure.

### Four leakage tests (cluster-level)

The Fundamental Concept Audit playbook's four leakage tests fire as follows on the cluster taxonomy. Each test that fires is a deprecation candidate for at least the values in the named cluster.

- **Test 1 (Property derivability):** Cluster 27D values are uniformly derivable from `(edge_type, dst.kind)` queries per ADR-0023's pattern. `event_publisher` = a symbol with an outgoing `event_publishes` edge; `graphql_resolver` = a method on a class with `dst.kind == "graphql_schema"`; `route_mount` = a symbol with an outgoing `dispatches_to` edge with `meta["dispatch_kind"] == "route"`. **Massive leakage in Cluster 27D.**

- **Test 2 (Apex/peer overloading):** Cluster 27C explicitly. `function`/`fn`, `variable`/`var`, `procedure`/`proc`, `struct`/`structure` — same source-language construct under multiple labels because language analyzers each chose their own. The detector must consume the union, the consumer must enumerate it, and the registry never converges. **Fires.**

- **Test 3 (Construct vs relationship):** Cluster 27E values are *relationships*, not symbol categories. `call` is what an `Edge` does; `Symbol.kind == "call"` either means "this is a call site" (which deserves its own canonical name like `call_site`, distinct from the edge's `calls`) or is a duplicate-of-Edge that should be eliminated at the producer. **Fires.**

- **Test 4 (Mechanism vs category):** Cluster 27D again — `*_publisher` / `*_subscriber` / `*_caller` / `*_consumer` suffixes encode mechanism (HOW the symbol participates in a pattern), not category (WHAT it is). Already flagged by ADR-0023 Open Question #6 as the natural follow-up to `Edge.edge_type`'s Cluster-27D deprecation. **Fires.**

### Concrete cost

The lack of a typing principle for `Symbol.kind` is the same shape of bug ADR-0023 documents for `Edge.edge_type`. Hardcoded `kind` enumerations in consumers (centrality weight tables, slice filters, sketch section selection) will silently drift in both directions: missing emitted values (e.g., a slice filter that lists `"function", "method"` doesn't catch `"fn"` or `"proc"`) AND including never-emitted values (e.g., a weight table that names `"graphql_resolver"` after that producer has been Phase-3-renamed). Without a registry + drift linter, every consumer carries its own private enumeration.

This is the structural cost the cadence-hook audit playbook §5 anticipates. Catching it at 192 values is significantly cheaper than catching it at 250+; the ADR-0023 lesson is that the migration cost scales linearly with vocabulary size.

## Decision

Adopt the following typing rule for the `Symbol.kind` field:

> **`Symbol.kind` names the source-language syntactic construct that the symbol represents. Properties of the symbol's participation in framework patterns, dispatch protocols, or roles in larger architectural structures go in `Symbol.meta` (or a dedicated `Symbol` field, when first-class enough to deserve one). Edge-shaped relationships go on the `Edge`, not the `Symbol`.**

Three operational corollaries:

1. **No new `kind` value may encode information already on an `Edge` or in framework metadata.** If a proposed new kind would only differ from an existing one based on what edges the symbol participates in or what framework context surrounds it, the right answer is to reuse the existing kind and let consumers query the edges or `meta["framework_role"]`.

2. **Mechanism / role / framework-participation metadata goes in `Symbol.meta`, not in a new `kind`.** ADR-0023's resolution for `Edge.edge_type` (Cluster 27D framework-roles fold to `meta["framework_dispatch"]` and `meta["channel_kind"]`) generalizes to `Symbol`: `kind="event_publisher"` becomes `kind="function"` (or `"method"`) plus `meta["framework_role"]="event_publisher"`.

3. **Deprecate the existing leaky kinds** in a controlled migration (see "Migration" below). The deprecation set is enumerated as a *first cut* below; per-cluster verdicts are deferred to audit-findings documents per ADR-0024 §"Family-audit verdict methodology".

Following [ADR-0024](0024-axis-declaration-template.md)'s four-part template:

### 1. Axis name

The **symbol-kind axis** (with sections `language_construct`, `endpoint_shape` for deprecation candidates, and `pending_classification` for the long-tail audit). The axis name appears in:

- Module-level constants in the registry (`AXIS_LANGUAGE_CONSTRUCT`, `AXIS_ENDPOINT_SHAPE`, `AXIS_PENDING`).
- The by-axis view's section headings (`docs/concept-axes.md`, extending `scripts/generate-concept-axes`'s `_SECTIONS` table).
- This ADR's title and first paragraph.

The section vocabulary deliberately reuses ADR-0023's `endpoint_shape` / `pending_classification` names, even though the leakage shape on `Symbol` is "edge-property leakage" rather than "endpoint leakage" in the strict sense. The names compose across axes; the reuse is a feature, not an accident.

### 2. Axiom

> **`Symbol.kind` names the source-language syntactic construct that the symbol represents. Properties derivable from edges or framework metadata are queried from those structures rather than smuggled into the kind label.**

Properties of this axiom:

- **Falsifiable** — given a candidate value, the axiom either accepts it or rejects it. `event_publisher` is rejected (the `_publisher` suffix encodes participation in a publish pattern; the symbol *itself* is a function or method). `function` is accepted (a syntactic construct named directly).
- **One sentence long.**
- **Distinguishes the canonical section from the rest** — Cluster 27A members are `language_construct`; Cluster 27D / 27E members are `endpoint_shape` deprecation candidates; Clusters 27B / 27G / 27H pieces are `pending_classification` until the per-value audit settles them.

### 3. Consumer pattern

The accessor function shape that consumers use to query the axis:

```python
from hypergumbo_core.symbol_kinds import (
    AXIS_LANGUAGE_CONSTRUCT, all_symbol_kind_names, symbol_kinds_on_axis,
)

# Instead of: _CALLABLE_KINDS = {"function", "method", "fn", "proc"}
canonical_callables = symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)
```

Consumers needing "all language-construct-shaped kinds" call `symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)` instead of maintaining a local set. The accessor signature mirrors `edge_types_on_axis` exactly:

- `symbol_kinds_on_axis(axis: str) -> tuple[SymbolKindSpec, ...]` — canonical form.
- Values returned are typed objects (`SymbolKindSpec` carrying name + axis + description), not raw strings.
- The accessor is colocated with the registry module at `packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py`.

Consumers that need to combine `kind` with edge or meta context (e.g., "all functions that participate in a publish pattern") query the composed shape:

```python
def is_event_publisher(symbol: Symbol, edges_from: list[Edge]) -> bool:
    return (
        symbol.kind in symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)
        and any(e.edge_type == "event_publishes" for e in edges_from)
    )
```

This is the (kind, edge_type, meta) shape ADR-0023 §6 Phase 2 introduced. Consumer migration in Phase 2 below replaces hardcoded kind sets with this query pattern.

### 4. Enforcement

Two-layered, mirroring ADR-0023:

- **Static (in-tree).** The shared AST walker `hypergumbo_core.axis_drift.find_drift` (already parameterized per `WI-pilam-jukus`) scans the package source tree for module-level sets whose target name contains a configurable substring filter. The `Symbol.kind` instantiation uses `name_filter="SYMBOL_KIND"` plus the new `SYMBOL_KINDS` registry. The instantiation surfaces in two places: a property test in `test_symbol_kinds.py` (CI gate) and a CLI script `scripts/check-symbol-kind-drift` (pre-commit gate). The wrapper is a one-liner per ADR-0024 §4:

  ```python
  from hypergumbo_core.axis_drift import find_drift
  from hypergumbo_core.symbol_kinds import all_symbol_kind_names

  def find_symbol_kind_drift(repo_root: Path) -> list[str]:
      return find_drift(
          repo_root,
          name_filter="SYMBOL_KIND",
          registry_names=all_symbol_kind_names(),
      )
  ```

- **Runtime corpus-based (planned).** A coverage check analogous to ADR-0023's runtime invariant: partition emitted symbols by `(language, file_extension, parent_kind)` and assert that within each partition, `kind` is constant up to a short allow-list. The implementation reuses the `runtime_coherence` harness from ADR-0023 §3 once Phase 1 lands. Allow-list growth requires a corresponding ADR amendment.

For the static check, the existing `axis_drift.find_drift` surface already supports the parameterization; this ADR adds no new helper, only the registry and the wrapper.

## Detailed analysis: per-cluster fold targets

Per ADR-0024 §"Family-audit verdict methodology" + the audit-findings filing convention recorded in this ADR's tracker discussion (WI-dahim, 2026-05-02), the per-value CANONICAL / FOLD / DEPRECATE-NO-FOLD verdicts with structured `diagnostic_test` and `rationale` are filed as audit-findings documents at `docs/audits/<NN>-<topic>.md`, NOT as embedded verdict tables in this ADR.

The high-level cluster taxonomy and fold targets are this section's responsibility:

- **Cluster 27A (~30 values):** Canonical seed. All values keep their existing form on the `language_construct` section. Per-value rationale (none expected to deprecate; some apex/peer overloads in Cluster 27C will be folded to a single canonical form per language family) goes into the cluster-27A audit-findings doc.

- **Cluster 27B (~12 values; arguably a separate axis):** File-shape entities are NOT language constructs in the same sense as Cluster 27A. Two options for resolution: (a) park them on `pending_classification` and file a follow-on ADR declaring a `Symbol.shape` or `Symbol.role` axis for file-shape entities; (b) fold them into Cluster 27A by treating `file` as a language construct in its own right (the file is a syntactic unit at the document level). The fold-residue discipline (ADR-0024 §"Fold-residue discipline" rule 2) suggests option (a) — a property describing a different axis routes to that axis, not into `meta`. Decision deferred to the cluster-27B audit-findings doc.

- **Cluster 27C (~6 distinguishing pairs):** Apex/peer overloads collapse to a single canonical per pair. `fn` → `function`, `var` → `variable`, `proc` → `procedure`, `structure` → `struct`. The choice of apex follows ADR-0023's pattern (most-frequent emitting language wins). This is Phase 3 producer migration; consumer-side enumerations don't need to change because the apex was already in the registry.

- **Cluster 27D (~25 values):** Fold to Cluster 27A canonical + `meta["framework_role"]`. Worked example: `event_publisher` → `kind="function"` (or `"method"`, depending on the symbol's actual construct) + `meta["framework_role"]="event_publisher"`. The 25 values map to ~5 distinct meta key/value pairs after deduplication; exact mapping in the cluster-27D audit-findings doc.

- **Cluster 27E (~10 values):** Two sub-cases. (a) Values that name *a call site or use site* (e.g., `call`, `function_call`, `subprocess_call`, `db_query`, `abi_call`) reclassify as `call_site` — a new canonical kind distinct from the edge's `calls`. The producer emits a Symbol-of-kind-`call_site` plus an Edge-of-edge_type-`calls`; the dual representation lets consumers query "list all call sites in this file" without having to traverse edges. (b) Values that *duplicate an edge* (`import`, `inherit`, `reference`, `write`, `read`, `include`, `extends`) drop entirely at the producer — the relationship was already captured by the corresponding `Edge`. Per-value sub-case assignment in the cluster-27E audit-findings doc.

- **Cluster 27F (~6 values):** `component_ref` is the same dst-kind leakage shape ADR-0023 caught for `imports_component`. Fold: `component_ref` → `reference` (the syntactic construct; an inline component-name token is a reference to a name) + `dst.kind == "component"`. `component_file` is Cluster 27B (file-shape). `slot` / `prop` may be language-construct-genuine for Vue / Svelte / Astro and stay on `language_construct`. Per-value assignment in the cluster-27F audit-findings doc.

- **Cluster 27G (~10 values):** Build / config-shape. Almost certainly a separate axis (a Makefile target is structurally different from a Python function). Park on `pending_classification`; cluster-27G audit-findings doc decides between (a) declare a `Symbol.role` or `Symbol.config_shape` sibling axis vs. (b) demote these entirely (e.g., a Makefile target may be better represented as a SymbolID with no language-level kind, depending on whether downstream consumers ever query it as a language construct).

- **Cluster 27H (~70+ values):** Per-value audit. The cluster-27H audit-findings doc is expected to be the longest of the series; many values are domain-vocabulary nouns that read fine as `language_construct` (`code_block`, `paragraph`, `diagram` for documentation languages) but a non-trivial subset are leakage of the Cluster-27D / -27E shape (`handler`, `signal`, `model`, `entity`).

Estimated emit-site distribution: Cluster 27D has ~25 values across ~15-20 producer linkers; Cluster 27E has ~10 values more diffusely scattered; Cluster 27H is per-value. Total Phase 3 producer edits ~50-70 across ~30-40 files, comparable to ADR-0023 Phase 3 scope.

## Migration

Mirrors ADR-0023's four-phase shape; consumers can keep working throughout. JSON output stability is treated as additive deprecation in Phase 1, hard rename in Phase 4.

### Phase 1 — registry + drift linter

Mirrors ADR-0023 Phase 1. Land:

1. The registry module at `packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py` with `AXIS_*` constants, `SymbolKindSpec` frozen dataclass, the `SYMBOL_KINDS` tuple-of-specs (seeded with Cluster 27A on `language_construct`, Clusters 27D / 27E on `endpoint_shape`, Clusters 27B / 27G / 27H on `pending_classification`), and the accessors (`all_symbol_kind_names()`, `symbol_kinds_on_axis(axis)`, `find_symbol_kind(name)`).
2. The drift-coherence linter at `scripts/check-symbol-kind-drift`, wiring into `.githooks/pre-commit` skipping when no `packages/*.py` files are staged.
3. The property test in `tests/test_symbol_kinds.py`: registry invariants (no duplicates, every spec has a valid axis, every axis section is non-empty) plus a drift-detection test calling the linter's underlying function on the live tree.
4. By-axis view extension: add the `Symbol.kind` axis's three sections to `scripts/generate-concept-axes`'s `_SECTIONS` table; regenerate `docs/concept-axes.md`.
5. JSON Schema integration: extend `scripts/generate-schema` to emit `x-axis-of-values` for the `Symbol.kind` enum (analogous to the existing `Edge.edge_type` integration).

   > **Schema-impact note (added 2026-05-05).** Phase 1 as originally shipped (PR #3546) emitted a *closed* JSON Schema enum derived from `sorted(spec.name for spec in SYMBOL_KINDS)`, with the closed-enum-with-known-gap caveat that dynamic `kind=f"ipc_{...}"` emit sites at `linkers/ipc.py` and `linkers/phoenix_ipc.py` produced runtime values outside the registry. ADR-0028 Phase 1 (PR #3549, commit `711e273d1`) retroactively *re-opened* this enum to `type: "string"` + `x-axis-of-values` annotation, mirroring the open-enum posture chosen for `Edge.evidence_type` for the same dynamic-emit reason. The closed enum returns at Phase 4b once the per-cluster producer migrations land. `SCHEMA_VERSION` patch-bumped 0.4.1 → 0.4.2 alongside ADR-0028's Phase 1 land. The four artifacts listed above (registry, drift linter, property test, by-axis view) are unaffected by the re-open; only the schema enum constraint relaxed.

Estimated effort: ~1-2 days. No producer or consumer changes ship in this phase; the registry is a pure addition.

### Phase 2 — migrate consumers

Update consumers that maintain hardcoded `kind` sets to query `symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)` or the composed `(kind, edge_type, meta)` shape. Known consumer surfaces (audit during Phase 1):

- Centrality weight tables in `packages/hypergumbo-core/src/hypergumbo_core/ranking.py` — likely an `_NODE_KIND_WEIGHTS` table with a frozen subset of kinds.
- Slice filters in `packages/hypergumbo-core/src/hypergumbo_core/slice.py` — `--exclude-types`, `--only-callable` and similar surface that filter by kind.
- Sketch section selection in `packages/hypergumbo-core/src/hypergumbo_core/sketch.py` — section budgets per kind.
- The fundamental-concept audit's existing inventory at `~/hypergumbo_lab_notebook/concept-audit-sweep_2026-04-30.md` lists additional consumer sites surfaced by the audit walk.

Each consumer migration is reversible: the old `kind` semantics still produce the right answer with the new query shape. This phase can land before producer migration begins.

Estimated effort: ~1-2 days, comparable to ADR-0023 Phase 2.

### Phase 3 — unify producers

Sweep the producer sites that emit deprecation-list `kind` values. For each:

1. Replace the specialized `kind` with the canonical (Cluster 27A) equivalent.
2. If the specialization carried a fact not derivable from edges or already-existing meta, move it to `Symbol.meta` (e.g., `meta["framework_role"]`).
3. Re-run the drift property test; the offender set strictly shrinks each migration.

Producer migration order, lowest-risk-first:

1. **Cluster 27C** (apex/peer collapse): purely additive on consumer side; canonical was already in the registry.
2. **Cluster 27D** (framework roles → `meta["framework_role"]`): largest single subset (~25 values across ~15-20 producer linkers); each producer subset can ship as its own PR with its own `awaits_bakeoff_validation` tag.
3. **Cluster 27E** (edge-label kinds): per-value sub-case (call_site reclassification vs producer drop) per the cluster-27E audit-findings doc.
4. **Cluster 27F** (component refs): aligns with the imports_component fold ADR-0023 already shipped; small subset.
5. **Clusters 27B / 27G / 27H** (pending_classification long tail): per-cluster audit-findings doc resolution before Phase 3 ships for that cluster.

Estimated effort: ~5-10 days plus bakeoff validation per phase, comparable to ADR-0023 Phase 3.

### Phase 4 — schema bump and deprecation removal

Mirrors ADR-0023 Phase 4's split shape:

- **Phase 4a (additive).** Each `endpoint_shape` value gets an `x-deprecated` annotation in `docs/schema.json`. Values stay valid in the enum during the deprecation window. `SCHEMA_VERSION` minor bump marks the announcement. Per-value migration guidance lives in the registry's `SymbolKindSpec.description`.

- **Phase 4b (hard-fail).** Remove deprecated values from `SYMBOL_KINDS` once the corresponding Phase 3 producer migration's `awaits_bakeoff_validation` tag clears. Schema enum values disappear for removed entries. Drift property test treats any remaining consumer-side reference as a regression. Second `SCHEMA_VERSION` minor bump.

Phase 4b prerequisites match ADR-0023's: bakeoff validation must clear for the corresponding Phase 3 subset before its 4b ships. The dual-validity window is the safety net for any centrality / slice / sketch regression the migration introduced.

Estimated effort: ~1-2 days for the additive 4a; per-cluster 4b ships piecewise as Phase 3 subsets validate.

### Total

~7-15 days of focused work, comparable to ADR-0023 (which came in around the lower end of its ~2-week estimate). Migration can interleave with feature work; only Phase 1 needs to land before other linker changes to prevent further accumulation.

## Consequences

### Positive

- **Cuts the `Symbol.kind` vocabulary** from 192 to a manageable canonical set (~30 in Cluster 27A plus the cluster-27H survivors after audit), making the IR easier to reason about.
- **Eliminates a structural class of silent bugs**: the same hardcoded-set drift pattern ADR-0023 caught for `Edge.edge_type` was poised to recur as more frameworks get analyzers (any new framework was likely to add a `kind="<framework>_handler"` value, fragmenting the vocabulary further). The drift linter blocks the recurrence.
- **Reuses ADR-0024's tooling**: registry shape, `axis_drift.find_drift`, by-axis view aggregator, JSON Schema extension all transfer one-for-one. The ADR-0027 implementation is mostly fill-in-the-template plus per-cluster verdict work.
- **Aligns Symbol with Edge**: ADR-0023's lesson — "the type field names the relationship/construct, properties of the participants are queried from the participants" — generalizes to symbols. Both core dataclasses now follow the same discipline.
- **Surfaces the file-shape question** (Cluster 27B) and the build/config-shape question (Cluster 27G) as concrete follow-on work rather than ambient confusion.

### Negative

- **JSON output changes**, breaking external consumers that filter by deprecated kind strings. Mitigated by the additive Phase 4a deprecation window.
- **Bakeoff revalidation cost**: any centrality / slice / sketch change driven by kind reclassification needs `awaits_bakeoff_validation` discipline.
- **Schema version bumps**: standard cost, but real (twice — Phase 4a and 4b).
- **Migration adds ~7-15 days** of work that is not directly user-facing.

### Risks

- **Cluster 27H is the long tail.** ~70+ values across many domain languages (CSS, SQL, Makefile, build configs, documentation, shaders, …). The per-value audit will be tedious and may surface several additional sub-axes (e.g., a CSS-specific axis distinct from a SQL-specific axis). Mitigation: file each cluster's audit-findings doc as a separate work item; don't try to settle Cluster 27H in a single pass.
- **File-shape question (Cluster 27B) may need its own ADR.** If the per-cluster audit decides Cluster 27B values are a separate axis (a `Symbol.shape` or `Symbol.entity_kind` axis), that axis declaration is itself a follow-on ADR per ADR-0024's seven-step workflow. This is anticipated, not regrettable.
- **Producer migration is wider-spread than ADR-0023's.** `Edge.edge_type` was concentrated in linker code; `Symbol.kind` is emitted by every language analyzer and several linkers. Phase 3 will touch more files than ADR-0023's did. Mitigation: per-cluster sub-PRs let migration interleave with feature work; the per-cluster `awaits_bakeoff_validation` tag isolates regressions to single clusters.
- **`Symbol.meta` doesn't yet have an established schema for `framework_role` keys.** The Cluster 27D fold target depends on `meta["framework_role"]` being a recognized convention. If this doesn't exist today, Phase 1 implicitly establishes it (with the recurrence-promotion threshold per ADR-0024 §"Fold-residue discipline" rule 3 watching the meta key for promotion to a dedicated field).

## Alternatives considered

### A. Status quo — no axis declaration

Keep `Symbol.kind` free-form, accept the proliferation. Cost: silent bugs ship; review burden grows; new analyzers reintroduce the pattern; the Adjacent Concept Sweep cycle re-flags this field every quarter without resolution. Rejected: the audit verdict was DEPRECATE.

### B. Multi-axis structured kind (`(construct, role, framework?)`)

Replace `Symbol.kind: str` with a tuple or structured object encoding multiple axes simultaneously. More expressive, but heavier migration cost and over-engineering relative to the actual problem — most leakage cleanly resolves to "use the canonical construct and query the meta or edges." Same trade-off ADR-0023 §"Alternatives B" rejected for `Edge.edge_type`. Rejected as over-design.

### C. ADR-0023-shape relabel (this ADR)

Define the principle, deprecate the leakage, enforce via property test plus drift linter. Lowest migration cost, smallest cognitive surface, aligns with ADR-0024's template. **Recommended.**

### D. Defer until ADR-0028 (evidence_type) lands

Both axes were flagged by the same Adjacent Concept Sweep; both have DEPRECATE verdicts; bundling them might amortize the migration work. Rejected: the two axes are independent, the migration paths don't share producer files, and bundling would produce a single ADR that's harder to amend without touching unrelated material (the same reason ADR-0024 §"Alternatives B" rejected one mega-ADR per host dataclass).

## Open questions

1. **Cluster 27B (file-shape) resolution.** Is `file` a language construct, a separate axis, or something else? Decision deferred to the cluster-27B audit-findings doc; this ADR parks Cluster 27B values on `pending_classification`.

2. **Cluster 27G (build/config) resolution.** Same shape as Cluster 27B but for build-system / config-system entities. Likely a separate `Symbol.role` or `Symbol.config_shape` axis. Decision deferred to the cluster-27G audit-findings doc.

3. **Apex selection in Cluster 27C.** When two language-specific labels coexist (`fn` vs `function`, `proc` vs `procedure`), which is the apex? The ADR-0023 heuristic was "most-frequent emitter wins"; the ADR-0027 default is the same. Per-pair confirmation in the cluster-27C audit-findings doc.

4. **Coordination with `Symbol.meta` schema.** The Cluster 27D fold target depends on `meta["framework_role"]` becoming a recognized convention. Should there be a parallel registry of meta keys (an `axis_meta_keys.py`)? Probably yes once the recurrence-promotion threshold per ADR-0024 §"Fold-residue discipline" rule 3 fires for the second time (the first being whatever this ADR introduces). Defer to a follow-on tracker item.

5. **Coordination with ADR-0028 (evidence_type axis).** Both ADRs depend on `axis_drift.find_drift` and the audit-findings filing convention; both will land registries that share the JSON Schema extension. Phasing: Phase 1 of either can land independently; Phase 3 producer migrations may benefit from sequencing (do the cluster with the smallest blast radius first across both axes). Tracked at the parent items WI-dahim and WI-pilit; the work-item chain for each axis's migration phases is filed once Phase 1 lands.

## Related

- **Surfaced by**: WI-dumiz-bikul-pitaf-gutiv-nudig-vovam-sinad-vogaj — the deep audit whose verdict produced this ADR.
- **Generalized template**: [ADR-0024](0024-axis-declaration-template.md) — the four-part axis-declaration template instantiated here.
- **Worked example**: [ADR-0023](0023-edge-type-relationship-not-endpoints.md) — the four-phase migration shape mirrored here.
- **Pattern precedent**: ADR-0015 (Dataflow Access Modes) — `meta` field for relationship metadata rather than `edge_type` proliferation; the same precedent for `Symbol.meta["framework_role"]` here.
- **Sibling axis ADR**: ADR-0028 (Edge.evidence_type axis declaration) — independent axis surfaced by the same Adjacent Concept Sweep; can ship in parallel.
- **Audit playbook**: [Fundamental Concept Audit](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md) — Step 6 *Document* outcome cites this ADR as the next-stage deliverable for the Symbol.kind sweep finding.
- **Audit-findings convention**: [`docs/audits/README.md`](../audits/README.md) — per-cluster verdict outputs file there, not embedded in this ADR; bucket boundary at [`docs/adr/README.md`](README.md).
- **Future tracker items**: After this ADR lands, file separate items for each migration phase (registry + linter, consumer migration, producer migration per cluster, schema bump). Same chain pattern as ADR-0023's WI-sahab-fatoz / WI-mokam-jalig / WI-vomoj-suhaz.
