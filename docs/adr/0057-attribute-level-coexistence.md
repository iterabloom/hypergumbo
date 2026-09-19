<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0057: Multi-Backend Coexistence at the Attribute, Not the Record

Status: Accepted — design adopted by the owner 2026-09-18 in session; implementation authorised by the owner the same day as an eleven-row sequence (§Tracker items) and in progress: §10's producer contract (WI-hohuh), §12's recorded fixtures and lint (WI-romuh) and §7's `kind` producer fix (WI-gapup) landed. WI-gojum stays parked until the owner unparks it.

- Date: 2026-09-18
- Supersedes: ADR-0012 §Step 3 (multi-fidelity passes — the record-level coexistence it described; Steps 1–2 are untouched)
- Superseded by: —
- Related: ADR-0012 (the pass-unification frame this refines), ADR-0035 (stable_id contract — §1 uniqueness, §3 SITE occurrence-indexing, §4 the "aggregate at presentation time" line this ADR narrows), ADR-0037 (edge resolution — the single finalize verdict that must see merged endpoints), ADR-0039 (`confidence_source` — the one existing per-attribute provenance field and the precedent here), ADR-0043 (stage ordering — Phase C placement, §6 single reconcile point, §8 round-trip gate), ADR-0045 (user configuration — where the arbitration default lives), ADR-0028 (evidence-type is inference-pathway-only — an edge seen by two pathways is its composite case), ADR-0030 (PROV vocabulary — the attribution terms), ADR-0023 / ADR-0027 (the edge-type and symbol-kind registries every arbitrated value must belong to). Tracker: WI-gojum (parked host), INV-lodum, INV-kutid, WI-nanom (the next backend this must serve), WI-sobig.

## Context

### Two backends already run for Rust, and nothing reconciles them

When the rust-analyzer SCIP backend is enabled it runs **alongside** the tree-sitter analyzer, by design (WI-gojum). Both emit a `Symbol` for every Rust declaration and edges from it. `run_all_analyzers` concatenates their symbols with no key of any kind and dedups edges only by `id` (`analyze/all_analyzers.py:449`); the first consumer, `refine_frameworks` (`cli.py:11305`), and every linker after it read both records. WI-gojum's 2026-09-18 note established by `git log -S` that a cross-backend merge was **never written** — the "stable_id parity" offered as readiness for one measures 0 of 52 on production input (INV-dolud, retracted in #1044).

### What the two backends actually see, measured

On `aardvark-dns` at b1008d3b67 with `HYPERGUMBO_RUST_ANALYZER=1`, pairing records by (same file, same base name, SCIP name token inside the tree-sitter item span) matches **138 of 169 SCIP nodes with zero tree-sitter leftovers**; 79 of the pairs are callables. Structural `contains` excluded:

| edges on the 79 paired callables | SCIP | tree-sitter | both |
| --- | --- | --- | --- |
| first-party callable → callable, outgoing | 99 | 100 | **92** |
| first-party callable → callable, incoming | 100 | 100 | **92** |
| all outgoing | 287 | 376 | 98 |

What only SCIP sees: 79 field references, 49 variable references, 19 structs, 17 classes, 15 enums — it reports every reference occurrence, not just calls. What only tree-sitter sees: **268 calls to synthesized external symbols** (stdlib and crates); SCIP emitted **zero** edges to anything outside its own index. The two backends are **complementary, not redundant**: they agree almost exactly on the in-crate call graph and each carries a class of edge the other cannot.

And the 92 edges they agree on do not carry the same label: the SCIP arm emits every edge as `references` (`scip/calls.py:232`), tree-sitter as `calls`. `edge_key` is `(src, dst, edge_type)`, so by the graph's own definition none of the 92 is a duplicate.

### Where the attributes disagree

The same two records for one function:

```
rust:src/dns/coredns.rs:141-141:process_message:method          <- scip
rust:src/dns/coredns.rs:141-256:CoreDns::process_message:method  <- tree-sitter
```

Span differs (SCIP's Definition `range` is the identifier token, INV-lodum), name differs, and on all 52 free functions `kind` differs — SCIP says `method` because `load_config().` parses as `DescriptorKind.METHOD`, tree-sitter correctly says `function`. Both `id` and `stable_id` are *computed from* these attributes, so neither backend's identity can serve as the key for recognising the pair.

### The three designs on the record before this ADR

1. **Coexist at the record** — ADR-0012 §Step 3 as written: both edges at one call site, different confidence, provenance-tracked, slicing prefers the higher. Lossless and honest by construction; but under a two-backend run every Rust callable is two nodes and the in-crate call graph is doubled, so every count, centrality and fan-out is wrong until each consumer reconciles.
2. **Replace** — WI-gojum sub-component 1, walked with the owner in July: tree-sitter finds call sites, rust-analyzer supplies targets, tree-sitter's guessy edges are dropped. One record, but lossy: the 268 external-stub edges and the 79 field references have no counterpart on the other side, and a wholesale winner discards one set.
3. **Merge, one value wins** — one record, a trust rule picks each contested attribute. Fixes the count, keeps both backends' unique edges, and opens a new place to be silently wrong: the type-aware backend is the *wrong* one on `kind`, and a chosen value is asserted with the combined authority of both origins.

### "Presentation time" has been carrying two meanings

ADR-0035 §4 says *aggregate at presentation time, never at identity time*, and means an output **view** — the "all dependencies" projection. But the pipeline's own traversals read contested attributes as settled scalars and branch on them long before any view exists: `slice.py:828` (`kind in _CONTAINER_KINDS` decides whether a file's imports enter the slice), `slice.py:838` (`kind in query.pass_through_kinds` decides what is stripped from output), `ranking.py:925` (`kind in _CALLABLE_KINDS` decides who gets callable centrality), `ranking.py:1044` (`edge_type in _STRUCTURAL_EDGE_TYPES` decides which edges count). That is **consumption**, not presentation. Any design that "defers arbitration to presentation" is in fact stamping a choice that ranking and slicing consume — a trust rule wearing a presentation costume.

## Decision

### 1. The unit of coexistence is the attribute

A merged record's attribute is a **set of (value, provenance) pairs**, where provenance is a pass id as in `origin` (INV-jidat):

- the producers **agree** → one value, two provenances (`kind = function ← {rust, scip}`);
- the producers **disagree** → two values, one provenance each (`kind = function ← {rust}; method ← {scip}`);
- the producers **touch different parts of the elephant** → one value with one provenance, and no entry at all from the producer that did not observe that attribute.

Nothing a producer emitted is discarded. This keeps ADR-0012's principle — both backends run, provenance is tracked, nothing is thrown away — and changes only the granularity, from the record to the attribute. The precedent is `confidence_source` (ADR-0039 ruling 2): the one existing **per-attribute provenance field**. The precedent is the field, not its `composite` value — ADR-0039 defines `composite` as a transitional label for confidence that still carries blended ranking, slated for elimination by its ruling 3, and nothing produces it. An earlier revision of this section cited that value as the precedent; that was wrong, and §13 adds the value this design actually needs.

### 2. There are three times, and resolution belongs to the middle one

**Identity time** (minting `id` / `stable_id` — ADR-0035: never resolve here), **consumption time** (every in-pipeline reader that branches on an attribute), and **presentation time** (projections). Contested attributes bite at consumption time. Therefore arbitration cannot be deferred to a view; it must be **declared, applied once, early, with the alternatives preserved** — the shape ADR-0043 §6 tolerates and the compute-twice-never-reconcile defect it forbids.

### 3. Recognising the pair is a pass, at the top of Phase C

A declared **merge pass** runs after Phase B relativization and **before the first Phase-C consumer** (`refine_frameworks`), because linkers read `kind` and `meta`. It has to be a pass, not an on-demand facade, for three reasons that are not taste: the merge key is **whole-table** (per file, the smallest tree-sitter item whose span contains the SCIP name token — a record cannot compute that about itself, and memoising it lazily makes the materialisation depend on which consumer asked first); it **rewrites identity** that 296 direct `.src`/`.dst` reads and 345 direct `.kind` reads across 89 files key on; and **membership must be settled before serialize** (ADR-0043 R1, §8) because the artifact — not an in-process API — is the product.

The merge key is (path, declared name key, declared span role) — **read from each producer's declaration (§10), never hardcoded**; the Rust instance is (path, last `::` segment, SCIP token inside tree-sitter item). Neither `id` nor `stable_id` qualifies (§Context). The merged record gets a **new `id`**, every edge from **both** producers is rewired to it, and `edge_key` is reset so `deduplicate_edges` recomputes it — the exact mechanics of `dedup_logical_synthetic_identities` (`analyze/base.py`). The merged span is the **item** span (rust-analyzer's `enclosing_range`, populated on 20 of 20 Definitions), so INV-lodum's cure lands inside this pass rather than as a separate importer edit. The pass is a **no-op when one producer emitted**: a tree-sitter-only run is byte-identical before and after.

It is a pass, not a traversal: one linear loop over symbols, one over edges, no edge-following. On `aardvark-dns` that is 756 nodes and 1,282 edges.

### 4. Arbitration is a property, with one stamped default

The scalar a consumer reads is produced by a **per-record arbitration property**: a pure function over the preserved candidate set — **precedence for categorical attributes, a declared combining rule for numeric ones (§13)** — O(candidates), needing no whole-table access and — because it is pure over data the record already holds — **no invalidation contract** (contrast `edge_key`, the codebase's memoised field, whose cost is exactly that contract: `ir.py:994`, `:1256`, `:2021`).

There is **one default**, stamped so that ranking, slicing, finalize and serialization all see the same value. Laziness does not remove the default; it only hides where it lives. Alternative policies are **opt-in reads of the candidate set**, never a second default. The scalar slot on `Symbol` / `Edge` keeps its current type, so the 89 files of consumers are unchanged by default.

### 5. The default is a preference in `config.toml`

The arbitration default lives in ADR-0045's **preferences** tiers — `$XDG_CONFIG_HOME/hypergumbo/config.toml` and the project tier — resolved through ADR-0045 ruling 4's chain (CLI flag > environment > project > user > built-in). It executes nothing, so it is not a trust key and is allowed in both tiers. The **built-in default is incumbent-first per categorical attribute** — tree-sitter before any SCIP/LSP backend — until a per-attribute measurement shows otherwise; `confidence` is numeric and follows §13, where incumbent-first would be actively wrong. "Type-aware backend wins" is **refused** as the blanket built-in: on the one attribute measured, `kind`, the type-aware backend was wrong on 52 of 52 free functions when this was ruled (the importer read only the leaf descriptor suffix and ignored `SymbolInformation.kind`; WI-gapup fixed the producer, and the refusal stands on its principle — a default is changed by a §5 measurement, not by one producer fix). The key is a per-attribute table so measured exceptions can be declared without flipping the whole default.

### 6. Provenance gets a registered, schema-versioned slot

`origin` is record-level and `meta` is not a registry (`parent_type` is written by four analyzers, read by a linker, declared nowhere), so attribute provenance needs its own slot on `Symbol` and `Edge`, with `# axis:` declarations, a `SCHEMA_VERSION` patch bump, and `docs/schema.json` / `docs/concept-axes.md` regenerated. The **semantics** are pinned here; the spelling is the implementing row's (proposed: `attribution: {field: [pass_id, …]}` plus `alternatives: {field: [{value, origin}]}` present only for contested fields; absence of both means single producer — today's records are unchanged). Edge `evidence_type` is a candidate set like any other attribute: an edge seen by two inference pathways is precisely the two-pathway case §13 keys on, not an exception to the axis.

### 7. Two producer fixes are prerequisites, not part of the mechanism

- **SCIP edges must carry the edge type SCIP already knows.** `Occurrence.symbol_roles` is a `SymbolRole` bitfield; `calls.py` reads it only to exclude Definition. Until a call-position reference is emitted as `calls`, the 92 shared edges are not duplicates under `edge_key` and the merge can fold nodes but never edges.
- **SCIP `kind` must come from the descriptor chain** (parent TYPE → method, parent NAMESPACE → function). Arbitration only helps when at least one side is right; fixing the producer removes the contest on 52 of 52 instead of adjudicating it. *Landed (WI-gapup), with a correction to the prescription:* rust-analyzer declares `SymbolInformation.kind` on every global definition (169 of 169 on the recorded aardvark-dns index), so the importer reads the producer's declaration first — Function → `function`, Method / StaticMethod / TraitMethod → `method`, EnumMember → `field` (the tree-sitter arm's choice for a variant), Struct / Enum / Trait / TypeAlias → their registered kinds, Constant → `constant` — and uses the chain only as the fallback for an emitter that leaves it unset, placing a METHOD or TERM leaf by its nearest ancestor that is not a type parameter (rust-analyzer spells an impl target as one: `impl#[Counter]increment().`; the immediate parent would have called 26 of 27 methods free functions). On the recorded fixture the two arms now agree on `kind` for 133 of the 148 paired records — 52 of 52 free functions, 27 of 27 methods, 29 of 29 fields, 9 of 9 variants, 10 of 10 structs, 3 of 3 enums, the trait and both statics; the 15 disagreements are every `const` item, where the SCIP arm says `constant` and the tree-sitter arm `variable` — the SCIP side is the more precise one, and the contest is left for arbitration.

### 8. What this makes moot

`split_within_file_stable_id_collisions` is **untouched**. It runs after the merge, so no same-`(path, stable_id)` cross-backend group ever reaches it, and it keeps doing its real job on repeated call sites. INV-lodum's clause "the splitter would re-mint the SCIP record" is true only if the span fold lands *without* the merge; with the ordering in §3 the question does not arise.

### 9. This is not a Rust feature

Attribute-level provenance is the rule for **any** pass that enriches a record another pass created — the pyright backend WI-nanom names next, and in principle every linker. Scoping it to Rust would undersell it and shape the slot wrong.

### 10. Every backend declares its merge anchor; the pass refuses the undeclared

*(Added 2026-09-18, same day, after the owner asked whether the design extends past Rust. It did not yet: the key in §3 as first filed was Rust-shaped.)*

`register_analyzer` already takes `backend` and `languages` (`analyze/registry.py:140`); when this section was written **neither Rust registration set either**, so the registry could not tell that `rust_analyzer` and `rust` were two backends for one language. WI-juzig closed that half: `languages` is now gated at registration against the taxonomy vocabulary (or an explicit `language_state` declaration), `rust_analyzer` declares `languages=["rust"], backend="scip"` and `rust` declares `backend="tree-sitter"`, and `analyzers_for_language()` enumerates every producer of a language. WI-hohuh landed the rest: `register_analyzer(..., merge=MergeAnchor(name_key, span_role, authoritative_for) | MergeDisjoint(partners), executes_analysed_code=)`, validated at decoration; `merge_participants(language)` is the pass's reader and refuses an undeclared producer by name; both Rust arms are anchored (tree-sitter: last segment under the taxonomy's declared separator, item span; SCIP: name as emitted, token span), the JS-family analyzers declare disjointness on both sides with a test that runs them on a component file, and the trust store's accept-list and the config deny-list are derived from `executes_analysed_code` rather than from `user_config.py`'s former hardcoded set. Whether scip-python or tsserver populate `enclosing_range` is still unchecked. A merge key hardcoded for Rust makes backend N+1 a rewrite of the pass — or, worse, merges nothing and reads as "nothing to merge."

Each backend registration therefore **declares**: `languages` (populated); a `name_key` normaliser (Rust incumbent: last `::` segment; SCIP arm: the emitted name — the incumbent and every alternative for one language must map one declaration to one key, and that equality on recorded fixtures is the registry test); a `span_role` of `token` or `item` (the containment test is directional: token inside item; item against item is equality); `authoritative_for`, the attributes this backend is authoritative on **by measurement**, each citing a committed `docs/audits/` table produced by the §5 instrument, empty by default; and ADR-0045 §5's `executes_analysed_code`, which that ADR rules and which is **also not yet implemented** — both land on one surface so the next backend cannot omit either. Three enforcements: a registry test in the ADR-0045 parity-test pattern; the merge pass **refuses, naming the analyzer**, to merge records from an undeclared producer rather than falling through; unknown declaration keys raise, as `user_config.py` does for unknown settings.

The §5 sentence "until a per-attribute measurement shows otherwise" is now load-bearing rather than aspirational: **no built-in default exception and no `authoritative_for` entry may be added except by citing a committed table produced by the backend-agreement instrument.** Until that instrument exists, the default cannot legitimately change.

### 11. On an edge, `src`, `dst` and `edge_type` are identity, not attributes

For a node, `kind` may hold two values. For an edge, `edge_type` is inside `edge_key`, and `src` and `dst` name the merged endpoints. A disagreement on any of the three is therefore **two edges**, each with its own `origin` — that *is* the provenance of the disagreement, and it is coexistence at the record level for exactly the fields that define what an edge is. "Duplicate edges become one edge" holds when the three agree, which is why §7's first prerequisite is a prerequisite. The same rule settles external targets: a backend that resolves a target to an in-repo node and one that emits an external stub disagree on `dst` and stay two edges; two external stubs merge iff their canonical external identity (ADR-0035 §"External-symbol identity key") matches. SCIP emits no external edges today, so this clause is written for the next backend.

### 12. Tests run on recorded producer-shaped input, never on the incumbent arm's output fed back

rust-analyzer executes the analysed crate's `build.rs`, is opt-in, and will not run in CI. The parity test that fed `rust.py`'s own spans back into the helper passed for months over a feature measuring 0 of 52 (INV-dolud); a control that cannot fail is not a control. Every merge, parity and contract test therefore imports **recorded** fixtures — the emitted index or its parsed Definition `range` / `enclosing_range` / `symbol_roles` per fixture crate, with the producer version pinned (the #1044 `RUST_ANALYZER_DEFINITION_LINES` pattern) — and a lint refuses a test in those families that calls the incumbent analyzer to build the alternative arm's input.

*Landed (WI-romuh).* The recordings live in `packages/hypergumbo-lang-rust-analyzer/recorded/` (`recorded_rust_analyzer_1_94_0.py`: the sample crate inline, and aardvark-dns — source at upstream `4444d90fee` committed beside its raw `index.scip`, 169 global definitions). `scripts/check-recorded-producer-input` is the lint: an AST taint walk from incumbent-producer calls (derived from the §10 declarations) to cross-backend consumers, with `@pytest.mark.incumbent_fed("<reason>")` for the two extraction contracts that feed on purpose. **Correction to §3's number:** under the declared anchors on the committed input, the live tree-sitter arm pairs **148 of 169** SCIP definitions, none ambiguous — not 138; the difference is 10 struct fields the incumbent now emits. The 21 leftovers are 18 module namespaces, two `type` aliases and one `static` for which the tree-sitter arm emits no Symbol. The committed number is the one WI-kokiz reproduces.

### 13. `confidence` is not arbitrated by precedence; corroboration is a declared level

*(Owner ruling 2026-09-18.)* Measured on the 92 agreed edges: tree-sitter's `calls` carry 0.4–0.85 `evidence_derived`; SCIP's `references` carry 0.85 `emitter_constant`, uniformly. Incumbent-first would leave an edge the type-aware backend just confirmed at 0.5 — inverting ADR-0012's founding premise that AST edges are *upgraded* when type resolution confirms them — and "take SCIP's" would launder a hardcoded constant into evidence. Three cases, only one of which combines:

1. **Sole producer** — the edge keeps its producer's `confidence` and `confidence_source`. Nothing to decide.
2. **Two producers, same inference pathway** (matching `evidence_type` candidates) — agreement by the same method is not new evidence. Precedence applies, incumbent-first.
3. **Two producers, distinct pathways** for one `(src, dst, edge_type)` — the merged `confidence` is a **declared corroboration level**, not a formula over the inputs: built-in **0.95** when one pathway is type-resolved (ADR-0012's own number), declared in the same per-attribute table as §5 so it is configurable; `confidence_source` takes a **new value `corroborated`**; both candidates stay in the provenance slot; `CONFIDENCE_MODEL` bumps `hypergumbo-evidence-v2.0 → v2.1`.

Why not `max` or noisy-OR: `max` returns the emitter constant; noisy-OR assumes independent detectors, and both backends read the same source text, so agreement on a plain direct call is near-certain and the formula would push everything to ≈0.98. A declared level states what can be defended — *a type-resolved pathway confirmed this* — and is how ADR-0012 already reasoned: a category with a number. `rank_score` initialises from the corroborated value (ADR-0039), so a corroborated edge ranks above its uncorroborated neighbours. Deliberately untouched: the SCIP arm's own 0.85 constant on its sole-producer edges — ADR-0039 permits a labelled constant, and making it evidence-derived is that backend's work.

### 14. A resolved edge demotes a same-site stub — by `rank_score`, recorded on the stub, never deleted

*(Owner ruling 2026-09-18: "yes, by rank_score".)* At 257 call sites on the measured crate tree-sitter emitted a `calls` edge to an external stub; at **33 of them** SCIP has a first-party reference on the same line — the syntactic backend said "outside the crate", the type-aware one says "this item, here". Under §11 `dst` is identity, so both edges are kept, and §11 alone leaves the wrong answer at equal standing with the right one — the tension WI-gojum's July note recorded, and where leverage against magnet edges lives.

A resolved (`is_resolved=True`) first-party edge at `(src, line)` **demotes** a same-site edge whose `dst` is an external stub, by `rank_score` — ADR-0039 ruling 3 puts ranking adjustments there and never on `confidence`. The stub keeps its confidence, its origin and its place in the graph; **no edge is deleted** (never pin a removal). The merge pass **stamps the supersession on the stub as a positive claim** — the superseding edge's id and producer — so a consumer can see *why* it ranks low; a bare lower number is an absent-versus-empty reading waiting to happen. Same `(src, line)` only, no cross-line inference; a site where both producers point at stubs is untouched. The 33 is an **upper bound** — a co-located field or type reference on a call's line is not the same call — and the implementing row measures the true count before claiming it.

## Consequences

**Positive.** Under a two-backend run, one node per declaration and one edge per relationship; nothing either backend saw is lost; disagreement is data, not a silent pick; consumers are unchanged by default; tree-sitter-only users see no change at all.

**Negative.** A schema bump and a new registered slot; a new stage in the ADR-0043 DAG; a config key surface; and a per-attribute default table whose *values* still have to be measured — this ADR pins the mechanism and the built-in, not the eventual table. §10–§12 add a declaration surface every backend must fill, a measurement instrument that must exist before any default changes, and a fixture discipline; and the results-cache key currently ignores which backends ran (WI-gojum sub-component 3, split out below), which silently corrupts any measurement until fixed.

**Measured baseline to re-verify on landing** (`aardvark-dns`, b1008d3b67, two arms): 169 SCIP + 138 tree-sitter Rust nodes → expected 138 merged + 31 SCIP-only + 0 tree-sitter-only; the 92 shared call edges collapse once §7's first fix lands; the 52 free functions show `kind` alternatives from both producers until §7's second fix lands.

## Alternatives considered

1. **Coexist at the record (ADR-0012 §Step 3 as written).** Rejected: doubles every Rust callable and the in-crate call graph under a two-backend run.
2. **Replace (WI-gojum sub-component 1).** Rejected: lossy in both directions — 268 external-stub edges on one side, 79 field references on the other, no counterparts.
3. **Merge, one value wins.** Rejected: a new silent-wrongness surface on attributes where the type-aware backend is measurably wrong.
4. **No pass — an access abstraction that re-arbitrates on every read.** Rejected: the merge key is whole-table by nature, so "recompute every time" is O(N) per read and "memoise" is a lazily-run pass with worse determinism; 641 direct attribute reads across 89 files would have to be intercepted; and membership must be settled before the artifact is written. Arbitration alone may be lazy (§4); recognition of the pair may not.

## Open questions

1. The exact serialization of the provenance slot (§6) — pinned in semantics, open in spelling.
2. Whether a consumer may request an alternative arbitration in-process (§4 says: opt-in read of the candidates, no second default; the API shape is open).
3. The measured per-attribute correctness table that sets the built-in default's exceptions (§5).

## Tracker items

- WI-zapuk — SCIP edges carry the role-derived edge type (§7, first prerequisite).
- WI-gapup — SCIP `kind` from the producer's declaration, else the descriptor chain (§7, second prerequisite). **Landed.**
- WI-kokiz — the merge pass (§3); INV-lodum's cure lands here.
- WI-binis — attribute-level provenance slot + arbitration property + schema bump (§1, §4, §6).
- WI-hukuf — the `config.toml` arbitration default (§5).
- WI-hohuh — producer contract: merge anchor, measured authority, `executes_analysed_code` on one surface; the pass refuses the undeclared (§10). Blocks WI-kokiz. **Landed.**
- WI-dajif — the backend-agreement instrument whose committed tables are the only evidence that may change a default (§5, §10). Blocks WI-hukuf.
- WI-romuh — recorded producer-shaped fixtures and the lint that forbids feeding the incumbent's output back (§12). Blocks WI-kokiz. **Landed.**
- WI-givib — results-cache key folds in the resolved backend set (WI-gojum sub-component 3, split out). Blocks WI-dajif.
- WI-lihis — same-site supersession by `rank_score`, stamped on the stub (§14). Blocked by WI-kokiz.
- WI-binis additionally carries §13: the `corroborated` source value, the combining case in the arbitration property, `CONFIDENCE_MODEL` v2.1. WI-gapup is widened to enum variants (9 of 9 pair SCIP `class` against tree-sitter `field`).
- WI-gojum — parked host; sub-component 1 is superseded by this ADR, sub-component 2 is WI-kokiz, sub-component 3 is WI-givib, sub-component 4 is WI-sobig's question.

## References

- `docs/adr/0012-pass-unification-and-multi-fidelity.md` §Step 3 — the superseded sliver.
- `docs/adr/0035-stable-id-v6-identity-contract.md` §4 — the presentation-time line §2 narrows.
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py` — `dedup_logical_synthetic_identities`, `split_within_file_stable_id_collisions`: the pass shape and the untouched splitter.
- `packages/hypergumbo-core/src/hypergumbo_core/scip/calls.py`, `scip/index.py` — the two producer fixes.
- `packages/hypergumbo-core/src/hypergumbo_core/user_config.py` — the preferences loader the default extends.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust_scip.py` — the parity helper this design retires as a mechanism (its span-matching insight becomes the merge key).
