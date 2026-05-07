<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0009: Symbol.kind Cluster 27C — Apex/Peer Overloads

- Date: 2026-05-05
- Status: All rows PRELIM_RESOLVED at filing (producer migration is structurally complete — no analyzer emits the peer values; registry entries remain on `endpoint_shape` through the Phase 4a deprecation window per ADR-0027 §"Phase 4")
- Closes: WI-rusit-tagap-tumul-bisuv-nipad-sozop-kobit-larah (Cluster 27C apex/peer collapse, ADR-0027 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Fifth audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster 27A canonical), 0005 (Cluster 27B file-shape), 0006 (Cluster 27G build/config), and 0007 (Cluster 27H domain long-tail).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Detailed analysis: per-cluster fold targets" Cluster 27C is the apex/peer-overload cluster of `Symbol.kind`: pairs of values where multiple language analyzers emit different labels for the same source-language construct. The four pairs seeded by ADR-0027 Phase 1:

| Apex | Peer | Source-language construct |
|------|------|---------------------------|
| `function` | `fn` | Top-level function definition |
| `variable` | `var` | Variable / let / mutable binding |
| `procedure` | `proc` | Procedure / proc declaration |
| `struct` | `structure` | Struct / record-type declaration |

ADR-0027 §"Phase 3" Cluster 27C states: "Apex/peer overloads collapse to a single canonical per pair. fn → function, var → variable, proc → procedure, structure → struct. The choice of apex follows ADR-0023's pattern (most-frequent emitting language wins). This is Phase 3 producer migration; consumer-side enumerations don't need to change because the apex was already in the registry."

## Methodology

Per [ADR-0027 §"Phase 3"](../adr/0027-symbol-kind-language-construct-only.md) Cluster 27C. Test 2 (apex/peer overloading) from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-much-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md) is the load-bearing test: each peer is a duplicate label for the same source-language construct that the apex already names. The fold preserves analyzer semantics — every analyzer that previously had a choice now uses the apex.

The empirical claim of this audit: **the producer fold is structurally complete already.** A grep across `packages/` for `Symbol(kind="fn")`, `Symbol(kind="var")`, `Symbol(kind="proc")`, `Symbol(kind="structure")` (excluding test files, the registry module, and `compute_stable_id` / `make_symbol_id` arguments) returns zero matches. All language analyzers that previously had a peer-label choice already emit the canonical apex in `Symbol.kind` — the peer label only appears as a `kind=` argument to ID-construction helpers (`compute_stable_id`, `make_symbol_id`), where it persists in the symbol_id string for stable identity.

The verification command (this audit's diagnostic test for the cluster):

```bash
grep -rn '\bkind=["\047](fn|var|proc|structure)["\047]' packages/ scripts/ \
  | grep -v 'test_\|symbol_kinds.py\|compute_stable_id\|make_symbol_id'
```

Expected output: empty.

## Diagnostic findings

### 1. Zero `Symbol.kind=peer` emit sites

The four peer values (`fn`, `var`, `proc`, `structure`) appear **only** as arguments to `compute_stable_id(node, kind="fn")` / `make_symbol_id(..., "fn")` style calls. These calls construct stable identifiers that encode the peer label in the ID string format, but the actual `Symbol.kind` field in the same emit site uses the canonical apex (e.g., fennel.py line 183 passes `kind="fn"` to `compute_stable_id` while line 185 sets `kind="function"` on the Symbol).

This decoupling — peer labels in IDs, apex labels in `Symbol.kind` — was apparently always present in the producer code; the Cluster 27C fold is a documentation / classification exercise that catches up to a producer state that historically converged on canonical apexes.

### 2. The peer-in-symbol_id format is not in scope for this fold

The audit explicitly does not address the peer label persisting in the symbol_id string format (e.g., `fennel:foo.fnl:1-2:bar:fn` rather than `fennel:foo.fnl:1-2:bar:function`). symbol_id is opaque to consumers — they query `Symbol.kind`, not the trailing fragment of the ID — so the persistence is harmless. ADR-0014 (stable ID format) governs the ID shape independently of this audit; any decision to canonicalize the symbol_id format too belongs in an ADR-0014 amendment, not here.

### 3. Phase 4b prerequisite is satisfied at filing

The lifecycle table in [`docs/audits/README.md`](README.md) requires a producer migration to ship before status can advance from UNRESOLVED to PRELIM_RESOLVED. For Cluster 27C, the producer migration is "no analyzer emits the peer label." Since this is empirically true at filing time, all four rows ship at PRELIM_RESOLVED.

Phase 4b removal (ADR-0027 §"Phase 4 — Schema bump and deprecation removal") is gated on `awaits_bakeoff_validation` clearing. For Cluster 27C the bakeoff validation question is degenerate — there is nothing for the bakeoff to confirm, since no behavior is changing. The PR can either skip the tag (no quantitative claim) or carry it as "no_move trivially confirmed" (the bakeoff queue absorbs the row and the next DEEP cycle strips it). The decision is recorded in WI-rusit's tracker discussion.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: fn
    verdict: FOLD
    fold_target: function
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]fn[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py\\|compute_stable_id\\|make_symbol_id'"
      expect: empty
    rationale: "Apex/peer overload of `function`. Empirically: no analyzer currently emits Symbol(kind='fn') — the peer label appears only in stable_id / symbol_id construction. Producer fold is structurally complete; registry entry stays on endpoint_shape through the Phase 4a deprecation window."
  - value: var
    verdict: FOLD
    fold_target: variable
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]var[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py\\|compute_stable_id\\|make_symbol_id'"
      expect: empty
    rationale: "Apex/peer overload of `variable`. Empirically: no analyzer currently emits Symbol(kind='var') — the peer label appears only in stable_id / symbol_id construction. Producer fold is structurally complete; registry entry stays on endpoint_shape through the Phase 4a deprecation window."
  - value: proc
    verdict: FOLD
    fold_target: procedure
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]proc[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py\\|compute_stable_id\\|make_symbol_id'"
      expect: empty
    rationale: "Apex/peer overload of `procedure`. Empirically: no analyzer currently emits Symbol(kind='proc') — the peer label appears only in stable_id / symbol_id construction (Tcl `proc`, Odin `proc`, etc.). Producer fold is structurally complete; registry entry stays on endpoint_shape through the Phase 4a deprecation window."
  - value: structure
    verdict: FOLD
    fold_target: struct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]structure[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py\\|compute_stable_id\\|make_symbol_id'"
      expect: empty
    rationale: "Apex/peer overload of `struct`. Empirically: no analyzer currently emits Symbol(kind='structure'). Producer fold is structurally complete; registry entry stays on endpoint_shape through the Phase 4a deprecation window."
```

## Migration impact

- **Producer-side:** No code change. The 84 canonical-apex emit sites across `packages/` (function: ~50, variable: ~25, procedure: ~5, struct: ~4) already produce the canonical labels.
- **Registry-side:** Four entries (`fn`, `var`, `proc`, `structure`) move from `language_construct` / `pending_classification` to `endpoint_shape` per the lifecycle. Phase 4b removal is gated on `awaits_bakeoff_validation` clearing per ADR-0027 §"Phase 4 — Schema bump and deprecation removal".
- **Schema-side:** Open enum on `Symbol.kind` already accommodates the additive change. The `x-axis-of-values` annotation in `docs/schema.json` updates to reflect the new axis classifications. No SCHEMA_VERSION bump.
- **Consumer-side:** No code change. Consumers querying by axis (e.g., `symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)`) automatically pick up the change — the peer values are no longer in the canonical set.
- **Test-side:** No production-code test changes. The four values are still in the registry, so the drift linter and registry-property tests continue to pass.

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — declares the `Symbol.kind` axis this audit applies. §"Detailed analysis" Cluster 27C and §"Phase 3" Cluster 27C are the load-bearing references.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0027 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy.
- Audit-findings 0008 — the Cluster 28B precedent for "producer migration shipped, registry stays on endpoint_shape during Phase 4a window" (Edge.evidence_type axis).
- WI-runod cross-axis schedule — Wave 4 of which this PR closes one item.
