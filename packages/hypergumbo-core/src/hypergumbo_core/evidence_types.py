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
  audit, plus the canonical promotions per audit-findings 0004 / 0008
  / 0012 / 0014).
- ``pending_classification`` — deferred follow-on long-tail rows
  pending per-cluster audit; see audit-findings 0004 §"Diagnostic
  findings" and the WI-nubuv ext A / ext B discovery sections below
  for the current Pending set.

The ``endpoint_shape`` axis was retired in PR #3635 (Phase 4b enum
closure / WI-porim). All 111 deprecated values that occupied that axis
during the Phase 4a deprecation window are now removed from the
registry; their fold targets live as canonical inference values plus
``Edge.is_resolved`` (Cluster B) and ``Edge.meta`` keys (Cluster C
framework dispatch, Cluster D call construct). See
:mod:`hypergumbo_core.axis_meta_keys` for the meta-key vocabulary and
audit-findings 0008 / 0012 / 0014 for per-value fold targets.

Seeding completeness (per the Phase 1 plan file):

- 207 static-literal evidence_type values from ``grep packages/*/src``.
- 10 enumerable dynamic variants from the f-string emits
  (``{pattern_type}_emit`` / ``{pattern_type}_endpoint``) in
  ``websocket.py`` for the 6 registered ``pattern_type`` literals
  (``socketio``, ``native``, ``ws``, ``fastapi``, ``starlette``,
  ``django_channels``). This Phase 1 seed was folded in Phase 3
  (audit-findings 0014): those variants were collapsed to
  ``ast_call_direct`` + ``meta["framework_dispatch"]``, so they are
  no longer seeded.
- The Phase-3 producer migration retired the former ``di_binding``
  colon-form placeholder: the DI-resolution site
  (``di_resolution.py:628``) now emits the canonical ``ast_call_direct``
  evidence type plus ``meta["framework_dispatch"]=binding.source`` and
  ``meta["mechanism"]="di"``, so ``di_binding`` is no longer seeded
  (``find_evidence_type('di_binding')`` returns None).
- The dynamic ``f"ast_{edge_type}"`` at ``inheritance.py:368`` only
  yields ``ast_extends`` / ``ast_implements``, both already in the
  static set.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final


AXIS_INFERENCE_PATHWAY: Final[str] = "inference_pathway"
AXIS_PENDING: Final[str] = "pending_classification"

# Retired axis name kept as a public constant for audit-findings
# validation (``hypergumbo_core.audit_findings._REGISTRIES``) and for
# downstream readers comparing schema versions across the Phase 4a
# deprecation window. Not in :data:`VALID_AXES` — no live spec may
# carry this axis; the property test in
# ``tests/test_evidence_types.py`` enforces the empty-axis invariant.
AXIS_ENDPOINT_SHAPE: Final[str] = "endpoint_shape"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_INFERENCE_PATHWAY,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class EvidenceTypeSpec:
    """A single Edge.evidence_type value and its axis classification.

    ``base_confidence`` is the ADR-0039 detection-reliability seed for this
    inference pathway — the canonical ``Edge.confidence`` for an edge whose
    only evidence is this pathway. ``None`` means the value is not yet
    derived for this type (the caller keeps its own literal); see
    :func:`hypergumbo_core.confidence.derive_confidence`.

    ``base_confidence_unresolved`` is the variant for unresolved edges
    (``Edge.is_resolved=False``) on pathways whose reliability is
    is_resolved-conditioned (the multimodal call types ``ast_call`` /
    ``ast_call_direct``). ``None`` means the pathway is not
    is_resolved-conditioned and ``base_confidence`` applies regardless.
    """

    name: str
    axis: str
    description: str
    base_confidence: float | None = None
    base_confidence_unresolved: float | None = None


_RAW_EVIDENCE_TYPES: tuple[EvidenceTypeSpec, ...] = (
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
                     "Edge inferred from a generic call expression in source AST.",
                     base_confidence=0.85, base_confidence_unresolved=0.40),
    EvidenceTypeSpec("ast_call_direct", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a direct (non-method) call site.",
                     base_confidence=0.85, base_confidence_unresolved=0.50),
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
                     "Edge inferred from an import statement in source AST.",
                     base_confidence=0.95),
    EvidenceTypeSpec("ast_include", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from an include directive in source AST (C/C++)."),
    EvidenceTypeSpec("ast_includes", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a runtime mixin declaration "
                     "(Ruby `include`/`extend`, etc.) — WI-hatip."),
    EvidenceTypeSpec("ast_method_inferred", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a method call where dispatch was inferred."),
    EvidenceTypeSpec("ast_method_this", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `this`/`self` method call."),
    EvidenceTypeSpec("ast_method_this_property", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `this.prop` / `self.attr` reference."),
    EvidenceTypeSpec("ast_method_type_inferred", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a method call with type-inferred receiver."),
    EvidenceTypeSpec("ast_name_read", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a bare-name read of a module-level variable (WI-jagus).",
                     base_confidence=0.85),
    EvidenceTypeSpec("ast_new", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a `new` constructor expression.",
                     base_confidence=0.95),
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
                     "Edge inferred from a callback function passed as an argument.",
                     base_confidence=0.75),
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
                     "Edge inferred from a manifest-driven import resolution.",
                     base_confidence=0.90),
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
                     "Edge inferred from a module-level attribute reference.",
                     base_confidence=0.85),
    EvidenceTypeSpec("module_export_heuristic", AXIS_INFERENCE_PATHWAY,
                     "Edge inferred from a module-export heuristic recognition.",
                     base_confidence=0.75),
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
                     "Edge inferred from text-span overlap between symbols.",
                     base_confidence=0.90),
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
    # Cluster B (resolution-status leakage) — Phase 4b removal complete.
    # Wave 3 (WI-nunal, PR #3564) folded all 18 ``*_unresolved`` /
    # ``unresolved_*`` variants to canonical inference + ``is_resolved=False``
    # per audit-findings 0008; deprecated registry entries removed in
    # PR #3635 (WI-porim).
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # Cluster C (framework-specific dispatch conventions) — Phase 4b
    # removal complete. Wave 5 (WI-kagik, PRs #3572 + selfh #162-166)
    # folded all 65 framework-prefixed values to canonical inference +
    # ``meta['framework_dispatch']`` / ``meta['detection_pattern']`` per
    # audit-findings 0014; deprecated registry entries removed in
    # PR #3635 (WI-porim).
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # Cluster D (apex/peer call-construct overloads) — Phase 4b removal
    # complete. Wave 4 (WI-nibis, PR #3570) collapsed all 28 call-
    # construct peers to ``ast_call`` apex + ``meta['call_construct']``
    # per audit-findings 0012; deprecated registry entries removed in
    # PR #3635 (WI-porim).
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # WI-nubuv ext A discoveries — assignment-form producer leaks
    # ----------------------------------------------------------------
    # Surfaced by the L3 producer-coherence linter when extension A
    # (function-local assignment trace) landed and walked back
    # ``evidence_type = "<literal>"`` shapes that were previously
    # invisible to the literal-kwarg-only matcher. All registered
    # AXIS_PENDING for follow-on Cluster-C audit; the values describe
    # language/framework mechanism dispatch and are at-risk for
    # mechanism-on-evidence_type fold per ADR-0028 §"Phase 3 Cluster C".
    EvidenceTypeSpec("cffi_call", AXIS_PENDING,
                     "Python cffi FFI call (linkers/pyffi.py). At-risk "
                     "Cluster C: fold candidate to canonical inference "
                     "+ `meta['ffi_mechanism']='cffi'`. Pending cluster-C audit."),
    EvidenceTypeSpec("ctypes_call", AXIS_PENDING,
                     "Python ctypes FFI call (linkers/pyffi.py). At-risk "
                     "Cluster C: fold candidate to canonical inference "
                     "+ `meta['ffi_mechanism']='ctypes'`. Pending cluster-C audit."),
    EvidenceTypeSpec("alias_resolution", AXIS_PENDING,
                     "JS module-resolution pathway via path alias "
                     "(linkers/js_module.py). Pending cluster-A audit "
                     "(could promote to AXIS_INFERENCE_PATHWAY canonical)."),
    EvidenceTypeSpec("import_resolution", AXIS_PENDING,
                     "JS module-resolution pathway via direct import "
                     "(linkers/js_module.py). Pending cluster-A audit "
                     "(could promote to AXIS_INFERENCE_PATHWAY canonical)."),

    # ----------------------------------------------------------------
    # WI-nubuv ext B + IfExp-classifier discoveries — leak shapes that
    # the literal-kwarg-only matcher (plus poison-on-non-string-Constant
    # walker) silently hid.
    # ----------------------------------------------------------------
    # The inline-ternary classifier fix (`evidence_type="a" if cond else
    # "b"`) and the empirical addendum from WI-nubuv ext A's tracker
    # discussion (dict-subscript-target ⟶ for-loop unpack producers in
    # `linkers/pyffi.py`) surfaced four pre-existing producer-emitted
    # values that were never registered. Registered AXIS_PENDING per the
    # follow-on-cluster discipline used for the prior ext-A discoveries.
    EvidenceTypeSpec("ipc_channel_match", AXIS_PENDING,
                     "Electron IPC channel-name matching inference "
                     "(linkers/ipc.py:546). Emitted in the canonical-"
                     "`event_publishes` fold for the Electron renderer→"
                     "main exchange when the publisher's channel name "
                     "matches the subscriber's pattern. Sibling of "
                     "`variable_match` (already canonical). At-risk "
                     "Cluster A: candidate for promotion to "
                     "AXIS_INFERENCE_PATHWAY or fold to "
                     "`naming_convention` + "
                     "`meta['detection_pattern']='ipc_channel'`. "
                     "Pending cluster-A/C audit."),
    EvidenceTypeSpec("topic_match", AXIS_PENDING,
                     "Message-queue topic-name matching inference "
                     "(linkers/message_queue.py:516). Emitted in the "
                     "canonical-`event_publishes` fold for MQ publisher→"
                     "subscriber via topic when the publisher's topic "
                     "matches the subscriber's pattern. Sibling of "
                     "`variable_match`. At-risk Cluster A: candidate "
                     "for promotion or fold to `naming_convention` + "
                     "`meta['detection_pattern']='mq_topic'`. "
                     "Pending cluster-A/C audit."),
    EvidenceTypeSpec("ctypes_stdlib_call", AXIS_PENDING,
                     "Python ctypes FFI call against the stdlib variant "
                     "(linkers/pyffi.py; `lib_vars[var_name] = "
                     "\"ctypes_stdlib_call\"` then for-loop unpack into "
                     "Edge.create). Distinguishes stdlib-loader scope "
                     "from the repo-local-loaded `ctypes_call`. At-risk "
                     "Cluster C peer: fold candidate to `ctypes_call` + "
                     "`meta['ffi_scope']='stdlib'`. Pending cluster-C "
                     "audit (see WI-nubuv tracker discussion 2026-05-06)."),
    EvidenceTypeSpec("cffi_stdlib_call", AXIS_PENDING,
                     "Python cffi FFI call against the stdlib variant "
                     "(linkers/pyffi.py; same dict-subscript-target "
                     "leak shape as `ctypes_stdlib_call`). At-risk "
                     "Cluster C peer: fold candidate to `cffi_call` + "
                     "`meta['ffi_scope']='stdlib'`. Pending cluster-C "
                     "audit."),
    EvidenceTypeSpec("qualified_call", AXIS_PENDING,
                     "R qualified function call via `pkg::fn` "
                     "(hypergumbo-lang-common/r_lang.py:385). Sibling of "
                     "the canonical `static` inference label; emitted "
                     "from the inline ternary "
                     "``'static' if not path_hint else 'qualified_call'`` "
                     "that the pre-WI-nubuv classifier silently skipped. "
                     "At-risk Cluster D call-construct: fold candidate "
                     "to `static` + `meta['call_construct']='qualified'`. "
                     "Pending cluster-D audit."),
    EvidenceTypeSpec("ast_call_namespace", AXIS_PENDING,
                     "JS/TS namespace-import call inference "
                     "(hypergumbo-lang-mainstream/js_ts.py:3858; "
                     "``import * as obj; obj.method()``). Sibling of "
                     "the canonical `ast_call_direct` / `ast_new` peers; "
                     "emitted from the inline ternary "
                     "``'ast_new' if is_class else 'ast_call_namespace'``. "
                     "At-risk Cluster D call-construct: fold candidate "
                     "to `ast_call_direct` + `meta['call_construct']='namespace'`. "
                     "Pending cluster-D audit."),
)


# --- Detection-reliability seeds (ADR-0039 / WI-nurun confidence:F1) ---------
# Single authoritative table of per-evidence-type base confidences for the
# single-valued inference pathways. The two multimodal call-types
# (``ast_call`` / ``ast_call_direct``), whose confidence is conditioned on
# ``is_resolved``, carry their (resolved, unresolved) pair inline on the spec
# above and are intentionally absent here. Each value is the edge-weighted
# modal confidence that pathway's producers currently hardcode (the
# corpus-dominant value, with the AST-scan site modal as the fallback for
# pathways absent from the self-corpus). So when a producer drops its explicit
# ``confidence=`` and lets ``Edge.create`` derive (the WI-nurun producer
# migration), the published value is unchanged for the dominant cohort while
# the per-emitter hardcoded outliers collapse to this single canonical value —
# the INV-suvil fix (confidence derived from evidence, not hardcoded per
# emitter). Keys are disjoint from the inline-seeded specs above
# (``test_confidence_seeds_disjoint_from_inline``).
_CONFIDENCE_SEEDS: dict[str, float] = {
    "ast_annotation": 0.5,
    "ast_attribute": 0.95,
    "ast_call_extension": 0.8,
    "ast_call_type_inferred": 0.85,
    "ast_decorator": 0.95,
    "ast_extends": 0.95,
    "ast_implements": 0.95,
    "ast_method_inferred": 0.7,
    "ast_method_this_property": 0.9,
    "ast_method_type_inferred": 0.85,
    "ast_type_ref": 0.85,
    "async_spawn": 0.85,
    "behaviour": 0.95,
    "behaviour_callback": 0.9,
    "bridging_header_import": 0.95,
    "build_target_main": 0.95,
    "canonical_name": 0.95,
    "closure_wrapper": 0.85,
    "dispatch_pattern": 0.7,
    "dispatch_table_reference": 0.85,
    "dockerfile_copy_from": 0.95,
    "dockerfile_from": 0.95,
    "enclosing_scope": 0.9,
    "eta_expansion": 0.85,
    "extends": 0.95,
    "function_pointer": 0.85,
    "function_reference": 0.8,
    "function_reference_arg": 0.7,
    "grpc_stub_resolution": 0.75,
    "hash_field_reference": 0.8,
    "hg_annotation": 0.95,
    "import": 0.95,
    "import_declaration": 0.95,
    "import_directive": 0.95,
    "import_statement": 0.95,
    "import_static": 0.95,
    "include": 0.95,
    "include_directive": 0.95,
    "instance": 0.9,
    "link": 0.95,
    "message_send": 0.9,
    "method_reference": 0.85,
    "module_identifier_reference": 0.85,
    "module_source": 0.95,
    # ADR-0039 R1 (WI-vakuh / WI-lutad): a name-parse containment heuristic —
    # reliable but structurally weaker than the certain span_overlap (0.90), so
    # it seeds BELOW it to restore reliability ordering, and in-band (the old
    # literal 1.0 breached the 0.95 ceiling on ~18k edges).
    "naming_convention": 0.85,
    "notify": 0.9,
    "object_field_reference": 0.8,
    "open": 0.95,
    "open_import": 0.95,
    "reference": 0.95,
    "require": 0.95,
    "require_dynamic": 0.4,
    "require_statement": 0.95,
    "require_static": 0.9,
    "signal_constraint": 0.85,
    "source_statement": 0.95,
    "stack_construction": 0.85,
    "static": 0.95,
    "struct_field_reference": 0.7,
    "trait_impl": 0.95,
    # ADR-0039 R1/R3 (WI-botif): a real dispatch relationship — reliably 0.85;
    # the 1/sqrt(N) fan-out dampener + test-file penalty are RANKING signals that
    # relocate to Edge.rank_score (type_hierarchy.py), so published confidence is
    # the flat in-band base, not the dampened value that undershot the 0.40 floor.
    "type_hierarchy": 0.85,
    "typeclass_instance": 0.9,
    "use": 0.95,
    "use-package": 0.95,
    "use_declaration": 0.95,
    "use_directive": 0.95,
    "using_directive": 0.95,
}


def _apply_confidence_seeds(
    specs: tuple[EvidenceTypeSpec, ...],
) -> tuple[EvidenceTypeSpec, ...]:
    """Overlay ``_CONFIDENCE_SEEDS`` onto the raw registry (WI-nurun).

    Pathways named in the seed table receive their ``base_confidence``;
    everything else (already-inline-seeded specs, and pathways whose
    producers always pass a computed ``confidence=``) passes through
    unchanged.
    """
    return tuple(
        replace(spec, base_confidence=_CONFIDENCE_SEEDS[spec.name])
        if spec.name in _CONFIDENCE_SEEDS else spec
        for spec in specs
    )


EVIDENCE_TYPES: Final[tuple[EvidenceTypeSpec, ...]] = _apply_confidence_seeds(
    _RAW_EVIDENCE_TYPES
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

    ``ENTRYPOINT_EVIDENCE_TYPES`` (``entrypoints.py``) is excluded: it
    matches the ``EVIDENCE_TYPE`` name filter by substring but enumerates
    the SEPARATE ``Entrypoint.meta`` inference-pathway axis (WI-rukam), not
    ``Edge.evidence_type``. Same shape as the ``KIND``-filter exclusions
    for ``PROTOCOL_KINDS`` / ``BRIDGE_KINDS`` — a name collision across
    axes, resolved by name-exclusion rather than renaming the constant.
    """
    from hypergumbo_core.axis_drift import find_drift
    return find_drift(
        repo_root,
        name_filter="EVIDENCE_TYPE",
        registry_names=all_evidence_type_names(),
        excluded_target_names=("ENTRYPOINT_EVIDENCE_TYPES",),
    )
