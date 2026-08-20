<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0040: Evidence-Field Descope — Remove `evidence_spans` and `meta.evidence[]`; Keep `evidence_lang`, Centrally Stamped

- Status: **Accepted**
- Date: 2026-06-10
- Supersedes: —
- Superseded by: —
- Related: ADR-0028 (Edge.evidence_type names the inference pathway — a *different* field on a *different* axis; it stays, fully populated), ADR-0033 (Spec-vs-Data Validator Stage — its writer-contract check class is what polices "declared ⇒ populated" going forward), ADR-0039 (confidence separation — `evidence_lang` is the `lang` input to the spec's `(language, evidence_type)` confidence matrix), ADR-0037 (edge resolution semantics — sibling campaign ADR on Edge); tracker items in the dedicated section at the end.

Decision provenance: this ruling was made explicitly by the project owner in a design interview held 2026-06-10, after reviewing the verified evidence from the 446-item tracker root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`, declared-fields family, fix F4 "evidence implement-vs-descope — human decision"). This ADR records a decided ruling, not a proposal.

## Context

### The edge-evidence triple and its verified state

The Edge dataclass (`ir.py::Edge`) declares three evidence-adjacent fields beyond `evidence_type`:

- `evidence_lang: Optional[str]` (`ir.py::Edge.evidence_lang`, `# axis: language`) — "Language for confidence scoring."
- `evidence_spans: Optional[List[Dict[str, Any]]]` (`ir.py::Edge`, since removed) — "Structured locations of evidence."
- `meta.evidence[]` — a multi-pass evidence accumulator promised in spec prose (`docs/hypergumbo-spec.md` §9): "When multiple analysis passes observe the same relationship, `meta.evidence[]` accumulates their individual observations."

The dogfood tranches and the 2026-06-10 root-cause analysis verified the production state of all three on the frozen self-corpus substrate (110,533 edges):

| Field | Declared | Populated | Mechanism |
|---|---|---|---|
| `evidence_lang` | `ir.py::Edge.evidence_lang`, `docs/schema.json`, spec §9 | 0 / 110,533 | ~22 long-tail analyzers stamp it per-site (a HEAD grep finds 25 producer modules passing `evidence_lang=`); none of them produced edges on the self-corpus. The mainstream Python/JavaScript/HTML analyzers never adopted it. |
| `evidence_spans` | `ir.py::Edge`, `docs/schema.json`, spec §9 | 0 / 110,533 | **Zero producers ever.** No emit site outside `ir.py` has ever passed `evidence_spans=`. The done-marker at spec §16 was satisfied solely by a constructor round-trip test (`test_edge_has_evidence_spans`, since deleted with the field) that exercises no producer. |
| `meta.evidence[]` | spec §9 prose only | 0 / 110,533 | **Structurally unemittable.** The Edge dataclass at `ir.py::Edge` has no backing `evidence` list field at all — the spec promises what the IR cannot represent. |

The serializer (`Edge.to_dict`, `ir.py::Edge.to_dict`) emits `evidence_lang` / `evidence_spans` only when non-`None`, so no behavior-map artifact ever produced contains any of these keys. The spec nonetheless carries a 🟩 done-marker at spec §16 — "Required field presence (execution_id, run_signature, evidence_lang, evidence_spans)" — over-claiming delivery of two 0%-coverage fields, and the confidence pseudocode at spec §9 reads "`lang: Language (from edge.meta.evidence_lang or src.language)`", a fallback that fires on 100% of edges.

### Why `evidence_lang` is different from the other two

`evidence_lang` is not a fiction in the same sense. It has real (if long-tail) producers, a registered axis (`# axis: language`, catalog-checked by the axis-conformance machinery), and a load-bearing consumer story: it is the language coordinate for cross-language edge reasoning and the `lang` input to the spec's `EVIDENCE_CONFIDENCE_MATRIX` (spec §12, the confidence-derivation layer ADR-0039 governs). The spec marks it **Required** for cross-language edges where src/dst languages differ (spec §9) — exactly the sub-population (63 edges on the tranche-03 substrate) where it is most acutely null today.

Its history is instead the family's proven *delivery* failure mode: per-producer adoption. Stamping `evidence_lang` was attempted as a sweep across the ~130 Edge emit sites and demonstrably stalled at the ~22 long-tail analyzers — leaving the mainstream analyzers, which produce essentially all edges on real corpora, unstamped. The strategy analysis names this the "no-chokepoint producer accident" pattern (its anti-leverage note: "per-symptom patching is empirically the worst strategy in this corpus — ... evidence_lang adoption stalled at 22 analyzers") and prescribes the same remedy shape used for fingerprints: a single central stamping point, never a per-site sweep.

### Why decide now

The strategy's Wave 4 includes schema:F3, a schema-generation content pass that derives spec field tables from emission. Declared-but-dead fields that survive until that pass get mechanically canonized into generated documentation. declared-fields:F4 therefore carries a hard sequencing constraint: the implement-vs-descope decision MUST precede schema:F3. This ADR is that decision.

## Decision

### 1. Remove `evidence_spans` and `meta.evidence[]`

- Delete `evidence_spans` from the Edge dataclass (`ir.py::Edge.create`), from `Edge.create`'s parameter list, and from the `to_dict` serialization branch (`ir.py::to_dict`).
- Delete the `evidence_spans` declaration from `docs/schema.json` (currently `docs/schema.json`, under Edge `meta` properties).
- Delete the `meta.evidence[]` multi-pass-accumulator paragraph from the spec (spec §9) and the residual "including evidence" phrasing in the schema's Edge-meta description (`docs/schema.json`) to the extent it implies an evidence array.
- Correct the spec field tables: drop the `evidence_spans[]` bullet (spec §9), and flip the stale done-marker at spec §16 — the "Required field presence" line must stop listing `evidence_spans` (and must not claim `evidence_lang` until central stamping actually lands).
- Delete `test_edge_has_evidence_spans` from `test_ir.py` with the field. It is the campaign's worked example of presence-only closure evidence: a constructor round-trip satisfying a done-marker while production coverage is 0%.

Removal is **byte-invisible on every artifact ever produced**: the serializer only emitted these keys when non-`None`, and they never were. This is a contraction of promises, not a change in emitted shape — no schema-version bump is forced on wire-format grounds.

### 2. Keep `evidence_lang`, stamped once at the chokepoint

`evidence_lang` is retained on Edge (`ir.py::Edge.evidence_lang`) with its `# axis: language` declaration and catalog check unchanged. Its population contract changes from "each producer should remember to stamp it" to:

> `evidence_lang` is stamped at exactly ONE central point — the `Edge.create` chokepoint — defaulting to the src symbol's language. Producers with genuine cross-language nuance (e.g., a Protocol linker whose evidence lives on the dst side) may pass an explicit value; everything else inherits the default.

`Edge.create` (`ir.py::Edge.create`) already accepts `evidence_lang: Optional[str] = None`; the landing PR wires the default. Where the src symbol's language is not resolvable at `Edge.create` call time (the factory receives an ID string, not a Symbol), the stamp is completed by the central finalize sweep that already exists for fingerprint-class post-passes — the contract is "one stamping point, default `src.language`", not "every call site computes it." This turns the spec's documented-but-unimplemented fallback clause ("Defaults to `src` node's language if omitted", spec §9) from a consumer-side hope (spec §9's `or src.language`) into a producer-side guarantee, and it satisfies the spec's Required-for-cross-language-edges clause structurally rather than by a second adoption sweep.

The ~22 long-tail per-site stamps become redundant once the chokepoint default lands; they may be deleted in the same PR or swept opportunistically — either way the central stamp, not the per-site code, is the contract.

### 3. Re-introduction rule for span-level evidence

Span-level evidence (`evidence_spans`, `meta.evidence[]`, or any successor) may return later, but **only with a named consumer and a central producer designed together** — never again as an aspirational schema field waiting for producers to adopt it. The realistic consumer need ("show me the source behind this edge") is already served without it:

- `derived_from` (being de-tautologized in the same campaign, declared-fields:F5) joins to node spans — the Symbols an edge was constructed from carry file/line ranges;
- `evidence_type` (ADR-0028) names the inference pathway;
- `origin` names the producing pass.

A future implementer who can name a consumer that this join does not serve writes the consumer and the central producer in one design, per this rule.

### 4. Sequencing constraint

This descope MUST land before the schema-generation content pass (strategy Wave 4, schema:F3). Otherwise the generated spec tables canonize the dead declarations and the cleanup cost recurs with interest. This ordering is recorded on the strategy's declared-fields:F4 row and is binding on Wave-4 planning.

## Alternatives considered

1. **Implement centrally (option (a) of declared-fields:F4).** Add the missing `Edge.evidence` list field, stamp `evidence_lang` centrally, and emit `evidence_spans` from the shared Pass-2 edge-construction helpers. Rejected: L-sized effort ahead of any named consumer, enlarges every record on a ~110k-edge substrate for nothing that consumes it, and competes directly with open P1 work. The central-stamping *shape* is right — which is why ruling 2 adopts it for the one field that earns it — but building span plumbing speculatively repeats the original sin at higher cost.
2. **Descope everything, including `evidence_lang`.** Rejected: `evidence_lang` is populated (by real if long-tail producers), cheap (one string, centrally derivable), axis-governed, and load-bearing for cross-language edge reasoning and the ADR-0039 confidence-derivation input. Deleting it is a small information regression, not a fiction cleanup — the line this ADR draws is precisely "fields the IR can honestly populate centrally" vs. "fields the schema promised and nothing ever wrote."

## Consequences

### Positive

- **The schema stops promising what the IR cannot represent.** `meta.evidence[]` (no backing field) and `evidence_spans` (no producer ever) leave `ir.py`, `schema.json`, and the spec field tables; the stale done-marker at spec §16 is corrected. Consumers reading the contract no longer code against keys that appear on 0% of edges.
- **`evidence_lang` goes from 0% to 100% population structurally.** One chokepoint default replaces the stalled ~130-site sweep; the spec-required cross-language sub-population is covered by construction. WI-kuluh's defect class (partial-adoption states passing both producer- and consumer-side gates) is closed by making partial adoption impossible rather than detectable.
- **The confidence-derivation layer (ADR-0039) gets its `lang` input.** The `(language, evidence_type)` matrix lookup becomes matchable on the mainstream pathway for the first time.
- **Wave-4 schema generation (schema:F3) starts from a truthful field inventory** — the sequencing constraint guarantees no dead declaration is canonized.

### Negative

- **Span-level evidence localization is explicitly given up for now.** A consumer wanting file+line+column of the exact call/import site behind an edge gets the edge's coarse `line` plus the `derived_from`→node-span join, not a dedicated span record. This is an honest reduction of the *promised* surface, not the *delivered* surface (which was already zero) — but the spec's apparent capability shrinks.
- **A removal-shaped schema/spec edit.** Although byte-invisible on artifacts, the contract documents change; downstream readers of `schema.json` who coded against the declared-but-never-emitted keys (none are known in-tree; out-of-tree readers have not been checked) must follow the spec correction.

### Neutral / acknowledged

- **`evidence_type` is untouched.** It is a different field on a different axis (ADR-0028), populated 110,533/110,533.
- **INV-luhur does not close on this ADR.** The umbrella ("meta-layer writers have no validator") closes solely via the declared-fields population-contract engine and the ADR-0033 validator stage; this ADR removes two rows from that engine's future workload and adds one enforceable contract row ("every edge carries `evidence_lang`").
- **The per-site stamps in the ~22 long-tail analyzers are dead weight, not hazards,** once the chokepoint lands; their removal is hygiene, not contract.

## Tracker items

- `WI-vozar-ropuf-nozuh-kubut-dohas-nakis-nalim-lajid` — "evidence_spans declared on Edge but 0/110533 populated; done-status met only by constructor round-trip test." Resolved by ruling 1 (removal) when the descope PR lands.
- `WI-gijus-laduz-zubuk-vutir-mopun-kopon-jipuh-dutag` — "meta.evidence[] multi-pass accumulator documented (spec §799) but has no backing Edge field; structurally impossible." Resolved by ruling 1 (spec correction; the spec line has since drifted to 814).
- `WI-kuluh-mobaf-zofap-hobum-bojir-fosin-vibat-hujuv` — "meta.evidence_lang null on 100% of 110533 edges incl spec-required cross-language." Resolved by ruling 2 (central stamping) when the chokepoint PR lands and a fresh substrate shows population; per the campaign's behavioral-closure discipline, presence of the code is not closure evidence.
- `INV-luhur-sital-lonar-tagiv-kuzab-mokol-jikot-fosug` — "META: AnalysisRun + behavior-map meta-layer writers have no validator." **Cross-reference only** — it closes via the population-contract engine (declared-fields family + ADR-0033 validator stage), not via this ADR.

## References

- Strategy: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` — declared-fields family (F4 "evidence implement-vs-descope", Wave-4 item 7, open-decisions list item re: declared-fields:F4); raw analysis JSON alongside it.
- ADR-0028 — `evidence_type` axiom (inference pathway); unaffected by this descope.
- ADR-0033 — the validator stage whose writer-contract class makes "declared ⇒ populated" checkable; the enforcement home for the new `evidence_lang` contract row.
- ADR-0039 — confidence separation; consumes `evidence_lang` as the matrix `lang` coordinate.
- Code: `ir.py::Edge.create` (Edge fields), `ir.py::Edge.create` (`Edge.create` chokepoint), `ir.py::Edge.create` (`to_dict` None-dropping), `test_edge_has_evidence_spans` in `test_ir.py` (the round-trip-only test, deleted with the field).
- Spec/schema: spec §9 (meta.evidence[] prose), spec §9 (meta-fields bullets), spec §9 (consumer-side fallback), spec §16 (stale done-marker); `docs/schema.json` (evidence_lang / evidence_spans declarations), `docs/schema.json` (Edge-meta description).
