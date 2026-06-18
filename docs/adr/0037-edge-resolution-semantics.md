<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0037: Edge Resolution Semantics — `is_resolved` Means In-Repo Target; Single Edge-Finalization Verdict; the `unresolved` Placeholder Kind Folds Into `external_symbol`

- Status: **Mosaic — Rulings 1, 2, 3, 5 Implemented; Ruling 4 (`unresolved`→`external_symbol` kind fold) pending ADR-0036 identity migration**
> Amended in place — see the "Implementation status" section for the per-ruling landed/deferred split (rulings 1/2/3/5 landed; ruling 4 deferred). Status line and inline pointers normalized so a fragment read cannot mistake ruling 4's prose for landed state.
- Date: 2026-06-10
- Supersedes: ADR-0017 (partial), ADR-0028 (partial), ADR-0034 (partial). Detail: ADR-0028's "Sibling-field design call-out" (the producer-stamped `is_resolved` contract — ruling 2's central derivation replaces it); ADR-0034's out-of-scope blessing of `make_unresolved_edge`'s `:unresolved` dst shape (retired by ruling 4's kind fold — see Ruling 4's DEFERRED marker); ADR-0017's dst-id string-shape coupling (the `{lang}:external:0-0:{name}:unresolved` matching and refinement-pass rewrites — re-keyed on `dst_ref` by the implementing fixes)
- Superseded by: —
- Related: ADR-0027 (Symbol.kind names the source-language construct — the construct-vs-relationship principle the node-kind fold here applies), ADR-0028 (evidence_type names the inference pathway — `resolution_quality` is its fold residue, whose MetaKeySpec doc this ADR corrects), ADR-0033 (Spec-vs-Data Validator Stage — the enforcement substrate gaining the FK predicate; its cross-field coherence invariant (a) re-anchors to the finalization verdict here), ADR-0035 (stable-id v6 identity contract — placeholder-node identity changes coordinate there), ADR-0036 (node.id grammar v2 — owns the kind-slot vocabulary the fold removes `unresolved` from). Tracker items: see the dedicated "Tracker items" section at the end.

**Decision provenance.** This ADR records a decided ruling, not a proposal. The decision was made by the project owner via design interview on 2026-06-10, after reviewing the verified evidence from the 446-item tracker root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`, "synthetic-node-stamping" family, fix F3).

## Context

### Three resolution surfaces, three disjoint writers, no reconciliation

An edge whose destination is not a first-party symbol carries its resolution state on three surfaces:

1. **`Edge.is_resolved: bool`** — defaults to `True` at the producer (`packages/hypergumbo-core/src/hypergumbo_core/ir.py:613` on the dataclass, `ir.py:654` on `Edge.create`). Its docstring reads "whether the dst symbol was resolved at analysis time."
2. **The `:unresolved` dst-id suffix** — stamped at two different stages: by `make_unresolved_edge` at producer time (`analyze/base.py:490`, via the shared `format_legacy_dst` shape at `ir.py:573`), and again — under the producer's untouched flag — by the boundary remap, which forces the kind slot to `"unresolved"` for every `ExternalRef`-bearing dangling dst (`ir.py:1238-1243`, rendered by `_canonical_external_id` at `ir.py:1004`).
3. **`Edge.dst_ref: Optional[ExternalRef]`** — documented as "canonical source of truth for external dsts" (`ir.py:595`), but optional in `make_unresolved_edge` (`analyze/base.py:454`) even though that helper already holds all three components (`lang`, `module_hint`, `callee_name`) needed to construct one (`analyze/base.py:490-509`).

No stage ever reconciles the three. The measured damage on the frozen analysis substrate (counts are substrate-pinned per the strategy doc's threats-to-validity note; mechanisms are code-verified):

- **4,507 flag-contradicted edges** (4,497 imports + 10 calls): `is_resolved=True` while the dst carries the `:unresolved` suffix — the remap itself rewrote the dst to a placeholder without touching the flag (WI-kukuk).
- **16,438 of 24,244 `is_resolved=False` edges (67.8%) have no `dst_ref`**: the import producers fill `dst_ref` but not the flag; the call/decorator producers fill the flag but not `dst_ref`. Only 7,806 (32.2%) carry both. A consumer collecting `e.dst_ref for e if e.dst_ref` recovers 32% of external targets and falls back to string-parsing `dst` — the exact brittleness `dst_ref` was introduced to eliminate (WI-zuhon).
- **31 method-call-recovery edges** carry `meta["resolution_quality"]="recovery"` with `is_resolved=True` (`linkers/method_call_recovery.py:202` never passes the flag, so the `Edge.create` default applies), while the `resolution_quality` MetaKeySpec claims the key is "set alongside `Edge.is_resolved=False`" (`axis_meta_keys.py:136-139`) (WI-mutuk).

### The empirical semantic nobody documented

WI-ninuv established that `is_resolved` does **not** mean "the dst node is absent from the graph": 100% of the 24,244 `is_resolved=False` edges point at a dst that exists — every one a synthesized `external_symbol` placeholder node — and the post-boundary-synthesis graph is referentially complete. The flag's true, empirically-load-bearing meaning is *"the dst is a real in-repo symbol, not a synthetic external placeholder."* That is also the meaning the downstream consumers actually need: slice traversal, io-chain construction, and dead-code analysis all branch on "does this edge leave the repo," not on "did the analyzer identify the external target."

### The construct-vs-relationship leak in the node vocabulary

The placeholder kind slot `"unresolved"` describes the *reference* ("we could not resolve this call to a project symbol"), not the *referent* (the external module/function being pointed at). Encoding it in node identity is precisely the construct-vs-relationship leak that ADR-0027's axiom — kind names the language construct, never the relationship to it — exists to prevent. The leak is visible as a dual-minting inconsistency today: the same external target gets different kinds depending on which path minted it. The boundary synthesizer mints the node itself with `kind="external_symbol"` (`ir.py:1287`) while its id's kind slot says `unresolved` (`ir.py:1242` → `ir.py:1004`); legacy-parsed dangling ids preserve whatever kind token their producer stamped (`ir.py:1245`). The validator's planned kind-slot↔registry cross-check cannot be stated cleanly while one entity answers "what are you" two different ways.

## Decision

### 1. `is_resolved` semantic: dst refers to a real, in-repo symbol node

`is_resolved := True` if and only if `Edge.dst` is the id of a first-party symbol node present in the graph. This adopts the empirical semantic WI-ninuv documented as the contractual one.

Identified externals are `is_resolved=False` **with `dst_ref` populated** — `requests.get` with a complete `ExternalRef` is identified, but not resolved, because resolution names in-repo-ness, not identification. Consumers get a clean derived space:

| `is_resolved` | `dst_ref`  | Meaning                                          |
|---------------|------------|--------------------------------------------------|
| `True`        | `None`     | In-repo target (first-party symbol node)         |
| `False`       | populated  | Identified external (stdlib / dependency target) |
| `False`       | `None`     | Unidentified reference                           |

The fourth cell (`True` + populated `dst_ref`) is never produced: `dst_ref` is external-dst identity by definition (`ir.py:595`), and the finalization step below derives both surfaces from one verdict, so they cannot disagree.

The `Edge.is_resolved` and `Edge.dst_ref` docstrings (`ir.py:594-595`) are rewritten to state this contract, including the fact — previously undocumented — that an `is_resolved=False` edge still has a materialized placeholder dst node after boundary synthesis (present-but-synthetic, not absent).

### 2. A single edge-finalization step derives all three surfaces from one verdict

A single finalization step at the boundary-synthesis/remap stage — the `apply_external_id_remap` locus (`ir.py:1306`), which already visits every edge — classifies each dst exactly once into **first-party | external-placeholder | dangling**, and derives ALL resolution surfaces from that one verdict:

- **first-party** → `is_resolved=True`, `dst_ref=None`.
- **external-placeholder** → `is_resolved=False`, `dst_ref` populated (from the producer's `ExternalRef` when present, else derived from the placeholder identity), dst id in the canonical boundary shape.
- **dangling** → `is_resolved=False`, `dst_ref=None`. Post-synthesis dangling endpoints should be empty; today's residue (the tier-filter-after-boundary-synthesis ordering, INV-jukok family) is owned by the sibling endpoint-closure work, not this ADR.

Consequences for the producers:

- The producer-side default `is_resolved=True` (`ir.py:613`/`ir.py:654`) **stops being load-bearing**. Producer-stamped values become advisory inputs; the finalizer's verdict is what serializes. This is what makes WI-kukuk's 4,507-edge contradiction structurally impossible rather than individually patched.
- `make_unresolved_edge` (`analyze/base.py:445`) derives `dst_ref` **unconditionally** [SENTINEL EXCEPTION (WI-huzuv): "unconditionally" reads as "for every edge with a *real* external module"; when the module is the `"external"` sentinel (module unknown) `dst_ref` stays `None` — the table's third "unidentified reference" cell — rather than fabricating `module_path="external"`. See "Implementation status".] from the `(lang, module_hint, callee_name)` components it already has (`analyze/base.py:490-509`), instead of accepting it as an optional kwarg that 67.8% of callers omit. This closes WI-zuhon at the producer side; the finalizer backstops any producer that bypasses the helper.

### 3. Recovery edges are correctly resolved; the `resolution_quality` doc is what gets fixed

Under ruling 1, the 31 method-call-recovery edges whose dsts ARE real in-repo nodes become **correctly** `is_resolved=True` by definition — a heuristic line-proximity resolution to a first-party method is uncertain in *pathway*, not in *locality*. WI-mutuk therefore resolves as a documentation fix, not a producer flip: the `resolution_quality` MetaKeySpec (`axis_meta_keys.py:136-139`) drops its "set alongside `Edge.is_resolved=False`" pairing claim and instead documents `resolution_quality` as a pathway-quality label orthogonal to `is_resolved` (resolution certainty lives in `confidence` and `evidence_type` per ADR-0028; target locality lives in `is_resolved`).

### 4. Node-kind fold: `unresolved` folds into `external_symbol`

> NOT YET LANDED — deferred; see "Implementation status". Ruling 4 (the `:unresolved` → `:external_symbol` id-kind-slot fold) is the one ruling in this ADR that has *not* shipped: it churns 458 node ids and rides the ADR-0036 identity migration. The remaining prose in this section describes the intended end state, not current behavior.

The placeholder node kind `unresolved` — today forced into the canonical boundary id's kind slot by the remap (`ir.py:1242` → `ir.py:1004`) and stamped into producer dst ids by `format_legacy_dst` (`ir.py:573`) / `make_unresolved_edge` (`analyze/base.py:490`) — **folds into `external_symbol`**. Resolution state is a property of the REFERENCE (the edge), not of the thing referenced; after this ADR it lives only on the edge surfaces of ruling 1. One external target gets one kind, regardless of which path minted it, eliminating the dual-minting inconsistency.

The symbol-kind vocabulary drops the value: `unresolved` never gains a `SymbolKindSpec` (it has none today — it exists only as an id kind-slot token), and the ADR-0036 id-grammar v2 kind-slot vocabulary excludes it. Boundary nodes keep `kind="external_symbol"` (`ir.py:1287`, already CANONICAL per audit-findings 0007); their ids' kind slots migrate to match, which also unblocks the validator's kind-slot ↔ `Symbol.kind` equality predicate without a placeholder exemption. Dst-id strings on external edges change shape (`…:unresolved` → `…:external_symbol`); the identity impact is coordinated under ADR-0035/ADR-0036, in whose migration the implementing PRs participate.

### 5. Validation and rollout discipline

- The ADR-0033 validator gains the foreign-key predicate: **`is_resolved=True` ⇒ `dst` ∈ nodes**. (The converse is deliberately not a predicate — `False` edges legitimately point at materialized placeholder nodes.)
- Flipping the ~4,507 contradicted edges to `False` is a quantitative, consumer-visible change to the published graph. Per the bakeoff-validation tagging discipline, the implementing PR's tracker item takes **`awaits_bakeoff_validation`**.
- **Ordering constraint:** this finalization must land BEFORE the io-boundary receiver-contract family — populated `dst_ref` / module identity on external edges is that family's missing input (its receiver-tuple matching cannot outgrow bare-short-name tag matching without it).

## Consequences

### Positive

- **One verdict, three coherent surfaces.** The flag, the dst-id shape, and `dst_ref` can no longer disagree, because none of them is written independently after finalization. WI-kukuk's defect class is closed structurally.
- **`dst_ref` coverage on external edges goes from 32.2% to 100%.** Consumers stop string-parsing `dst`.
- **Consumers get the semantic they were already assuming.** slice / io-chains / dead-code branch on in-repo-ness; that is now the documented contract, not an empirical accident.
- **Referential integrity becomes checkable.** The FK predicate turns "is_resolved=True with a phantom dst" from a latent contradiction into a CI failure.
- **The node vocabulary stops encoding edge state.** ADR-0027's axiom holds for placeholder nodes; the kind-slot↔registry validator predicate needs no special case.

### Negative

- **~4,507 edges flip `True` → `False`.** Any consumer filtering `is_resolved=True` sees a smaller resolved set. Mitigated by bakeoff validation (the `awaits_bakeoff_validation` tag) before the claim is treated as an improvement.
- **Placeholder dst-id strings and boundary-node ids change** (`:unresolved` → `:external_symbol` kind slot). Cross-run diffs churn once; coordination cost with the ADR-0035/0036 identity migration is real and is why the implementing PRs ride that migration rather than shipping standalone.
- **Producer-stamped `is_resolved` becomes advisory**, which can confuse producer authors reading `Edge.create`'s signature. Mitigated by docstring rewrites at the dataclass, `Edge.create`, and `make_unresolved_edge`.

### Neutral / acknowledged

- Substrate counts (4,507 / 16,438 / 31) are pinned to the frozen analysis substrate; mechanisms are code-verified but numbers were not re-derived at ADR time. The implementing PR re-measures on its own substrate.
- `make_unresolved_edge`'s 0.50 confidence and the broader confidence-vocabulary questions are out of scope (ADR-0039).
- Endpoint closure for the residual dangling-src population (tier filtering running after boundary synthesis) is sibling work, not closed here.

## Alternatives considered

1. **`is_resolved` = "target identified."** `requests.get` with a complete `dst_ref` would be `True`. Rejected: in-repo-ness — the property slice, io-chains, and dead-code actually branch on — would then need a separate derivation on every consumer, and the semantic diverges from the documented empirical use (WI-ninuv measured the in-repo meaning as the one that holds on 100% of edges today).
2. **Tri-state field replacing the boolean** (`resolved | external | unknown`). Rejected: a schema migration plus a full consumer sweep, to encode information already cleanly derivable from the `(is_resolved, dst_ref)` pair under ruling 1.
3. **Keeping both node kinds** (`unresolved` and `external_symbol`). Rejected: permanently codifies resolution state — an edge property — in the node vocabulary, in direct violation of ADR-0027's axiom; and the dual-minting inconsistency (same target, different kinds by minting path) needs reconciling anyway, so keeping both buys nothing.

## Tracker items

| Full ID | Finding | Disposition under this ADR |
|---|---|---|
| `WI-kukuk-ranof-rifag-sigis-botot-vavil-pipuv-hojof` | 4,507 edges with dst `:unresolved` suffix while `is_resolved=True` (sibling-field disagreement) | Closed structurally by ruling 2 (single finalization verdict); edges flip to `False` |
| `WI-zuhon-lavoh-bumog-fihos-dasan-dudut-vuruz-kuzom` | `dst_ref` null on 67.8% of `is_resolved=False` edges; flag and structured ref populated by disjoint producer sets | Closed by ruling 2 (`make_unresolved_edge` derives `dst_ref` unconditionally; finalizer backstops) |
| `WI-ninuv-turuh-lagul-najaj-losiz-hirum-rubik-vizab` | `is_resolved=False` does not mean dst-node-absent; all 24,244 unresolved dsts exist as placeholders; meaning undocumented | Closed by ruling 1 (the empirical semantic becomes the documented contract, including present-but-synthetic placeholders) |
| `WI-mutuk-gatik-sojik-nisul-kopom-fofiv-tonug-rifab` | 31 recovery edges carry `is_resolved=True` with `resolution_quality="recovery"` (pairing inverted) | Closed by ruling 3 (edges are correctly `True`; the MetaKeySpec pairing claim at `axis_meta_keys.py:136-139` is what gets fixed) |

## Implementation status

- **Rulings 1, 2, 3, 5 — IMPLEMENTED** (the edge-resolution finalization train, Wave 2). The single edge-finalization verdict lands as `finalize.py::_finalize_edge_resolution` (sub-step 7, before commit so the serialized edges carry the verdict and before referential-integrity so the FK predicate validates it). It classifies each `edge.dst` against the final node set purely by node *kind* (`external_symbol` ⇒ external-placeholder) and derives `is_resolved` + `dst_ref` from that one verdict; `make_unresolved_edge` derives `dst_ref` from its components (ruling 2), and the finalizer's `_derive_dst_ref_from_id` backstops bypassing producers. **Sentinel exception (WI-huzuv):** when the module is the `"external"` sentinel (module unknown) `dst_ref` stays `None` — the table's third "unidentified reference" cell — rather than fabricating `module_path="external"`; ruling 2's "unconditionally" is read as "for every edge with a *real* external module," which was the WI-zuhon gap. The ruling-5 FK predicate (`spec_validator.py` cross-field) is scoped to `is_resolved=True ∧ dst.kind=='external_symbol'`; the dst-*absent* (dangling) half is left to the sibling endpoint-closure work (ADR §"dangling" / INV-jukok family), so it does not fire on isolated unit fixtures. Closes WI-kukuk / WI-zuhon / WI-ninuv / WI-mutuk.
- **Ruling 4 — DEFERRED** (the `:unresolved` → `:external_symbol` id-kind-slot fold). It is *not* required for the four closures above: under ruling 1 the contradicted edges flip to `is_resolved=False`, at which point the `is_resolved=False` flag and the `:unresolved` dst suffix *agree* (the inverse direction is the one that was clean). The residual `:unresolved` id-slot-vs-`external_symbol` node-kind divergence is the pre-existing dual-mint, an ADR-0036 id-grammar concern. Deferred because (a) it churns 458 node ids + their inbound edge dsts — coordinate with the ADR-0035/0036 identity migration as §"Negative" anticipates — and (b) it forces refactoring the three `:unresolved`-string-matching consumers (`linkers/cgo.py`, `taint.py`, `taint_refine.py`), which is a *different* root-cause family (consumer re-derives the producer verdict), not edge-finalization. Tracked as a follow-up work item.

## References

- Strategy doc: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` — "synthetic-node-stamping" family; fix F3 ("single edge-finalization") and its Wave 2 scheduling, including the *must-precede-io-boundary* constraint.
- Raw root-cause analysis: `~/hypergumbo_lab_notebook/correctness_strategy_06102026_full_workflow_result.json` (the verified three-surfaces/disjoint-writers finding, including `ir.py:1241-1243` + `ir.py:1004` forcing `kind='unresolved'` under the producer's flag).
- ADR-0027 (Symbol.kind names the source-language construct): the construct-vs-relationship principle behind ruling 4.
- ADR-0028 (evidence_type names the inference pathway): pathway-quality vocabulary that `resolution_quality` is residue of; ruling 3 re-anchors its MetaKeySpec there.
- ADR-0033 (Spec-vs-Data Validator Stage): home of the new FK predicate (ruling 5).
- ADR-0035 (stable-id v6 identity contract) / ADR-0036 (node.id grammar v2): the identity migration the kind-slot fold (ruling 4) coordinates with; ADR-0036 owns the kind-slot vocabulary that drops `unresolved`.
- Audit-findings 0007: the existing CANONICAL verdict on `external_symbol` that ruling 4 folds into.
