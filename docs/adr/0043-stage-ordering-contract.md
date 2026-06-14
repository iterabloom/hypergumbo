<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0043: Stage-Ordering Contract for `run_behavior_map`

- Status: **Accepted**
- Date: 2026-06-12
- Supersedes: —
- Superseded by: —
- Related: ADR-0033 (Spec-vs-Data Validator Stage — its ratchet matrix is the substrate for ruling §7's two-pass validation, and its referential-integrity check is the post-filter pass that moves into finalize), ADR-0037 (Edge Resolution Semantics — its single edge-finalization verdict is the stage ruling §4/§6 places *after* filtering), ADR-0035 (stable-id v6 identity contract — the identity-hashing stage that ruling §2 pins strictly after early-relativize), ADR-0034 (id-construction discipline). Tracker items: see the "Tracker items" section at the end.

**Decision provenance.** Unlike ADRs 0035–0042, this ADR does **not** record a fresh human ruling. It records an *engineering artifact* — the stage-ordering contract for the analysis pipeline — derived from those rulings and from the `META-jalur` root-cause family. The design-decision register (2026-06-10 interview, `~/hypergumbo_lab_notebook/decision_register_06102026.md`) states: *"Remaining open human decisions … none. The stage-ordering ADR (META-jalur child) … [is an] engineering artifact to be authored with [its] implementing fixes, governed by [the] rulings above."* Every resolution below cites a prior accepted ruling or direct code evidence; **no new human-level policy is introduced.** This ADR fixes the *order* of the pipeline and names its invariants; the *code* is carried by the implementing fixes (`run-lifecycle:F1`, `synthetic:F3`, `validator:F3`, `entrypoint:F4`, `projection:F1`), tracked under `WI-pozur` below.

## Context

### The `run_behavior_map` stage order is a shared seam with ~ten owners

`run_behavior_map` (`packages/hypergumbo-core/src/hypergumbo_core/cli.py`, roughly lines 7679–8400 at the time of writing) executes the analysis pipeline as a long straight-line sequence of ~29 stages: collect (run analyzers) → resolve deferred refs → relativize paths → framework refinement → route materialization → linkers → edge dedup → boundary-node synthesis → fingerprint stamping → supply-chain classification → tier filtering → noise filtering → centrality ranking → dict conversion → repo-fingerprint stamping → metrics / entrypoints / handler-slices / supply-summary / sketch → limits population → budget tiers → compact → `validate_ir` → serialize.

The correctness strategy (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`, "Shared seams", seam (a)) identifies this stage list as a collision point with about ten distinct fix-families wanting to **insert or move** a stage: `synthetic-node-stamping` (edge finalization), `run-lifecycle-provenance` (the finalize stage), `projection-finalize` (the late projection stages), `identity-hash-coarseness` / `identity:F5` (fingerprint + shape_id stamping), `supply-verdict-truth` (tier classification + filtering), `entrypoint-surface-contract` (noise-filter exemption), `analyzer-emission-parity` / `dispatch` (relativization of `meta` ids), `validator-false-all-clear` (validation placement), `file-anchor-accounting` (file synthesis), and `vocab-axis-governance` (central derived-meta). Ten families editing one straight-line region independently is a structural hazard: each one's correctness depends on *where in the sequence* it lands, and the sequence has no declared contract. This ADR is the single owner the seam requires.

### Four ordering defects in the current sequence

The current order produces four concrete, code-verified defects (labelled C2–C5 below by their resolutions; C1 is the validation-substrate defect):

* **C1 — validation runs over a filtered substrate.** `validate_ir` runs at the very end, *after* tier filtering and noise filtering. Per the strategy's `validator-false-all-clear` analysis, this means defects that filtering removed are never validated — the gate reports "all clear" over a substrate from which the violations were already deleted. The validator's denominator silently excludes the filtered cohort.
* **C2 — boundary synthesis precedes filtering, leaving dangling sources.** `create_boundary_nodes` mints boundary endpoints *before* tier and noise filtering run. When filtering then deletes a node that a freshly-minted edge pointed *from*, the edge is left with a dangling `src`. This is the residual dangling-source population ADR-0037 explicitly left as sibling work ("endpoint closure for the residual dangling-src population").
* **C3 — the noise filter erases entrypoint-bearing symbols before they are detected.** `filter_noise_kinds` runs before `detect_entrypoints`. Manifest-script and other entrypoint-bearing symbols are removed as "noise" before the entrypoint pass can see them, so they can never be emitted as entrypoints.
* **C4 — relativization is incomplete and mistimed.** `_relativize_ir_paths` rewrites `Symbol.id`, `Symbol.path`, `Edge.src`/`dst`, and `UsageContext.path` — but **not** id-bearing `meta` keys (e.g. `meta.handler_ref`), and it runs *before* the linker and boundary-synthesis stages, which can themselves introduce absolute paths. Verified: the relativization body touches only the `id`/`path`/`src`/`dst`/`uc` fields and never `meta`. The result is a second ad-hoc normalize spliced in after the linkers, and absolute paths leaking through `meta`.

### Compute-once-never-reconcile: five uncoordinated finalizers (C5)

`META-jalur` ("values computed once from placeholders, never reconciled by a finalize pass") is the super-cluster behind the late stages. At least five fields are finalized independently with no single reconcile point: the repo-fingerprint stamp, the `analysis_runs` population (whose `run_signature` is hashed from placeholder inputs — `META-hufaz`, and skipped on override-analyze analyzers — `WI-mipul`), the limits / `partial_results_reason` scan, the budget-tier projection, and the compact projection. Each reads or rewrites state that a *later* stage then changes, so the emitted artifacts can disagree with one another and with the substrate they claim to describe. `META-jalur`'s invariant is explicit: *"the pipeline needs a finalize/reconcile step so run_signature, compact-shrunk dependent fields, edge confidence, and the validation_report agree with the substrate actually emitted."*

### Scope boundary

This ADR fixes the **order** and names the **invariants**. It does **not** write the code, and in particular it does **not** specify the internal API of the finalize stage — that stage does not yet exist and is co-designed by `run-lifecycle:F1` together with `projection-finalize`, `declared-fields`, and `confidence` before any of them implements independently (strategy "Compute-once-never-reconcile"). Substrate counts cited in the strategy (the dangling-source population, the `--include-docs` defect baseline, the five finalizers) are pinned to the frozen analysis substrate and re-measured by the implementing PRs; this ADR makes **no** validated-improvement claim.

## Decision

### 1. The target stage DAG

The pipeline is organised into six ordered phases. Within a phase, stage order is free unless noted; across phases, the order is the contract.

* **A — Collect.** Run analyzers; resolve deferred `UsageContext` references over the complete symbol table.
* **B — Early-relativize.** A single, *total* relativization stage (ruling §2). Post-condition: no stage after Phase B emits an absolute path.
* **C — Idempotent meta sweep.** Framework refinement, framework-metadata enrichment, route materialization, class-based-view expansion, linkers, central derived-meta (`vocab:F3`, pinned after synthetic stamping), supply-chain classification, route-tier promotion. All Phase-C stages are additive/conditional and **idempotent** (ruling §3); their order within the phase is free.
* **D — Filter to the final node set.** Edge dedup, then tier filtering, then noise filtering *with* the entrypoint-exemption predicate (ruling §5). This phase fixes the surviving node/edge membership (ruling §4).
* **E — Terminal synthesis + edge finalization.** Boundary-node synthesis (minted only for endpoints that survived Phase D) and the single edge-finalization verdict of ADR-0037 (`is_resolved`/`dst_ref`/kind-fold stamped exactly once over the final edge set).
* **F — Rank, finalize, serialize.** Fingerprint + central `shape_id` stamping (pinned strictly after Phase B so it hashes relative paths — `identity:F5`); centrality ranking; dict conversion; read-only derivations (metrics, entrypoints, handler-slices, supply-summary, sketch); the single **finalize** stage (ruling §6); projections as consumers; serialize.

### 2. Early-relativize is total and terminal (resolves C4)

Relativization is promoted to a single Phase-B stage that rewrites **every** id-bearing field, including id-bearing `meta` keys (`meta.handler_ref` and any other id-bearing meta key). Its contract is a **post-condition**, not a fixed key list: *no stage after early-relativize may emit an absolute path.* The implementing fix enumerates the id-bearing `meta` key set and adds a validator predicate that fails on any downstream absolute path — so an incomplete enumeration is caught by the gate rather than silently re-introducing C4. Producers that legitimately mint paths later (linkers, boundary synthesis) route through one **idempotent** re-relativize inside finalize (Phase F), replacing the current ad-hoc second normalize. Identity scope-chain and `shape_id` hashing (ADR-0035 / `identity:F5`) are pinned strictly after Phase B so they hash relative paths.

### 3. The meta-population sweep is idempotent and order-free within its phase

Every Phase-C stage performs only additive or conditional mutation (it never depends on *not* having run before), so the sweep is safe to run more than once and the stages may be reordered within the phase without changing output. This is the property that lets the finalize re-relativize (§2) and any future re-derivation run without a bespoke ordering argument.

### 4. Filter to the final node set before boundary synthesis and edge finalization (resolves C2)

Tier filtering and noise filtering (Phase D) run **before** boundary-node synthesis and edge finalization (Phase E). Boundary nodes are minted only for endpoints that survive filtering, so the post-filter dangling-source class is closed by construction. This places ADR-0037's already-accepted single-finalization verdict at the correct point in the sequence; it introduces no new resolution policy.

### 5. The noise filter exempts entrypoint-bearing symbols (resolves C3)

The noise filter carries a declared **exemption predicate**: entrypoint-bearing symbols (e.g. `entry_role=script` and the per-language entrypoint concepts tracked under `WI-tosul`) are exempt from `_is_noise` and survive into `detect_entrypoints`. The exemption set is *enumerated*, not discretionary. This is the single noise-filter edit; `entrypoint:F4`'s exemption and any file-anchor carve-outs land as one change with an explicit predicate list. `detect_entrypoints` is **not** moved.

### 6. A single finalize stage is the only pre-serialization reconcile point (resolves C5)

One **finalize** stage is the single point at which placeholder-derived fields are reconciled against final state before serialization. It subsumes the currently-scattered finalizers: the repo-fingerprint stamp and the skipped-files → `limits.partial_results_reason` scan fold into it; it recomputes `run_signature` from the final `AnalysisRun` fields (`META-hufaz`), stamps `pass_version`/emission counts and backstops `origin_run_id` including on override-analyze analyzers (`WI-mipul`), and runs the idempotent re-relativize backstop (§2). Budget-tier and compact projections become **consumers** of the finalized map via one shared re-derive helper with a deterministic lexicographic tie-break (so output does not depend on `PYTHONHASHSEED`). This ADR fixes the stage's *position and responsibilities*; its internal API is designed by `run-lifecycle:F1` (the finalize carrier). That design — deferred when this ADR was first authored — is **now specified in §6.1 below** (amendment, 2026-06-13).

### 6.1 Finalize internal API (amendment, 2026-06-13)

**Decision provenance (distinct from this ADR's parent body).** Unlike the rest of
ADR-0043 — which §"Decision provenance" notes records *no* fresh human ruling — this
subsection **does** carry one: the cross-family finalize internal-API co-design
(strategy "Compute-once-never-reconcile"; open decision #4) was produced and
**ratified by the lead on 2026-06-13**. It is the explicitly-deferred second half of
§6, not new policy about *what* finalize does. Working artifact:
`~/hypergumbo_lab_notebook/finalize_stage_codesign_06132026.md`.

**API shape.** A new module `analyze`-adjacent `finalize.py` exposing one public
entry point whose **body is the ordering contract**:

```python
def finalize(ctx: FinalizeContext) -> FinalizedMap: ...
```

- `FinalizeContext` is a mutable carrier (`symbols`, `edges`, `usage_contexts`,
  `analysis_runs` [dicts — note the `to_dict()` key is `"pass"`, not `"pass_id"`],
  `behavior_map`, `limits`, `repo_root`, `config`, a `PassMetadataLookup`, and a
  `violations` accumulator) threaded through the sub-steps.
- Each sub-step is a free function `_finalize_<concern>(ctx) -> None` that mutates
  `ctx` in place — identical in shape to the existing house pattern
  (`stamp_symbol_fingerprints`, `populate_synthetic_class_b_identity`,
  `_relativize_ir_paths`). The orchestrator body is a flat, hand-ordered call list;
  **the source order IS the schedule.**
- `finalize()` returns a `@dataclass(frozen=True) FinalizedMap` — the single
  reconciled view §8's round-trip test asserts on. Immutability is **shallow**
  (rebind raises; consumers contract not to mutate the inner dict).
- A **registry / Protocol / topological-scheduler is explicitly rejected**: finalize
  is a closed set with a shallow (~four-edge) dependency graph fixed by §6's
  responsibility list, so a scheduler is apparatus the graph does not need. The two
  load-bearing orderings are pinned by call-site adjacency **and** two ~5-line
  white-box guard tests (R2, R3 below).

**Ordered sub-steps.** `finalize()` runs, top to bottom:

| # | sub-step | responsibility / family | depends on |
|---|----------|-------------------------|-----------|
| — | *(entry precondition)* final node/edge set fixed (Phase D filter + Phase E boundary/edge-final complete); finalize never changes membership | R1 | — |
| 1 | `_finalize_re_relativize` | §2 idempotent re-relativize backstop (replaces the ad-hoc second normalize) | precondition |
| 2 | `_finalize_stamp_run_lifecycle` | `WI-mipul`: backstop `pass_version`/`config_fingerprint`/`toolchain`/`origin_run_id` (incl. override-analyze) | 1 |
| 3 | `_finalize_recompute_run_signature` | `META-hufaz`: re-hash from **final** AR fields | **2 (hard)** |
| 4 | `_finalize_repo_fingerprint` | subsumes the scattered repo-fingerprint stamp | precondition |
| 5 | `_finalize_emission_counts` | per-analyzer `files_analyzed`/`files_skipped` over the final substrate (per-run by `origin_run_id`) | precondition |
| 6 | `_finalize_skipped_into_limits` | subsumes the skipped-files → `limits.partial_results_reason` scan (crashed-pass reason wins) | 5 |
| 7 | `_finalize_confidence_aggregates` | confidence: recompute aggregate sums over the final EP/datamodel set (per-edge `Edge.confidence` left untouched, ADR-0039) | precondition |
| 8 | `_finalize_commit_dicts` | write reconciled dicts into `behavior_map` so every downstream reader sees one view | all mutators |
| 9 | `_finalize_declared_fields` | declared-fields writer/population-contract over the **final stamped** substrate (read-only) | 2, 3 |
| 10 | `_finalize_referential_integrity` | §7 FK predicate (edges ⊆ nodes; `is_resolved ⇒ dst ∈ nodes`) + ADR-0039 per-edge range — **structurally LAST** | all preceding |
| — | `re_derive_view(finalized, selected_ids)` — **not a sub-step**; the pure helper budget-tier/compact projections call *after* their shrink loops | projection-finalize | `finalize()` returned |

**Dependency rules.** R1 entry-precondition (finalize never mutates membership);
**R2 (hard):** run_signature recompute strictly after the AR-field stamp (else it
hashes create-time placeholders — the META-hufaz defect); **R3 (hard):** the FK /
referential-integrity check is the last violation-appending sub-step (validates
exactly the substrate that serializes, §7); R4 declared-fields after the stamp
sub-steps; R5 re-relativize first; R6 limits after counts; R7 projections are strictly
downstream consumers of the frozen handle (a projection cannot re-introduce a
reconciled value); R8 the remainder is order-free (the §3 idempotency property carried
into Phase F). R2 and R3 are each pinned by a white-box test, not just by position.

**Determinism.** `finalize()` itself iterates the existing list order and never
iterates a set to drive output; `violations` are sorted before the report is built.
The only `PYTHONHASHSEED` exposure lives in the **projections** and is removed by
`re_derive_view`, which sorts every set into a lexicographically-ordered list before
iterating and uses sorted-by-id as the tie-break at every decision point. Guarded by a
subprocess test asserting byte-identical artifacts under `PYTHONHASHSEED=0` and `=1`.

**Ratified sub-decisions.** (4) `config_fingerprint` on override-analyze runs:
**backstop-with-violation** — retain the default only when no better value is
available and record a violation, keeping `WI-mipul`'s broken-cache-key concern
visible rather than silently papering over it. (5) emission-counts source of truth:
**per-run, counted by `origin_run_id`** over the final substrate. (6) `FinalizedMap`
immutability: **shallow `frozen=True`** plus a consumer-side no-mutation contract.

**Phasing — `run-lifecycle:F1` is the carrier.** F1's PR lands the module + the
orchestrator spine + the fully-implemented run-lifecycle sub-steps, with the other
three families' sub-steps as **documented stubs** (confidence: no-op; declared-fields:
the existing `validate_ir` subset; referential-integrity: the lifted existing
`validate_ir` call, structurally last from day one), so F1 merges green **without**
them. `projection-finalize`, `declared-fields`, and `confidence` then each fill one
**named slot with zero orchestrator change** — this is what dissolves the seam-(a)
merge-collision hazard. Per the §"Sequencing constraint", finalize still lands **after**
the **reader-half** of seam (b) — the `py.py` scope-stack / call-resolution rewrite
(`WI-jafat`, T0, **merged**) — and the Phase D/E reorder (`WI-pozur`, **merged**), so the
entry precondition (substrate final on entry) holds. The **producer-half** of seam (b) —
the stable-id v6 hash bump (`WI-gitun`, T1) — is *structurally independent* of finalize's
landing: finalize reads `node.id` and the run-signature inputs
(`pass_id`/version/config/toolchain), neither of which v6 changes, so finalize's v6
dependency is **diff-churn coordination, not correctness or purpose** (the one predicted
cross-run diff — the §8 round-trip golden — regenerates when v6 lands). F1 may therefore
land as a T0 ahead of v6.

**Closure.** Unchanged from §8: the serialization round-trip property test (every
emitted artifact reflects one reconciled view) is the closure evidence for `META-jalur`
(+ `META-hufaz`, `WI-mipul`); it lands with the implementing fixes, not this amendment.

### 7. Validation runs at two points, with denominator-scope disclosure (resolves C1)

The C1 defect is that the single `validate_ir` stage runs *after* tier and noise filtering, so defects that filtering removed are never validated and the validator's denominator silently excludes the filtered cohort. The contract runs validation at two points:

* a **pre-filter** pass over the full (pre-tier, pre-noise) substrate, emitting per-substrate, shrink-only ratchet counts so filtered-out defects stay visible; and
* a **post-filter referential-integrity check** (edges ⊆ nodes; ADR-0037's `is_resolved ⇒ dst ∈ nodes` foreign-key predicate) as the **last sub-step inside finalize** (§6), validating the substrate actually emitted.

`validation_report` records **both** counts with an explicit `denominator_scope` tag disclosing which substrate each is over (`WI-niluv`).

Both new pieces are deferred implementation, not existing behaviour. What exists today is only the `validate_ir` *stage* (ADR-0033) running at the post-filter position — that placement is the C1 defect, and the stage runs axis/writer-contract/cross-field/verdict-enum/id-format predicates, **not** a referential check. The **referential-integrity / FK predicate** is ADR-0037 future work (ruling 5: the validator "gains the foreign-key predicate"), which this contract places as finalize's last sub-step. The **pre-filter pass** is owned by `validator:F3` (Wave 2, deferred); it reuses the shrink-only ratchet baselines that `validator:F1` (Wave 1, realized) established for the flag-varied substrate matrix, extending them to the literal pre-tier/pre-noise substrate. This ADR states their position and substrate-scope contract, not their implementation.

### 8. Closure gate: a serialization round-trip property test

`META-jalur` closes when `run_signature`, edge finalization, validation, and the compact/budget projections all route through the one finalize stage and a **serialization round-trip property test** asserts that every emitted artifact (the main map, budget tiers, compact, and `validation_report`) reflects one reconciled view of the substrate. This property test is the closure evidence for `META-jalur`; it lands with the implementing fix, not with this ADR.

### Sequencing constraint

This reorder is the owner of seam (a), but it sequences **after** the identity scope-stack rewrite that owns seam (b) (`py.py` call-resolution region; strategy "Shared seams"). The identity rewrite lands first and all other families — including this reorder — rebase as additive patches. Landing the reorder before the identity rewrite would invert the rebase order.

## Consequences

### Positive

- The dangling-source class (C2) is closed by construction rather than by a post-hoc sweep.
- Entrypoint-bearing kinds (C3) become emittable.
- Validation (C1) is honest about its denominator: defects removed by filtering are still counted by the pre-filter pass, and the post-filter pass keeps its referential-integrity role.
- A single finalize point (C5) makes `META-jalur` and its children (`META-hufaz`, `WI-mipul`) closeable, and gives `projection:F1` a real stage to consume instead of duplicating.
- `meta`-borne absolute paths (C4) are eliminated and the absence is enforceable by a downstream-absolute-path predicate.

### Negative

- The Phase D/E swap and the relativization widening touch the edit regions of ~ten families simultaneously; the reorder must land as a coordinated train, sequenced after the identity rewrite. This ADR scopes the contract and defers the code to the implementing fixes precisely to bound that risk.
- Every cross-run diff churns once when the order changes (stable_id-adjacent ordering shifts); this coordinates with the separate stable-id v6 migration, it does not precede it.

### Neutral / acknowledged

- The substrate counts behind C1–C5 (the dangling population, the `--include-docs` defect baseline, the five finalizers) are pinned to the frozen analysis substrate and re-measured by the implementing PRs. This ADR records no validated-improvement metric; any implementing PR that claims one carries the `awaits_bakeoff_validation` tag.
- The finalize stage does not yet exist; this ADR specifies its position and responsibilities as a contract and leaves its internal API to `run-lifecycle:F1`.

## Alternatives considered

1. **Move `validate_ir` alone to pre-filter.** Rejected: it would lose the final referential-integrity check over the emitted substrate. Ruling §7 keeps both passes against one predicate set instead.
2. **Keep boundary synthesis before filtering and add a post-filter dangling-source sweep.** Rejected: a second sweep re-introduces exactly the compute-twice-never-reconcile defect `META-jalur` forbids. Ruling §4 filters first instead.
3. **Per-family stage edits with no single ADR owner.** Rejected: seam (a) has ~ten independent editors; without one owner they collide and the ordering invariants are never stated. This ADR is that owner.

## Tracker items

- `META-jalur-mikob-vatub-gagam-lomib-rojud-madip-rafil` — parent super-cluster ("values computed once from placeholders, never reconciled by a finalize pass"); this ADR is its decision artifact, and ruling §6/§8 define its closure.
- `WI-pozur-fajal-roroj-lotal-hadun-niguh-lasah-lijat` — implementation of this contract (the code; coordinates the ~ten families).
- `META-hufaz-dazip-hijop-nijam-lasut-jigup-dahak-jirup` — `run_signature` recomputed from final `AnalysisRun` fields; folds into the finalize stage (§6).
- `WI-mipul-fajaf-dibab-vinap-kafur-podov-huzik-dunuh` — `pass_version`/`config_fingerprint`/`toolchain` stamping on override-analyze analyzers; backstopped by finalize (§6).
- Cross-references (not resolved here): `validator:F3` (the pre-filter validation pass, C1/§7), `synthetic:F3` (edge finalization, C2/E, governed by ADR-0037), `entrypoint:F4` (noise-filter exemption, C3/§5), `projection:F1`/`F5` (projections as finalize consumers, C5/§6).

## References

- `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` — "Shared seams" seam (a) (the stage-order collision and its ~ten owners), the Decision-ADRs item (this ADR's scope), the `META-jalur` closure gate.
- `~/hypergumbo_lab_notebook/decision_register_06102026.md` — the 2026-06-10 design-interview rulings (D1–D15) that govern this engineering artifact.
- `packages/hypergumbo-core/src/hypergumbo_core/cli.py` — `run_behavior_map`, the stage sequence this ADR reorders.
- ADR-0033 (validator stage / ratchet), ADR-0035 (stable-id v6), ADR-0037 (edge resolution / single finalization verdict).
