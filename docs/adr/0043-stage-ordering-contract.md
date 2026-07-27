<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0043: Stage-Ordering Contract for `run_behavior_map`

- Status: **Accepted**
- Date: 2026-06-12
- Supersedes: —
- Superseded by: —

> Amended in place — Core ordering contract in force; the finalize internal API has been amended repeatedly — read §6.1 (2026-06-13) for the live sub-step set, NOT the original §6 table. (§6.1 amendment 2026-06-13; recons 2026-06-15 remove sub-steps 5/7/9, vacate rules R4/R6, reverse sub-decision #7.)
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

The noise filter carries a declared **exemption predicate**: entrypoint-bearing symbols survive `_is_noise` and reach `detect_entrypoints`. The predicate is *enumerated*, not discretionary — and for `entry_role=script` it is **subset-refined** (WI-papag), because that role covers two distinct populations:

- **Entrypoint-bearing console-scripts** — pyproject `[project.scripts]` (and equivalent) entries declare a code target (`meta["entry_point"]="pkg.mod:func"`, plus a `defines_target` edge) and are detected as `CLI_COMMAND` @0.99 (`manifest_declared`). These are **exempt** and must survive; filtering them *is* the C3 defect (a package's sole declared CLI command vanishing before detection — reproduced on real repos).
- **Bare npm run-scripts** — package.json `"scripts"` (`build`/`test`/`lint`) are degree-0 shell commands (`meta["command"]`, no `entry_point`), carry the unrecognized `npm_script` concept, and are never detected as entrypoints. These are **filtered as noise** (audit-findings 0005).

The filter-time discriminator is `meta["entry_point"]` (the `pyproject_script` concept is not yet attached when Phase D runs). This reconciles audit-findings 0005 (filter npm run-scripts) with this ruling's C3 exemption (keep entrypoint-bearing scripts): both stand, each scoped to the subset it correctly describes. The single noise-filter predicate lives in `noise_filter.is_noise_symbol`; `entrypoint:F4`'s exemption and any file-anchor carve-outs land there, alongside the per-language entrypoint concepts tracked under `WI-tosul`. `detect_entrypoints` is **not** moved.

### 6. A single finalize stage is the only pre-serialization reconcile point (resolves C5)

> **Live sub-step set: see §6.1 (amendment, 2026-06-13) and its recons (2026-06-15).** The original §6 prose below lists finalizers as first authored; the amended §6.1 table is authoritative — sub-steps 5/7/9 are REMOVED, rules R4/R6 are VACATED, and ratified sub-decision #7 is REVERSED. Read §6.1 for the live set, NOT this paragraph's original enumeration.

One **finalize** stage is the single point at which placeholder-derived fields are reconciled against final state before serialization. It subsumes the currently-scattered finalizers: the repo-fingerprint stamp and the skipped-files → `limits.partial_results_reason` scan fold into it; it recomputes `run_signature` from the final `AnalysisRun` fields (`META-hufaz`) and backfills `pass_version` including on override-analyze analyzers (`WI-mipul`; the `config_fingerprint` backstop and the emission-counts recompute originally listed here are deferred/removed in `run-lifecycle:F1` — see §6.1's amended ratified sub-decisions), and runs the idempotent re-relativize backstop (§2). Budget-tier and compact projections become **consumers** of the finalized map via one shared re-derive helper with a deterministic lexicographic tie-break (so output does not depend on `PYTHONHASHSEED`). This ADR fixes the stage's *position and responsibilities*; its internal API is designed by `run-lifecycle:F1` (the finalize carrier). That design — deferred when this ADR was first authored — is **now specified in §6.1 below** (amendment, 2026-06-13).

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
  `behavior_map`, `limits`, `repo_root`, a `PassMetadataLookup`, and a
  `violations` accumulator) threaded through the sub-steps. (The `run-lifecycle:F1`
  carrier omits the originally-listed `config` field — no F1 sub-step reads it; it is added
  when a later slot-filler needs it, no orchestrator change required.)
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
| 2 | `_finalize_stamp_run_lifecycle` | `WI-mipul`: backstop `pass_version` from pass_metadata (incl. override-analyze). The `config_fingerprint` backstop is deferred to `WI-mipul`'s producer-side work (see amended ratified #4); `toolchain`/`origin_run_id` are populated upstream. | 1 |
| 3 | `_finalize_recompute_run_signature` | `META-hufaz`: re-hash from **final** AR fields | **2 (hard)** |
| 4 | `_finalize_repo_fingerprint` | subsumes the scattered repo-fingerprint stamp | precondition |
| ~~5~~ | *(removed — 2026-06-15 recon; amended ratified #5)* ~~`_finalize_emission_counts`~~ | **Removed**, not stubbed — the ratified per-run-by-`origin_run_id` recompute is unsound: `files_analyzed` is contractually a *file* count (`== profile.languages[L].files`), but the recompute yields a *node/path* count and does not close `INV-gizik` (IR-consuming passes stay 0). The real fix is a new provenance field, tracked under `INV-gizik`. Numbering keeps the gap to match the code. | — |
| 6 | `_finalize_skipped_into_limits` | subsumes the skipped-files → `limits.partial_results_reason` scan (crashed-pass reason wins) | precondition |
| ~~7~~ | *(removed — 2026-06-15 recon; reverses ratified #9, see amended ratified #7)* ~~`_finalize_confidence_aggregates`~~ | **Removed**, not stubbed — the confidence:F1/F2 IDs denote per-edge *derivation* / ranking-detection *separation* (producer-side, ADR-0039, `INV-suvil` family), which ADR-0039 keeps **out** of finalize (`Edge.confidence` left untouched). The behavior_map confidence aggregate this row described already exists over the final set: `metrics.avg_confidence` is computed *after* `finalize()` (`cli.py`) over the committed final edges, and `sketch.confidence_mass` rolls up EP/datamodel confidence — so there is no reconcile gap and no consumer for a finalize-time tenant. Numbering keeps the gap to match the code. See amended ratified #7. | — |
| 8 | `_finalize_commit_dicts` | write reconciled dicts into `behavior_map` so every downstream reader sees one view | all mutators |
| ~~9~~ | *(removed — 2026-06-15 recon; amended ratified #9)* ~~`_finalize_declared_fields`~~ | **Removed**, not stubbed — the writer-contract half already runs over the final substrate in sub-step 10's `validate_ir` (declared-fields:F1(a) / `INV-zotip`, satisfied); the population-contract half lands in the writer-contract *validator* (`WI-libib` — "extend the validator class", inside `_check_writer_contract` where the record stream lives) and in producers (declared-fields:F5 / `INV-dubam`), never in finalize. A finalize-time population re-check would append net-new violations and *grow* the shrink-only ratchet (measured +2…+57/substrate against the zero-headroom baseline of 12). Numbering keeps the gap to match the code. See amended ratified #9. | — |
| 10 | `_finalize_referential_integrity` | §7 FK predicate (edges ⊆ nodes; `is_resolved ⇒ dst ∈ nodes`) + ADR-0039 per-edge range — **structurally LAST** | all preceding |
| — | `recompute_view_summary(view_map, population, centrality)` — **not a sub-step**; the pure helper the tiered (budget-tier) projection calls *after* its shrink loop to re-derive `nodes_summary` from the FINAL on-disk arrays (projection:F1 / INV-pazur). Implemented narrower than the design-time `re_derive_view(finalized, selected_ids)`: it re-derives only the summary block from the authoritative post-shrink arrays, leaving the node/edge/entrypoint sets the shrink loop already produced untouched | projection-finalize | `finalize()` returned |

**Dependency rules.** *(R4 and R6 VACATED — 2026-06-15 recons; the sub-steps they ordered against [declared-fields #9, emission-counts #5] were removed.)* R1 entry-precondition (finalize never mutates membership);
**R2 (hard):** run_signature recompute strictly after the AR-field stamp (else it
hashes create-time placeholders — the META-hufaz defect); **R3 (hard):** the FK /
referential-integrity check is the last violation-appending sub-step (validates
exactly the substrate that serializes, §7); R4 (vacated — the declared-fields
sub-step it ordered after the stamps was removed; see amended ratified #9); R5
re-relativize first; R6 (vacated — the emission-counts step it ordered limits against was removed); R7 projections are strictly
downstream consumers of the frozen handle (a projection cannot re-introduce a
reconciled value); R8 the remainder is order-free (the §3 idempotency property carried
into Phase F). R2 and R3 are each pinned by a white-box test, not just by position.

**Determinism.** `finalize()` itself iterates the existing list order and never
iterates a set to drive output; `violations` are sorted before the report is built.
The tiered projection's `nodes_summary` is re-derived by `recompute_view_summary`,
which iterates the caller-supplied **population list** (so its bag-of-words tie-order
and the whole summary block are `PYTHONHASHSEED`-independent — projection:F1 /
INV-pazur). A **separate** `PYTHONHASHSEED` exposure remains in the projection
*selection* itself: `select_by_connectivity`'s frontier tie-break picks the first node
in set-iteration order on score ties, so the selected node set/order can still vary
across seeds. Removing it is a behavior change to a bakeoff-tuned hot loop, tracked
separately as **WI-nivuj** (with the byte-identical-artifacts subprocess test as its
closure evidence); it is deliberately out of scope for projection:F1, which closes the
summary↔array inconsistency (INV-pazur) by construction without changing the emitted
node/edge set.

**Ratified sub-decisions.** (4) `config_fingerprint` on override-analyze runs:
**backstop-with-violation** was the original ruling, but the `run-lifecycle:F1` carrier
**defers** it to `WI-mipul`'s producer-side work rather than recording a validation-class
violation here. Recording one would *grow* the shrink-only validation ratchet
(`test_validation_report_empty.py`) — `validate_ir` is silent on the default
`config_fingerprint` today, so the backstop violations would be net-new (~+8/substrate) —
for a concern whose real fix is producer-side. The broken-cache-key concern stays visible
via `WI-mipul` / `INV-lidul` in the tracker (per "we track everything in git"), not a
runtime key. F1 still performs the `pass_version` backfill (pure fill, ratchet-safe).
(5) emission-counts: the original **per-run, counted by `origin_run_id`** ruling is
**withdrawn as unsound** — `files_analyzed` is contractually a *file* count
(`== profile.languages[L].files`), but that recompute yields a *node/path* count and does
not close `INV-gizik` (IR-consuming passes stay 0). The sub-step is **removed** from
finalize; the real fix is a new provenance field, tracked under `INV-gizik`. (6)
`FinalizedMap` immutability: **shallow `frozen=True`** plus a consumer-side no-mutation
contract. (9) `_finalize_declared_fields`: planned as a documented stub for
declared-fields:F1(a)/F5; **removed** (not stubbed) by a post-ratification analysis (the
declared-fields-f1 fill-vs-remove recon, 2026-06-15) that **falsified the slot's premise**.
The premise was that declared-fields:F1(a)/F5 would *fill* this slot with zero orchestrator
change; the analysis found the work lands elsewhere: F1(a) (getattr→`_read`, `INV-zotip`,
satisfied) already ships through sub-step 10's `validate_ir` writer-contract subset over the
final substrate (its check fires on the dict-shaped AnalysisRun records F1(a) repaired); the
population-contract engine (declared-fields:F1 / G8) extends
the writer-contract *validator* (`WI-libib`, inside `_check_writer_contract`), not finalize;
and F5 is producer-side (`INV-dubam`). The stub was a true no-op whose only possible
finalize-time payload — appending population violations — would *grow* the shrink-only
validation ratchet (`test_validation_report_empty.py`, zero headroom at 12/substrate; measured
+2…+57). Per "we track everything in git" and the emission-counts precedent (#5), the slot is
removed; the work stays visible via `WI-libib` / `INV-dubam` / `INV-jahiv`. (7)
`_finalize_confidence_aggregates`: planned as a documented stub for confidence:F1/F2;
**removed** (not stubbed) by a follow-on recon (the confidence-f1 fill-vs-remove recon,
2026-06-15). *This reverses the retention #9 originally asserted* — that the confidence stub
had "a genuine finalize-time tenant" because its payload would be a `behavior_map` aggregate.
The recon falsified that premise exactly as #9 falsified declared-fields': (a) the
confidence:F1/F2 IDs denote per-edge *derivation* and ranking-detection *separation*
(producer-side, ADR-0039, `INV-suvil`/`META-ruhob` family), and ADR-0039 keeps per-edge
confidence **out** of finalize — so the named owner never fills this slot; (b) the aggregate
it described already exists over the final set (`metrics.avg_confidence`, computed *after*
`finalize()`; `sketch.confidence_mass` over EP/datamodels), so there is no reconcile gap; (c)
no consumer reads a finalize-emitted confidence field. "Ratchet-safe" only meant a fill would
be *harmless*, not that the slot had a tenant. The slot is removed; the genuine confidence
work stays tracked under `INV-suvil` / `META-ruhob` / ADR-0039.

**Phasing — `run-lifecycle:F1` is the carrier.** F1's PR lands the module + the
orchestrator spine + the fully-implemented run-lifecycle sub-steps, with the other
three families' sub-steps as **documented stubs** (confidence: no-op, **since removed** — see
amended ratified #7; declared-fields: originally the existing `validate_ir` subset, **since
removed** — see amended ratified #9; referential-integrity: the lifted existing `validate_ir`
call, structurally last from day one), so F1 merges green **without** them. Two run-lifecycle sub-steps named in the table above did **not** land as written:
`_finalize_emission_counts` (5) is **removed** (unsound — amended ratified #5) and the
`config_fingerprint` half of sub-step 2 is **deferred** to `WI-mipul` (amended #4); both
are tracked in the tracker, not stubbed in code. **The Phase-2 "each family fills a named
slot" model did not materialize.** Of the three families: `projection-finalize` became a
*downstream consumer* (`compact.recompute_view_summary`, not a sub-step); and both the
`declared-fields` (9) and `confidence` (7) stubs were **removed** (amended ratified #9 and #7)
once follow-on recons found their work lands outside finalize — declared-fields in the
writer-contract *validator* (`WI-libib`) + producers (declared-fields:F5), confidence in the
per-edge derivation family (`INV-suvil`, ADR-0039) with its aggregate already covered by
`metrics`/`sketch`. So no Phase-2 family adds a finalize sub-step; the finalize body is exactly
the run-lifecycle:F1 carrier's own sub-steps (the seam-(a) merge-collision hazard the stubs
were meant to dissolve simply did not arise, since the families landed elsewhere). Per the
§"Sequencing constraint", finalize still lands **after**
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
