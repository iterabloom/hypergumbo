<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0039: Confidence Separation — confidence Is Pure Detection Reliability; Ranking Moves to rank_score; quality.* Deleted

- Status: **Accepted**
- Date: 2026-06-10
- Supersedes: ADR-0005 (partial — its entrypoint-selection tables key on the composite confidence; post-implementation they read as `rank_score`-keyed)
- Superseded by: —
- Related: ADR-0005 (Sketch Budget Allocation — its selection tables key on the composite confidence; read them as rank_score-keyed post-implementation), ADR-0024 (Axis Declaration Template — `confidence_source` and `rank_score` are new fields whose axis declarations follow its discipline), ADR-0028 (Edge.evidence_type Names the Inference Pathway — the evidence-type registry this ADR extends with per-type confidence data), ADR-0033 (Spec-vs-Data Validator Stage — the enforcement home for the range checks this ADR makes checkable), ADR-0037 (Edge Resolution Semantics — the `is_resolved` field that the deleted `quality.*` derivation reads). Sibling ADRs in the 2026-06-10 series: 0035 (stable-id v6), 0036 (node.id grammar v2), 0038 (access_mode contract), 0040 (evidence-field descope), 0041 (supply-chain tier purity), 0042 (survey rename).

This ADR records a **decided ruling, not a proposal**. The decision was made by the project owner via a design interview held 2026-06-10, after reviewing the evidence assembled by the 446-item tracker root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` and its raw workflow result). Each numbered ruling below was explicitly chosen over the named alternatives.

## Context

### One field, two quantities

`Edge.confidence` (and entrypoint `confidence`) is documented as an evidence-derived detection-reliability score — "higher means more certain" on a 0.0–1.0 scale, with documented ranges per producer category (analyzer edges 0.30–0.95, linker edges 0.40–0.95, entrypoints 0.70–0.99; spec §12). In production code the field actually carries a blend of two distinct quantities:

1. **Detection reliability** — how sure the producer is that the relationship exists. This is what the spec describes and what consumers are told they are reading.
2. **Ranking adjustment** — post-detection boosts and penalties that encode "how prominently should this rank," applied multiplicatively or additively in place:
   - `linkers/type_hierarchy.py:544` — the `0.85 / sqrt(N)` fan-out dampener (WI-kabom: interfaces with many implementors otherwise dominate reverse slices), and the flat `0.30` test-file override penalty at `:566-567` (WI-supok).
   - `entrypoints.py:1316-1342` — multiplicative penalties: ×0.1 test files (the DMD case: 98% of `main()`s in test files), ×0.5 utility/example files, ×0.3 vendor tiers, ×0.5 deeply nested Go `cmd/`.
   - `entrypoints.py:1419` — library-export demotion ×0.1 when semantic entrypoints exist (the "forgejo problem," `:1350`: 7,474 Go uppercase exports outranking real routes).
   - `entrypoints.py:1504-1519` — additive in-degree (cap +0.35) and out-degree (cap +0.25) connectivity boosts with language-dominance scaling (the gemini-cli telemetry-export case, `:1491`).

The blend is not hypothetical drift; it is measurable. Only **2 of 104** entrypoint confidences on the self-analysis corpus land on any documented tier value — the producers' base constants match the spec's tiers, but post-detection adjustments move nearly every published value off-tier. Range violations exist in **both directions**: `linkers/containment.py:266` emits 18,388 `naming_convention` edges at exactly **1.0**, above the documented 0.95 linker ceiling (WI-lutad), while the `1/sqrt(N)` dampener drives `dispatches_to` edges down to **~0.094**, below every documented floor — 313 edges under 0.40, 236 under even the 0.30 analyzer floor (WI-botif). The ranges are unenforceable because the field's two meanings make any single range wrong for one of them.

### No derivation layer

Independently of the blend, the "evidence-derived" half of the contract is also fictional. There is no evidence→confidence derivation anywhere: confidence is assigned at ~566 hardcoded `confidence=` call sites across `packages/*/src`, plus the flat dataclass default `0.85` (`ir.py:607`, mirrored in `Edge.create` at `ir.py:651`). The modal published value — 0.85 on ~41.7k edges — is the modal value **purely because the largest emit path passes nothing** and inherits the default, not because anything assessed those edges at 0.85 (INV-suvil). Confidence is not even a function of `evidence_type`: `ast_call` carries 4 distinct constants and `ast_call_direct` carries 5, with the variance persisting when (evidence_type, edge_type, language) is held fixed (WI-fuhof).

The spec is internally inconsistent about this rather than merely violated: §12 self-discloses "Not yet implemented … Edge default confidence is 0.85 in code" (spec:1165) while keeping normative MUSTs anchored to the unimplemented model — "Consumers MUST default unknown `evidence_type` values to 0.30" (spec:1161, repeated at spec:1797) — and a ~40-line `EVIDENCE_CONFIDENCE_MATRIX` code block (spec:1169-1208) that has zero implementation.

### quality.* carries zero independent signal

`Edge.quality` (`{score, reason}`, derived at `ir.py:816-827` via `_derive_edge_quality`) was verified to be a pure function of `(confidence, is_resolved, derived_from)`: `quality.score == round(clamp(confidence, 0, 1), 3)` on **110,533 / 110,533** edges of the verification corpus — no edge anywhere carries independent quality signal. The 5-value `quality.reason` vocabulary is undocumented (zero hits in spec or schema.json) and its tier-named values (`high_confidence_direct`, `medium_confidence`, `low_confidence_fallback`) do not partition the confidence axis — the field encodes the emitter mechanism, not a tier (WI-humok). The rounding even makes `quality.score` diverge cosmetically from top-level confidence on the 257 `dispatches_to` edges whose computed confidence carries more than 3 decimals (WI-riguh).

## Decision

Five rulings, all decided.

### 1. Published `confidence` is evidence-derived detection reliability — nothing else

`Edge.confidence`'s contract narrows to: *the producer's evidence-derived estimate that the relationship exists.* An evidence→confidence derivation layer is built to make that true:

- `EvidenceTypeSpec` (`evidence_types.py:87-93`) gains `base_confidence: float` and a `[lo, hi]` legal range per evidence type. The per-type values live in the ADR-0028 registry, making the range contract registry-backed instead of prose-only.
- A `derive_confidence(...)` function reads the registry; `Edge.create` routes through it **when no explicit confidence is passed**, replacing the flat `0.85` dataclass default (`ir.py:607` / `ir.py:651`). The ~41.7k edges that today inherit 0.85 by omission instead get the registered base confidence for their `evidence_type`.
- Range validation (each edge's confidence within its evidence type's `[lo, hi]`) becomes mechanically checkable and joins the ADR-0033 validator stage.

### 2. `confidence_source` discriminator enables incremental migration

`Edge` gains a `confidence_source` field — `# axis: bounded-enum`, values `evidence_derived | emitter_constant | composite`:

- `evidence_derived` — confidence came through `derive_confidence(...)`.
- `emitter_constant` — a hardcoded producer constant (the legacy state of the ~566 sites).
- `composite` — confidence still carries blended ranking adjustment (transitional; eliminated by ruling 3).

The discriminator lets the ~566 hardcoded emitter constants migrate incrementally — each producer flips to `evidence_derived` as it adopts the derivation layer, with no flag-day — and makes the guarding property test expressible: **no emitter may produce a single constant confidence value across >100 edges without declaring `confidence_source=emitter_constant`.** The test fails any producer that silently re-grows a flat constant while claiming derivation.

### 3. Ranking separation: adjustments relocate INTACT to `rank_score`

The ranking adjustments move, unchanged in formula and magnitude, to a separate `rank_score` field; published `confidence` stops absorbing them:

- `linkers/type_hierarchy.py`: the `1/sqrt(N)` fan-out dampener (`:544`) and the 0.30 test-override penalty (`:566-567`).
- `entrypoints.py`: the multiplicative penalties (`:1316-1342`), the library-export demotion (`:1419`), and the in/out-degree connectivity boosts (`:1504-1519`).

Entrypoint sorting (`entrypoints.py:1528`) and `MIN_ENTRYPOINT_CONFIDENCE` filtering (`:1541`) re-key from `confidence` to `rank_score`. **The ruling explicitly requires the ranking signal survive relocation unchanged**: these adjustments encode hard-won bakeoff fixes — the forgejo Go-export demotion (`:1350`), the DMD test-`main()` penalty (`:1310-1315`), the gemini-cli telemetry-export carve-out (`:1491`) are named in the code comments — and regressing them is out of scope. `rank_score` initializes from detection confidence and accumulates the same adjustments the confidence field accumulates today; only the field name changes for the ranking consumer. Edges/entrypoints whose producers have not yet separated continue publishing `confidence_source=composite` until their migration PR lands.

`rank_score` is a numeric field (not axis-governed vocabulary); `confidence_source` carries the bounded-enum axis declaration per ADR-0024.

### 4. `quality.score` / `quality.reason` are deleted via a one-version deprecation window

Verified vacuous (pure function of `(confidence, is_resolved, derived_from)`; `score == round(clamped confidence, 3)` on 110,533/110,533 edges; `ir.py:816-827`), `quality.*` is removed rather than repaired or repurposed:

- **Deprecation release (N):** `quality` continues to be emitted, marked deprecated in the spec and schema with a pointer to `confidence` + `confidence_source` + `is_resolved` as the replacement surface.
- **Removal release (N+1):** `_derive_edge_quality`, the `__post_init__` population (`ir.py:639`), the serialization (`ir.py:739`), and the `quality` field itself are deleted. Schema bump accordingly.

The `reason` vocabulary is not migrated anywhere: its mechanism-encoding job is already done better by `evidence_type` (ADR-0028) and `derived_from`, and its tier-encoding pretense was false (WI-humok).

### 5. Spec reconciliation is two-stage

- **Stage A (immediate truth-telling; no code prerequisites):** delete the fictional `EVIDENCE_CONFIDENCE_MATRIX` block (spec:1169-1208); withdraw the unimplemented 0.30-default MUST (spec:1161 and spec:1797). The spec stops asserting normative requirements against a model it simultaneously discloses as unimplemented. Stage A precedes everything else in this ADR.
- **Stage B (after rulings 1–3 land):** regenerate the per-evidence-type confidence table **from the registry** (the `EvidenceTypeSpec` `base_confidence`/range data), in the same generated-docs pattern as the concept-axes docs. The spec's table becomes a projection of the code's single source of truth, not parallel prose.

## Alternatives considered

1. **Composite stays published, with `base_confidence` alongside.** Keep the blended value as the headline `confidence` and add a secondary evidence-derived field. Rejected: the headline field keeps two meanings forever, every existing consumer keeps reading the blend, and range validation is only possible on the secondary field — the documented contract stays unenforceable exactly where consumers look.
2. **Pure confidence, drop the adjustments.** Make `confidence` pure and simply delete the ranking boosts/penalties. Rejected: knowingly regresses bakeoff-won entrypoint ranking (forgejo, DMD, gemini-cli cases). The adjustments are correct *as ranking signal*; the defect is the field they live in, not their existence.
3. **Repurpose `quality.*` as the ranking home.** `quality.score` becomes `rank_score` in place. Rejected: a semantic rename-in-place — old consumers that learned `quality.score ≈ confidence` would silently misread the repurposed field as reliability. A dead field is deleted; a new quantity gets a new name.

## Consequences

### Positive

- `confidence` means one thing, the spec's range table becomes registry-backed and validator-enforceable (ADR-0033), and the 1.0-above-ceiling / 0.094-below-floor violations become detectable defects instead of definitional ambiguity.
- The 0.85-by-omission pathology ends: an unexamined dataclass default stops being the modal "evidence-derived" value on ~41.7k edges.
- Bakeoff-won ranking behavior is preserved verbatim under its honest name; future ranking tuning no longer corrupts the reliability signal.
- One IR concept (`quality.*`) is deleted outright — net schema shrinkage in a release series that mostly adds fields.
- `confidence_source` makes migration state machine-readable, so "how much of the graph is actually evidence-derived yet" is a query, not an audit.

### Negative

- Two new Edge-adjacent fields (`confidence_source`, `rank_score`) and one schema-visible deletion (`quality`) across a one-version window: consumers of `quality.score` must move during release N.
- ~566 emitter sites eventually touched. Mitigated by incrementality: `emitter_constant` is a legal declared state, so the migration is monotone, not flag-day.
- Published confidence values shift for any edge whose registered base confidence differs from its old hardcoded constant, and entrypoint `confidence` stops matching the old sort order (which `rank_score` now reproduces). Downstream thresholds tuned against blended values need one recalibration pass.

### Neutral / acknowledged

- This ADR fixes *where* ranking signal lives, not *whether the formulas are optimal* — re-tuning the dampener or boosts stays ordinary bakeoff work, now decoupled from the reliability contract.
- WI-lutad closes only jointly: the 18,388 `naming_convention`-at-1.0 half is a containment-producer fix under ruling 1's range enforcement; the entrypoint-tier half is ruling 3.
- Contextual adjustments that are genuinely *evidence-strength* signals (e.g., the message_queue cross-language −0.1 noted in ADR-0031) remain legal inputs to detection confidence; the line drawn here is detection evidence vs. ranking policy, not "constants vs. arithmetic."

## Tracker items

| Item | Role |
|---|---|
| `INV-suvil-jojoh-gifib-zirig-famom-sorim-tarar-hojok` | The violated invariant: confidence hardcoded per emitter, not evidence-derived. Rulings 1–2 are its structural fix. |
| `META-ruhob-lubob-matip-vinol-duzab-nafom-tikas-vajur` | Root-cause family umbrella ("edge confidence is a hardcoded per-emitter constant"); this ADR is the family's decision record. |
| `WI-fuhof-nizos-zofok-lotot-sadah-famar-novor-vunag` | Confidence not a function of evidence_type (4–5 distinct constants per type); closed by the ruling-1 derivation layer. |
| `WI-botif-kufat-nunal-havuh-mikit-lulof-hulud-fukid` | type-hierarchy `dispatches_to` edges down to ~0.094 below every floor; closed by ruling 3 (dampener moves to `rank_score`). |
| `WI-lutad-huhaj-kuvak-vonis-vifut-fadig-jivim-sibut` | 18,388 linker edges at 1.0 above ceiling + 81/104 entrypoints below floor; closes jointly via rulings 1 and 3. |
| `WI-dojor-rajaf-bajos-hifan-lilos-sikuj-todaj-horod` | Undocumented in-degree boost at `entrypoints.py:1504-1507`; resolved by ruling 3 (relocation) + ruling 5 Stage B (documentation from truth). |
| `WI-humok-nisaj-pilov-daboz-tunup-jikaf-garam-nobur` | `quality.reason` taxonomy undocumented and mis-named; closed by ruling 4 deletion. |
| `WI-riguh-kogar-zasol-fobos-volin-valum-nujik-sobiv` | `quality.score` rounding divergence on 257 edges; mooted by ruling 4 deletion. |

## References

- Strategy document: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` (confidence-evidence-derivation family, fixes F1/F2/F3/F4/F6) and its raw workflow result JSON.
- Code: `ir.py:607,651` (0.85 default), `ir.py:816-827` (quality derivation), `evidence_types.py:87-93` (EvidenceTypeSpec), `linkers/type_hierarchy.py:544,566-567`, `linkers/containment.py:266`, `entrypoints.py:1316-1342,1419,1504-1519,1528,1541`.
- Spec: `docs/hypergumbo-spec.md` §12 — range table (:1155-1161), self-disclosure (:1165), fictional matrix (:1169-1208), repeated MUST (:1797).
- ADR-0024 (axis declaration for `confidence_source`), ADR-0028 (evidence-type registry extended here), ADR-0033 (validator stage hosting the range checks), ADR-0037 (`is_resolved` semantics read by the deleted quality derivation).
