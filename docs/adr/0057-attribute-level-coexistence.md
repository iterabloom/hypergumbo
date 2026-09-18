<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0057: Multi-Backend Coexistence at the Attribute, Not the Record

Status: Accepted — design adopted by the owner 2026-09-18 in session; implementation is sequenced under the five rows in §Tracker items and is NOT yet authorised to start (WI-gojum stays parked until the owner unparks it).

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

Nothing a producer emitted is discarded. This keeps ADR-0012's principle — both backends run, provenance is tracked, nothing is thrown away — and changes only the granularity, from the record to the attribute. It is what the codebase already does for one field: `confidence` 0.85 per tree-sitter and 0.95 per SCIP with `confidence_source = composite` (ADR-0039 ruling 2).

### 2. There are three times, and resolution belongs to the middle one

**Identity time** (minting `id` / `stable_id` — ADR-0035: never resolve here), **consumption time** (every in-pipeline reader that branches on an attribute), and **presentation time** (projections). Contested attributes bite at consumption time. Therefore arbitration cannot be deferred to a view; it must be **declared, applied once, early, with the alternatives preserved** — the shape ADR-0043 §6 tolerates and the compute-twice-never-reconcile defect it forbids.

### 3. Recognising the pair is a pass, at the top of Phase C

A declared **merge pass** runs after Phase B relativization and **before the first Phase-C consumer** (`refine_frameworks`), because linkers read `kind` and `meta`. It has to be a pass, not an on-demand facade, for three reasons that are not taste: the merge key is **whole-table** (per file, the smallest tree-sitter item whose span contains the SCIP name token — a record cannot compute that about itself, and memoising it lazily makes the materialisation depend on which consumer asked first); it **rewrites identity** that 296 direct `.src`/`.dst` reads and 345 direct `.kind` reads across 89 files key on; and **membership must be settled before serialize** (ADR-0043 R1, §8) because the artifact — not an in-process API — is the product.

The merge key is (path, base name, span containment); neither `id` nor `stable_id` qualifies (§Context). The merged record gets a **new `id`**, every edge from **both** producers is rewired to it, and `edge_key` is reset so `deduplicate_edges` recomputes it — the exact mechanics of `dedup_logical_synthetic_identities` (`analyze/base.py`). The merged span is the **item** span (rust-analyzer's `enclosing_range`, populated on 20 of 20 Definitions), so INV-lodum's cure lands inside this pass rather than as a separate importer edit. The pass is a **no-op when one producer emitted**: a tree-sitter-only run is byte-identical before and after.

It is a pass, not a traversal: one linear loop over symbols, one over edges, no edge-following. On `aardvark-dns` that is 756 nodes and 1,282 edges.

### 4. Arbitration is a property, with one stamped default

The scalar a consumer reads is produced by a **per-record arbitration property**: a pure function over the preserved candidate set, O(candidates), needing no whole-table access and — because it is pure over data the record already holds — **no invalidation contract** (contrast `edge_key`, the codebase's memoised field, whose cost is exactly that contract: `ir.py:994`, `:1256`, `:2021`).

There is **one default**, stamped so that ranking, slicing, finalize and serialization all see the same value. Laziness does not remove the default; it only hides where it lives. Alternative policies are **opt-in reads of the candidate set**, never a second default. The scalar slot on `Symbol` / `Edge` keeps its current type, so the 89 files of consumers are unchanged by default.

### 5. The default is a preference in `config.toml`

The arbitration default lives in ADR-0045's **preferences** tiers — `$XDG_CONFIG_HOME/hypergumbo/config.toml` and the project tier — resolved through ADR-0045 ruling 4's chain (CLI flag > environment > project > user > built-in). It executes nothing, so it is not a trust key and is allowed in both tiers. The **built-in default is incumbent-first per attribute** — tree-sitter before any SCIP/LSP backend — until a per-attribute measurement shows otherwise. "Type-aware backend wins" is **refused** as the blanket built-in: on the one attribute measured, `kind`, the type-aware backend is wrong on 52 of 52 free functions. The key is a per-attribute table so measured exceptions can be declared without flipping the whole default.

### 6. Provenance gets a registered, schema-versioned slot

`origin` is record-level and `meta` is not a registry (`parent_type` is written by four analyzers, read by a linker, declared nowhere), so attribute provenance needs its own slot on `Symbol` and `Edge`, with `# axis:` declarations, a `SCHEMA_VERSION` patch bump, and `docs/schema.json` / `docs/concept-axes.md` regenerated. The **semantics** are pinned here; the spelling is the implementing row's (proposed: `attribution: {field: [pass_id, …]}` plus `alternatives: {field: [{value, origin}]}` present only for contested fields; absence of both means single producer — today's records are unchanged). Edge `evidence_type` is a candidate set like any other attribute: an edge seen by two inference pathways is precisely ADR-0028's composite case, not an exception to it.

### 7. Two producer fixes are prerequisites, not part of the mechanism

- **SCIP edges must carry the edge type SCIP already knows.** `Occurrence.symbol_roles` is a `SymbolRole` bitfield; `calls.py` reads it only to exclude Definition. Until a call-position reference is emitted as `calls`, the 92 shared edges are not duplicates under `edge_key` and the merge can fold nodes but never edges.
- **SCIP `kind` must come from the descriptor chain** (parent TYPE → method, parent NAMESPACE → function). Arbitration only helps when at least one side is right; fixing the producer removes the contest on 52 of 52 instead of adjudicating it.

### 8. What this makes moot

`split_within_file_stable_id_collisions` is **untouched**. It runs after the merge, so no same-`(path, stable_id)` cross-backend group ever reaches it, and it keeps doing its real job on repeated call sites. INV-lodum's clause "the splitter would re-mint the SCIP record" is true only if the span fold lands *without* the merge; with the ordering in §3 the question does not arise.

### 9. This is not a Rust feature

Attribute-level provenance is the rule for **any** pass that enriches a record another pass created — the pyright backend WI-nanom names next, and in principle every linker. Scoping it to Rust would undersell it and shape the slot wrong.

## Consequences

**Positive.** Under a two-backend run, one node per declaration and one edge per relationship; nothing either backend saw is lost; disagreement is data, not a silent pick; consumers are unchanged by default; tree-sitter-only users see no change at all.

**Negative.** A schema bump and a new registered slot; a new stage in the ADR-0043 DAG; a config key surface; and a per-attribute default table whose *values* still have to be measured — this ADR pins the mechanism and the built-in, not the eventual table.

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
- WI-gapup — SCIP `kind` from the descriptor chain (§7, second prerequisite).
- WI-kokiz — the merge pass (§3); INV-lodum's cure lands here.
- WI-binis — attribute-level provenance slot + arbitration property + schema bump (§1, §4, §6).
- WI-hukuf — the `config.toml` arbitration default (§5).
- WI-gojum — parked host; its sub-component 1 is superseded by this ADR, sub-component 2 is WI-kokiz, sub-component 3 (cache key ignores the backend) is unaffected and still open, sub-component 4 is WI-sobig's question.

## References

- `docs/adr/0012-pass-unification-and-multi-fidelity.md` §Step 3 — the superseded sliver.
- `docs/adr/0035-stable-id-v6-identity-contract.md` §4 — the presentation-time line §2 narrows.
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py` — `dedup_logical_synthetic_identities`, `split_within_file_stable_id_collisions`: the pass shape and the untouched splitter.
- `packages/hypergumbo-core/src/hypergumbo_core/scip/calls.py`, `scip/index.py` — the two producer fixes.
- `packages/hypergumbo-core/src/hypergumbo_core/user_config.py` — the preferences loader the default extends.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust_scip.py` — the parity helper this design retires as a mechanism (its span-matching insight becomes the merge key).
