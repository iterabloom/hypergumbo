<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0041: Supply-Chain Tier Purity — Directness and Ecosystem Are Separate Axes

- Status: **Accepted**
- Date: 2026-06-10
- Decided by: project owner, via design interview (2026-06-10) over the verified 446-item tracker root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`, supply-verdict-truth family — 9 items; listed there as open human decision "tier/directness split (supply:F5)"). This document records a decided ruling, not a proposal.
- Supersedes: the direct-dependency → `Tier.INTERNAL_DEP` mapping (`DependencyManifest.classify_import`, `supply_chain.py:111-112`, and the boundary-node tier-min relabeling at `ir.py:1269-1278`); spec §14's tier-2 prose in both its drifted forms (the original "internal libraries, monorepo packages **plus test dirs**" and the implementation's silent repurposing to "direct external dependencies").
- Superseded by: —
- Related: ADR-0004 (File Taxonomy — adopted the 4-tier provenance model this ADR purifies), ADR-0024 (Axis Declaration Template — registration mechanism for the two new axes), ADR-0035 (stable_id v6 — ruling 4's per-manifest dependency nodes are the join from which directness is derivable), ADR-0039 (confidence separation — sibling single-meaning-per-field decision in the same train).

## Context

### One field, two meanings

`Tier` (`supply_chain.py:49-55`) is an ordinal `IntEnum` whose documented semantic is supply-chain *position*: 1 first-party, 2 internal dependency, 3 external dependency, 4 derived artifact. Every consumer treats it ordinally — `--max-tier` filtering does `tier.value <= N` traversal cuts (spec §3, §14), and `TIER_WEIGHTS` (`ranking.py:147`, `{1: 2.0, 2: 1.5, 3: 1.0, 4: 0.0}`) treats lower tier as more trustworthy/relevant.

Into this distance axis, the manifest classifier smuggled a second meaning: *declaration status*. `DependencyManifest.classify_import` (`supply_chain.py:111-112`) returns `Tier.INTERNAL_DEP` for any import whose longest-prefix manifest match carries `direct: true` — that is, **a third-party PyPI/Go-module package gets tier 2 simply for being declared in the project's manifest**. The boundary-node synthesizer compounds it: tier-min selection at `ir.py:1269-1278` relabels canonical external nodes to tier 2 with reason strings like `"direct dependency (pyproject.toml)"`.

So tier 2 operationally meant "workspace package OR declared third-party package" — distance and directness conflated in one cell.

### The stdlib inversion

The conflation produced a concrete trust inversion. Python stdlib modules (`os`, `json`, `pathlib`) — shipped with the runtime, near-zero supply-chain risk — classify as tier 3 (`classify_import` finds no manifest entry). Any declared PyPI package classifies as tier 2. Every `TIER_WEIGHTS` consumer therefore weighted an arbitrary third-party package (1.5) **above** the standard library (1.0). The ranking surface, slice tier filtering, and any "how trusted is this node" read-out all inherited the inversion.

### The phantom-twin amplifier

INV-nuzas (P1, violated) documents the workspace-resolution failure: in-tree `hypergumbo_*` modules emit as external symbols with `path='<external>'`. The direct-dependency mapping then *amplified* the mislabel — 144 first-party workspace modules relabeled tier 2 with reason `"direct dependency (pyproject.toml)"` (verified on the self-corpus: `nodes_by_tier = {'external_dep': 1228, 'internal_dep': 480}`). A first-party module masquerading as a "trusted direct dependency" is two lies stacked; this ADR removes the second one (the tier relabel), while the first (the phantom twin itself) is owned by the workspace-resolution work under INV-nuzas.

### The documentation residue

Spec §14's tier table drifted twice (WI-golov): row 2 still says "Internal libraries, monorepo packages" while listing test directories under tier 2 — but the INV-tisid fix re-routed tests to tier 1 (`is_test=True`), and the implementation meanwhile repurposed tier 2 to "direct external dependencies" (on the self-corpus, 100% of tier-2 nodes are `external_symbol`-kind). Three incompatible tier-2 definitions coexist across spec prose, docstrings, and code. INV-tisid (P2, violated) is held open by exactly this residue.

## Decision

Four rulings, all decided.

### §1 Tier names supply-chain distance — and nothing else

The axiom (per ADR-0024, one sentence, falsifiable): *`tier` names the supply-chain distance of code from the project under analysis; no other fact may influence the value.*

| Tier | Name | Post-split semantic |
|------|------|---------------------|
| 1 | `first_party` | The project's own source, including its tests (`is_test=True`) |
| 2 | `internal_dep` | Workspace / org-internal packages **only** (monorepo siblings, local forks) |
| 3 | `external_dep` | **All** third-party code, regardless of declaration status — direct, transitive, undeclared, and stdlib alike |
| 4 | `derived` | Derived/vendored build artifacts (transpiled, bundled, minified) — unchanged |

The direct-dependency → `Tier.INTERNAL_DEP` mapping **retires**. `classify_import` (`supply_chain.py:111-112`) returns `EXTERNAL_DEP` for every third-party match; the boundary tier-min relabeling (`ir.py:1269-1278`) and its `"direct dependency (…)"` reason strings retire with it — the direct/transitive knowledge the classifier was burning into the tier is re-emitted as the directness stamp (§2). Enum names and numbering are untouched: `--max-tier N` keeps its ordinal contract and now reads coherently as "distance ≤ N."

Note the irony recorded for posterity: this ruling lands tier 2 *closer to spec §14's original prose* ("internal libraries, monorepo packages") than the implementation's drift did. The implementation, not the original concept, is what's being reverted — but the spec still needs §4's rewrite for the test routing, directness, and ecosystem additions.

### §2 Directness becomes its own recorded fact

A registered `Symbol.meta` key `directness` (a `MetaKeySpec` entry in `axis_meta_keys.py`, `AXIS_SYMBOL_META`), with the axiom: *`directness` records the declaration relationship between the project's manifests and an external dependency — `direct` (declared in a project manifest), `transitive` (pulled in by another dependency), or `undeclared` (imported but declared nowhere).*

It is stamped **once, at classification time**, on dependency and boundary nodes — the single producer is the classification chokepoint that today computes the tier-2/tier-3 split. `undeclared` is itself a supply-chain signal (a phantom dependency: imported, resolved from somewhere, declared in no manifest).

Directness is derivable from the per-manifest dependency-node join (ADR-0035 ruling 4: a package declared in N manifests is N nodes), but it is **recorded anyway**, per the no-consumer-re-derivation rule. META-bifif is the cluster-level lesson: producers emit a finer/correct verdict that consumers ignore or re-derive coarser — WI-popok is the worked example (entrypoint detector re-deriving test-ness from a filename heuristic instead of consuming `supply_chain.is_test_file`). Making each consumer reconstruct directness from the manifest join would mint the next instance of that family.

ADR-0024 promotion-math note: three distinct values touches the ≥3-value promotion threshold, but with exactly one producer module and a population restricted to dependency/boundary nodes, the registered-meta-key form is the ruling. Promotion to a typed `supply_chain.directness` field remains available under ADR-0024 §"Fold-residue discipline" if producers multiply.

### §3 Stdlib stays tier 3, distinguished by a registered ecosystem axis

Stdlib modules remain tier 3 — they are external to the project; distance says so. The residual trust/provenance distinction that supply-chain triage actually needs moves to a registered `ecosystem` axis (initial values `stdlib | third_party`, extensible per language), with the axiom: *`ecosystem` names the provenance class of an external dependency's distribution channel — shipped with the language runtime (`stdlib`) vs fetched from a package registry (`third_party`).* Registered as a `MetaKeySpec` entry; lightweight axis per ADR-0024 §4.

This is **not** the existing `package_ecosystem` meta key (`axis_meta_keys.py:233`), which names the package-*manager registry* of package-shaped symbols (`npm`, `composer`). Different question (which registry vs which provenance class); both keys keep their lanes, and the registration descriptions must say so explicitly.

**The single-source constraint (normative).** Exactly one stdlib catalog per language feeds **both** the supply-chain classifier's ecosystem stamping **and** the io-boundary closed-world gates: the language-profile `stdlib_modules` machinery (python.yaml, parsed and merged at `io_boundary.py:391,508-521,665-680`; WI-bifih wires the same catalog into Python import resolution). Two independent stdlib-recognition sources would recreate the consumer-re-derivation disease *inside the campaign that exists to fix it*. Per-language extension goes through the same profile machinery (Go's no-dot first-segment heuristic at `supply_chain.py:89-96` may remain Go's recognizer only as the single shared implementation both consumers call, not as a private second source).

Consumers: `supply_chain_summary` sub-buckets tier-3 externals by ecosystem; ranking may key on `(tier, ecosystem)` where the distinction matters. The triage payoff is the one the conflated axis could never express: a `third_party` vuln means *pin/update the package*; a `stdlib` vuln means *upgrade the runtime*.

### §4 Spec §14 is rewritten to the post-split semantics

The spec §14 rewrite documents: the §1 tier table and axiom; tests at tier 1 with `is_test=True`; the directness and ecosystem stamps with their value vocabularies; `supply_chain_summary` ecosystem sub-bucketing; and removal of the tier-2 test-directory listing and the `"direct dependency"` reason-string examples. This closes the INV-tisid documentation residue (the strategy doc's "docs-only slice can close INV-tisid early") and is the deliverable of WI-golov.

## Consequences

### Positive

- **The stdlib inversion self-resolves for ranking.** All third-party code is tier 3 / weight 1.0; no `TIER_WEIGHTS` consumer can weight a PyPI package above the standard library again. The ecosystem axis carries the residual distinction for consumers that want it.
- **The tier axis is single-meaning.** `--max-tier` reads as a pure distance cut; "kills the tier-2 'direct dependency' lie on every monorepo" (strategy doc, prioritized-fix row 27).
- **INV-nuzas's amplifier is removed.** Phantom workspace twins can no longer be relabeled into "trusted direct dependencies"; they fail honestly as tier-3 externals until the workspace-resolution fix lands.
- **Directness survives as a better fact than it was as a tier.** As a tier it could only say "direct ⇒ closer"; as a recorded stamp it distinguishes direct/transitive/undeclared — the third value (phantom-dependency detection) was inexpressible under the old encoding.

### Negative

- **Behavior-map churn.** Every node currently tier 2 via the manifest mapping moves to tier 3; `tier_name` changes in JSON output; consumers filtering `tier <= 2` see direct deps drop out. On the self-corpus, tier 2 becomes *empty* until INV-nuzas's workspace resolution correctly populates it with the 7 actual workspace packages (today's tier-2 cohort is 100% `external_symbol`-kind, i.e. all of it is the retiring mapping).
- **Ranking shifts.** Direct third-party deps drop from weight 1.5 to 1.0. Quantitative effects on ranking/slice quality are bakeoff-validation-tagged per the tagging discipline, not asserted here.
- **Two new registered meta keys** plus their registry descriptions, schema documentation, and property tests — incremental vocabulary surface under the ADR-0024 regime.

### Neutral / acknowledged

- **INV-nuzas is not fixed by this ADR.** The phantom twins come from workspace-member union in the manifest layer and missing in-tree module resolution (supply:F3+F4); this ADR only stops promoting the mislabeled nodes to tier 2.
- **WI-bifih's analyzer-side import resolution is separate work.** This ADR makes its catalog the mandated single source; wiring it into Python import resolution proceeds under its own item.
- **Tier 4 is untouched.** Derived-artifact detection and exclusion semantics are out of scope.

## Alternatives considered

1. **Keep-and-document.** Leave the mapping, document that tier 2 means "workspace OR declared third-party." Rejected: the axis stays two-meaning — exactly the pattern the 6.0.0 concept-axis campaign (ADR-0023/0027/0028/0031/0032) exists to eliminate; the stdlib inversion survives untouched.
2. **Full enum redesign** (`first_party / workspace / platform / third_party_direct / third_party_transitive / …`). Rejected: re-conflates directness into *more* cells instead of zero, breaks every ordinal consumer, and forces a large migration for no semantic gain over two orthogonal recorded facts.
3. **New `platform` tier for stdlib.** Rejected twice over: it re-admits a second meaning (trust) into the just-purified distance axis, and it renumbers an ordinal enum that `--max-tier` filtering and `TIER_WEIGHTS` keys depend on.
4. **Weights-only fix** (adjust `TIER_WEIGHTS` so tier 2 ≤ tier 3, or special-case stdlib in ranking). Rejected: the classification stays wrong; the next consumer of `tier` re-inherits the inversion. This is the workaround-vs-root-cause distinction of the Structural Fix Protocol.

## Tracker items

Full eight-segment IDs per tracker convention:

- `INV-tisid-ponal-vokap-kopap-bofin-rogob-jufus-zihut` — P2, violated: workspace-package test dirs routed to tier 2; code fix landed (tests → tier 1), held open by the spec §14 documentation residue. §4 closes it.
- `WI-golov-pitiz-tahim-hitiz-dotog-goduk-jifup-jagur` — P1, todo_hard: spec §14 tier-2 text never updated after the INV-tisid refactor; the work item that executes §4's rewrite.
- `WI-bifih-haguk-jilat-kobof-hoduj-fabal-noras-dosih` — P1, todo_hard: wire python.yaml `stdlib_modules` into Python import resolution; its catalog is §3's mandated single source for stdlib recognition.
- `INV-nuzas-rumaf-nijaz-kivah-hovov-riviz-jodip-hunap` — P1, violated (cross-ref): tier classifier mislabels workspace and first-party source as external. Fixed by workspace resolution (supply:F3+F4), not this ADR; this ADR removes the tier-2 relabel that amplified its phantom twins.
- `WI-popok-dijih-hohub-pojop-novar-dakus-kulaj-sufif` — P3, todo_hard (cross-ref): entrypoint detector re-derives test-ness instead of consuming the canonical verdict; the worked precedent behind §2's record-don't-re-derive ruling.
- `META-bifif-luzin-nuzul-fogom-famig-hupoh-zanav-pohud` — META: producers emit a finer/correct verdict that consumers ignore or re-derive coarser; §2 and §3's single-source constraint are its enactment for the supply-chain surface.

## References

- ADR-0004 (File Taxonomy) — adopted the 4-tier provenance model whose tier-2 cell this ADR purifies.
- ADR-0024 (Axis Declaration Template) — registration mechanism and promotion math for the `directness` and `ecosystem` axes.
- ADR-0035 (stable_id v6 Identity Contract) — ruling 4's per-manifest dependency nodes; the join from which directness is derivable but deliberately not re-derived.
- ADR-0039 (confidence separation) — sibling single-meaning-per-field ruling in the same 2026-06-10 decision train.
- Code: retiring mapping `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py:111-112`; boundary relabeling `packages/hypergumbo-core/src/hypergumbo_core/ir.py:1269-1278`; `TIER_WEIGHTS` `packages/hypergumbo-core/src/hypergumbo_core/ranking.py:147`; Tier enum `supply_chain.py:49-55`; stdlib-profile machinery `io_boundary.py:391,508-521,665-680`.
- Spec: `docs/hypergumbo-spec.md` §14 (rewritten per §4), §3 (`--max-tier`).
- Strategy document: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` (supply-verdict-truth family; fixes F5 tier/directness split; open-decision ledger item "tier/directness split (supply:F5)" — resolved by this ADR).
