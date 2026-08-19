<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0004: Edge.evidence_type Cluster 28A — Canonical Inference Pathways

- Date: 2026-05-05
- Status: All rows RESOLVED
- Closes: WI-nurot-lituj-ganoh-nujup-gigot-dohib-mosas-vikub (Cluster 28A audit-findings: canonical inference-pathway evidence types, ADR-0028 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). First audit-findings doc on the `Edge.evidence_type` axis declared by [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md).

## Context

[ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) declares `Edge.evidence_type` as the inference-pathway axis — the label that names *how* an edge was inferred (AST traversal, SCIP indexer evidence, naming-convention heuristic, tree-sitter pattern, framework-specific dispatch detection, etc.). Cluster 28A is the canonical seed: ~110 values whose names identify the inference pathway directly (`ast_*`, `scip_*`, `naming_convention`, `tree_sitter`, `import_*`, `module_*`, etc.) without smuggling resolution status, framework dispatch context, or call construct into the label.

ADR-0028 §"Detailed analysis: per-cluster fold targets" assigns Cluster 28A the canonical verdict at axis declaration time. This audit-findings document records the per-value verdicts so the Cluster 28A baseline is enumerated under the audit-findings format — the same posture the sibling audit-findings 0003 takes for ADR-0027's Cluster 27A.

This is the no-migration cluster on the ADR-0028 roadmap: no producer changes are required for these values; the registry already places them on `inference_pathway`, and Phase 3 (B / C / D) folds preserve their canonical form.

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test diagnostic procedure are defined in [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). This document applies that methodology to the Cluster 28A subset of `Edge.evidence_type` values.

The `diagnostic_test` field for each row is a one-line Python invocation that asserts the value is present in the live `EVIDENCE_TYPES` registry on the `inference_pathway` axis. The `expect: exit_code:0` shape lets a future audit runner execute the test and assert success.

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.evidence_type
verdicts:
  - value: ast_annotation
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_annotation\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a type/decorator annotation in source AST."
  - value: ast_attribute
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_attribute\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an attribute access in source AST."
  - value: ast_call
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic call expression in source AST."
  - value: ast_call_direct
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_direct\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a direct (non-method) call site."
  - value: ast_call_extension
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_extension\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an extension-method call (Kotlin / Swift / C#)."
  - value: ast_call_inherited
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_inherited\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a call on an inherited member."
  - value: ast_call_inherited_field
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_inherited_field\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from access to an inherited field."
  - value: ast_call_inherited_method
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_inherited_method\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a call on an inherited method."
  - value: ast_call_static
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_static\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a static (class-level) method call."
  - value: ast_call_this
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_this\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `this`/`self` receiver call."
  - value: ast_call_this_property
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_this_property\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `this.property` / `self.attr` resolved access."
  - value: ast_call_type_inferred
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_call_type_inferred\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a call site where the receiver type was inferred."
  - value: ast_cite
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_cite\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a citation/cross-reference link in source."
  - value: ast_decorator
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_decorator\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a decorator/annotation node in source AST."
  - value: ast_extends
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_extends\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an `extends` clause in source AST."
  - value: ast_implements
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_implements\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an `implements` clause in source AST."
  - value: ast_import
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_import\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an import statement in source AST."
  - value: ast_include
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_include\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an include directive in source AST (C/C++)."
  - value: ast_method_inferred
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_method_inferred\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a method call where dispatch was inferred."
  - value: ast_method_this
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_method_this\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `this`/`self` method call."
  - value: ast_method_this_property
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_method_this_property\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `this.prop` / `self.attr` reference."
  - value: ast_method_type_inferred
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_method_type_inferred\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a method call with type-inferred receiver."
  - value: ast_new
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_new\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `new` constructor expression."
  - value: ast_package
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_package\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a package declaration."
  - value: ast_perform
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_perform\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `perform`/effect-handler invocation (OCaml/Eff)."
  - value: ast_ref
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_ref\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic name reference in source AST."
  - value: ast_static_call
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_static_call\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a static method call (qualifier-resolved)."
  - value: ast_type_ref
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"ast_type_ref\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a type reference (annotation, generic, etc.)."
  - value: async_spawn
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"async_spawn\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an async spawn / task-creation construct."
  - value: behaviour
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"behaviour\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an Erlang `-behaviour(...)` attribute."
  - value: behaviour_callback
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"behaviour_callback\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an Erlang behaviour callback definition."
  - value: bridging_header_import
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"bridging_header_import\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an Objective-C bridging-header import."
  - value: build_dependency
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"build_dependency\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a build-system dependency declaration."
  - value: build_target_main
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"build_target_main\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a build target's main entry point."
  - value: callable_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"callable_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a callable reference (Kotlin `::fn`, etc.)."
  - value: callback_argument_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"callback_argument_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a callback function passed as an argument."
  - value: canonical_name
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"canonical_name\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from canonical-name resolution."
  - value: cgo_call
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"cgo_call\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Go cgo C-function call."
  - value: closure_wrapper
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"closure_wrapper\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a closure/lambda wrapper construct."
  - value: cmake_target_link
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"cmake_target_link\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a CMake `target_link_libraries` call."
  - value: constructor_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"constructor_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a constructor reference (Java `::new`, etc.)."
  - value: designated_init_fptr
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"designated_init_fptr\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a designated-initializer function pointer (C99)."
  - value: dispatch_pattern
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"dispatch_pattern\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic dispatch-pattern recognition."
  - value: dispatch_table_initializer
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"dispatch_table_initializer\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a dispatch-table initializer entry."
  - value: dispatch_table_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"dispatch_table_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a reference into a dispatch table."
  - value: dockerfile_copy_from
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"dockerfile_copy_from\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Dockerfile `COPY --from=...` directive."
  - value: dockerfile_from
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"dockerfile_from\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Dockerfile `FROM` directive."
  - value: enclosing_scope
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"enclosing_scope\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an enclosing-scope relationship."
  - value: eta_expansion
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"eta_expansion\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an eta-expansion (point-free → pointed)."
  - value: extends
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"extends\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic extends/inheritance relationship."
  - value: function_pointer
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"function_pointer\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a function-pointer assignment or use."
  - value: function_pointer_arg
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"function_pointer_arg\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a function pointer passed as an argument."
  - value: function_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"function_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a function reference (not a call)."
  - value: function_reference_arg
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"function_reference_arg\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a function reference passed as an argument."
  - value: hash_field_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"hash_field_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a hash/dict field reference."
  - value: hg_annotation
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"hg_annotation\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a hypergumbo-emitted analyzer annotation."
  - value: import
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"import\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic import construct."
  - value: import_declaration
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"import_declaration\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an import declaration node."
  - value: import_directive
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"import_directive\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an import directive (C# `using`, etc.)."
  - value: import_statement
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"import_statement\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an import statement node."
  - value: import_static
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"import_static\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Java `import static` declaration."
  - value: import_to_manifest
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"import_to_manifest\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a manifest-driven import resolution."
  - value: include
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"include\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic include construct."
  - value: include_directive
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"include_directive\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `#include` directive (C / C++)."
  - value: instance
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"instance\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a typeclass / trait instance declaration."
  - value: interface_dispatch
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"interface_dispatch\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from interface-method dispatch resolution."
  - value: jsx_element
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"jsx_element\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a JSX element reference."
  - value: link
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"link\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an OTP link/monitor relationship."
  - value: make_prerequisite
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"make_prerequisite\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Make/CMake prerequisite declaration."
  - value: message_send
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"message_send\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a message-send construct (Erlang `!`, Smalltalk)."
  - value: method_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"method_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a method reference (Java `::method`, etc.)."
  - value: module_attribute_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"module_attribute_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a module-level attribute reference."
  - value: module_export_heuristic
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"module_export_heuristic\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a module-export heuristic recognition."
  - value: module_identifier_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"module_identifier_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a module-qualified identifier reference."
  - value: module_source
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"module_source\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a module's source-file relationship."
  - value: naming_convention
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"naming_convention\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a language-level naming convention."
  - value: notify
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"notify\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a notification/signal construct."
  - value: object_field_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"object_field_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an object-field reference."
  - value: open
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"open\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an `open` directive (OCaml / F#)."
  - value: open_import
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"open_import\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Go open-import (qualified-but-unbound)."
  - value: recipe_dependency
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"recipe_dependency\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Bazel/Buck recipe-dependency declaration."
  - value: reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic name-reference."
  - value: require
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"require\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `require` construct (Ruby / Node)."
  - value: require_alias_call
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"require_alias_call\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `require(...)` aliased to a local name."
  - value: require_dynamic
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"require_dynamic\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a dynamic `require(...)` call."
  - value: require_statement
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"require_statement\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a top-level `require` statement."
  - value: require_static
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"require_static\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a static `require(...)` invocation."
  - value: schema_relation
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"schema_relation\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a schema-declared relation."
  - value: scip_occurrence_ref
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"scip_occurrence_ref\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a SCIP occurrence cross-reference."
  - value: scip_relationship
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"scip_relationship\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a SCIP-emitted symbol-relationship record."
  - value: signal_constraint
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"signal_constraint\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from an HDL signal-constraint declaration."
  - value: source_statement
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"source_statement\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a generic source-level statement."
  - value: span_overlap
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"span_overlap\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from text-span overlap between symbols."
  - value: sql_foreign_key
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"sql_foreign_key\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a SQL `FOREIGN KEY` constraint."
  - value: stack_construction
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"stack_construction\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a stack-frame construction site."
  - value: static
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"static\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a static-linkage declaration."
  - value: struct_field_reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"struct_field_reference\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a struct-field reference."
  - value: subdir_include
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"subdir_include\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a subdirectory-include in a build file."
  - value: trait_impl
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"trait_impl\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Rust `impl Trait for Type` block."
  - value: tree_sitter
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"tree_sitter\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a tree-sitter query match."
  - value: type_hierarchy
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"type_hierarchy\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a type-hierarchy traversal."
  - value: typeclass_instance
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"typeclass_instance\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a typeclass-instance declaration (Haskell, Scala)."
  - value: use
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"use\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `use` directive (Rust, PHP)."
  - value: use_declaration
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"use_declaration\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `use` declaration node."
  - value: use_directive
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"use_directive\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `use` directive (qualifier-bound)."
  - value: use-package
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"use-package\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Common Lisp `use-package` form (hyphenated identifier per CL convention)."
  - value: using_directive
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"using_directive\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a `using` directive (C# / C++)."
  - value: variable_match
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"variable_match\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a variable-name match across sites."
  - value: verilog_instantiation
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"verilog_instantiation\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a Verilog module instantiation."
  - value: vhdl_architecture
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.evidence_types import EVIDENCE_TYPES; assert any(s.name == \"vhdl_architecture\" and s.axis == \"inference_pathway\" for s in EVIDENCE_TYPES)'"
      expect: exit_code:0
    rationale: "Cluster 28A inference pathway: Edge inferred from a VHDL architecture declaration."
```

## Related

- [ADR-0028: Edge.evidence_type Names the Inference Pathway, Not Resolution Status](../adr/0028-evidence-type-inference-pathway-only.md) — the originating axis declaration. Cluster 28A is the canonical seed.
- [ADR-0027: Symbol.kind Names the Source-Language Syntactic Construct](../adr/0027-symbol-kind-language-construct-only.md) — sibling-axis ADR; Cluster 27A audit-findings 0003 carries the analogous canonical seed for `Symbol.kind`.
- [ADR-0024: Axis Declaration Template](../adr/0024-axis-declaration-template.md) — §"Family-audit verdict methodology" defines the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy applied here.
- [`docs/audits/README.md`](README.md) — format spec.
- [Audit-findings 0003](0003-symbol-kind-cluster-a-language-constructs.md) — sibling Cluster 27A audit on the `Symbol.kind` axis.
- WI-runod (cross-axis Phase 3 sequencing schedule) — this document is Wave 1 in the schedule.
