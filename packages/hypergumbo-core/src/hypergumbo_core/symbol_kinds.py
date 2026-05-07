# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of Symbol.kind values in hypergumbo's behavior map.

Per ADR-0027, every value in the canonical registry should have
``axis="language_construct"`` — ``Symbol.kind`` names the source-language
syntactic construct that the symbol represents, with framework
participation queried from ``Symbol.meta`` and edge-shaped relationships
queried from the ``Edge`` rather than smuggled into the kind label.

This module is the single source of truth: ``scripts/generate-schema``
imports ``SYMBOL_KINDS`` to emit both the JSON Schema enum and per-value
axis annotations (under the ``x-axis-of-values`` extension keyword).
Consumers that need a subset of kinds (for example, a "callable kinds"
filter) should call ``symbol_kinds_on_axis(...)`` rather than maintain
their own hardcoded set; the property test in
``tests/test_symbol_kinds.py`` enforces that every hardcoded set in the
codebase whose name contains ``KIND`` is a subset of this registry.

Axis taxonomy (per ADR-0027 §1):

- ``language_construct`` — ADR-0027 compliant. The value names the
  source-language syntactic construct the symbol represents (Cluster A
  in the WI-dumiz audit).
- ``endpoint_shape`` — deprecation candidate per ADR-0027 §"Detailed
  analysis: per-cluster fold targets". The value's meaning is captured
  by edges (Cluster E) or framework metadata (Cluster D), or is
  dst-kind leakage (Cluster F's ``component_ref``); migration plan
  folds these back into the canonical Cluster-A construct + meta key
  or drops them entirely as edge-only.
- ``pending_classification`` — deferred to per-cluster audit-findings
  doc per ADR-0027 §"Migration" Phase 3 (the file-shape / build-config
  / domain long-tail clusters contain a mix of genuinely distinct
  constructs and possible separate-axis candidates; per-value verdicts
  arrive with each cluster's audit).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


AXIS_LANGUAGE_CONSTRUCT: Final[str] = "language_construct"
AXIS_ENDPOINT_SHAPE: Final[str] = "endpoint_shape"
AXIS_PENDING: Final[str] = "pending_classification"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_LANGUAGE_CONSTRUCT,
    AXIS_ENDPOINT_SHAPE,
    AXIS_PENDING,
})


@dataclass(frozen=True)
class SymbolKindSpec:
    """A single Symbol.kind value and its axis classification."""

    name: str
    axis: str
    description: str


SYMBOL_KINDS: Final[tuple[SymbolKindSpec, ...]] = (
    # ----------------------------------------------------------------
    # Cluster A — Pure language constructs (AXIS_LANGUAGE_CONSTRUCT).
    # Each value names a source-language syntactic construct directly.
    # ----------------------------------------------------------------
    SymbolKindSpec("function", AXIS_LANGUAGE_CONSTRUCT,
                   "Top-level function definition."),
    SymbolKindSpec("method", AXIS_LANGUAGE_CONSTRUCT,
                   "Method on a class / struct / interface."),
    SymbolKindSpec("class", AXIS_LANGUAGE_CONSTRUCT,
                   "Class declaration."),
    SymbolKindSpec("interface", AXIS_LANGUAGE_CONSTRUCT,
                   "Interface declaration."),
    SymbolKindSpec("struct", AXIS_LANGUAGE_CONSTRUCT,
                   "Struct / record-type declaration."),
    SymbolKindSpec("enum", AXIS_LANGUAGE_CONSTRUCT,
                   "Enum declaration."),
    SymbolKindSpec("union", AXIS_LANGUAGE_CONSTRUCT,
                   "Union / sum-type declaration."),
    SymbolKindSpec("trait", AXIS_LANGUAGE_CONSTRUCT,
                   "Trait declaration (Rust / Scala / Groovy)."),
    SymbolKindSpec("module", AXIS_LANGUAGE_CONSTRUCT,
                   "Module declaration (the source-level construct)."),
    SymbolKindSpec("namespace", AXIS_LANGUAGE_CONSTRUCT,
                   "Namespace declaration (C++ / TypeScript / C#)."),
    SymbolKindSpec("variable", AXIS_LANGUAGE_CONSTRUCT,
                   "Variable / let / mutable binding."),
    SymbolKindSpec("constant", AXIS_LANGUAGE_CONSTRUCT,
                   "Constant / final / let-immutable binding."),
    SymbolKindSpec("const", AXIS_LANGUAGE_CONSTRUCT,
                   "Const declaration (C / C++ / Rust / JS const)."),
    SymbolKindSpec("property", AXIS_LANGUAGE_CONSTRUCT,
                   "Property declaration (Kotlin / Swift / C#)."),
    SymbolKindSpec("attribute", AXIS_LANGUAGE_CONSTRUCT,
                   "Attribute declaration (Python class attribute, etc.)."),
    SymbolKindSpec("field", AXIS_LANGUAGE_CONSTRUCT,
                   "Field declaration on a struct / class / record."),
    SymbolKindSpec("constructor", AXIS_LANGUAGE_CONSTRUCT,
                   "Constructor / __init__ / init method."),
    SymbolKindSpec("getter", AXIS_LANGUAGE_CONSTRUCT,
                   "Property getter accessor."),
    SymbolKindSpec("setter", AXIS_LANGUAGE_CONSTRUCT,
                   "Property setter accessor."),
    SymbolKindSpec("type", AXIS_LANGUAGE_CONSTRUCT,
                   "Type declaration (TypeScript type, Haskell type, etc.)."),
    SymbolKindSpec("type_alias", AXIS_LANGUAGE_CONSTRUCT,
                   "Type alias declaration."),
    SymbolKindSpec("typedef", AXIS_LANGUAGE_CONSTRUCT,
                   "C/C++ typedef declaration."),
    SymbolKindSpec("alias", AXIS_LANGUAGE_CONSTRUCT,
                   "Generic alias declaration."),
    SymbolKindSpec("simple_type", AXIS_LANGUAGE_CONSTRUCT,
                   "Simple-type declaration (XSD-shape)."),
    SymbolKindSpec("defined_type", AXIS_LANGUAGE_CONSTRUCT,
                   "Defined / nominal type declaration (Puppet / Coq)."),
    SymbolKindSpec("macro", AXIS_LANGUAGE_CONSTRUCT,
                   "Macro definition (Rust / C / Scheme)."),
    SymbolKindSpec("mixin", AXIS_LANGUAGE_CONSTRUCT,
                   "Mixin declaration (Ruby / Sass)."),
    SymbolKindSpec("record", AXIS_LANGUAGE_CONSTRUCT,
                   "Record declaration (Java 14+, Erlang, Haskell)."),
    SymbolKindSpec("abstract", AXIS_LANGUAGE_CONSTRUCT,
                   "Abstract class / member declaration."),
    SymbolKindSpec("instance", AXIS_LANGUAGE_CONSTRUCT,
                   "Typeclass / interface instance declaration."),
    SymbolKindSpec("subroutine", AXIS_LANGUAGE_CONSTRUCT,
                   "Subroutine / sub declaration (Fortran / Perl)."),
    SymbolKindSpec("procedure", AXIS_LANGUAGE_CONSTRUCT,
                   "Procedure declaration (Pascal / Ada / SQL)."),
    SymbolKindSpec("call_site", AXIS_LANGUAGE_CONSTRUCT,
                   "Call-expression site as a syntactic construct. Cluster E sub-case (a) "
                   "fold target per audit-findings 0010: the call expression is an AST "
                   "node worth representing as a Symbol, distinct from the relationship "
                   "captured by an Edge of edge_type='calls'. Producers that previously "
                   "emitted kind='function_call' / 'subprocess_call' / 'db_query' / "
                   "'abi_call' now emit kind='call_site' with the prior specialisation "
                   "moved to meta['call_kind']."),
    SymbolKindSpec("proc", AXIS_ENDPOINT_SHAPE,
                   "Cluster C apex/peer: deprecated peer of `procedure`. "
                   "No producer emits this kind (verified WI-rusit Wave 4); "
                   "registry entry remains through the Phase 4a deprecation "
                   "window per ADR-0027. Fold target: procedure."),
    SymbolKindSpec("fn", AXIS_ENDPOINT_SHAPE,
                   "Cluster C apex/peer: deprecated peer of `function`. "
                   "No producer emits this kind (verified WI-rusit Wave 4); "
                   "registry entry remains through the Phase 4a deprecation "
                   "window per ADR-0027. Fold target: function."),
    SymbolKindSpec("var", AXIS_ENDPOINT_SHAPE,
                   "Cluster C apex/peer: deprecated peer of `variable`. "
                   "No producer emits this kind (verified WI-rusit Wave 4); "
                   "registry entry remains through the Phase 4a deprecation "
                   "window per ADR-0027. Fold target: variable."),
    SymbolKindSpec("arrow_function", AXIS_LANGUAGE_CONSTRUCT,
                   "Arrow-function expression (JS / TS)."),
    SymbolKindSpec("object", AXIS_LANGUAGE_CONSTRUCT,
                   "Object / singleton declaration (Scala / Kotlin)."),
    SymbolKindSpec("prop", AXIS_LANGUAGE_CONSTRUCT,
                   "Component prop declaration (Vue / React)."),
    SymbolKindSpec("slot", AXIS_LANGUAGE_CONSTRUCT,
                   "Component slot declaration (Vue / Svelte)."),
    SymbolKindSpec("template", AXIS_LANGUAGE_CONSTRUCT,
                   "Template declaration (C++ / Vue / Handlebars)."),
    SymbolKindSpec("directive", AXIS_LANGUAGE_CONSTRUCT,
                   "Directive declaration (Vue / Angular / GraphQL)."),
    SymbolKindSpec("declaration", AXIS_LANGUAGE_CONSTRUCT,
                   "Generic declaration (catch-all for non-categorized syntactic forms)."),
    SymbolKindSpec("keyword", AXIS_LANGUAGE_CONSTRUCT,
                   "Keyword-shaped construct (configuration languages)."),
    SymbolKindSpec("export", AXIS_LANGUAGE_CONSTRUCT,
                   "Export declaration (JS / TS / TOML / Rust)."),
    SymbolKindSpec("import", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (b) DEPRECATE-NO-FOLD per audit-findings 0010: "
                   "the imports Edge captures the relationship; no replacement Symbol "
                   "kind. Four producers (css.py, jsonnet.py, astro.py, r_lang.py) "
                   "dropped across PRs 1, 2, and WI-kunag. Registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("include", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (b) DEPRECATE-NO-FOLD per audit-findings 0010: "
                   "the include-family Edges capture the relationship; no replacement "
                   "Symbol kind. Five producers (puppet.py, scss.py, twig.py x2, make.py) "
                   "dropped across PRs 1, 2, and WI-kunag. Registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("extends", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (b) DEPRECATE-NO-FOLD per audit-findings 0010: "
                   "the extends_template Edge captures the relationship; no replacement "
                   "Symbol kind. Two producers (twig.py, blade.py) dropped across PRs 2 "
                   "and WI-kunag. Registry entry stays through the Phase 4a deprecation "
                   "window."),

    # ----------------------------------------------------------------
    # Cluster D — Framework roles / dispatch participation.
    # AXIS_ENDPOINT_SHAPE: deprecation candidates per ADR-0027.
    # Fold target: kind=<canonical> + meta["framework_role"]=<value>.
    # ----------------------------------------------------------------
    SymbolKindSpec("event_publisher", AXIS_ENDPOINT_SHAPE,
                   "Symbol that publishes events. Fold to function/method + meta['framework_role']='event_publisher'."),
    SymbolKindSpec("event_subscriber", AXIS_ENDPOINT_SHAPE,
                   "Symbol that subscribes to events. Fold to function/method + meta['framework_role']='event_subscriber'."),
    SymbolKindSpec("ipc_publisher", AXIS_ENDPOINT_SHAPE,
                   "IPC publish endpoint. Fold to function/method + meta['framework_role']='ipc_publisher'."),
    SymbolKindSpec("ipc_subscriber", AXIS_ENDPOINT_SHAPE,
                   "IPC subscribe endpoint. Fold to function/method + meta['framework_role']='ipc_subscriber'."),
    SymbolKindSpec("ipc_caller", AXIS_ENDPOINT_SHAPE,
                   "IPC call endpoint. Fold to function/method + meta['framework_role']='ipc_caller'."),
    SymbolKindSpec("ipc_bridge_caller", AXIS_ENDPOINT_SHAPE,
                   "IPC bridge call endpoint. Fold to function/method + meta['framework_role']='ipc_bridge_caller'."),
    SymbolKindSpec("ipc", AXIS_ENDPOINT_SHAPE,
                   "Generic IPC endpoint. Fold to function/method + meta['framework_role']='ipc'."),
    SymbolKindSpec("objc_bridge", AXIS_ENDPOINT_SHAPE,
                   "Objective-C bridge call. Fold to function/method + meta['framework_role']='objc_bridge'."),
    SymbolKindSpec("crypto_producer", AXIS_ENDPOINT_SHAPE,
                   "Crypto-flow producer. Fold to function/method + meta['framework_role']='crypto_producer'."),
    SymbolKindSpec("crypto_consumer", AXIS_ENDPOINT_SHAPE,
                   "Crypto-flow consumer. Fold to function/method + meta['framework_role']='crypto_consumer'."),
    SymbolKindSpec("message_sender", AXIS_ENDPOINT_SHAPE,
                   "Message-bus sender. Fold to function/method + meta['framework_role']='message_sender'."),
    SymbolKindSpec("message_handler", AXIS_ENDPOINT_SHAPE,
                   "Message-bus handler. Fold to function/method + meta['framework_role']='message_handler'."),
    SymbolKindSpec("mq_publisher", AXIS_ENDPOINT_SHAPE,
                   "Message-queue publisher. Fold to function/method + meta['framework_role']='mq_publisher'."),
    SymbolKindSpec("mq_subscriber", AXIS_ENDPOINT_SHAPE,
                   "Message-queue subscriber. Fold to function/method + meta['framework_role']='mq_subscriber'."),
    SymbolKindSpec("grpc_server", AXIS_ENDPOINT_SHAPE,
                   "gRPC server class. Fold to class + meta['framework_role']='grpc_server'."),
    SymbolKindSpec("grpc_stub", AXIS_ENDPOINT_SHAPE,
                   "gRPC stub call site. Fold to function + meta['framework_role']='grpc_stub'."),
    SymbolKindSpec("grpc_service", AXIS_ENDPOINT_SHAPE,
                   "gRPC `service Foo {...}` proto declaration. Fold to interface "
                   "+ meta['framework_role']='grpc_service'. Added 2026-05-06 (WI-nitil) — "
                   "assignment-form producer at linkers/grpc.py:655 was missed by the "
                   "original literal-grep audit. Registry entry stays through the Phase 4a "
                   "deprecation window."),
    SymbolKindSpec("grpc_servicer", AXIS_ENDPOINT_SHAPE,
                   "gRPC servicer class. Fold to class + meta['framework_role']='grpc_servicer'. "
                   "Added 2026-05-06 (WI-nitil) — assignment-form producer at linkers/grpc.py:657 "
                   "was missed by the original literal-grep audit. Registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("grpc_client", AXIS_ENDPOINT_SHAPE,
                   "gRPC client call site (sibling of grpc_stub). Fold to function "
                   "+ meta['framework_role']='grpc_client'. Added 2026-05-06 (WI-nitil) — "
                   "assignment-form producer at linkers/grpc.py:660 was missed by the original "
                   "literal-grep audit. Registry entry stays through the Phase 4a deprecation "
                   "window."),
    SymbolKindSpec("websocket_endpoint", AXIS_ENDPOINT_SHAPE,
                   "WebSocket endpoint. Fold to function/method + meta['framework_role']='websocket_endpoint'."),
    SymbolKindSpec("websocket_emitter", AXIS_ENDPOINT_SHAPE,
                   "WebSocket emitter. Fold to function/method + meta['framework_role']='websocket_emitter'."),
    SymbolKindSpec("websocket_listener", AXIS_ENDPOINT_SHAPE,
                   "WebSocket listener. Fold to function/method + meta['framework_role']='websocket_listener'."),
    SymbolKindSpec("dispatcher", AXIS_ENDPOINT_SHAPE,
                   "Generic dispatcher symbol. Fold to function/method + meta['framework_role']='dispatcher'."),
    SymbolKindSpec("graphql_resolver", AXIS_ENDPOINT_SHAPE,
                   "GraphQL resolver. Fold to function/method + meta['framework_role']='graphql_resolver'."),
    SymbolKindSpec("graphql_client", AXIS_ENDPOINT_SHAPE,
                   "GraphQL client call site. Fold to function/method + meta['framework_role']='graphql_client'."),
    SymbolKindSpec("http_client", AXIS_ENDPOINT_SHAPE,
                   "HTTP client call site. Fold to function/method + meta['framework_role']='http_client'."),
    SymbolKindSpec("route_mount", AXIS_ENDPOINT_SHAPE,
                   "Route mount declaration. Fold to function/method + meta['framework_role']='route_mount'."),
    SymbolKindSpec("route", AXIS_ENDPOINT_SHAPE,
                   "Route declaration. Fold to function/method + meta['framework_role']='route'."),
    SymbolKindSpec("route_include", AXIS_ENDPOINT_SHAPE,
                   "Route include declaration. Fold to function/method + meta['framework_role']='route_include'."),
    SymbolKindSpec("openapi_operation", AXIS_ENDPOINT_SHAPE,
                   "OpenAPI operation. Fold to function/method + meta['framework_role']='openapi_operation'."),
    SymbolKindSpec("abi_call", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (a) FOLD per audit-findings 0010 (reclassified "
                   "from Cluster D in this PR — the Solidity ABI emit site names a "
                   "call expression, not a framework role): the solidity_abi linker "
                   "was reclassified to kind='call_site' + meta['call_kind']='abi'. "
                   "Registry entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("selector_ref", AXIS_ENDPOINT_SHAPE,
                   "ObjC selector reference. Fold to reference + meta['framework_role']='selector_ref'."),
    SymbolKindSpec("rpc", AXIS_ENDPOINT_SHAPE,
                   "RPC method declaration. Fold to function/method + meta['framework_role']='rpc'."),
    SymbolKindSpec("service", AXIS_ENDPOINT_SHAPE,
                   "Service declaration (gRPC service, k8s service). Fold to interface/class + meta['framework_role']='service'."),

    # ----------------------------------------------------------------
    # Cluster E — Edge labels masquerading as Symbol kinds.
    # AXIS_ENDPOINT_SHAPE: per-value sub-case in cluster-E audit.
    # ----------------------------------------------------------------
    SymbolKindSpec("call", AXIS_ENDPOINT_SHAPE,
                   "Cluster E DEPRECATE-NO-FOLD per audit-findings 0010: zero "
                   "Symbol.kind=call producers (the value lives only on UsageContext.kind, "
                   "a different field). Registry entry stays through the Phase 4a "
                   "deprecation window."),
    SymbolKindSpec("inherit", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (b) FOLD-clean-drop per audit-findings 0010: the "
                   "BitBake inherit-clause Symbol was dropped (relationship captured by "
                   "the inherits Edge with src=bitbake:{file}, dst=bitbake:class:{cls}). "
                   "Registry entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("function_call", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (a) FOLD per audit-findings 0010: the Twig "
                   "function-call producer (twig.py) was reclassified to "
                   "kind='call_site'. Registry entry stays through the Phase 4a "
                   "deprecation window."),
    SymbolKindSpec("subprocess_call", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (a) FOLD per audit-findings 0010: the "
                   "subprocess_cli linker was reclassified to kind='call_site' + "
                   "meta['call_kind']='subprocess'. Registry entry stays through the "
                   "Phase 4a deprecation window."),
    SymbolKindSpec("db_query", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (a) FOLD per audit-findings 0010: the "
                   "database_query linker was reclassified to kind='call_site' + "
                   "meta['call_kind']='db_query'. Registry entry stays through the "
                   "Phase 4a deprecation window."),
    SymbolKindSpec("read", AXIS_ENDPOINT_SHAPE,
                   "Cluster E DEPRECATE-NO-FOLD per audit-findings 0010: zero "
                   "Symbol.kind=read producers (matches in pub/sub linkers are on "
                   "internal dataclass fields YjsSite.kind / CryptoSite.kind / "
                   "DispatchSite.kind, not Symbol.kind). Registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("write", AXIS_ENDPOINT_SHAPE,
                   "Cluster E DEPRECATE-NO-FOLD per audit-findings 0010: symmetric "
                   "counterpart of read; zero Symbol.kind=write producers. Registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("reference", AXIS_ENDPOINT_SHAPE,
                   "Cluster E sub-case (b) per audit-findings 0010: UNRESOLVED — sole "
                   "producer (json_config.py) is shape-2 edge-endpoint-dependent "
                   "(references Edge has dst=symbol_id). Drop deferred to follow-on PR."),

    # ----------------------------------------------------------------
    # Cluster F — Component / UI references.
    # component_ref → AXIS_ENDPOINT_SHAPE (dst-kind leakage).
    # component itself stays AXIS_LANGUAGE_CONSTRUCT.
    # ----------------------------------------------------------------
    SymbolKindSpec("component", AXIS_LANGUAGE_CONSTRUCT,
                   "Component declaration (Vue / Svelte / Astro / React)."),
    SymbolKindSpec("component_ref", AXIS_ENDPOINT_SHAPE,
                   "Cluster F dst-kind leakage per audit-findings 0011: "
                   "DEPRECATE-NO-FOLD (PRELIM_RESOLVED). Three producers "
                   "(vue.py / svelte.py / astro.py) drop the per-reference "
                   "Symbol; the companion imports Edge re-routes src to "
                   "make_file_id and carries component_name + source_path "
                   "in meta. Registry entry stays through the Phase 4a "
                   "deprecation window."),
    SymbolKindSpec("view", AXIS_LANGUAGE_CONSTRUCT,
                   "View declaration (MVC / template languages)."),

    # ----------------------------------------------------------------
    # Cluster B — File-shape entities.
    # CANONICAL promotions per audit-findings 0005 (WI-runod Wave 6 PR 1):
    # build-DSL / source-language top-level constructs (CMake, Meson,
    # COBOL, Pascal, Fortran, VHDL declarations) are language-level
    # constructs in their own tongue. Test 4 (mechanism vs. category)
    # confirms each names a *category* of source-language construct,
    # not a *mechanism* qualifier. Test 1 (property derivability) does
    # not fire — none is derivable from another field. The remaining
    # AXIS_PENDING entries below (module_file, component_file,
    # npm_package, composer_package, main_entry, bin, library_export,
    # export_entry, wasm_module, wasm_import, tsconfig) ship FOLD or
    # DEPRECATE-NO-FOLD verdicts; their producer migrations are
    # subsequent Wave-6 PRs.
    # ----------------------------------------------------------------
    SymbolKindSpec("file", AXIS_LANGUAGE_CONSTRUCT,
                   "File-shape symbol — top-level file declaration in build / "
                   "source DSLs. CANONICAL per audit-findings 0005."),
    SymbolKindSpec("library", AXIS_LANGUAGE_CONSTRUCT,
                   "Library declaration (CMake `add_library`, Meson `library`, "
                   "Cargo `[lib]`, etc.). CANONICAL per audit-findings 0005."),
    SymbolKindSpec("package", AXIS_LANGUAGE_CONSTRUCT,
                   "Package declaration (CMake `find_package`, VHDL `package`, "
                   "Go `package`, JS `package.json` synthesis, etc.). "
                   "CANONICAL per audit-findings 0005."),
    SymbolKindSpec("executable", AXIS_LANGUAGE_CONSTRUCT,
                   "Executable declaration (CMake `add_executable`, Meson "
                   "`executable`). CANONICAL per audit-findings 0005."),
    SymbolKindSpec("program", AXIS_LANGUAGE_CONSTRUCT,
                   "Program declaration (Fortran `PROGRAM`, COBOL `PROGRAM-ID`, "
                   "Pascal `program`). CANONICAL per audit-findings 0005."),
    SymbolKindSpec("project", AXIS_LANGUAGE_CONSTRUCT,
                   "Project declaration (Meson `project()`, .csproj root, "
                   "etc.). CANONICAL per audit-findings 0005."),
    # Wave 6 PR 3 FOLDs per audit-findings 0005 — producer migration
    # shipped, registry entries kept on AXIS_ENDPOINT_SHAPE through the
    # Phase 4a deprecation window. Each fold target is the canonical
    # Cluster A construct above; the framework / ecosystem qualifier
    # moves to ``Symbol.meta`` under the named axis key.
    SymbolKindSpec("module_file", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``file`` + ``meta['module_system']`` ('esm' / "
                   "'commonjs') per audit-findings 0005, Wave 6 PR 3. Producer "
                   "(``js_module.py``) migrated; registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("component_file", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``file`` + ``meta['component_framework']`` ('vue', "
                   "'svelte', 'astro', etc.) per audit-findings 0005, Wave 6 "
                   "PR 3. Producer (``vue_component.py``) migrated; registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("npm_package", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``package`` + ``meta['package_ecosystem']='npm'`` "
                   "per audit-findings 0005, Wave 6 PR 3. Producer "
                   "(``js_module.py``) migrated; registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("composer_package", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``package`` + ``meta['package_ecosystem']="
                   "'composer'`` per audit-findings 0005, Wave 6 PR 3. Producer "
                   "(``json_config.py``) migrated; registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("main_entry", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``file`` + ``meta['entry_role']='main'`` per "
                   "audit-findings 0005, Wave 6 PR 3. Producer "
                   "(``json_config.py``) migrated; registry entry stays through "
                   "the Phase 4a deprecation window."),
    SymbolKindSpec("bin", AXIS_PENDING,
                   "Binary executable symbol. Pending cluster-B audit."),
    SymbolKindSpec("library_export", AXIS_ENDPOINT_SHAPE,
                   "Audit-findings 0005 verdict FOLD to ``export`` + "
                   "``meta['export_scope']='library'``. Migration is vacuous "
                   "on the Symbol.kind axis: the value is only emitted as "
                   "``UsageContext.kind`` (``js_ts.py``), which is a separate "
                   "axis. The Symbol.kind registry entry was a Phase-1 seed "
                   "error; this entry stays on AXIS_ENDPOINT_SHAPE through "
                   "the Phase 4a deprecation window for symmetry with the "
                   "rest of the Cluster B fold cohort."),
    SymbolKindSpec("export_entry", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``export`` + ``meta['export_source']="
                   "'package_exports_map'`` per audit-findings 0005, Wave 6 "
                   "PR 3. Producer (``json_config.py``) migrated; registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("wasm_module", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``module`` + ``meta['compilation_target']='wasm'`` "
                   "per audit-findings 0005, Wave 6 PR 3. Producer "
                   "(``wasm_bindgen.py``) migrated; registry entry stays "
                   "through the Phase 4a deprecation window."),
    SymbolKindSpec("wasm_import", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``import`` + ``meta['compilation_target']='wasm'`` "
                   "per audit-findings 0005, Wave 6 PR 3. Note: the fold "
                   "target ``import`` is itself on AXIS_ENDPOINT_SHAPE per "
                   "audit-findings 0010 — wasm_bindgen requires the synthetic "
                   "node for slicer BFS continuity, so this is the one Cluster "
                   "B FOLD where the target is a deprecation-window kind. "
                   "Producer (``wasm_bindgen.py``) migrated."),
    SymbolKindSpec("tsconfig", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0005, Wave 6 PR 3: "
                   "producer (``json_config.py``) drops the kind specialisation "
                   "and emits ``kind='file'`` + ``is_config_file=True`` + "
                   "``meta['config_format']='tsconfig'`` instead. Registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("script", AXIS_ENDPOINT_SHAPE,
                   "FOLDed to ``file`` + ``meta['entry_role']='script'`` per "
                   "audit-findings 0005, Wave 6 PR 3. Producers "
                   "(``json_config.py`` for npm scripts, ``toml_config.py`` "
                   "for ``[project.scripts]``) migrated; registry entry stays "
                   "through the Phase 4a deprecation window."),

    # ----------------------------------------------------------------
    # Cluster G — Build / config-shape.
    # AXIS_PENDING: separate-axis vs canonical decision per cluster-G
    # audit-findings doc.
    # ----------------------------------------------------------------
    SymbolKindSpec("test", AXIS_LANGUAGE_CONSTRUCT,
                   "Test-case symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("test_case", AXIS_PENDING,
                   "Test-case symbol (alternate label). Pending cluster-G audit."),
    SymbolKindSpec("work_item", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0006, Wave 6 PR 4: "
                   "tracker `Item.kind` value mistakenly registered against "
                   "`Symbol.kind`. No producer emits this kind. Registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("target", AXIS_LANGUAGE_CONSTRUCT,
                   "Build-target symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("special_target", AXIS_LANGUAGE_CONSTRUCT,
                   "Make special-target symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("recipe", AXIS_LANGUAGE_CONSTRUCT,
                   "Make recipe symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("env_var", AXIS_LANGUAGE_CONSTRUCT,
                   "Environment variable symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("build_arg", AXIS_LANGUAGE_CONSTRUCT,
                   "Build argument symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("exposed_port", AXIS_LANGUAGE_CONSTRUCT,
                   "Container exposed-port symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("stage", AXIS_LANGUAGE_CONSTRUCT,
                   "Build / pipeline stage. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("requirement", AXIS_LANGUAGE_CONSTRUCT,
                   "Requirement / pip requirement. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("editable", AXIS_PENDING,
                   "Editable install symbol. Pending cluster-G audit."),
    SymbolKindSpec("url_requirement", AXIS_PENDING,
                   "URL-requirement install symbol. Pending cluster-G audit."),
    SymbolKindSpec("setting", AXIS_LANGUAGE_CONSTRUCT,
                   "Setting / option symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("config", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0006, Wave 6 PR 4: "
                   "Prisma generic block placeholder where the real construct "
                   "(``datasource`` / ``generator``) lives in "
                   "``meta['block_type']``. Producer (``prisma.py:179``) "
                   "drops the kind specialisation and emits ``kind='block'`` "
                   "instead. Registry entry stays through the Phase 4a "
                   "deprecation window."),
    SymbolKindSpec("derivation", AXIS_LANGUAGE_CONSTRUCT,
                   "Nix derivation symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("dependency", AXIS_LANGUAGE_CONSTRUCT,
                   "Dependency entry. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("devDependency", AXIS_PENDING,
                   "JS devDependency entry. Pending cluster-G audit."),
    SymbolKindSpec("dev-dependency", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0006, Wave 6 PR 4: "
                   "dead vocabulary — no producer emits this kind. Registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("build-dependency", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0006, Wave 6 PR 4: "
                   "dead vocabulary — no producer emits this kind. Registry "
                   "entry stays through the Phase 4a deprecation window."),
    SymbolKindSpec("addtask", AXIS_LANGUAGE_CONSTRUCT,
                   "BitBake addtask symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("python_task", AXIS_PENDING,
                   "BitBake Python task symbol. Pending cluster-G audit."),
    SymbolKindSpec("task", AXIS_LANGUAGE_CONSTRUCT,
                   "Generic task symbol. CANONICAL per audit-findings 0006."),
    SymbolKindSpec("trigger", AXIS_LANGUAGE_CONSTRUCT,
                   "Pipeline / DB trigger symbol. CANONICAL per audit-findings 0006."),

    # ----------------------------------------------------------------
    # Cluster H — Domain-specific long tail.
    # AXIS_PENDING: per-value audit pending in cluster-H audit-findings.
    # ----------------------------------------------------------------
    SymbolKindSpec("section", AXIS_LANGUAGE_CONSTRUCT,
                   "Section symbol (markdown / config). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("paragraph", AXIS_LANGUAGE_CONSTRUCT,
                   "Paragraph symbol (markdown / docs). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("heading", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0007, Wave 6 PR 4: "
                   "dead vocabulary — markdown emits ``kind='section'`` "
                   "(``markdown.py:198``); no producer emits ``kind='heading'``. "
                   "Registry entry stays through the Phase 4a deprecation "
                   "window."),
    SymbolKindSpec("code_block", AXIS_LANGUAGE_CONSTRUCT,
                   "Code-block symbol (markdown). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("diagram", AXIS_LANGUAGE_CONSTRUCT,
                   "Diagram symbol (mermaid / graphviz). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("plot", AXIS_LANGUAGE_CONSTRUCT,
                   "Plot symbol (notebook / R). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("yield", AXIS_LANGUAGE_CONSTRUCT,
                   "Yield-statement symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("for_loop", AXIS_LANGUAGE_CONSTRUCT,
                   "For-loop symbol (control-flow). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("conditional", AXIS_LANGUAGE_CONSTRUCT,
                   "Conditional-statement symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("block", AXIS_LANGUAGE_CONSTRUCT,
                   "Block symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("prefix", AXIS_LANGUAGE_CONSTRUCT,
                   "Prefix symbol (URI / namespace). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("base", AXIS_LANGUAGE_CONSTRUCT,
                   "Base symbol (XML / OWL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("query", AXIS_LANGUAGE_CONSTRUCT,
                   "Query symbol (GraphQL / SQL operation). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("entry", AXIS_LANGUAGE_CONSTRUCT,
                   "Entry symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("entity", AXIS_LANGUAGE_CONSTRUCT,
                   "Entity symbol (DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("architecture", AXIS_LANGUAGE_CONSTRUCT,
                   "Architecture symbol (VHDL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("participant", AXIS_LANGUAGE_CONSTRUCT,
                   "Participant symbol (mermaid). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("state", AXIS_LANGUAGE_CONSTRUCT,
                   "State symbol (state-machine DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("model", AXIS_ENDPOINT_SHAPE,
                   "DEPRECATE-NO-FOLD per audit-findings 0007, Wave 6 PR 4: "
                   "ID-string-only synthetic — only appears in "
                   "``prisma.py:120``'s ``compute_stable_id`` / "
                   "``make_symbol_id`` arguments. The actual emitted Symbol "
                   "for a Prisma ``model`` block is ``kind='class'``. "
                   "Registry entry stays through the Phase 4a deprecation "
                   "window."),
    SymbolKindSpec("fragment", AXIS_LANGUAGE_CONSTRUCT,
                   "Fragment symbol (GraphQL / template). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("partial", AXIS_LANGUAGE_CONSTRUCT,
                   "Partial symbol (template). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("provider", AXIS_LANGUAGE_CONSTRUCT,
                   "Provider symbol (Terraform / DI). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("local", AXIS_LANGUAGE_CONSTRUCT,
                   "Local symbol (Terraform local). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("style_block", AXIS_LANGUAGE_CONSTRUCT,
                   "Style-block symbol (Vue / scoped CSS). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("permission", AXIS_LANGUAGE_CONSTRUCT,
                   "Permission symbol (k8s / Solidity). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("keyframes", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS @keyframes symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("media", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS @media symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("font_face", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS @font-face symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("class_selector", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS class selector symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("id_selector", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS id selector symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("rule_set", AXIS_LANGUAGE_CONSTRUCT,
                   "CSS / shader rule-set symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("subdirectory", AXIS_LANGUAGE_CONSTRUCT,
                   "Subdirectory pseudo-symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("table", AXIS_LANGUAGE_CONSTRUCT,
                   "Table symbol (SQL / TOML / Markdown). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("table_array", AXIS_LANGUAGE_CONSTRUCT,
                   "TOML table-array symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("link", AXIS_LANGUAGE_CONSTRUCT,
                   "Link symbol (markdown / yaml-anchor). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("label", AXIS_LANGUAGE_CONSTRUCT,
                   "Label symbol (assembly / k8s). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("command", AXIS_LANGUAGE_CONSTRUCT,
                   "Command symbol (shell / Cobra). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("environment", AXIS_LANGUAGE_CONSTRUCT,
                   "Environment symbol (LaTeX / shell). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("binding", AXIS_LANGUAGE_CONSTRUCT,
                   "Binding symbol (DSL / DI). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("id", AXIS_LANGUAGE_CONSTRUCT,
                   "Id symbol (k8s / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("source", AXIS_LANGUAGE_CONSTRUCT,
                   "Source symbol (data-flow / shell). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("port", AXIS_LANGUAGE_CONSTRUCT,
                   "Port symbol (k8s / VHDL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("output", AXIS_LANGUAGE_CONSTRUCT,
                   "Output symbol (Terraform / shader). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("input", AXIS_LANGUAGE_CONSTRUCT,
                   "Input symbol (Terraform / shader). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("value", AXIS_LANGUAGE_CONSTRUCT,
                   "Value symbol (key-value DSLs). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("pattern", AXIS_LANGUAGE_CONSTRUCT,
                   "Pattern symbol (DSL / regex). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("subscript", AXIS_LANGUAGE_CONSTRUCT,
                   "Subscript symbol (Swift / Python __getitem__). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("signal", AXIS_LANGUAGE_CONSTRUCT,
                   "Signal symbol (VHDL / Verilog / Qt). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("message", AXIS_LANGUAGE_CONSTRUCT,
                   "Protobuf ``message`` declaration (``proto.py:260``). "
                   "CANONICAL per audit-findings 0007 (reclassified Wave 6 "
                   "PR 4 — the original DEPRECATE-NO-FOLD verdict was a "
                   "literal-grep blind-spot miss; ``proto.py`` emits via "
                   "``_make_proto_symbol(..., 'message', ...)`` indirection)."),
    SymbolKindSpec("data", AXIS_LANGUAGE_CONSTRUCT,
                   "Data symbol (Terraform data block). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("resource", AXIS_LANGUAGE_CONSTRUCT,
                   "Resource symbol (Terraform / k8s). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("event", AXIS_LANGUAGE_CONSTRUCT,
                   "Event symbol (DSL / Solidity). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("protocol", AXIS_LANGUAGE_CONSTRUCT,
                   "Protocol symbol (Swift / Solidity / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("index", AXIS_LANGUAGE_CONSTRUCT,
                   "Index symbol (SQL / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("node", AXIS_LANGUAGE_CONSTRUCT,
                   "Node symbol (k8s / DSL). CANONICAL per audit-findings 0007."),
    SymbolKindSpec("inductive", AXIS_LANGUAGE_CONSTRUCT,
                   "Lean ``inductive`` type declaration (``lean.py:247``). "
                   "CANONICAL per audit-findings 0007 (reclassified Wave 6 "
                   "PR 4 — the original DEPRECATE-NO-FOLD verdict was a "
                   "literal-grep blind-spot miss; ``lean.py`` emits via "
                   "``add_symbol(..., 'inductive')`` indirection)."),
    SymbolKindSpec("theorem", AXIS_LANGUAGE_CONSTRUCT,
                   "Theorem-prover top-level construct (Lean theorems and "
                   "lemmas at ``lean.py:222,231``; TLA+ theorems at "
                   "``tlaplus.py:207``). CANONICAL per audit-findings 0007 "
                   "(reclassified Wave 6 PR 4 — the original DEPRECATE-NO-FOLD "
                   "verdict was a literal-grep blind-spot miss; both producers "
                   "emit via ``add_symbol(..., 'theorem')`` indirection)."),
    SymbolKindSpec("playbook", AXIS_LANGUAGE_CONSTRUCT,
                   "Ansible playbook symbol. CANONICAL per audit-findings 0007."),
    SymbolKindSpec("structure", AXIS_ENDPOINT_SHAPE,
                   "Cluster C apex/peer: deprecated peer of `struct`. "
                   "No producer emits this kind (verified WI-rusit Wave 4); "
                   "registry entry remains through the Phase 4a deprecation "
                   "window per ADR-0027. Fold target: struct."),
    SymbolKindSpec("external_symbol", AXIS_PENDING,
                   "External-symbol pseudo-node. Pending cluster-H audit."),
    SymbolKindSpec("unresolved", AXIS_PENDING,
                   "Unresolved-symbol pseudo-node. Pending cluster-H audit."),

    # ----------------------------------------------------------------
    # WI-nubuv ext A discoveries — assignment-form producer leaks
    # ----------------------------------------------------------------
    # Surfaced by the L3 producer-coherence linter when extension A
    # (function-local assignment trace) landed and walked back
    # ``kind = "<literal>"`` shapes that were previously invisible to
    # the literal-kwarg-only matcher. All registered AXIS_PENDING for
    # follow-on Cluster-G / Cluster-H audit; the values themselves
    # are real language-level kinds, just never previously surfaced.
    SymbolKindSpec("binary", AXIS_PENDING,
                   "Cargo `[[bin]]` target kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("benchmark", AXIS_PENDING,
                   "Cargo `[[bench]]` target kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("example", AXIS_PENDING,
                   "Cargo `[[example]]` target kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("workspace", AXIS_PENDING,
                   "Cargo `[workspace]` table kind. "
                   "Pending cluster-G audit."),
    SymbolKindSpec("handler", AXIS_PENDING,
                   "Ansible playbook handler. Pending cluster-G audit."),
    SymbolKindSpec("pattern_rule", AXIS_PENDING,
                   "Make pattern-rule target. Pending cluster-G audit."),
    SymbolKindSpec("helper", AXIS_PENDING,
                   "Handlebars block helper (non-builtin). "
                   "Pending cluster-H audit."),
    SymbolKindSpec("uniform", AXIS_PENDING,
                   "Shader uniform binding (GLSL / WGSL). "
                   "Pending cluster-H audit."),
    SymbolKindSpec("varying", AXIS_PENDING,
                   "GLSL varying qualifier (legacy interpolation). "
                   "Pending cluster-H audit."),
    SymbolKindSpec("private", AXIS_PENDING,
                   "WGSL `var<private>` address space. "
                   "Pending cluster-H audit."),
    SymbolKindSpec("storage", AXIS_PENDING,
                   "WGSL `var<storage>` address space. "
                   "Pending cluster-H audit."),
    SymbolKindSpec("workgroup", AXIS_PENDING,
                   "WGSL `var<workgroup>` address space. "
                   "Pending cluster-H audit."),
)


def all_symbol_kind_names() -> frozenset[str]:
    """Return every canonical Symbol.kind name."""
    return frozenset(spec.name for spec in SYMBOL_KINDS)


def symbol_kinds_on_axis(axis: str) -> tuple[SymbolKindSpec, ...]:
    """Return all Symbol.kind specs whose axis equals *axis*.

    Use this in place of hardcoded sets like
    ``_CALLABLE_KINDS = {"function", "method", "fn", "proc"}``: query
    by axis (or by another property) instead of enumerating values, so
    new specs that match the axis are picked up automatically.
    """
    return tuple(spec for spec in SYMBOL_KINDS if spec.axis == axis)


def find_symbol_kind(name: str) -> SymbolKindSpec | None:
    """Look up a Symbol.kind spec by name; return None if not registered."""
    for spec in SYMBOL_KINDS:
        if spec.name == name:
            return spec
    return None


def find_axis_drift(repo_root: Path) -> list[str]:
    """Scan the repo for hardcoded ``*KIND*`` sets that drift from the registry.

    Wraps the field-agnostic AST walker in
    :mod:`hypergumbo_core.axis_drift` with the parameterization for
    ``Symbol.kind``: scans for module-level set / frozenset assignments
    whose target name contains ``KIND`` and returns a human-readable
    list of drift locations (file:line references plus the offending
    values).

    Per ADR-0027 §"Phase 1": the registry is seeded with every value
    currently emitted by producer code, so on a clean tree this
    function returns an empty list. Adding a NEW hardcoded value at a
    consumer site without first registering it in :data:`SYMBOL_KINDS`
    causes this scan to surface the addition; that's the structural
    enforcement the registry exists to provide.

    Used by the property test in ``tests/test_symbol_kinds.py`` and the
    pre-commit linter at ``scripts/check-symbol-kind-drift``.
    """
    from hypergumbo_core.axis_drift import find_drift
    return find_drift(
        repo_root,
        name_filter="KIND",
        registry_names=all_symbol_kind_names(),
        # ``PROTOCOL_KINDS`` and ``BRIDGE_KINDS`` in ``edge_types.py``
        # are vocabularies for ``Edge.meta['protocol']`` /
        # ``Edge.meta['bridge_kind']`` — not ``Symbol.kind`` sets.
        # They share the ``KIND`` substring but live on a different
        # axis; exclude them by name.
        excluded_target_names=("PROTOCOL_KINDS", "BRIDGE_KINDS"),
    )
