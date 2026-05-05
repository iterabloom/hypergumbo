# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of Edge.evidence_type values in hypergumbo's behavior map.

Per ADR-0028, every value in the canonical registry should have
``axis="inference_pathway"`` — ``Edge.evidence_type`` names the inference
pathway by which the analyzer concluded this edge exists, with resolution
status queried from ``Edge.is_resolved`` and framework dispatch convention
queried from ``Edge.meta`` rather than smuggled into the evidence label.

This module is the single source of truth: ``scripts/generate-schema``
imports ``EVIDENCE_TYPES`` to emit ``x-axis-of-values`` annotations on
the ``Edge.evidence_type`` schema property. (The schema enum stays open
— ``type: "string"`` only — until per-cluster Phase 4b producer
migrations land; see ADR-0028 §"Phase 4" and the Path-B decision in the
Phase 1 plan file.) Consumers that need a subset of evidence types
should call ``evidence_types_on_axis(...)`` rather than maintain their
own hardcoded set; the property test in
``tests/test_evidence_types.py`` enforces that every hardcoded set in
the codebase whose name contains ``EVIDENCE_TYPE`` is a subset of this
registry, and the L3 producer-coherence linter at
``scripts/check-producer-axis-coherence`` enforces that
``Edge.create(evidence_type="...")`` literal arguments are also in the
registry.

Axis taxonomy (per ADR-0028 §1):

- ``inference_pathway`` — ADR-0028 compliant. The value names how the
  analyzer concluded this edge exists (Cluster A in the WI-turin-pajuk
  audit).
- ``endpoint_shape`` — deprecation candidate per ADR-0028 §"Detailed
  analysis: per-cluster fold targets". The value's meaning is captured
  by ``Edge.is_resolved`` (Cluster B's ``*_unresolved`` resolution
  status), by ``Edge.meta`` keys (Cluster C's framework dispatch
  conventions; Cluster D's call-construct surface forms), and folds
  back into the canonical inference label plus the appropriate sibling.
- ``pending_classification`` — deferred to per-cluster audit-findings
  doc per ADR-0028 §"Migration".

Seeding completeness (per the Phase 1 plan file):

- 207 static-literal evidence_type values from ``grep packages/*/src``.
- 10 enumerable dynamic variants from f-string emits at
  ``websocket.py:572`` (``{pattern_type}_emit``) and
  ``websocket.py:613`` (``{pattern_type}_endpoint``) for the 5
  registered ``pattern_type`` literals (``socketio``, ``native``, ``ws``,
  ``fastapi``, ``django_channels``).
- 1 placeholder ``di_binding`` for the unbounded colon-form emit at
  ``di_resolution.py:608`` (``f"di_binding:{binding.source}"``);
  Phase 3 producer migration normalizes that site to a canonical
  inference label plus ``meta["framework_dispatch"]``.
- The dynamic ``f"ast_{edge_type}"`` at ``inheritance.py:258`` only
  yields ``ast_extends`` / ``ast_implements``, both already in the
  static set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


AXIS_INFERENCE_PATHWAY: Final[str] = "inference_pathway"
AXIS_ENDPOINT_SHAPE: Final[str] = "endpoint_shape"
AXIS_PENDING: Final[str] = "pending_classification"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_INFERENCE_PATHWAY,
    AXIS_ENDPOINT_SHAPE,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class EvidenceTypeSpec:
    """A single Edge.evidence_type value and its axis classification."""

    name: str
    axis: str
    description: str


EVIDENCE_TYPES: Final[tuple[EvidenceTypeSpec, ...]] = (
    # ----------------------------------------------------------------
    # Cluster A — Canonical inference pathways (AXIS_INFERENCE_PATHWAY).
    # Each value names how the analyzer concluded this edge exists.
    # ----------------------------------------------------------------

    # AST-derived inference labels.
    EvidenceTypeSpec("ast_annotation", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a type/decorator annotation in source AST."),
    EvidenceTypeSpec("ast_attribute", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an attribute access in source AST."),
    EvidenceTypeSpec("ast_call", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic call expression in source AST."),
    EvidenceTypeSpec("ast_call_direct", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a direct (non-method) call site."),
    EvidenceTypeSpec("ast_call_extension", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an extension-method call (Kotlin / Swift / C#)."),
    EvidenceTypeSpec("ast_call_inherited", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a call on an inherited member."),
    EvidenceTypeSpec("ast_call_inherited_field", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from access to an inherited field."),
    EvidenceTypeSpec("ast_call_inherited_method", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a call on an inherited method."),
    EvidenceTypeSpec("ast_call_static", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a static (class-level) method call."),
    EvidenceTypeSpec("ast_call_this", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `this`/`self` receiver call."),
    EvidenceTypeSpec("ast_call_this_property", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `this.property` / `self.attr` resolved access."),
    EvidenceTypeSpec("ast_call_type_inferred", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a call site where the receiver type was inferred."),
    EvidenceTypeSpec("ast_cite", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a citation/cross-reference link in source."),
    EvidenceTypeSpec("ast_decorator", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a decorator/annotation node in source AST."),
    EvidenceTypeSpec("ast_extends", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an `extends` clause in source AST."),
    EvidenceTypeSpec("ast_implements", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an `implements` clause in source AST."),
    EvidenceTypeSpec("ast_import", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an import statement in source AST."),
    EvidenceTypeSpec("ast_include", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an include directive in source AST (C/C++)."),
    EvidenceTypeSpec("ast_method_inferred", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a method call where dispatch was inferred."),
    EvidenceTypeSpec("ast_method_this", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `this`/`self` method call."),
    EvidenceTypeSpec("ast_method_this_property", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `this.prop` / `self.attr` reference."),
    EvidenceTypeSpec("ast_method_type_inferred", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a method call with type-inferred receiver."),
    EvidenceTypeSpec("ast_new", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `new` constructor expression."),
    EvidenceTypeSpec("ast_package", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a package declaration."),
    EvidenceTypeSpec("ast_perform", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `perform`/effect-handler invocation (OCaml/Eff)."),
    EvidenceTypeSpec("ast_ref", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic name reference in source AST."),
    EvidenceTypeSpec("ast_static_call", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a static method call (qualifier-resolved)."),
    EvidenceTypeSpec("ast_type_ref", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a type reference (annotation, generic, etc.)."),

    # Generic inference / detection labels.
    EvidenceTypeSpec("async_spawn", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an async spawn / task-creation construct."),
    EvidenceTypeSpec("behaviour", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an Erlang `-behaviour(...)` attribute."),
    EvidenceTypeSpec("behaviour_callback", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an Erlang behaviour callback definition."),
    EvidenceTypeSpec("bridging_header_import", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an Objective-C bridging-header import."),
    EvidenceTypeSpec("build_dependency", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a build-system dependency declaration."),
    EvidenceTypeSpec("build_target_main", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a build target's main entry point."),
    EvidenceTypeSpec("callable_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a callable reference (Kotlin `::fn`, etc.)."),
    EvidenceTypeSpec("callback_argument_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a callback function passed as an argument."),
    EvidenceTypeSpec("canonical_name", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from canonical-name resolution."),
    EvidenceTypeSpec("cgo_call", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Go cgo C-function call."),
    EvidenceTypeSpec("closure_wrapper", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a closure/lambda wrapper construct."),
    EvidenceTypeSpec("cmake_target_link", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a CMake `target_link_libraries` call."),
    EvidenceTypeSpec("constructor_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a constructor reference (Java `::new`, etc.)."),
    EvidenceTypeSpec("designated_init_fptr", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a designated-initializer function pointer (C99)."),
    EvidenceTypeSpec("dispatch_pattern", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic dispatch-pattern recognition."),
    EvidenceTypeSpec("dispatch_table_initializer", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a dispatch-table initializer entry."),
    EvidenceTypeSpec("dispatch_table_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a reference into a dispatch table."),
    EvidenceTypeSpec("dockerfile_copy_from", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Dockerfile `COPY --from=...` directive."),
    EvidenceTypeSpec("dockerfile_from", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Dockerfile `FROM` directive."),
    EvidenceTypeSpec("enclosing_scope", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an enclosing-scope relationship."),
    EvidenceTypeSpec("eta_expansion", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an eta-expansion (point-free → pointed)."),
    EvidenceTypeSpec("extends", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic extends/inheritance relationship."),
    EvidenceTypeSpec("function_pointer", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a function-pointer assignment or use."),
    EvidenceTypeSpec("function_pointer_arg", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a function pointer passed as an argument."),
    EvidenceTypeSpec("function_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a function reference (not a call)."),
    EvidenceTypeSpec("function_reference_arg", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a function reference passed as an argument."),
    EvidenceTypeSpec("grpc_stub_resolution", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a gRPC stub-method resolution lookup. "
                     "Cluster B canonical for `grpc_unresolved_resolution` (ADR-0028 §Phase 3 Cluster B / WI-nunal)."),
    EvidenceTypeSpec("hash_field_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a hash/dict field reference."),
    EvidenceTypeSpec("hg_annotation", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a hypergumbo-emitted analyzer annotation."),
    EvidenceTypeSpec("import", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic import construct."),
    EvidenceTypeSpec("import_declaration", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an import declaration node."),
    EvidenceTypeSpec("import_directive", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an import directive (C# `using`, etc.)."),
    EvidenceTypeSpec("import_statement", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an import statement node."),
    EvidenceTypeSpec("import_static", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Java `import static` declaration."),
    EvidenceTypeSpec("import_to_manifest", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a manifest-driven import resolution."),
    EvidenceTypeSpec("include", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic include construct."),
    EvidenceTypeSpec("include_directive", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `#include` directive (C / C++)."),
    EvidenceTypeSpec("instance", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a typeclass / trait instance declaration."),
    EvidenceTypeSpec("interface_dispatch", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from interface-method dispatch resolution."),
    EvidenceTypeSpec("jsx_element", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a JSX element reference."),
    EvidenceTypeSpec("link", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an OTP link/monitor relationship."),
    EvidenceTypeSpec("luajit_ffi_lookup", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a LuaJIT FFI symbol lookup. "
                     "Cluster B canonical for `luajit_ffi_unresolved` (ADR-0028 §Phase 3 Cluster B / WI-nunal)."),
    EvidenceTypeSpec("make_prerequisite", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Make/CMake prerequisite declaration."),
    EvidenceTypeSpec("message_send", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a message-send construct (Erlang `!`, Smalltalk)."),
    EvidenceTypeSpec("method_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a method reference (Java `::method`, etc.)."),
    EvidenceTypeSpec("module_attribute_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a module-level attribute reference."),
    EvidenceTypeSpec("module_export_heuristic", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a module-export heuristic recognition."),
    EvidenceTypeSpec("module_identifier_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a module-qualified identifier reference."),
    EvidenceTypeSpec("module_source", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a module's source-file relationship."),
    EvidenceTypeSpec("naming_convention", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a language-level naming convention."),
    EvidenceTypeSpec("notify", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a notification/signal construct."),
    EvidenceTypeSpec("object_field_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an object-field reference."),
    EvidenceTypeSpec("open", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an `open` directive (OCaml / F#)."),
    EvidenceTypeSpec("open_import", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Go open-import (qualified-but-unbound)."),
    EvidenceTypeSpec("recipe_dependency", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Bazel/Buck recipe-dependency declaration."),
    EvidenceTypeSpec("reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic name-reference."),
    EvidenceTypeSpec("require", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `require` construct (Ruby / Node)."),
    EvidenceTypeSpec("require_alias_call", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `require(...)` aliased to a local name."),
    EvidenceTypeSpec("require_dynamic", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a dynamic `require(...)` call."),
    EvidenceTypeSpec("require_statement", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a top-level `require` statement."),
    EvidenceTypeSpec("require_static", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a static `require(...)` invocation."),
    EvidenceTypeSpec("schema_relation", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a schema-declared relation."),
    EvidenceTypeSpec("scip_occurrence_ref", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a SCIP occurrence cross-reference."),
    EvidenceTypeSpec("scip_relationship", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a SCIP-emitted symbol-relationship record."),
    EvidenceTypeSpec("signal_constraint", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an HDL signal-constraint declaration."),
    EvidenceTypeSpec("source_statement", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a generic source-level statement."),
    EvidenceTypeSpec("span_overlap", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from text-span overlap between symbols."),
    EvidenceTypeSpec("sql_foreign_key", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a SQL `FOREIGN KEY` constraint."),
    EvidenceTypeSpec("stack_construction", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a stack-frame construction site."),
    EvidenceTypeSpec("static", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a static-linkage declaration."),
    EvidenceTypeSpec("struct_field_reference", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a struct-field reference."),
    EvidenceTypeSpec("subdir_include", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a subdirectory-include in a build file."),
    EvidenceTypeSpec("trait_impl", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Rust `impl Trait for Type` block."),
    EvidenceTypeSpec("tree_sitter", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a tree-sitter query match."),
    EvidenceTypeSpec("type_hierarchy", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a type-hierarchy traversal."),
    EvidenceTypeSpec("typeclass_instance", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a typeclass-instance declaration (Haskell, Scala)."),
    EvidenceTypeSpec("use", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `use` directive (Rust, PHP)."),
    EvidenceTypeSpec("use_declaration", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `use` declaration node."),
    EvidenceTypeSpec("use_directive", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `use` directive (qualifier-bound)."),
    EvidenceTypeSpec("use-package", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Common Lisp `use-package` form (hyphenated identifier per CL convention)."),
    EvidenceTypeSpec("using_directive", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `using` directive (C# / C++)."),
    EvidenceTypeSpec("variable_match", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a variable-name match across sites."),
    EvidenceTypeSpec("verilog_instantiation", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a Verilog module instantiation."),
    EvidenceTypeSpec("vhdl_architecture", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a VHDL architecture declaration."),

    # ----------------------------------------------------------------
    # Cluster B — Resolution-status leakage (AXIS_ENDPOINT_SHAPE).
    # Every *_unresolved / unresolved_* variant doubles as a flag
    # squeezed into the inference label. Phase 3 strips the suffix and
    # sets Edge.is_resolved=False.
    # ----------------------------------------------------------------
    EvidenceTypeSpec("ast_annotation_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_annotation` + `is_resolved=False`."),
    EvidenceTypeSpec("ast_attribute_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_attribute` + `is_resolved=False`."),
    EvidenceTypeSpec("ast_call_unresolved_import", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_call_direct` (or producer-specific) + `is_resolved=False`."),
    EvidenceTypeSpec("ast_decorator_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_decorator` + `is_resolved=False`."),
    EvidenceTypeSpec("ast_method_unresolved_global", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_method_inferred` + `is_resolved=False`."),
    EvidenceTypeSpec("ast_method_unresolved_namespace", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_method_inferred` + `is_resolved=False`."),
    EvidenceTypeSpec("chained_call_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `method_call_field_chain` apex + `is_resolved=False`."),
    EvidenceTypeSpec("django_signal_receiver_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B+C fold: canonical inference + `meta['framework_dispatch']='django_signal'` + `is_resolved=False`."),
    EvidenceTypeSpec("grpc_unresolved_resolution", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: new canonical (e.g. `grpc_stub_resolution`) + `is_resolved=False`."),
    EvidenceTypeSpec("luajit_ffi_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: new canonical (e.g. `luajit_ffi_lookup`) + `is_resolved=False`."),
    EvidenceTypeSpec("ruby_ffi_attach_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ruby_ffi_attach` canonical + `is_resolved=False`."),
    EvidenceTypeSpec("trait_impl_unresolved", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `trait_impl` canonical + `is_resolved=False`."),
    EvidenceTypeSpec("unresolved_dotted_submodule_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: canonical call inference + `is_resolved=False`."),
    EvidenceTypeSpec("unresolved_external_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_call_direct` + `is_resolved=False`."),
    EvidenceTypeSpec("unresolved_imported_name_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `ast_call_direct` + `is_resolved=False`."),
    EvidenceTypeSpec("unresolved_method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `method_call` (post-collapse `ast_call`) + `is_resolved=False`."),
    EvidenceTypeSpec("unresolved_module_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: canonical call inference + `is_resolved=False`."),
    EvidenceTypeSpec("unresolved_variable_method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster B fold: `method_call_type_inferred` apex + `is_resolved=False`."),

    # ----------------------------------------------------------------
    # Cluster C — Framework-specific dispatch conventions
    # (AXIS_ENDPOINT_SHAPE). Phase 3 folds each to a canonical
    # inference label + meta['framework_dispatch'] / meta['detection_pattern'].
    # ----------------------------------------------------------------
    EvidenceTypeSpec("abi_name_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['detection_pattern']='abi_name_match'`."),
    EvidenceTypeSpec("activerecord_association", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='activerecord_association'`."),
    EvidenceTypeSpec("airflow_framework_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='airflow'`."),
    EvidenceTypeSpec("context_bridge_wrapper", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='electron_context_bridge'`."),
    EvidenceTypeSpec("controller_routes", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='controller_routes'`."),
    EvidenceTypeSpec("crypto_api_pattern", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['detection_pattern']='crypto_api'`."),
    EvidenceTypeSpec("cuda_kernel_launch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='cuda_kernel_launch'`."),
    EvidenceTypeSpec("di_binding", AXIS_ENDPOINT_SHAPE,
                     "Cluster C placeholder for `f\"di_binding:{source}\"` colon-form emits at di_resolution.py:608. Phase 3 folds to canonical + `meta['framework_dispatch']` per binding source."),
    EvidenceTypeSpec("django_orm_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='django_orm'`."),
    EvidenceTypeSpec("django_signal_receiver", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['framework_dispatch']='django_signal'`."),
    EvidenceTypeSpec("django_channels_emit", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:572): `f\"{pattern_type}_emit\"`. Fold: canonical + `meta['framework_dispatch']='django_channels'`."),
    EvidenceTypeSpec("django_channels_endpoint", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:613): `f\"{pattern_type}_endpoint\"`. Fold: canonical + `meta['framework_dispatch']='django_channels'`."),
    EvidenceTypeSpec("event_name_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical inference + `meta['detection_pattern']='event_name'`."),
    EvidenceTypeSpec("fastapi_emit", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='fastapi'`."),
    EvidenceTypeSpec("fastapi_endpoint", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='fastapi'`."),
    EvidenceTypeSpec("go_cobra_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='cobra'`."),
    EvidenceTypeSpec("go_memberlist_delegate", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='memberlist'`."),
    EvidenceTypeSpec("graphql_operation_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='graphql_operation'`."),
    EvidenceTypeSpec("grpc_go_server_method", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='grpc_go_server'`."),
    EvidenceTypeSpec("grpc_rpc_definition", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='grpc_rpc_definition'`."),
    EvidenceTypeSpec("grpc_server_to_service", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='grpc_server_to_service'`."),
    EvidenceTypeSpec("grpc_service_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='grpc_service_match'`."),
    EvidenceTypeSpec("http_url_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['detection_pattern']='http_url'`."),
    EvidenceTypeSpec("implicit_convention", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['detection_pattern']='implicit_convention'`."),
    EvidenceTypeSpec("jackson_bean_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='jackson_bean'`."),
    EvidenceTypeSpec("jni_naming_convention", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['detection_pattern']='jni_naming_convention'`."),
    EvidenceTypeSpec("job_enqueue", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='job_enqueue'`."),
    EvidenceTypeSpec("kafka_streams_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='kafka_streams'`."),
    EvidenceTypeSpec("middleware_chain", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='middleware_chain'`."),
    EvidenceTypeSpec("native_emit", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='native_websocket'`."),
    EvidenceTypeSpec("native_endpoint", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='native_websocket'`."),
    EvidenceTypeSpec("nestjs_module_registration", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: `ast_decorator` + `meta['framework_dispatch']='nestjs_module'`."),
    EvidenceTypeSpec("npm_package_import", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical import inference + `meta['framework_dispatch']='npm_package'`."),
    EvidenceTypeSpec("openapi_operation_id_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='openapi_operation_id'`."),
    EvidenceTypeSpec("openapi_path_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='openapi_path'`."),
    EvidenceTypeSpec("orm_accessor_pattern", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='orm_accessor'`."),
    EvidenceTypeSpec("otp_genserver_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='otp_genserver'`."),
    EvidenceTypeSpec("phoenix_event_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: `naming_convention` + `meta['detection_pattern']='phoenix_event'`."),
    EvidenceTypeSpec("pyo3_bridge", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='pyo3_bridge'`."),
    EvidenceTypeSpec("rails_block_callback", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='rails_block_callback'`."),
    EvidenceTypeSpec("rails_callback", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='rails_callback'`."),
    EvidenceTypeSpec("registry_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='registry_dispatch'`."),
    EvidenceTypeSpec("resolver_field_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='graphql_resolver_field'`."),
    EvidenceTypeSpec("resolver_type_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='graphql_resolver_type'`."),
    EvidenceTypeSpec("route_mount", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='route_mount'`."),
    EvidenceTypeSpec("router_routes", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='router_routes'`."),
    EvidenceTypeSpec("ruby_c_extension", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='ruby_c_extension'`."),
    EvidenceTypeSpec("ruby_delegate", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='ruby_delegate'`."),
    EvidenceTypeSpec("ruby_ffi_attach", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='ruby_ffi_attach'`."),
    EvidenceTypeSpec("rust_trait_dispatch", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='rust_trait_dispatch'`."),
    EvidenceTypeSpec("script_src", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='html_script_src'`."),
    EvidenceTypeSpec("socketio_emit", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='socketio'`."),
    EvidenceTypeSpec("socketio_endpoint", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='socketio'`."),
    EvidenceTypeSpec("specta_wrapper_import", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='specta_wrapper'`."),
    EvidenceTypeSpec("subprocess_cli_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['detection_pattern']='subprocess_cli'`."),
    EvidenceTypeSpec("table_name_match", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['detection_pattern']='table_name'`."),
    EvidenceTypeSpec("tauri_emit_listen", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='tauri_emit_listen'`."),
    EvidenceTypeSpec("tauri_invoke", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='tauri_invoke'`."),
    EvidenceTypeSpec("vue_component_import", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='vue_component'`."),
    EvidenceTypeSpec("vue_event_handler", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='vue_event_handler'`."),
    EvidenceTypeSpec("wasm_bindgen_import", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='wasm_bindgen_import'`."),
    EvidenceTypeSpec("wasm_instantiate", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='wasm_instantiate'`."),
    EvidenceTypeSpec("ws_emit", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='ws'`."),
    EvidenceTypeSpec("ws_endpoint", AXIS_ENDPOINT_SHAPE,
                     "Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='ws'`."),
    EvidenceTypeSpec("yjs_crdt_pattern", AXIS_ENDPOINT_SHAPE,
                     "Cluster C fold: canonical + `meta['framework_dispatch']='yjs_crdt'`."),

    # ----------------------------------------------------------------
    # Cluster D — Apex/peer call-construct overloads
    # (AXIS_ENDPOINT_SHAPE). Phase 3 collapses to canonical apex (likely
    # `ast_call`) plus meta['call_construct'].
    # ----------------------------------------------------------------
    EvidenceTypeSpec("ambiguous_method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='ambiguous'`."),
    EvidenceTypeSpec("bare_method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='bare'`."),
    EvidenceTypeSpec("call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` (the apex; `call` is the generic peer)."),
    EvidenceTypeSpec("chained_return_type_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='chained_return_type'`."),
    EvidenceTypeSpec("constructor_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='constructor'`."),
    EvidenceTypeSpec("cross_file_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='cross_file'`."),
    EvidenceTypeSpec("cross_file_message_send", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `message_send` + `meta['call_construct']='cross_file'`."),
    EvidenceTypeSpec("external_receiver_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='external'`."),
    EvidenceTypeSpec("function_application", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='application'`."),
    EvidenceTypeSpec("function_application_external", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='application_external'`."),
    EvidenceTypeSpec("function_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` apex (the high-frequency emitter)."),
    EvidenceTypeSpec("local_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='local'`."),
    EvidenceTypeSpec("macro_body_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='macro_body'`."),
    EvidenceTypeSpec("method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'`."),
    EvidenceTypeSpec("method_call_field_chain", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='field_chain'`."),
    EvidenceTypeSpec("method_call_recovery", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='recovery'`."),
    EvidenceTypeSpec("method_call_typed", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='typed'`."),
    EvidenceTypeSpec("method_call_type_inferred", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='type_inferred'`."),
    EvidenceTypeSpec("method_group", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method_group'` (C# delegate group)."),
    EvidenceTypeSpec("object_creation", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='constructor'` (peer of constructor_call)."),
    EvidenceTypeSpec("pipe_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='pipe'` (Elixir / F# pipe)."),
    EvidenceTypeSpec("receiver_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='generic'`."),
    EvidenceTypeSpec("remote_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='remote'`."),
    EvidenceTypeSpec("remote_call_external", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='remote_external'`."),
    EvidenceTypeSpec("stdlib_method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='stdlib'`."),
    EvidenceTypeSpec("typed_field_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='typed_field'`."),
    EvidenceTypeSpec("typed_receiver_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='typed_receiver'`."),
    EvidenceTypeSpec("unexported_method_call", AXIS_ENDPOINT_SHAPE,
                     "Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['visibility']='unexported'` (Go)."),
)


def all_evidence_type_names() -> frozenset[str]:
    """Return every canonical Edge.evidence_type name."""
    return frozenset(spec.name for spec in EVIDENCE_TYPES)


def evidence_types_on_axis(axis: str) -> tuple[EvidenceTypeSpec, ...]:
    """Return all Edge.evidence_type specs whose axis equals *axis*.

    Use this in place of hardcoded sets like
    ``_AST_DERIVED_INFERENCE = {"ast_call_direct", "ast_import"}``: query
    by axis instead of enumerating values, so new specs that match the
    axis are picked up automatically.
    """
    return tuple(spec for spec in EVIDENCE_TYPES if spec.axis == axis)


def find_evidence_type(name: str) -> EvidenceTypeSpec | None:
    """Look up an Edge.evidence_type spec by name; return None if not registered."""
    for spec in EVIDENCE_TYPES:
        if spec.name == name:
            return spec
    return None


def find_axis_drift(repo_root: Path) -> list[str]:
    """Scan the repo for hardcoded ``*EVIDENCE_TYPE*`` sets that drift from the registry.

    Wraps the field-agnostic AST walker in
    :mod:`hypergumbo_core.axis_drift` with the parameterization for
    ``Edge.evidence_type``: scans for module-level set / frozenset
    assignments whose target name contains ``EVIDENCE_TYPE`` and returns
    a human-readable list of drift locations (file:line references plus
    the offending values).

    Per ADR-0028 §"Phase 1": the registry is seeded with every value
    currently emitted by static-literal producer code plus the
    enumerable dynamic variants from f-string emit sites. Adding a NEW
    hardcoded value at a consumer site without first registering it in
    :data:`EVIDENCE_TYPES` causes this scan to surface the addition;
    that's the L1 (consumer-side) structural enforcement the registry
    exists to provide. The L3 producer-side check lives in
    :mod:`hypergumbo_core.producer_coherence` and gates literal emit
    sites at ``Edge.create(...)`` call positions.

    Used by the property test in ``tests/test_evidence_types.py`` and
    the pre-commit linter at ``scripts/check-evidence-type-drift``.
    """
    from hypergumbo_core.axis_drift import find_drift
    return find_drift(
        repo_root,
        name_filter="EVIDENCE_TYPE",
        registry_names=all_evidence_type_names(),
    )
