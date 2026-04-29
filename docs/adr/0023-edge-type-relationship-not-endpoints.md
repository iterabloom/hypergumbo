<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0023: Edge Type Names the Relationship, Not the Endpoints

Date: 2026-04-29
Status: Draft

## Context

Hypergumbo's `Edge` dataclass (`packages/hypergumbo-core/src/hypergumbo_core/ir.py:336`)
defines `edge_type` as a free-form `str`, with the docstring "Type of relationship
(calls, imports, inherits, etc.)". There is no enum, no canonical vocabulary, no
governing principle, and no ADR that constrains what an `edge_type` value should
encode. Each analyzer and linker makes its own labeling decision in isolation.

A grep over the producers in `packages/` finds **~80 distinct `edge_type` values
in active emission**. The high-frequency core (`calls`, `imports`, `references`,
`instantiates`, `dispatches_to`, `decorated_by`, `inherits`, `contains`,
`module_attr_ref`) is healthy — these name distinct syntactic constructs and
read cleanly. The long tail is where this ADR lives.

### The proliferation pattern

Many of the long-tail edge types encode information that is **already on the
endpoints** of the edge. They differ from the canonical core not by naming a
different relationship, but by relabeling the relationship based on what the
src or dst happens to be. Four representative families:

#### "Imports" zoo — same construct, different dst kind

| Edge type | Producer | Same syntactic construct? |
|---|---|---|
| `imports` | Python (`py.py:_extract_import_edges`), most languages | Yes — import statement |
| `imports_module` | `linkers/js_module.py:772, 827` | Yes — JS module import |
| `imports_component` | `linkers/vue_component.py:235`, `lang-common/svelte.py:264`, `lang-common/astro.py:335`, `lang-common/vue.py:584` | Yes — component framework import |

All three are "this file has an import statement that brings in a name from
elsewhere." The label varies by what the dst happens to be (a JS module file vs.
a Vue/Svelte component file). That information is already present as `dst.kind`.

#### "References" zoo — same relationship, different dst kind

| Edge type | When emitted | Endpoint property that determines this |
|---|---|---|
| `references` | Generic "X uses Y" | (default) |
| `query_references` | Y is a SQL query | `dst.kind == "query"` |
| `model_reference` | Y is an ORM model | `dst.kind == "model"` |
| `type_ref` | Y is a type | `dst.kind == "type"` |

Pure dst-kind leakage. A consumer asking "what does X reference?" must learn the
full set; a consumer asking "what queries does X use?" can already answer that
from the dst's `kind` field.

#### "References" is also overloaded as both apex and peer

`packages/hypergumbo-core/src/hypergumbo_core/scip/calls.py:48` documents:

> `edge_type="references"` uniformly. SCIP's SymbolRole bitfield (ReadAccess /
> WriteAccess / Import / Generated / Test / Definition / ForwardDefinition) is
> preserved in `meta["symbol_roles"]` so a downstream specialization pass can
> refine to "calls", "writes_to", or "imports" when it has target-kind context.

So the SCIP integration treats `references` as the **generic top type**
(specializable to calls / imports / writes via metadata). Meanwhile, twelve
language analyzers (`java.py`, `kotlin.py`, `scala.py`, `swift.py`, `csharp.py`,
`ruby.py`, `py.py`, `sql.py`, `latex.py`, `rst.py`, `json_config.py`, `tlaplus.py`)
emit `references` as **a specific edge type for their own per-construct
semantics**. And `query_references` / `model_reference` are framed as **subtypes
of references**. The same string is simultaneously apex, peer, and parent —
querying it requires examining `evidence_type` and `meta` to disambiguate.

#### Bridge / FFI zoo — same relationship, different language pair

`ffi_bridge`, `cgo_bridge`, `napi_bridge`, `wasm_bridge`, `native_bridge`,
`bridge_invokes`, `caller_invokes` — seven edge types for "this code invokes
across a language boundary." The distinction is the language pair (which is
on `src.language` and `dst.language`) plus optionally the bridge mechanism (cgo
vs. N-API vs. ctypes), which is genuine edge metadata but doesn't justify seven
edge types.

#### Publish / send / dispatch zoos

- **Publish family:** `event_publishes`, `crdt_publishes`, `annotated_publishes`,
  `message_send`, `enqueues` — five labels for "this code sends a message."
- **Dispatch family:** `dispatches_to`, `routes_to`, `delegates_to`,
  `message_dispatch`, `uses_dispatch_table` — five labels for indirect
  invocation. Some of these are genuinely different relationships; some are
  protocol-conditional labels of the same relationship. A case-by-case audit
  is required, but the smell is the same.

### The two axes that are actually being conflated

Across these families, the real structure is two-dimensional:

1. **The relationship that produced the edge** — the syntactic construct or
   semantic action: import statement, function call, attribute access, JSX
   render, decorator application. This is what `edge_type` should describe.

2. **The endpoints** — `src.kind`, `src.language`, `src.framework`, `dst.kind`,
   `dst.language`, `dst.framework`. These are properties of the nodes, already
   correctly populated, accessible by consumers via Symbol fields.

The proliferation pattern is information from axis 2 leaking into axis 1.
There is no genuine third axis; "this is a `cgo_bridge`" is fully derivable
from `src.language="go"` AND `dst.language="c"` AND (optionally)
`meta["bridge_kind"]="cgo"`.

### What IS genuinely edge metadata

To be precise about what's left over after stripping endpoint properties:
some facts about the relationship are not on either node and not encoded in
the construct name. These belong in `edge.meta` or dedicated edge fields,
**not** as new edge types:

| Fact | Already represented as |
|---|---|
| Line/span of the use site | `Edge.line` |
| Confidence of the inference | `Edge.confidence` |
| How the edge was inferred | `Edge.evidence_type` |
| Access mode (read / write / mutate / delete) | `meta["access_mode"]` (ADR-0015) |
| Channel / topic / shared-state name | `meta["channel"]` (ADR-0015) |
| Bridge mechanism for FFI (cgo vs napi vs ctypes) | should be `meta["bridge_kind"]` (currently a separate edge type — leakage) |
| Protocol for publish (event vs CRDT vs queue) | should be `meta["protocol"]` (currently a separate edge type — leakage) |

ADR-0015's treatment of `access_mode` is the working precedent: dataflow
direction lives in `meta` rather than in `edge_type`, and edges keep their
relationship label (`event_publishes`, `data_flows_to`) while the per-edge
direction-of-access is queried from `meta`.

### Concrete cost: silent bugs already in production

The lack of a typing principle has shipped bugs:

1. **`packages/hypergumbo-core/src/hypergumbo_core/ranking.py:1053`** —
   ```python
   _IMPORT_EDGE_TYPES = {"imports", "imports_module"}
   ```
   `imports_component` is missing. Vue, Svelte, Astro, and React imports are
   silently miscategorized in centrality ranking — they are weighted as generic
   edges instead of import edges. Whether this is intentional or oversight is
   undocumented.

2. **`packages/hypergumbo-core/src/hypergumbo_core/slice.py:640`** —
   ```python
   if query.exclude_imports and edge.edge_type in ("imports", "imports_module"):
   ```
   Same hardcoded set, same omission. `slice --exclude-imports` does not
   exclude framework-component imports.

3. **SCIP / native edge reconciliation.** The SCIP integration emits
   `references` as a generic supertype while native analyzers emit
   construct-specific types. The "downstream specialization pass" mentioned in
   `scip/calls.py:48` does not exist. The same logical edge can appear twice
   with different `edge_type` values depending on which analyzer found it.

The pattern of the bugs is structural: any consumer that filters by
`edge_type` must know the entire proliferation set, and inevitably gets
out of sync as new specialized types ship.

### Why this matters now

The proximate cause of this ADR is the WI-jagus-bufip Deliverable A debate:
should `from X import BAR` emit a `references` edge instead of an `imports` edge
when BAR is a variable? The local answer (no — `imports` is a syntactic fact;
`BAR.kind == "variable"` is on the node and queryable) is straightforward.
The systemic question is harder: the pattern Deliverable A would introduce
is **already shipped a dozen times** under other names (`query_references`,
`imports_component`, `cgo_bridge`, ...). A decision about Deliverable A in
isolation does not address the surrounding inconsistency, and accreting a
no-on-A precedent without a stated principle leaves the next analyzer free
to reintroduce the same shape under a different label.

The cost grows with every new linker added in the current idiom. Catching
the pattern at ~80 edge types is significantly cheaper than catching it at
~150.

## Decision

Adopt the following typing rule for the `Edge.edge_type` field:

> **`edge_type` names the relationship that produced the edge. Properties of
> either endpoint are queried from the endpoint. Other properties of the
> relationship go in `edge.meta` (or a dedicated `Edge` field, when
> first-class enough to deserve one).**

Three operational corollaries:

1. **No new `edge_type` value may encode information derivable from
   `src.*` or `dst.*`.** If a proposed new edge type would only differ from
   an existing one based on what kind / language / framework one of its
   endpoints has, the right answer is to reuse the existing type and let
   consumers query the endpoint.

2. **Mechanism / protocol / bridge-kind metadata goes in `edge.meta`, not
   in a new `edge_type`.** ADR-0015 established this pattern for
   `access_mode` and `channel`; this ADR generalizes the discipline.

3. **Deprecate the existing leaky edge types** in a controlled migration
   (see "Migration" below). The deprecation set is enumerated below as a
   *first cut* and will be refined during the property-test audit.

### Likely-deprecate list (first cut, to be confirmed by audit)

| Edge type | Replacement | Rationale |
|---|---|---|
| `query_references` | `references` (query via `dst.kind == "query"`) | Pure dst.kind leakage |
| `model_reference` | `references` (query via `dst.kind == "model"`) | Pure dst.kind leakage |
| `type_ref` | `references` (query via `dst.kind == "type"`) | Pure dst.kind leakage |
| `imports_component` | `imports` (query via `dst.kind == "component"`) | dst.kind leakage |
| `imports_module` | `imports` (query via `dst.kind == "module"` or `dst.kind == "file"`) | dst.kind leakage; also collides with Python's plain `imports` for the same relationship |
| `renders_component` | TBD — possibly `references` with `meta["construct"] = "jsx"` | Both src.framework and dst.kind leakage; needs case-by-case review |
| `cgo_bridge`, `napi_bridge`, `wasm_bridge`, `native_bridge`, `ffi_bridge`, `bridge_invokes`, `caller_invokes` | Single canonical `bridges_to` (or fold into `calls`), plus `meta["bridge_kind"]` for the mechanism | Language-pair derivable from endpoints; mechanism is meta |

The publish / dispatch zoos (`event_publishes`, `crdt_publishes`,
`message_dispatch`, `uses_dispatch_table`, etc.) are deferred to a per-family
audit during migration: some may be genuinely distinct relationships, others
are protocol-conditional labels.

### Edge types that stay (and why)

The following are NOT subject to deprecation under this rule, because each
names a distinct syntactic construct or semantic action that is not
derivable from endpoints alone:

`calls`, `imports`, `references`, `contains`, `instantiates`, `inherits`,
`implements`, `decorated_by`, `extends`, `module_attr_ref`,
`includes`, `defines_target`, `data_flows_to` (ADR-0015), and the
access-mode-annotated edges introduced by ADR-0015.

Notably `module_attr_ref` survives because `imported_module.X` is a
syntactically different construct from a bare-name `X` reference, and the
distinction is a property of the use site (axis 1), not the dst (axis 2).

### Property test (the enforcement mechanism)

Add a parameterized invariant test that auto-discovers new offenders:

```
test_edge_type_does_not_encode_endpoint_metadata:
  for each emitted edge in a corpus run:
    assert edge.edge_type is not derivable from
      (src.kind, src.language, src.framework,
       dst.kind, dst.language, dst.framework)
    by any pure function.
```

The test is implemented as a coverage check: running the analyzers on a
representative corpus, partition emitted edges by
`(src.kind, src.language, src.framework, dst.kind, dst.language, dst.framework)`
and assert that within each partition, `edge_type` is constant up to a
short allow-list. Allow-list growth requires a corresponding ADR amendment.

This makes the principle empirically enforceable rather than reliant on
reviewer vigilance.

## Migration

The migration is staged so consumers can keep working throughout. JSON
output stability is treated as an additive deprecation rather than a hard
rename in the first phase.

### Phase 1 — ADR + property test (1 day)

- Land this ADR (Status: Draft → Proposed → Accepted as discussion progresses).
- Add the property-test scaffold described above. Initially configured to
  print a warning rather than fail, so the existing offender set is visible
  but not blocking.
- Confirm or revise the deprecation list against the property test's actual
  output.

### Phase 2 — Migrate consumers (2-3 days)

Update the ~10 consumer files to query by `(edge_type, dst.kind)` rather
than by hardcoded edge-type sets. Known consumers:

- `packages/hypergumbo-core/src/hypergumbo_core/ranking.py` (`_IMPORT_EDGE_TYPES`,
  edge-type weight table around line 198).
- `packages/hypergumbo-core/src/hypergumbo_core/slice.py` (line 640
  `exclude_imports` set; possibly other edge-type filters elsewhere).
- `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
  (any edge-type-based section selection).
- `packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py` and
  `taint.py` (whitelisted edge-type sets — already use sets, just need audit).
- The SCIP merger (`packages/hypergumbo-core/src/hypergumbo_core/scip/`)
  — switch from "type-string match" to "merge by (src, dst, line) and
  consolidate `meta`."

Each consumer migration is a reversible refactor — the old edge-type
semantics still produce the right answer with the new query shape, so this
phase can land before producers change.

### Phase 3 — Unify producers (3-5 days)

Sweep the ~20-30 producer sites that emit deprecation-list edge types.
For each:

1. Replace the specialized `edge_type` with the canonical type.
2. If the specialization carried a fact not on the endpoints (e.g., bridge
   mechanism), move that fact into `edge.meta`.
3. Run the property test: it should now report a strictly smaller offender
   set with each migration.

Producer migration order: start with the dst-kind leakage cases (lowest
risk, purely additive on the consumer side), then bridge / FFI, then the
publish / dispatch families after their per-family audits.

### Phase 4 — Schema bump and deprecation removal (1-2 days)

- Bump `docs/schema.json` per ADR-0014's schema-change protocol. The
  deprecated edge-type strings remain valid for one minor version (so
  external consumers can adapt), then hard-fail in the version after.
- Remove dead code paths in producers and consumers.
- Bakeoff revalidation: any centrality-weight or sketch-section change
  needs an `awaits_bakeoff_validation` tag on the migration tracker
  items.

### Total

~2 weeks of focused work, ~3 weeks if unknowns surface. Migration can
interleave with feature work; only Phase 1 needs to land before other
linker changes to prevent further accumulation.

## Consequences

### Positive

- **Eliminates two known silent bugs** (`imports_component` missing from
  ranking and slice exclusion sets).
- **Cheaper SCIP / native edge reconciliation**: no more apex-vs-peer
  conflict on the `references` string; merging is by `(src, dst, line)`
  with `meta` consolidation.
- **Cheaper review of new linkers**: "does this `edge_type` encode anything
  on the endpoints?" is a one-line review rule.
- **Shrinks the edge-type vocabulary** from ~80 to a manageable canonical
  set (~15-20 names), making the IR easier to hold in working memory.
- **Aligns with property-graph design tradition**: edges describe
  relationships; nodes describe what they are. This lets external graph-DB
  exports produce clean schemas.

### Negative

- **JSON output changes**, breaking external consumers that filter by the
  deprecated edge-type strings. Mitigated by the additive-deprecation
  phase in Phase 4.
- **Bakeoff revalidation cost**: any ranking-weight or centrality-affecting
  change needs `awaits_bakeoff_validation` discipline.
- **Schema version bump**: standard cost, but real.
- **Migration adds ~2-3 weeks of work** that is not directly user-facing.

### Risks

- **Hidden coupling**: edge-type filtering may exist in places not yet
  identified (custom user analyzers, downstream consumers like
  `verify-claims`, third-party tooling). Mitigated by the Phase 2 audit
  and the property-test reporter.
- **Borderline cases create churn**: the publish / dispatch families
  require per-family audits, and the right answer is not always "deprecate
  all but one." This may produce minor revisits of this ADR.
- **Backsliding**: without the property test running in CI, future
  analyzers will reintroduce the same pattern under different names. The
  property test is the load-bearing piece, not the deprecation list.

## Open questions (Draft status)

The following are explicitly unresolved at draft time and should be
settled before this ADR moves to Proposed or Accepted.

1. **Exact canonical set of edge types.** The "Edge types that stay" list
   above is provisional. A full audit may surface candidates for further
   consolidation (e.g., `extends` vs. `inherits` — are these genuinely
   different in this IR?).

2. **Boundary between `edge_type` and `evidence_type`.** Today,
   `evidence_type` carries values like `ast_call_direct`,
   `scip_occurrence_ref`, `middleware_chain`, which are about the
   inference pathway. After this ADR, `evidence_type` is doing more work
   (it's the field that distinguishes "same relationship, different
   inference"). Does this need its own ADR? Probably yes.

3. **Schema bump strategy.** Patch (additive deprecation only, deprecated
   types remain valid) vs. minor (eventual hard removal). The migration
   plan assumes additive, but the time-to-removal window needs to be
   stated explicitly. Recommend: deprecated edge types are accepted for
   one minor version, removed in the next.

4. **Per-family audits**: the publish family
   (`event_publishes` / `crdt_publishes` / `annotated_publishes` /
   `message_send` / `enqueues`) and the dispatch family
   (`dispatches_to` / `routes_to` / `delegates_to` /
   `message_dispatch` / `uses_dispatch_table`) need case-by-case review.
   Some of these likely name genuinely distinct relationships and should
   stay; others are protocol leakage. The audit is deferred to Phase 3.

5. **Property-graph "label" concept.** Some graph databases distinguish
   `edge_type` (the relationship) from `edge_label` (a free-form tag).
   Worth considering whether hypergumbo wants a similar split, or whether
   `meta` is sufficient. Recommend: stick with `meta` unless a concrete
   use case forces the split.

6. **Symbol `kind` audit.** This ADR is scoped to edges, but the same
   conflation pattern likely affects `Symbol.kind` (see "patient is
   healthy" prognosis discussion). A follow-up ADR (separate scope) should
   apply the same lens to node-type vocabulary.

## Alternatives considered

### A. Status quo

Keep `edge_type` free-form, accept the proliferation. Cost: silent bugs
keep shipping; review burden grows; new analyzers reintroduce the pattern.
Rejected.

### B. Tuple-typed edges (`(construct, framework?, role?)`)

Replace `edge_type: str` with a structured tuple. More expressive, but
heavier migration cost and over-engineering relative to the actual
problem — most of the leakage cleanly resolves to "use the canonical
type and query the endpoint." Rejected as over-design.

### C. Property-graph relabel (this ADR)

Define the principle, deprecate the leakage, enforce via property test.
Lowest migration cost, smallest cognitive surface, aligns with existing
ADR-0015 metadata pattern. **Recommended.**

## Related

- **Surfaced by**: WI-jagus-bufip-mogah-fifom-dalug-sobip-hilom-rogoz
  (Deliverable A debate — `from X import BAR` edge labeling).
- **Pattern precedent**: ADR-0015 (Dataflow Access Modes) — `meta` field
  for relationship metadata rather than edge-type proliferation.
- **Schema mechanics**: ADR-0014 (Generalized Symbol Identity) — schema
  bump protocol applicable here.
- **Likely follow-up**: `Symbol.kind` taxonomy audit (separate ADR).
- **Bug references**: silent miscategorization at `ranking.py:1053`,
  `slice.py:640`; SCIP merger non-existence at `scip/calls.py:48`.
