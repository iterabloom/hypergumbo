<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0011: Symbol.kind Cluster F — Component / UI References

- Date: 2026-05-05
- Status: Mixed — `component_ref` PRELIM_RESOLVED (shape-2 edge-endpoint redesign + producer drop ship in this PR); `component`, `slot`, `prop`, `view` CANONICAL + RESOLVED on `language_construct`; `component_file` deferred to audit-findings 0005 Cluster B per the file-shape verdict already on record there.
- Closes: WI-mihiz-vulon-tidiz-napir-vabup-zudun-jurat-lolaz (Cluster F, ADR-0027 Phase 3).
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Seventh audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster A canonical), 0005 (Cluster B file-shape), 0006 (Cluster G build/config), 0007 (Cluster H domain long-tail), 0009 (Cluster C apex/peer), and 0010 (Cluster E edge-label leakage).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Detailed analysis: per-cluster fold targets" Cluster F is the component / UI-reference cluster of `Symbol.kind`. Six values seeded by Phase 1: `component`, `component_ref`, `component_file`, `slot`, `prop`, `view`. Five are unproblematic syntactic constructs (`component`, `slot`, `prop`, `view`) or file-shape entries (`component_file`) already governed by another audit; the sixth (`component_ref`) is a dst-kind leakage of the same shape ADR-0023 §"Detailed analysis" Cluster F caught for `imports_component`. The Phase-3 prescription for `component_ref` was originally "fold to `reference` + `dst.kind == 'component'`", but `reference` was itself resolved DEPRECATE-NO-FOLD in audit-findings 0010 sub-case (b). This audit therefore substitutes a parallel disposition: drop `component_ref` entirely and let the `imports` Edge carry the relationship, exactly as audit-findings 0010 PR 2 did for `puppet.py`'s `include` and `scss.py`'s `include` (also shape-2 edge-endpoint sites).

## Methodology

Per [ADR-0027 §"Phase 3" Cluster F](../adr/0027-symbol-kind-language-construct-only.md) and the four leakage tests from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md). Test 4 (mechanism vs. category) is the load-bearing test for `component_ref`: the `_ref` suffix names the *use* of a thing, not the thing itself, while the `imports` Edge already names the use-relationship structurally. Two values describing the same relationship — once via `Symbol.kind` and once via `Edge.edge_type` — is the canonical signature of a category-vs-mechanism leak.

The audit's empirical scope is `Symbol(kind=...)` emissions only. The producer count below is the count of `Symbol(kind="<value>", ...)` constructions across `packages/`.

## Diagnostic findings

### 1. `component_ref` is structurally the same shape as audit-findings 0010 sub-case (b) shape-2

The three `component_ref` producers — `vue.py:555`, `svelte.py:245`, `astro.py:316` — each emit a `component_ref` Symbol *and* a companion `imports` Edge whose `src` is the Symbol id. This is precisely the audit-findings 0010 sub-case (b) shape-2 ("edge-endpoint dependency"): dropping the Symbol orphans the Edge unless the Edge `src` is re-routed to a containing file/scope id first.

The PR 2 pattern from WI-zarov (audit-findings 0010 §"Diagnostic findings" #2) applies unchanged: re-route the `imports` Edge `src` to the source file's `make_file_id` boundary, move the component name to `meta["component_name"]`, then drop the Symbol. The companion `vue_component` linker (`linkers/vue_component.py`) reads its source path from the existing `symbol_path_map`, which after the refactor will not contain a Symbol for the file-id `src`; this audit's PR ships a `_resolve_source_path` helper that prefers `edge.meta["source_path"]` (set by the producer) and falls back to parsing the path slot out of the `make_file_id`-shaped `src` if needed.

### 2. The original ADR-0027 prescription "fold to `reference`" is invalidated by audit-findings 0010

ADR-0027 §"Detailed analysis" Cluster F line 167 records the fold target for `component_ref` as "`reference` + `dst.kind == 'component'`". That target value was deprecated in audit-findings 0010 sub-case (b) (`reference` → DEPRECATE-NO-FOLD, no replacement Symbol kind, the relationship lives on the `references` / `imports` Edge). The cross-axis disposition for `component_ref` therefore loses its fold target at the same time. This audit applies the parallel resolution: DEPRECATE-NO-FOLD, no replacement Symbol kind, the `imports` Edge carries the relationship.

This is structurally identical to the propagation already captured in audit-findings 0010's PR 2 verdicts: shape-2 edge-endpoint sites all resolved to DEPRECATE-NO-FOLD, not to a fold-into-`reference`.

### 3. `component`, `slot`, `prop`, `view` are unambiguous CANONICAL on `language_construct`

All four name genuine syntactic constructs:
- `component` — QML / Blade / VHDL component declarations (`qml.py:97`, `blade.py:124`, `vhdl.py:214`).
- `slot` — Vue / Svelte / Astro `<slot>` declarations (`vue.py:654`, `svelte.py:301`, `astro.py:400`).
- `prop` — Vue prop declarations (`vue.py:367`, `vue.py:425`).
- `view` — SQL `CREATE VIEW` (`sql.py:254`).

Each is an AST-level declarative form in its source language. The four leakage tests from the Fundamental Concept Audit playbook all return non-leaky on these values: each `Symbol(kind=...)` describes a declaration site, not a relationship, not a dst-kind summary, and not a flavor of an existing canonical. RESOLVED at this audit.

### 4. `component_file` is governed by audit-findings 0005

`component_file` was filed in audit-findings 0005 Cluster B (file-shape entries) as `verdict: FOLD, fold_target: file, status: PRELIM_RESOLVED` with the rationale "Vue / Svelte / Astro single-file component. Structurally a kind=file with framework metadata. Fold to file + meta['component_framework']='vue' (or 'svelte', etc.)." The Cluster B Wave 6 PR group (per audit-findings 0005 §"Migration impact") will execute the producer-side fold. This audit-findings doc only notes the cross-cluster reference and does not introduce a separate verdict row.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: component_ref
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]component_ref[\"\\047]' packages/ scripts/ | grep -v 'test_\\|BRANCHES_test\\|symbol_kinds.py'"
      expect: empty
    rationale: "Cluster F dst-kind leakage; the imports Edge captures the relationship; no replacement Symbol kind. Three producers (vue.py, svelte.py, astro.py) all shape-2 edge-endpoint-dependent (imports Edge had src=symbol_id). This PR re-routes the Edge src to make_file_id(<lang>, rel_path) and moves the component name to Edge.meta['component_name'], then drops the Symbol. The original ADR-0027 fold target (`reference`) was deprecated in audit-findings 0010 sub-case (b); this audit applies the parallel DEPRECATE-NO-FOLD disposition."
  - value: component
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"component\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Genuine syntactic construct in QML / Blade / VHDL. AST-level declarative form. No leakage."
  - value: slot
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"slot\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Genuine syntactic construct in Vue / Svelte / Astro. <slot> declaration. No leakage."
  - value: prop
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"prop\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Genuine syntactic construct in Vue. Component prop declaration. No leakage."
  - value: view
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"view\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Genuine syntactic construct in SQL. CREATE VIEW declaration. No leakage."
```

`component_file` is intentionally absent — its verdict and disposition are recorded in audit-findings 0005 Cluster B (file-shape) as `FOLD → file + meta['component_framework']`. Wave 6 of the Cluster B migration handles the producer-side fold; tracking it here would dual-file the verdict.

## Migration impact

- **Producer-side this PR:** three producer migrations.
  - `vue.py:553` — `Symbol(kind="component_ref", ...)` removed; companion `imports` Edge `src` re-routed from `symbol_id` to `make_file_id("vue", str(rel_path))`; `meta["component_name"]=tag_name` and `meta["source_path"]=str(rel_path)` added; `meta["directives"]` and `meta["has_slot_attr"]` (previously on the Symbol) move to the Edge.
  - `svelte.py:245` — same shape; `meta["events"]` and `meta["has_slot_attr"]` move to the Edge.
  - `astro.py:316` — same shape; `meta["client_directive"]` and `meta["attributes"]` move to the Edge.
- **Linker-side this PR:** `linkers/vue_component.py` source-path lookup updated to prefer `edge.meta["source_path"]` (set by the producer) over `symbol_path_map[edge.src]` (which after the refactor returns empty for file-id `src` values). Backwards-compatible — pre-refactor edges still resolve via the existing path map.
- **Registry-side this PR:** `component_ref` registry entry stays on `AXIS_ENDPOINT_SHAPE` through the Phase 4a deprecation window (per audit-findings 0009 / 0010 precedent); description updated to reference this audit. The registry-side prune is a Phase 4b operation gated on bakeoff validation.
- **Schema-side:** no change. The drop is producer-side; the open enum on `Symbol.kind` is unaffected.
- **Consumer-side:** no immediate change. Tests asserting `kind="component_ref"` migrate (~9 test files updated in this PR — assertions move from Symbol-side to Edge-side, mirroring the audit-findings 0010 PR 2 pattern).
- **Test-side:** ~9 test files updated. The `vue_component_linker` tests change from constructing `component_ref` Symbol fixtures to constructing `imports` Edge fixtures with file-id `src` and `meta["source_path"]`. Diagnostic-test assertions in `tests/test_audit_findings.py` continue to pass (every row's status agrees with registry presence).

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — declares the `Symbol.kind` axis this audit applies. §"Detailed analysis" Cluster F and §"Phase 3" Cluster F are the load-bearing references.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0027 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy.
- [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` precedent for dst-kind-leakage cleanup; `imports_component` was caught and folded to `imports` + `dst.kind=='component'` per ADR-0023 §"Detailed analysis" Cluster F. `component_ref` is the same leak shape on the `Symbol.kind` axis.
- Audit-findings 0010 — Cluster E shape-2 PR 2 precedent (puppet.py / scss.py / json_config.py / twig.py); the structural template this audit's `component_ref` resolution applies.
- Audit-findings 0005 — Cluster B file-shape; governs `component_file` (referenced but not re-filed here).
- WI-runod cross-axis schedule — Wave 4 of which this PR closes one item.
