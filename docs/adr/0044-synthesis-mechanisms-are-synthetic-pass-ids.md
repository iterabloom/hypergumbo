<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0044: Symbol-Synthesis Values Are Synthetic Pass IDs, Not a Separate Axis — WI-kadop `synthesis_mechanism` Field-Split Withdrawn

- Status: **Accepted**
- Date: 2026-07-01
- Supersedes: —
- Superseded by: —
- Related: ADR-0024 (Axis Declaration Template — the field-split promotion math this ADR *declines* to apply), ADR-0027 / ADR-0031 / ADR-0032 (the `Symbol.kind` / `Symbol.language` / `Symbol.canonical_name` field-splits this ADR was expected to mirror but does not), the WI-busij `# axis: pass-id` conformance linter (`multi_value_field_axis`), `catalog.all_known_pass_ids()`; tracker items WI-kadop (this decision re-scopes it), INV-numat (parent umbrella — vocabulary fields mixing semantic axes), WI-dizir / WI-mosil / WI-sijut (synthetic:F1 — the intervening work that changed the ground truth), WI-zabus (the SCIP-side follow-on split off here).

## Context

`Symbol.origin` (and `Edge.origin`) is declared `# axis: pass-id`: each element names a pass that contributed to the record. The WI-busij conformance linter resolves that axis against `catalog.all_known_pass_ids()`.

WI-kadop (filed 2026-06-01, parented to INV-numat) observed that some Symbols carried values in `origin` that named *how* the Symbol was synthesized rather than *which registered pass* produced it — `inheritance`, `orchestrator_file_symbol_synthesis`, `boundary_external_symbol_synthesis`, `scip` — and proposed the campaign's established remedy: split a new typed `Symbol.synthesis_mechanism` field off `origin`, mirroring ADR-0031 (`language` → `discovery_language` + `protocol_origin`) and ADR-0032 (`canonical_name` → `display_label` + `qualified_name`). Phase 6 PR5 added `_SYNTHESIS_MECHANISMS = {inheritance, orchestrator_file_symbol_synthesis, boundary_external_symbol_synthesis, scip}` to `all_known_pass_ids()` as a stopgap so the conformance linter wouldn't false-positive until "the sibling-field ADR ships."

### What changed since WI-kadop was filed

Re-verifying the premise against the current codebase (empirically, on the self-corpus) surfaced that the ground truth had shifted:

| Value | Symbols on self-corpus | Owning `AnalysisRun` / real pass-id? |
|---|---|---|
| `orchestrator_file_symbol_synthesis` | 518 | **Yes** — `analyze/all_analyzers.py` emits a real `AnalysisRun` with this `pass_id`; the synthesized Symbols' `origin_run_id` joins to it (synthetic:F1 / WI-dizir·WI-mosil) |
| `boundary_external_symbol_synthesis` | 1620 | **Yes** — same shape, `ir.create_boundary_nodes` + join (synthetic:F1 / WI-sijut) |
| `inheritance` | **0** | n/a — **no producer** sets the bare string (the inheritance linker stamps `make_pass_id("inheritance-linker")`) — a **phantom** |
| `scip` | 0 (off-corpus) | **No** owning run on the Symbol side — bare literal; the SCIP-import edges *do* join a `rust-scip-translate` run, but the Symbols do not |

The **synthetic:F1** campaign (which post-dates WI-kadop's filing) gave the two high-volume synthesizers real `AnalysisRun`s whose `pass_id` *is* the origin value. That collapses the "mechanism vs pass" distinction the field-split assumed: a synthesized Symbol's *how* turns out to be *which pass synthesized it*.

## Decision

**Withdraw the `Symbol.synthesis_mechanism` field split. Treat the synthesis/import values as what they structurally are — synthetic pass IDs — and finish legitimizing them.**

Concretely:

1. **No new field.** `Symbol.origin` stays the sole home of pass-id provenance. There is no value that is genuinely "a mechanism that cannot be a pass": `orchestrator`/`boundary` *are* passes (real runs), `scip` *should* be one (WI-zabus), `inheritance` is dead.
2. **Rename `_SYNTHESIS_MECHANISMS` → `_SYNTHETIC_PASS_IDS`** and re-document it as legitimate synthetic pass IDs (not a "pending field-split" stopgap).
3. **Drop the `inheritance` phantom** — zero producers, so it can leave the catalog with no conformance effect.
4. **Split off the SCIP Symbol-side join gap as WI-zabus.** `scip` stays in `_SYNTHETIC_PASS_IDS` for now; giving SCIP-sourced Symbols a real `AnalysisRun` join (mirroring synthetic:F1, cross-package into `hypergumbo-lang-rust-analyzer`, off-self-corpus) is that follow-on's job, after which the bare `scip` literal is replaced by the real `rust-scip-translate` pass-id and can leave the set.

### Why not the field split (the option this ADR declines)

Because `orchestrator`/`boundary` Symbols' `origin_run_id` **must** stay joined to their `AnalysisRun` (keyed by that `pass_id`), `origin` must retain those values. A `synthesis_mechanism` field would therefore store the **same string in two fields** (`origin=[...]` *and* `synthesis_mechanism=...`) — introducing a redundancy leak rather than removing one. The field would have nothing clean to hold: it would duplicate legitimate pass-ids for the two real cases and carry a phantom + an off-corpus case for the other two. The INV-numat "one field, two axes" motivation does not apply once synthetic:F1 established that these are one axis (pass-id).

## Consequences

- The WI-busij `# axis: pass-id` conformance linter continues to pass (the three legitimate synthetic pass IDs remain resolvable via `all_known_pass_ids()`); no false positives.
- No IR shape change, no `SCHEMA_VERSION` bump, no producer/consumer migration for the self-corpus values — this is a catalog re-classification + documentation change plus this ADR.
- WI-kadop is re-scoped (not built as filed): its deliverable becomes this ADR + the catalog cleanup, with the SCIP provenance gap tracked as WI-zabus.
- Precedent: a filed field-split can be *correctly withdrawn* when intervening work (here synthetic:F1) resolves the axis conflation it targeted. Verify the premise before applying a campaign pattern mechanically.
