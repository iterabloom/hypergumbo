<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0031: Symbol.language Reshape — discovery_language and protocol_origin Typed Fields

- Status: **DRAFT** (2026-05-30) — pending review
- Date: 2026-05-30
- Supersedes: —
- Superseded by: —
- Related: ADR-0014 (Generalized Symbol Identity — stable_id factories that hash `Symbol.language`), ADR-0023 (Edge Type Names the Relationship — the worked example for axis-declaration), ADR-0024 (Axis Declaration Template — the four-part template this ADR instantiates, plus §"Fold-residue discipline" promotion-gating rule applied here), ADR-0027 (Symbol.kind axis — the sibling axis on Symbol whose Phase 3 fold this ADR mirrors), ADR-0028 (Edge.evidence_type axis — another sibling using the canonical-value + meta pattern); tracker items WI-zadot (the dogfood finding this ADR closes), INV-tofun (closed by the 2026-05-30 objc/ansible PR), INV-numat (META: vocabulary fields mix axes — this ADR closes the language-axis expression), INV-kovob and INV-fogum (sister cluster members under INV-kurup — not addressed here; separate reshape work); lab-notebook companions `/tmp/symbol_audit_output.md` (the main audit), `~/hypergumbo_lab_notebook/audit-supplement-linker-language-provenance-05302026.md` (the supplement), `~/hypergumbo_lab_notebook/three-policies-pros-cons-05302026.md` (the per-policy analysis).

## Context

### How we got here

The `Symbol` dataclass (`ir.py:363-388`) declares `language: str` with the docstring "Programming language (python, javascript, etc.)." The implicit semantic: the source language in which this symbol was declared. The field is governed by the catalog-derived language axis per ADR-0024 §4 (lightweight axis carveout), with the catalog computed from `@register_analyzer` / `@register_linker` declarations.

When Symbol meant "thing declared in source code" — function, class, method, variable in a real .py / .go / .java file — this semantic was clean. Over time, Symbol's usage grew. Per ADR-0027's audit, the kind axis has 192 distinct values across 8 clusters representing file-shape entities (cluster 27B), framework roles (27D), build entities (27G), document structures (27H), and other non-source-declaration categories. Symbol is effectively now "any node in the graph," not just "thing declared in source code."

The language field grew with it, but without the same audit attention. The 2026-05-30 symbol-emit-coherence audit and its linker-language-provenance supplement found that **linkers fabricating synthetic stand-in Symbols (Kafka topics, WS endpoints, WASM modules, GraphQL operations, IPC channels, etc.) populate `Symbol.language=` according to three different unwritten policies:**

- **INHERIT-EMITTER (10 linkers, 13 emit sites):** `language=` is the discovering/calling file's language. A Kafka topic discovered in `producer.py` gets `language="python"`; the same topic discovered in `consumer.java` gets `language="java"`. Two stand-ins per logical entity. The synthetic looks identical to a real Python or Java function for any consumer that filters by language.
- **LITERAL-HOST (8 linkers, ~13 emit sites):** `language=` is hardcoded to the protocol's traditional host language regardless of where the pattern was found. `ipc.py` always emits `"javascript"`; `phoenix_ipc.py` always `"elixir"`; `subprocess_cli.py` always `"python"`. Wrong when the protocol surfaces in unexpected languages.
- **LITERAL-SENTINEL (4 linkers, 8 emit sites):** `language=` is a synthetic value the catalog doesn't recognize: `"unknown"` (annotation_convention), `"wasm"` (wasm_bindgen), `"protobuf"` (grpc Route synthetics), `"openapi"` (openapi linker).

Three policies, no governing principle, no documentation of which fits when. Each linker author made a local call.

### WI-zadot's specific finding

Dogfood pass 10 (2026-05-28, anon_id ITEM-8b4eb5) flagged the INHERIT-EMITTER case directly: "Synthetic linker nodes (kafka:/redis:/ws:/Mutation./Query.) tagged kind=function language=python." The `kind=function` half was closed by ADR-0027 Phase 3 (synthetic stand-ins fold to `kind="function"` + `meta["framework_role"]=<role>`). The `language=` half was left at `needs_human_review` pending policy decision.

The audit subsequently revealed that WI-zadot is one symptom of a three-way policy fragmentation, not a singleton.

### The hidden second semantic

The audit-supplement's downstream-consumer analysis found that four linkers depend on the INHERIT-EMITTER discovery-language encoding load-bearingly:

| Site | What it does |
|---|---|
| `event_sourcing.py:753` | `is_cross_language = pub.language != sub.language` → sets edge `cross_language` meta |
| `database_query.py:434` | Same shape |
| `message_queue.py:497-505` | Same shape, **plus** confidence adjustment `-0.1` when cross-language |
| `graphql_resolver.py:504,530` | Same shape, two comparison sites |

These consumers aren't accidentally using `Symbol.language` for cross-language detection; they're deliberately consuming it as a discovery-context signal. The field is overloaded with two semantics — nominally "source language of declaration," operationally "host language where the synthetic was discovered" — and the cross-language detectors read the second.

This is precisely the INV-numat pattern ("vocabulary fields mix axes") manifested concretely on the language field.

### ADR-0024 promotion-gating math

ADR-0024 §"Fold-residue discipline" prescribes: *when a meta key would recur with ≥3 distinct values OR ≥2 producer modules, promote it to a sibling typed field on the parent dataclass rather than use a meta key.*

For the operational "discovery language" semantic that the 13 INHERIT-EMITTER sites encode:
- **Distinct values:** at least 5-10 (python, javascript, typescript, java, ruby, go, rust, ...).
- **Producer modules:** 10 linker files.

Both thresholds passed by a wide margin on the day the field would be introduced. ADR-0024's discipline says: skip the meta-key intermediate, promote directly to a typed sibling field.

For the protocol-identity semantic that the LITERAL-SENTINEL sites encode (and that LITERAL-HOST partially encodes via host-language proxy):
- **Distinct values:** at least 4-6 (kafka, websocket, ipc, wasm, openapi, ...).
- **Producer modules:** 8+ linker files (4 LITERAL-SENTINEL + the LITERAL-HOST ones that smuggle protocol identity through host-language hardcoding).

Both thresholds also passed. Same prescription: typed field.

### What this ADR does

Recognizes that `Symbol.language` is doing three jobs across the linker codebase, applies ADR-0024 §"Fold-residue discipline," and reshapes the IR so each job lives in its own typed field with its own documented semantic.

## Decision

### 1. Three coordinated field changes on `Symbol`

**Relax `Symbol.language: str` to `Symbol.language: Optional[str]`.**

The field's documented semantic is restored to its original meaning: *the source language in which this symbol's declaration appears.* For Symbols representing real source-code declarations (the bulk of analyzer output), the value is the host file's language as before. For synthetic linker stand-ins representing entities with no source-language declaration (Kafka topics, WASM modules, IPC channels, GraphQL operations, etc.), the value is `None`.

The `# axis: language` annotation on the field remains; the axiom updates to admit `None` as a legal value meaning "the symbol has no source-language declaration."

**Add `Symbol.discovery_language: Optional[str]` typed field.**

The field's documented semantic: *the host source language where the linker discovered the pattern that produced this synthetic stand-in.* For synthetic stand-ins emitted by linkers that detect protocol/framework patterns in source code, this carries the language of the file where detection happened. For synthetic stand-ins that are not anchored to a discovering file (e.g., a WASM module referenced by URL with no per-file discovery anchor), this is `None`. For real-source-declaration Symbols emitted by analyzers, this is `None` — `Symbol.language` already carries that signal.

The field shares the language axis with `Symbol.language` — same catalog, same value vocabulary. The `# axis: language` annotation applies to both fields.

**Add `Symbol.protocol_origin: Optional[str]` typed field.**

The field's documented semantic: *the protocol or framework identity of a synthetic stand-in.* Values are protocol-family identifiers (e.g., `"kafka"`, `"redis"`, `"websocket"`, `"ipc"`, `"wasm"`, `"openapi"`, `"grpc"`, `"graphql"`). The field is populated by linkers fabricating synthetic stand-ins. Real-source-declaration Symbols leave it `None`.

`protocol_origin` belongs to a new axis (the *protocol-origin axis*) with its own catalog. The axis declaration follows ADR-0024 §4 lightweight pattern: catalog-derived from a hand-maintained enumeration in `packages/hypergumbo-core/src/hypergumbo_core/protocol_origins.py` (new module) with a `MetaKeySpec`-style entry per legal value.

### 2. Per-linker producer policy

The 35+ linker Symbol-emit sites split into three migration classes based on what they represent:

**Class A — Real source-language declarations** (small set; mostly file Symbols from linkers, plus `grpc.py`'s proto-file scan emits).
- Keep `language=<host source language>` (catalog-conformant value).
- `discovery_language=None`, `protocol_origin=None`.
- No semantic change from current state.

**Class B — Synthetic protocol stand-ins discovered in real source files** (the WI-zadot family + the LITERAL-HOST family + the LITERAL-SENTINEL family).
- `language=None` (no source-language declaration).
- `discovery_language=<host language of the discovering file>` — derived from the file extension via existing helpers (`_language_for_file`, `_get_language`) or from the pattern dataclass that captured the discovery context.
- `protocol_origin=<protocol identity>` — e.g., `"kafka"`, `"websocket"`, `"ipc"`, `"wasm"`, `"openapi"`, `"graphql"`.

Per-linker migration target (Class B unless noted):

| Linker | Current policy | Migration |
|---|---|---|
| `annotation_convention.py` | LITERAL-SENTINEL `"unknown"` | `language=None`, `discovery_language=<file>`, `protocol_origin="annotation"` |
| `database_query.py` | INHERIT-EMITTER | `language=None`, `discovery_language=pattern.language`, `protocol_origin="database_query"` |
| `event_sourcing.py` | INHERIT-EMITTER | Same shape; `protocol_origin="event_sourcing"` |
| `graphql.py` (client) | INHERIT-EMITTER | Same shape; `protocol_origin="graphql"` |
| `graphql_resolver.py` | INHERIT-EMITTER | Same shape; `protocol_origin="graphql"` |
| `grpc.py:230,734` (proto-file scan) | INHERIT-EMITTER via pattern.language | **Class A** for proto-file scans (language="proto" stays); Class B for client-call scans |
| `grpc.py:944` (Route synthetic) | LITERAL-SENTINEL `"protobuf"` | `language=None`, `discovery_language=None` (route has no host discovery context), `protocol_origin="grpc"` |
| `http.py` | INHERIT-EMITTER | Same shape; `protocol_origin="http"` |
| `ipc.py` | LITERAL-HOST + INHERIT-EMITTER | `language=None`, `discovery_language=<file language>`, `protocol_origin="ipc"` |
| `js_module.py` | INHERIT-FROM-IMPORTING-SYMBOL | `language=None`, `discovery_language=<importing symbol's language>`, `protocol_origin="js_module"` or `"npm"` |
| `message_dispatch.py` | CONDITIONAL-LITERAL | Decision deferred — conditional literal isn't cleanly Class A or B; see Open Questions |
| `message_queue.py` | INHERIT-EMITTER | Same shape; `protocol_origin="message_queue"` |
| `openapi.py` | LITERAL-SENTINEL `"openapi"` | `language=None`, `discovery_language=<spec-file language>`, `protocol_origin="openapi"` |
| `phoenix_ipc.py` | LITERAL-HOST `"elixir"` | `language=None`, `discovery_language=<file language>`, `protocol_origin="phoenix_ipc"` |
| `solidity_abi.py` | LITERAL-HOST `"typescript"` | `language=None`, `discovery_language=<file language>`, `protocol_origin="solidity_abi"` |
| `subprocess_cli.py` | LITERAL-HOST `"python"` | `language=None`, `discovery_language=<file language>`, `protocol_origin="subprocess_cli"` |
| `swift_objc.py` | LITERAL-HOST `"swift"` | `language=None`, `discovery_language="swift"`, `protocol_origin="objc_bridge"` |
| `tauri_ipc.py` | LITERAL-HOST | `language=None`, `discovery_language=<file language>`, `protocol_origin="tauri_ipc"` |
| `vue_component.py` | LITERAL `"vue"` | Class A: vue IS a real template language. Keep `language="vue"`. |
| `wasm_bindgen.py:269` (TS-side import) | LITERAL `"typescript"` | `language=None`, `discovery_language="typescript"`, `protocol_origin="wasm"` |
| `wasm_bindgen.py:399` (WASM module) | LITERAL-SENTINEL `"wasm"` | `language=None`, `discovery_language=None`, `protocol_origin="wasm"` |
| `websocket.py` | INHERIT-EMITTER via `_language_for_file` | Class B; `protocol_origin="websocket"` |
| `yjs_crdt.py` | LITERAL `"typescript"` | Class B; `discovery_language="typescript"`, `protocol_origin="yjs_crdt"` |

### 3. The protobuf collapse (separate fix folded into this ADR)

The non-catalog `language="protobuf"` value at `grpc.py:230` (GrpcPattern construction for proto-file scans) is replaced with `"proto"` to match the proto analyzer's registered catalog value. This is one of the four catalog-conformance findings from the main audit, the cheapest of the four to fix, and naturally folds into this ADR's migration scope. The Symbol emit at `grpc.py:734` propagates the change automatically via `pattern.language`. The Route synthetic at `grpc.py:944` migrates to Class B (language=None, protocol_origin="grpc").

### 4. Consumer migration

Four cross-language-detection sites migrate from `sym.language` to `sym.discovery_language`:

- `event_sourcing.py:753`: `is_cross_language = pub.discovery_language != sub.discovery_language`
- `database_query.py:434`: same shape
- `message_queue.py:497-505`: same shape, plus the `-0.1` confidence adjustment
- `graphql_resolver.py:504,530`: same shape, both comparison sites

Filter consumers (the ~7 language-filter sites in linkers like jackson_dispatch, napi, tauri_ipc, crypto_flow that select for specific source languages) require no change — they were already filtering for real source-language Symbols, and synthetic stand-ins now have `language=None` so they're correctly excluded.

Display consumers (slice.py:150, entrypoints.py:716, etc.) need `Symbol.language or "synthetic"` handling, where the display string falls back to either `protocol_origin` or a generic label when `language is None`. Three or four short helper additions in CLI formatters.

### 5. ADR-0024 four-part template instantiation

#### Axis names

- **language axis** (existing, expanded scope): now applies to two fields (`Symbol.language` and `Symbol.discovery_language`) instead of one.
- **protocol-origin axis** (new, lightweight per ADR-0024 §4): applies to `Symbol.protocol_origin` and possibly to a corresponding `Edge.protocol_origin` if cross-edge protocol identity emerges as a recurring need (not in scope for this ADR).

#### Axioms

- `Symbol.language`: *names the source language in which this Symbol's declaration appears, or `None` if the Symbol does not represent a declaration in any source language.*
- `Symbol.discovery_language`: *names the host source language where the linker discovered the pattern that produced this Symbol, or `None` if the Symbol was not produced by a linker pattern-detection pass.*
- `Symbol.protocol_origin`: *names the protocol or framework family that the Symbol is a synthetic stand-in for, or `None` if the Symbol is not a synthetic protocol stand-in.*

Each axiom is falsifiable: given a candidate value, it either passes or fails. Each is one-sentence. Each distinguishes the axis-conformant case from the non-applicable case.

#### Consumer pattern

`Symbol.language` and `Symbol.discovery_language` both use the existing language catalog accessor:

```python
from hypergumbo_core.catalog import all_known_languages
catalog = all_known_languages()
# Both Symbol.language and Symbol.discovery_language values must be in catalog ∪ {None}
```

`Symbol.protocol_origin` gets a new accessor in the new module:

```python
from hypergumbo_core.protocol_origins import all_known_protocol_origins
catalog = all_known_protocol_origins()
```

#### Enforcement

Three layers:

- **Static field-axis declaration check** — `multi_value_field_axis.py` already enforces that every `str` field on dataclasses in `ir.py` / `datamodels.py` carries an `# axis: <category>` annotation. New fields gain annotations: `language: Optional[str]  # axis: language`, `discovery_language: Optional[str]  # axis: language`, `protocol_origin: Optional[str]  # axis: protocol-origin`.
- **Property tests** — `tests/test_axis_meta_keys.py` already enforces registry shape for meta keys; analogous tests added for the new `protocol_origins` registry.
- **Runtime / corpus check** — known gap. The catalog-derived axes don't have a runtime drift linter today (the static AST walker is the wrong shape for catalog-derived values). This ADR doesn't fix that gap; a future ADR addressing INV-sugat's "no spec-vs-data validator stage" super-META is the right home for the runtime check.

## Migration plan

Five phases over an estimated 4-6 PRs.

### Phase 0 — ADR + protocol_origins module + tests

- Land this ADR (or its accepted successor) at `docs/adr/0031-symbol-language-reshape.md`.
- Land `packages/hypergumbo-core/src/hypergumbo_core/protocol_origins.py` with the initial value catalog (annotation, database_query, event_sourcing, graphql, grpc, http, ipc, js_module, npm, message_queue, openapi, phoenix_ipc, solidity_abi, subprocess_cli, objc_bridge, tauri_ipc, vue, wasm, websocket, yjs_crdt — ~18-20 values).
- Land property tests at `packages/hypergumbo-core/tests/test_protocol_origins.py`.
- Update `Symbol` dataclass to add `discovery_language: Optional[str]` and `protocol_origin: Optional[str]`, both `None`-defaulted. Relax `language: str` to `Optional[str]`. No producer changes yet — the new fields are dormant.

### Phase 1 — Producer migration (per linker)

One PR per linker, in priority order driven by frequency in the prospector corpus:

- **High priority:** the 4 cross-language-detection-feeding linkers (event_sourcing, database_query, message_queue, graphql_resolver). These have downstream consumers waiting on the new fields.
- **Medium priority:** the remaining INHERIT-EMITTER linkers (graphql, http, ipc, websocket, js_module).
- **Lower priority:** LITERAL-HOST and LITERAL-SENTINEL linkers (phoenix_ipc, subprocess_cli, swift_objc, solidity_abi, tauri_ipc, wasm_bindgen, openapi, annotation_convention, vue_component, yjs_crdt).
- **grpc.py special:** lands with the protobuf collapse in one PR. Two emit sites change.

Each linker PR includes the producer-side migration + the test fixture updates for that linker's tests. Estimated diff per linker: ~20-50 lines source + ~30-80 lines test updates.

### Phase 2 — Consumer migration

One PR that updates the 4 cross-language-detection sites from `sym.language` to `sym.discovery_language`. **Must ship in the same release window as the producers' Phase 1 migration** — otherwise the cross_language metadata regresses for whichever linker migrated first while the consumer still read the old field.

Coordination: the recommended sequencing is:
1. Land Phase 0 (dormant fields).
2. Land Phase 2's consumer changes such that they prefer the new field but fall back to the old: `pub.discovery_language or pub.language`. Idempotent under either producer state.
3. Land Phase 1's producer migrations in any order. Each migration is a no-op for the consumers because of the fallback.
4. After all Phase 1 PRs land, land a Phase 2 cleanup PR that removes the fallback, leaving only `pub.discovery_language`.

This double-write / read-prefer pattern eliminates the coordination-window concern.

### Phase 3 — Schema bump

`SCHEMA_VERSION` 0.11.0 → 0.12.0. Two new fields appear in the behavior-map output for every Symbol. `Symbol.language` becomes `Optional` — JSON output includes `"language": null` for synthetic stand-ins post-migration.

Stable_id impact: the seven `stable_id` factories in `analyze/base.py:649-656` use `Symbol.language` as a SHA256 input. Class B Symbols (synthetic stand-ins) now have `language=None`. The factories need a defined behavior for `None` — either hash the string `"none"`, skip the field, or hash a sentinel like `<none>`. The choice affects which Class B Symbols' stable_ids change vs stay stable. This ADR specifies: **hash the empty string `""` for `None` values**, which is the simplest backward-compatible behavior. All Class B Symbols' stable_ids change in this release (~20-30 Symbols across the linker family).

This is the same shape of breakage as ADR-0023 §6 and ADR-0027 Phase 1 step 5; consistent precedent.

### Phase 4 — Documentation and drift-monitoring follow-ups

- Update `docs/MIGRATION-5.X-CONCEPT-AXES.md` with the per-value rename table for JSON consumers.
- Update `docs/concept-axes.md` via `scripts/generate-concept-axes` (existing infrastructure).
- Update `docs/hypergumbo-spec.md` §6 to mention the new fields.
- File a follow-up tracker item for the runtime drift gate on the language and protocol-origin axes (relating to INV-sugat super-META).

## Consequences

### Positive

- **Closes WI-zadot.** The language= half gets a typed-field home (`discovery_language`) and an honest `language=None` for the synthetic stand-ins. The dogfood-pass-10 finding is resolved with a coherent structural answer rather than a per-instance patch.
- **Addresses INV-numat's language-axis expression.** The same axis no longer encodes two unrelated semantics. The cross-language-detection logic now reads the field whose name matches what it semantically wants.
- **Closes the 4 audit catalog-conformance findings on `Symbol.language`.** `protobuf` collapses to `proto` (catalog-conformant). `unknown` / `wasm` / `openapi` move out of `language=` to `protocol_origin=` (their natural axis), removing them from the language catalog concern entirely.
- **Documents the previously-implicit second semantic.** The "language as discovery-context" overload that 4 consumers depended on is now a typed field with a documented semantic. New consumers don't have to reverse-engineer the convention from looking at how it's emitted.
- **Symmetric with ADR-0027 (kind) and ADR-0028 (evidence_type) resolutions.** Three axes on Symbol/Edge now follow the canonical-value-plus-typed-sibling pattern. Consistent IR shape.

### Negative

- **Schema bump 0.12.0 with stable_id changes for ~20-30 Class B Symbols.** Cross-version stable_id pinning (e.g., dogfood-corpus tracker links) for those Symbols breaks. Mitigation: the dogfood corpus uses anon_ids as durable identifiers; hypergumbo's primary stable_id consumer is within-process. Same precedent as ADR-0023 / ADR-0027 stable_id changes.
- **~20 linker files touched** across Phase 1 PRs over an estimated 4-6 weeks of staged migration. Per-PR diff is small (~50-100 lines); the total is the sum.
- **One new module + one new registry + property tests added.** `protocol_origins.py` joins `symbol_kinds.py`, `evidence_types.py`, `axis_meta_keys.py` as the fourth axis-registry in the ADR-0024 pattern. Net IR complexity grows.
- **Two new typed fields on `Symbol`.** The dataclass goes from 28 to 30 fields. Each new field is an axis for future drift; the axis-declaration discipline applies.
- **The LITERAL-HOST migration is partially controversial.** Some LITERAL-HOST linkers' hardcoded values (e.g., `phoenix_ipc.py`'s `"elixir"`) might be argued to belong in `language=` as a "this protocol is fundamentally an Elixir thing" claim. This ADR migrates them anyway on the grounds that protocols aren't languages; the hardcode is wrong in any multi-language stack. Future Phoenix bindings in Erlang or Gleam would surface the problem; better to fix before that.

### Neutral / acknowledged

- **`canonical_name` and `fingerprint` divergences are NOT addressed by this ADR.** Those fields have their own three- and two-use-respective desire-paths analyses in the lab notebook. A coherent Symbol-reshape ADR could in principle bundle them; this ADR scopes narrowly to the language axis for tractability. A follow-up ADR addressing INV-kovob and INV-fogum is signaled but not promised.
- **The runtime axis-enforcement gap stays open.** INV-sugat super-META covers this; a future ADR is the right home. This ADR doesn't make the gap worse; it just doesn't close it.
- **The conditional-literal linkers (`crypto_flow.py`, `message_dispatch.py`) are deferred.** Their `lang = "typescript" if write.api == "..." else "rust"` pattern isn't cleanly Class A or B. See Open Questions.

## Alternatives considered

The three-policies analysis at `~/hypergumbo_lab_notebook/three-policies-pros-cons-05302026.md` is the full version; summarized here:

1. **Keep INHERIT-EMITTER everywhere.** Don't reshape; accept the conceptual leak; rely on per-consumer documentation. Rejected: leaves INV-numat permanently violated; doesn't address the LITERAL-HOST / LITERAL-SENTINEL fragmentation.
2. **One canonical field + meta key.** `language` stays as a single field; the discovery-context semantic moves to `meta["discovery_language"]`. Rejected per ADR-0024 §"Fold-residue discipline" promotion threshold — the recurrence math passes both thresholds on day one, prescribing typed-field promotion. Meta-key intermediate is doctrinally inferior here.
3. **Three fields as proposed (this ADR).** Doctrinally correct under ADR-0024. Adopted.
4. **`Symbol.language="synthetic"` sentinel + `meta["protocol_origin"]` meta key.** A canonical sentinel for "no source language" with the protocol identity in meta. Rejected for the same ADR-0024 reason as option 2 (the protocol identity passes the typed-field promotion threshold) and for muddying the language catalog with a non-language value.

## Likely-deprecate (first cut)

No existing catalog values become deprecation candidates from this ADR's language-axis changes. The catalog gains:
- `discovery_language` joins the language axis as a second field using the same value set
- `protocol_origin` axis is new with ~18-20 initial values

Producer-side policies are deprecated (not catalog values):
- INHERIT-EMITTER policy on synthetic stand-ins → migrates to Class B
- LITERAL-HOST policy → migrates to Class B
- LITERAL-SENTINEL policy → migrates to Class B (`protocol_origin` carries the sentinel)

The `language="protobuf"` outlier in `grpc.py:230,944` deprecates and collapses to `language="proto"` (matching the proto analyzer's catalog registration) for proto-file scans, and to `language=None` for the Route synthetic.

## Open questions

1. **Conditional-literal linkers (`crypto_flow.py`, `message_dispatch.py`).** Their `lang = "typescript" if write.api == "webcrypto" else "rust"` pattern dispatches the host language based on protocol API family. Are these emitting Class A (real declarations whose target language varies) or Class B (synthetic stand-ins)? Looking at the code, they emit synthetic stand-ins for crypto/dispatch operations — but the language= value picks between real client languages. Resolution proposed: treat as Class B (`language=None`, `discovery_language` = the conditional value, `protocol_origin = "crypto_flow"` or `"message_dispatch"`).

2. **`vue_component.py` — language=vue.** Vue is a real template language with its own analyzer (treesitter-vue). The Symbols emitted from .vue files are arguably real-source declarations. Resolution proposed: keep as Class A.

3. **gRPC Route synthetic — `protocol_origin="grpc"` or `"protobuf"`?** Both are defensible. The Route is derived from a proto service definition (suggesting "protobuf") but represents an HTTP-shaped gRPC route (suggesting "grpc"). Resolution proposed: `"grpc"` — the route IS a gRPC concept; protobuf is the schema language it's derived from.

4. **`protocol_origin` catalog growth pressure.** ~18-20 initial values; future linkers add more. Same Kessler concern as the language catalog: each new protocol adds to the vocabulary. Mitigation: the ADR-0024 lightweight-axis pattern keeps additions cheap; the recurrence math suggests this is at or past threshold already (we'd be promoting from meta-key if we'd used one).

5. **Edge.protocol_origin?** If consumers want to filter edges by protocol family (e.g., "show me all Kafka edges"), an Edge-side field might emerge as useful. Not in scope for this ADR. Filed as future work.

## References

- ADR-0014 (Generalized Symbol Identity): the stable_id factories whose `language=` hash inputs change at SCHEMA_VERSION 0.12.0.
- ADR-0023 (Edge Type Names the Relationship): worked example for axis-declaration template; same shape as the language-axis migration here.
- ADR-0024 (Axis Declaration Template): the four-part template this ADR instantiates, plus §"Fold-residue discipline" promotion-gating rule that prescribes typed-field promotion for `discovery_language` and `protocol_origin`.
- ADR-0027 (Symbol.kind = source-language syntactic construct): the sibling-axis ADR on Symbol whose Phase 3 fold this ADR mirrors. WI-zadot's kind= half was closed by ADR-0027 Phase 3; this ADR closes the language= half.
- ADR-0028 (Edge.evidence_type Names the Inference Pathway): another sibling using canonical-value + typed-field pattern.
- Tracker WI-zadot: the dogfood-pass-10 finding (ITEM-8b4eb5) closed by this ADR.
- Tracker INV-tofun: separately closed by the 2026-05-30 objc/ansible language-tag-harmonization PR; not addressed here.
- Tracker INV-numat: META "vocabulary fields mix axes." This ADR resolves the language-axis instance. Other axes (canonical_name dual-mode under INV-kovob, fingerprint format under INV-fogum) remain open.
- Audit: `/tmp/symbol_audit_output.md`
- Audit supplement: `~/hypergumbo_lab_notebook/audit-supplement-linker-language-provenance-05302026.md`
- Three-policies analysis: `~/hypergumbo_lab_notebook/three-policies-pros-cons-05302026.md`
- Symbol field population plan (parallel concern): `~/hypergumbo_lab_notebook/symbol-field-population-plan-05302026.md`

---

**Drafting notes (to be removed before merge):**

- Draft lives at `~/hypergumbo_lab_notebook/adr_0031_symbol_language_reshape_DRAFT.md`. Promote to `docs/adr/0031-symbol-language-reshape.md` after review and rename to drop `_DRAFT`.
- Numbering as 0031: ADR-0030 is the latest in `docs/adr/README.md`'s index. 0031 is next.
- Estimated total work: 4-6 PRs over 4-6 weeks. Per-PR scope is bounded; coordination concern is the Phase 1/Phase 2 sequencing.
- The protobuf collapse and the audit's other 3 catalog findings are folded into this ADR's scope rather than addressed as standalone fixes. This is intentional: the reshape removes them from the language-axis question entirely by moving the sentinels to `protocol_origin`.
