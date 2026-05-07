<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0008: Edge.evidence_type Cluster 28B — Resolution-Status Leakage

- Date: 2026-05-05
- Status: All rows PRELIM_RESOLVED at filing (Phase 3 producer migration shipped this PR; values remain on `endpoint_shape` through the Phase 4a deprecation window per ADR-0028 §"Phase 4")
- Closes: WI-nunal-razud-gajum-fukig-lijul-bahik-bumul-vugis (Cluster 28B canary producer fold, ADR-0028 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Second audit-findings doc on the `Edge.evidence_type` axis declared by [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md), companion to audit-findings 0004 (Cluster 28A canonical inference).

## Context

[ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) §"Phase 3" Cluster 28B is the resolution-status-leakage cluster of `Edge.evidence_type`: every value of the form `*_unresolved` or `unresolved_*` smuggles a resolution-status property into the inference-pathway label. The "this dst was unresolved at analysis time" property belongs on the `Edge.is_resolved: bool` sibling field (added alongside the registry in ADR-0028 §"Sibling-field design call-out"), not on the inference label.

The 18 Cluster 28B values seeded by ADR-0028 Phase 1 (registry lines 325–360):

```
ast_annotation_unresolved, ast_attribute_unresolved, ast_call_unresolved_import,
ast_decorator_unresolved, ast_method_unresolved_global, ast_method_unresolved_namespace,
chained_call_unresolved, django_signal_receiver_unresolved, grpc_unresolved_resolution,
luajit_ffi_unresolved, ruby_ffi_attach_unresolved, trait_impl_unresolved,
unresolved_dotted_submodule_call, unresolved_external_call, unresolved_imported_name_call,
unresolved_method_call, unresolved_module_call, unresolved_variable_method_call
```

Wave 3 of the WI-runod cross-axis schedule designates this cluster as the **canary** producer fold for ADR-0028: the smallest blast radius (~22 emit sites across 11 producer files, vs ~30+ for Cluster 28C and ~60+ for Cluster 28D), the most mechanical fold pattern (strip suffix → set `is_resolved=False`), and the first production write of the new `is_resolved` field.

This audit answers: **all 18 values FOLD** to canonical Cluster 28A (AXIS_INFERENCE_PATHWAY) inference labels + `Edge.is_resolved=False`. Two values (`grpc_unresolved_resolution`, `luajit_ffi_unresolved`) require new Cluster 28A canonical entries (`grpc_stub_resolution`, `luajit_ffi_lookup`); the other sixteen fold to existing Cluster 28A entries.

**No new axis ADR required.** The four-leakage-test pass for each of the 18 values fired exclusively on Test 4 (mechanism vs. category) — the `_unresolved` suffix encodes a resolution-status property, not an inference-pathway category — and is exactly the leak that ADR-0028's `Edge.is_resolved` sibling field absorbs.

## Methodology

Per [ADR-0028 §"Phase 3" Cluster 28B](../adr/0028-evidence-type-inference-pathway-only.md). Each value's verdict applies the four leakage tests from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md). Test 4 (mechanism vs. category) is the load-bearing test for this cluster: every value pairs an inference-pathway label with a resolution-status flag, where the flag is mechanism (HOW the dst symbol arrived: resolved vs. heuristic-guess) and the inference-pathway is category (WHAT the inference looked at: an annotation, a call, a method invocation).

Producer-side migration: each emit site replaces `evidence_type="<name>_unresolved"` with `evidence_type="<canonical>"` + `is_resolved=False`. The L3 producer-side coherence linter (`scripts/check-producer-axis-coherence`) catches drift at pre-commit.

## Diagnostic findings

### 1. Two new Cluster 28A canonicals required

`grpc_unresolved_resolution` and `luajit_ffi_unresolved` had no existing Cluster 28A counterpart in the registry — every other Cluster 28B value's fold target was already present (`ast_annotation`, `ast_attribute`, `ast_call_direct`, `ast_decorator`, `ast_method_inferred`, `method_call`, `method_call_field_chain`, `method_call_type_inferred`, `ruby_ffi_attach`, `trait_impl`, `django_signal_receiver`).

Two new Cluster 28A entries shipped in this PR:

- `grpc_stub_resolution` — Edge inferred from a gRPC stub-method resolution lookup (the gRPC linker's heuristic match between client stub and server method definition).
- `luajit_ffi_lookup` — Edge inferred from a LuaJIT FFI symbol lookup.

Both are canonical inference-pathway labels: they name the AST/IR pattern the inference looked at, with no resolution-status component on the label itself.

### 2. django_signal_receiver_unresolved is Cluster 28B+C — Wave 3 strips Cluster 28B only

`django_signal_receiver_unresolved` is the only value in this cluster that also carries a Cluster 28C (framework-dispatch) leak — the `django_signal_receiver` portion encodes "this is a Django @receiver-decorated handler", a framework-specific dispatch convention.

Wave 3's scope is Cluster 28B only. The producer migration in this PR sets `evidence_type="django_signal_receiver"` (preserving the Cluster 28C value, which remains on AXIS_ENDPOINT_SHAPE) + `is_resolved=False`. Wave 5 (WI-kagik framework-dispatch coordinated fold) will subsequently fold `django_signal_receiver` itself to a canonical inference label + `meta["framework_dispatch"]="django_signal"`.

This two-step migration is intentional and follows the ADR-0028 §"Migration" sequencing principle of one-axis-per-wave: stripping resolution-status now without waiting for framework-dispatch fold lets Wave 3 ship as a pure Cluster 28B canary, and lets Wave 5 ship as a pure Cluster 28C coordinated fold.

### 3. Three "all-fold-to-ast_call_direct" values are not Cluster 28C overloads

`unresolved_dotted_submodule_call`, `unresolved_external_call`, `unresolved_imported_name_call`, `unresolved_module_call` all fold to the same canonical (`ast_call_direct`). The values' producer-side comments described them as different *receiver shapes* (dotted submodule vs. bare external vs. imported-name vs. module-qualified), but those distinctions are derivable from the dst Symbol's ID format — the inference pathway is the same in all four cases (an AST direct-call expression). The shape information is on the dst, not on the edge's evidence label, so the canonical value carries no shape suffix.

This is the same shape-not-on-label discipline ADR-0023 §"Detailed analysis" enforced for `Edge.edge_type`: when a property is derivable from the endpoints, do not duplicate it on the edge label.

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.evidence_type
verdicts:
  - value: ast_annotation_unresolved
    verdict: FOLD
    fold_target: ast_annotation
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ast_annotation_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the ast_annotation pathway. Producer-side fold: evidence_type=ast_annotation + is_resolved=False. Consumers: kotlin.py:1471, java.py:1782 — both annotation-decoration emit sites where the annotation reference could not be resolved at analysis time."
  - value: ast_attribute_unresolved
    verdict: FOLD
    fold_target: ast_attribute
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ast_attribute_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the ast_attribute pathway. Producer: csharp.py:1446 — C# attribute decoration with unresolved attribute reference. Fold: evidence_type=ast_attribute + is_resolved=False."
  - value: ast_call_unresolved_import
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ast_call_unresolved_import\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the ast_call_direct pathway. Producer: js_ts.py:3741 — JS/TS call to imported name not resolvable to a definition. Fold: evidence_type=ast_call_direct + is_resolved=False."
  - value: ast_decorator_unresolved
    verdict: FOLD
    fold_target: ast_decorator
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ast_decorator_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the ast_decorator pathway. Producers: py.py:2846, js_ts.py:2473 — Python @decorator and JS/TS @decorator emit sites with unresolved decorator references. Fold: evidence_type=ast_decorator + is_resolved=False."
  - value: ast_method_unresolved_global
    verdict: FOLD
    fold_target: ast_method_inferred
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ast_method_unresolved_global\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. The 'global' suffix is shape on the dst (a global object's method), not on the inference pathway. Producer: js_ts.py:3944. Fold: evidence_type=ast_method_inferred + is_resolved=False."
  - value: ast_method_unresolved_namespace
    verdict: FOLD
    fold_target: ast_method_inferred
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ast_method_unresolved_namespace\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. The 'namespace' suffix is shape on the dst, not on the inference pathway. Producer: js_ts.py:3880. Fold: evidence_type=ast_method_inferred + is_resolved=False."
  - value: chained_call_unresolved
    verdict: FOLD
    fold_target: method_call_field_chain
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"chained_call_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the method_call_field_chain apex. Producer: go.py:2292 — Go chained-call expression. Fold: evidence_type=method_call_field_chain + is_resolved=False."
  - value: django_signal_receiver_unresolved
    verdict: FOLD
    fold_target: django_signal_receiver
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"django_signal_receiver_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Cluster 28B+C combined leak. Wave 3 (this PR) strips the Cluster 28B (resolution-status) component; the Cluster 28C (framework-dispatch) component remains on the django_signal_receiver value (still on endpoint_shape) and folds in Wave 5 / WI-kagik. Producer: py.py:2924. Fold: evidence_type=django_signal_receiver + is_resolved=False."
  - value: grpc_unresolved_resolution
    verdict: FOLD
    fold_target: grpc_stub_resolution
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"grpc_unresolved_resolution\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. New Cluster 28A canonical grpc_stub_resolution shipped in this PR (registry line 199). Producer: linkers/grpc.py:934 — gRPC client-stub-to-server-method heuristic resolution. Fold: evidence_type=grpc_stub_resolution + is_resolved=False."
  - value: luajit_ffi_unresolved
    verdict: FOLD
    fold_target: luajit_ffi_lookup
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"luajit_ffi_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. New Cluster 28A canonical luajit_ffi_lookup shipped in this PR (registry line 229). Producer: linkers/lua_ffi.py:243. Fold: evidence_type=luajit_ffi_lookup + is_resolved=False."
  - value: ruby_ffi_attach_unresolved
    verdict: FOLD
    fold_target: ruby_ffi_attach
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ruby_ffi_attach_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the ruby_ffi_attach pathway. Producer: linkers/ruby_ffi.py:240 — Ruby FFI attach call to external library. Fold: evidence_type=ruby_ffi_attach + is_resolved=False."
  - value: trait_impl_unresolved
    verdict: FOLD
    fold_target: trait_impl
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"trait_impl_unresolved\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the trait_impl pathway. Producer: rust.py:1134 — Rust 'impl Trait for Type' where Trait could not be resolved. Fold: evidence_type=trait_impl + is_resolved=False."
  - value: unresolved_dotted_submodule_call
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unresolved_dotted_submodule_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status + dst-shape leakage. The dotted-submodule shape is derivable from the dst Symbol's ID; it does not belong on the inference label. Producer: py.py:3281. Fold: evidence_type=ast_call_direct + is_resolved=False."
  - value: unresolved_external_call
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unresolved_external_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. Producer: analyze/base.py:410 — generic unresolved-external-call helper used by multiple analyzers. Fold: evidence_type=ast_call_direct + is_resolved=False."
  - value: unresolved_imported_name_call
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unresolved_imported_name_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. The 'imported_name' shape is derivable from the dst Symbol's ID. Producer: py.py:3299. Fold: evidence_type=ast_call_direct + is_resolved=False."
  - value: unresolved_method_call
    verdict: FOLD
    fold_target: method_call
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unresolved_method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the method_call pathway. Producers: py.py:3232, py.py:3244, go.py:2512. Fold: evidence_type=method_call + is_resolved=False."
  - value: unresolved_module_call
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unresolved_module_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage. The 'module' shape is derivable from the dst Symbol's ID. Producer: elixir.py:1153. Fold: evidence_type=ast_call_direct + is_resolved=False."
  - value: unresolved_variable_method_call
    verdict: FOLD
    fold_target: method_call_type_inferred
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unresolved_variable_method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Resolution-status leakage on top of the method_call_type_inferred apex. The 'variable' shape (call on an inferred-type variable) is captured by the canonical apex. Producer: py.py:3258. Fold: evidence_type=method_call_type_inferred + is_resolved=False."
```

## Migration impact

- **Producer-side:** 22 emit sites across 11 files migrated from `evidence_type="<name>_unresolved"` to `evidence_type="<canonical>"` + `is_resolved=False`. Files: `analyze/base.py`, `linkers/lua_ffi.py`, `linkers/ruby_ffi.py`, `linkers/grpc.py`, `hypergumbo_lang_common/elixir.py`, `hypergumbo_lang_mainstream/csharp.py`, `hypergumbo_lang_mainstream/rust.py`, `hypergumbo_lang_mainstream/py.py` (×6), `hypergumbo_lang_mainstream/go.py` (×2), `hypergumbo_lang_mainstream/kotlin.py`, `hypergumbo_lang_mainstream/js_ts.py` (×4), `hypergumbo_lang_mainstream/java.py`.
- **Registry-side:** Two new Cluster 28A canonical entries added (`grpc_stub_resolution`, `luajit_ffi_lookup`). The 18 Cluster 28B values stay in the registry on AXIS_ENDPOINT_SHAPE through the Phase 4a deprecation window per ADR-0028 §"Phase 4"; Phase 4b (gated on bakeoff validation per the `awaits_bakeoff_validation` discipline) will remove them.
- **Schema-side:** Open enum on `Edge.evidence_type` already accommodates the additive change (no SCHEMA_VERSION bump). The two new Cluster 28A canonicals appear in the by-axis view auto-generated by `scripts/generate-concept-axes`.
- **Test-side:** Tests previously asserting `evidence_type == "<name>_unresolved"` on the migrated edges update to assert `evidence_type == "<canonical>" and is_resolved is False`.

## Related

- [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) — declares the `Edge.evidence_type` axis and `Edge.is_resolved` sibling field this audit applies.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0028 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy.
- Audit-findings 0004 — Cluster 28A canonical inference (the registry seed for the inference_pathway axis).
- WI-runod cross-axis schedule — Wave 3 of which this PR closes.
