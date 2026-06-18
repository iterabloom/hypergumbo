<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0029: Cross-language inherited-call linker

- Status: Accepted (in active implementation — see INV-nilud campaign)
- Date: 2026-05-25
- Supersedes: —
- Superseded by: —
- Related: ADR-3bbb (linker subcategory restoration — Infrastructure subcategory), ADR-0023 (edge-type axis), ADR-0028 (evidence-type axis)

## Context

Inheritance-aware call resolution — the rule that `someMethod()` inside class `Foo` might dispatch to `Foo`'s parent's method when `Foo` itself doesn't define it — was duplicated in 2 of 14 language analyzers when this work began:

- **Java** (`packages/hypergumbo-lang-mainstream/.../java.py:1414-1591`) — three call sites emitting `ast_call_inherited` (Site 1: bare/`this` calls), `ast_call_inherited_method` (Site 2: typed-receiver calls), and `ast_call_inherited_field` (Site 3: inherited-field-receiver calls). Each site walks the class's parent chain up to 10 hops looking for the called method short-name.
- **Ruby** (`packages/hypergumbo-lang-mainstream/.../ruby.py:1901-1956`) — `_find_inherited_initialize` walks the `base_classes` metadata chain at constructor (`SomeClass.new`) sites to find inherited `#initialize` methods.

The other 12 analyzers (Python, Kotlin, Scala, PHP, C#, C++, Go, Swift, Obj-C, Groovy, Rust, JS/TS) had no equivalent walk, producing a silent dead-code FP shape for every framework convention or analog that relies on inherited dispatch.

WI-puluf surfaced an adjacent gap on the Ruby side: `include Module` / `extend Module` mixin declarations weren't modelled at all, so Sidekiq workers, ActiveModel validations, and Rails concerns were invisible to the call graph. Investigation against `~/puluf-plan.md` (Alternative B) concluded the right architectural move was a single cross-language Tier-2 Infrastructure linker — same layer as `linkers/inheritance.py` — that owns inherited-call resolution across every language.

## Decision

A new Infrastructure linker `packages/hypergumbo-core/src/hypergumbo_core/linkers/inherited_calls.py` owns all inheritance-aware call resolution. Language analyzers MUST stop walking ancestor chains themselves; instead they emit `make_unresolved_edge(...)` with optional hints (`enclosing_class`, `receiver_type_hint`, `inherited_field_receiver`) on `Edge.meta`, and the linker walks the chain.

### Contract

`packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py::make_unresolved_edge` accepts three optional kwargs added in PR-1 (WI-gifar) that land in `Edge.meta`:

| kwarg | Site | Confidence | Evidence type |
| --- | --- | --- | --- |
| `enclosing_class` | Site 1 — bare / `this` / `self` calls inside class X | 0.90 | `ast_call_inherited` |
| `receiver_type_hint` | Site 2 — `receiver.method()` where receiver type inferred | 0.70 | `ast_call_inherited_method` |
| `inherited_field_receiver` | Site 3 — `self.field.method()` where field is inherited | 0.80 | `ast_call_inherited_field` |

The PR-2 linker (this ADR's substrate) implements Site 1 only. PR-3 lifts Java's Site 1 walk into the linker; PR-4 enriches the Java analyzer with `var_types` / `class_fields` meta; PR-5 implements Site 2 + Site 3 resolvers consuming that meta.

### Per-language MRO

Per-language ancestor-walk semantics live in a hardcoded module-level dispatch table `_MRO_WALKERS: dict[str, Callable]` inside `linkers/inherited_calls.py`. Hardcoded over YAML/decorators because the table is static language semantics — see the WI-hatip plan discussion at `~/puluf-plan.md`.

Initial walkers (PR-2 ships `_walk_insertion_order` only):

| Walker | Languages | Algorithm |
| --- | --- | --- |
| `_walk_insertion_order` | Ruby, Groovy | BFS through inheritance edges in declaration order (covers Ruby mixin contributions via `include`/`extend`) |
| `_walk_single_then_interfaces` | Java, Kotlin, C#, Scala-class | Single superclass first, then interface list — PR-3 |
| `_walk_c3` | Python | C3 linearization — future |
| `_walk_left_to_right` | PHP, Swift, Obj-C, C++ | Left-to-right depth-first — future |
| `_walk_linearization` | Scala traits | Scala trait linearization — future |
| (default fallback) | — | `_walk_single_then_interfaces` once PR-3 lands |

Depth cap = 10, matching the existing Java and Ruby walks.

Languages whose walker isn't registered yet are silently no-op'd; the analyzer must opt in by emitting the hint AND the linker must have a walker registered for that source language.

### Edge types

The linker reuses existing edge types and evidence types (no new heavyweight axis values needed):

- `edge_type="calls"` always (the resolved edge).
- `evidence_type` per Site as the table above shows. All three `ast_call_inherited*` evidence types already exist in `evidence_types.py::EVIDENCE_TYPES` from the original Java/Ruby precedent.

PR-2 also lights up an adjacent registry entry: the canonical `includes` edge type (already in `edge_types.py` for LaTeX `\include`, RST, Meson subdir, etc.) gains Ruby `include`/`extend` mixin as a new producer. A new `evidence_type="ast_includes"` distinguishes mixin-derived `includes` edges from file-include ones.

### Linker registration

```python
@register_linker(
    "inherited-calls",
    priority=18,  # Between inheritance (15) and type_hierarchy (60).
    activation=LinkerActivation(always=True),
)
```

`priority=18` was chosen so the linker:

1. Runs **after** `inheritance` (15), which produces the `extends`/`implements`/`includes` edges this linker walks.
2. Runs **before** `type_hierarchy` (60), the next caller of `build_method_index`. (The original ADR text said `type_hierarchy=20`; that was a transcription error caught during PR-5 — the actual registered priority is 60, set at the `@register_linker` call in `linkers/type_hierarchy.py`.)

The linker uses `build_inheritance_index(edges, edge_types=("extends", "implements", "includes"))` (the PR-1 generalized helper) so Ruby mixin contributions participate in the same walk as concrete inheritance. It uses `build_method_index(...)` (the PR-1-extracted helper from `type_hierarchy.py`) for O(1) short-method-name lookups by class id.

### Migration strategy

The campaign ships five sequential PRs, each gated by `awaits_bakeoff_validation` on merge:

| PR | WI | Scope | Behavior change |
| --- | --- | --- | --- |
| PR-1 | WI-gifar | Contract + shared helpers (additive refactor) | None |
| PR-2 | WI-hatip | Linker substrate + Ruby migration + WI-puluf closure | Ruby `_find_inherited_initialize` lifts to linker; `include`/`extend` mixins newly visible |
| PR-3 | WI-dukog | Java Site 1 lift | Java Site-1 walk lifts to linker |
| PR-4 | WI-sivuk | Java analyzer enrichment for Sites 2/3 (meta only) | None (analyzer-side; meta unconsumed) |
| PR-5 | WI-puvil | Java Sites 2+3 lift | Java Sites 2+3 lift to linker; META INV-nilud → `pending_validation` |

Each PR's success criterion is empirical on BROAD bakeoff cohorts (spring-petclinic, spring-boot, chatwoot, hypergumbo-self): edge-set parity with pre-PR baseline ± per-PR tolerance. The campaign's META invariant `INV-nilud` moves to `satisfied` after a full BROAD cycle confirms no regression once PR-5 lands.

### What stays in analyzers

Direct-resolution paths stay in analyzers. The new linker handles only the inherited case (when the named class doesn't directly define the called method). The Ruby `.new → #initialize` direct case (when `Class#initialize` IS in the global symbol table) still emits a resolved edge from the analyzer; only the inherited fallback case now produces an unresolved edge with the `enclosing_class` hint.

## Consequences

### Positive

- A single source of truth for inheritance-aware call resolution across all 14 languages.
- The 12 silent-gap analyzers get inherited-call resolution by opting in (emit the hint + register a walker for the language).
- Ruby `include`/`extend` mixins newly visible to the call graph — closes WI-puluf without requiring a separate `ruby_mixin.py` Framework linker.
- LOC delta: ~250 lines deleted from `java.py` + ~80 deleted from `ruby.py`; ~280 added to `linkers/inherited_calls.py`. Net break-even with deduplication benefit.

### Negative

- A class of dangling unresolved edges enters the graph: when an analyzer emits the hint but no project ancestor defines the method, the unresolved edge sits in the behavior map. These are harmless (most consumers ignore unresolved edges), but the analysis-run edge count grows slightly versus pre-PR.
- The linker can produce a wider span of edges than the prior in-analyzer walks (insertion-order BFS through `includes` may surface ancestors the in-analyzer walks missed). This is the intended improvement but means bakeoff edge counts will not be byte-identical for Ruby — only the Java edge set is expected byte-identical PR-by-PR.

### Risks

- A bug in a walker function affects all languages assigned to that walker. Mitigation: per-walker unit tests with synthetic fixtures; per-language pipeline-mode integration tests that exercise the full analyzer→linker stack.
- The `inheritance.py` extension to emit `includes` edges introduces a new edge-type producer at the existing priority 15. Existing consumers of `extends`/`implements` (centrality, dispatch closure, dead-code) don't currently special-case `includes`; they will see it as just another structural edge. If any consumer needs to distinguish concrete inheritance from mixin, it queries `edge_type == "includes"` explicitly.

## Closure criteria for INV-nilud

- All 5 PRs merged.
- `INV-nilud` META moves `violated` → `pending_validation` on PR-5 merge.
- A full BROAD bakeoff cycle (spring-petclinic + spring-boot + chatwoot + hypergumbo-self) confirms no regression.
- META → `satisfied`.
- Java analyzer LOC: ~250 lines deleted from `java.py`.
- Ruby analyzer LOC: ~80 lines deleted from `ruby.py` (`_find_inherited_initialize`).
- New core LOC: ~280 lines in `linkers/inherited_calls.py`.

A follow-on META (filed after PR-5 closes) covers "Inherited-call parity across the 12 silent-gap analyzers" (Python / Kotlin / Scala / PHP / C# / C++ / Go / Swift / Obj-C / Groovy / Rust / JS-TS). Sibling WIs ship one-per-language as `todo_soft someday`, activated only when a bakeoff dead-code FP traces to missing inheritance coverage in that language.

## References

- `~/puluf-plan.md` — canonical campaign plan (Alternative B post-WI-puluf design discussion).
- ADR-3bbb: linker subcategory restoration (Infrastructure subcategory).
- ADR-0023: edge-type axis (`includes` is on the `relationship` axis).
- ADR-0028: evidence-type axis (`ast_call_inherited` and `ast_includes` on the `inference_pathway` axis).
- Tracker:
  - META: INV-nilud-zivag-jibak-danov-polak-sibam-fahif-katat
  - WI-gifar (PR-1): contract + shared helpers — merged 2026-05-24
  - WI-hatip (PR-2): linker substrate + Ruby migration — this PR
  - WI-dukog (PR-3), WI-sivuk (PR-4), WI-puvil (PR-5) — downstream
  - WI-puluf-nutuv-mahol-lopov-dahif-sabus-baban-nokag — closes with PR-2
