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
    SymbolKindSpec("import", AXIS_LANGUAGE_CONSTRUCT,
                   "Import declaration as a syntactic-form symbol."),
    SymbolKindSpec("include", AXIS_LANGUAGE_CONSTRUCT,
                   "Include declaration (Ruby include, C #include, Make include)."),
    SymbolKindSpec("extends", AXIS_LANGUAGE_CONSTRUCT,
                   "Extends clause as a syntactic form (Java, Solidity)."),

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
                   "gRPC server method. Fold to function/method + meta['framework_role']='grpc_server'."),
    SymbolKindSpec("grpc_stub", AXIS_ENDPOINT_SHAPE,
                   "gRPC stub method. Fold to function/method + meta['framework_role']='grpc_stub'."),
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
    # AXIS_PENDING: separate-axis vs language-construct decision per
    # cluster-B audit-findings doc.
    # ----------------------------------------------------------------
    SymbolKindSpec("file", AXIS_PENDING,
                   "File-shape symbol. Pending cluster-B audit."),
    SymbolKindSpec("library", AXIS_PENDING,
                   "Library-shape symbol. Pending cluster-B audit."),
    SymbolKindSpec("package", AXIS_PENDING,
                   "Package-shape symbol. Pending cluster-B audit."),
    SymbolKindSpec("executable", AXIS_PENDING,
                   "Executable-shape symbol. Pending cluster-B audit."),
    SymbolKindSpec("program", AXIS_PENDING,
                   "Program-shape symbol. Pending cluster-B audit."),
    SymbolKindSpec("project", AXIS_PENDING,
                   "Project-shape symbol. Pending cluster-B audit."),
    SymbolKindSpec("module_file", AXIS_PENDING,
                   "Module-as-file symbol. Pending cluster-B audit."),
    SymbolKindSpec("component_file", AXIS_PENDING,
                   "Component-as-file symbol. Pending cluster-B audit."),
    SymbolKindSpec("npm_package", AXIS_PENDING,
                   "NPM package symbol. Pending cluster-B audit."),
    SymbolKindSpec("composer_package", AXIS_PENDING,
                   "Composer package symbol. Pending cluster-B audit."),
    SymbolKindSpec("main_entry", AXIS_PENDING,
                   "Main-entry pseudo-symbol. Pending cluster-B audit."),
    SymbolKindSpec("bin", AXIS_PENDING,
                   "Binary executable symbol. Pending cluster-B audit."),
    SymbolKindSpec("library_export", AXIS_PENDING,
                   "Library-export entry. Pending cluster-B audit."),
    SymbolKindSpec("export_entry", AXIS_PENDING,
                   "Generic export entry. Pending cluster-B audit."),
    SymbolKindSpec("wasm_module", AXIS_PENDING,
                   "WebAssembly module symbol. Pending cluster-B audit."),
    SymbolKindSpec("wasm_import", AXIS_PENDING,
                   "WebAssembly import symbol. Pending cluster-B audit."),
    SymbolKindSpec("tsconfig", AXIS_PENDING,
                   "TypeScript tsconfig symbol. Pending cluster-B audit."),
    SymbolKindSpec("script", AXIS_PENDING,
                   "Shell-script-shape symbol. Pending cluster-B audit."),

    # ----------------------------------------------------------------
    # Cluster G — Build / config-shape.
    # AXIS_PENDING: separate-axis vs canonical decision per cluster-G
    # audit-findings doc.
    # ----------------------------------------------------------------
    SymbolKindSpec("test", AXIS_PENDING,
                   "Test-case symbol. Pending cluster-G audit."),
    SymbolKindSpec("test_case", AXIS_PENDING,
                   "Test-case symbol (alternate label). Pending cluster-G audit."),
    SymbolKindSpec("work_item", AXIS_PENDING,
                   "Work-item symbol. Pending cluster-G audit."),
    SymbolKindSpec("target", AXIS_PENDING,
                   "Build-target symbol. Pending cluster-G audit."),
    SymbolKindSpec("special_target", AXIS_PENDING,
                   "Make special-target symbol. Pending cluster-G audit."),
    SymbolKindSpec("recipe", AXIS_PENDING,
                   "Make recipe symbol. Pending cluster-G audit."),
    SymbolKindSpec("env_var", AXIS_PENDING,
                   "Environment variable symbol. Pending cluster-G audit."),
    SymbolKindSpec("build_arg", AXIS_PENDING,
                   "Build argument symbol. Pending cluster-G audit."),
    SymbolKindSpec("exposed_port", AXIS_PENDING,
                   "Container exposed-port symbol. Pending cluster-G audit."),
    SymbolKindSpec("stage", AXIS_PENDING,
                   "Build / pipeline stage. Pending cluster-G audit."),
    SymbolKindSpec("requirement", AXIS_PENDING,
                   "Requirement / pip requirement. Pending cluster-G audit."),
    SymbolKindSpec("editable", AXIS_PENDING,
                   "Editable install symbol. Pending cluster-G audit."),
    SymbolKindSpec("url_requirement", AXIS_PENDING,
                   "URL-requirement install symbol. Pending cluster-G audit."),
    SymbolKindSpec("setting", AXIS_PENDING,
                   "Setting / option symbol. Pending cluster-G audit."),
    SymbolKindSpec("config", AXIS_PENDING,
                   "Config symbol. Pending cluster-G audit."),
    SymbolKindSpec("derivation", AXIS_PENDING,
                   "Nix derivation symbol. Pending cluster-G audit."),
    SymbolKindSpec("dependency", AXIS_PENDING,
                   "Dependency entry. Pending cluster-G audit."),
    SymbolKindSpec("devDependency", AXIS_PENDING,
                   "JS devDependency entry. Pending cluster-G audit."),
    SymbolKindSpec("dev-dependency", AXIS_PENDING,
                   "Dev-dependency entry. Pending cluster-G audit."),
    SymbolKindSpec("build-dependency", AXIS_PENDING,
                   "Build-dependency entry. Pending cluster-G audit."),
    SymbolKindSpec("addtask", AXIS_PENDING,
                   "BitBake addtask symbol. Pending cluster-G audit."),
    SymbolKindSpec("python_task", AXIS_PENDING,
                   "BitBake Python task symbol. Pending cluster-G audit."),
    SymbolKindSpec("task", AXIS_PENDING,
                   "Generic task symbol. Pending cluster-G audit."),
    SymbolKindSpec("trigger", AXIS_PENDING,
                   "Pipeline / DB trigger symbol. Pending cluster-G audit."),

    # ----------------------------------------------------------------
    # Cluster H — Domain-specific long tail.
    # AXIS_PENDING: per-value audit pending in cluster-H audit-findings.
    # ----------------------------------------------------------------
    SymbolKindSpec("section", AXIS_PENDING,
                   "Section symbol (markdown / config). Pending cluster-H audit."),
    SymbolKindSpec("paragraph", AXIS_PENDING,
                   "Paragraph symbol (markdown / docs). Pending cluster-H audit."),
    SymbolKindSpec("heading", AXIS_PENDING,
                   "Heading symbol (markdown / docs). Pending cluster-H audit."),
    SymbolKindSpec("code_block", AXIS_PENDING,
                   "Code-block symbol (markdown). Pending cluster-H audit."),
    SymbolKindSpec("diagram", AXIS_PENDING,
                   "Diagram symbol (mermaid / graphviz). Pending cluster-H audit."),
    SymbolKindSpec("plot", AXIS_PENDING,
                   "Plot symbol (notebook / R). Pending cluster-H audit."),
    SymbolKindSpec("yield", AXIS_PENDING,
                   "Yield-statement symbol. Pending cluster-H audit."),
    SymbolKindSpec("for_loop", AXIS_PENDING,
                   "For-loop symbol (control-flow). Pending cluster-H audit."),
    SymbolKindSpec("conditional", AXIS_PENDING,
                   "Conditional-statement symbol. Pending cluster-H audit."),
    SymbolKindSpec("block", AXIS_PENDING,
                   "Block symbol. Pending cluster-H audit."),
    SymbolKindSpec("prefix", AXIS_PENDING,
                   "Prefix symbol (URI / namespace). Pending cluster-H audit."),
    SymbolKindSpec("base", AXIS_PENDING,
                   "Base symbol (XML / OWL). Pending cluster-H audit."),
    SymbolKindSpec("query", AXIS_PENDING,
                   "Query symbol (GraphQL / SQL operation). Pending cluster-H audit."),
    SymbolKindSpec("entry", AXIS_PENDING,
                   "Entry symbol. Pending cluster-H audit."),
    SymbolKindSpec("entity", AXIS_PENDING,
                   "Entity symbol (DSL). Pending cluster-H audit."),
    SymbolKindSpec("architecture", AXIS_PENDING,
                   "Architecture symbol (VHDL). Pending cluster-H audit."),
    SymbolKindSpec("participant", AXIS_PENDING,
                   "Participant symbol (mermaid). Pending cluster-H audit."),
    SymbolKindSpec("state", AXIS_PENDING,
                   "State symbol (state-machine DSL). Pending cluster-H audit."),
    SymbolKindSpec("model", AXIS_PENDING,
                   "Model symbol (DSL). Pending cluster-H audit."),
    SymbolKindSpec("fragment", AXIS_PENDING,
                   "Fragment symbol (GraphQL / template). Pending cluster-H audit."),
    SymbolKindSpec("partial", AXIS_PENDING,
                   "Partial symbol (template). Pending cluster-H audit."),
    SymbolKindSpec("provider", AXIS_PENDING,
                   "Provider symbol (Terraform / DI). Pending cluster-H audit."),
    SymbolKindSpec("local", AXIS_PENDING,
                   "Local symbol (Terraform local). Pending cluster-H audit."),
    SymbolKindSpec("style_block", AXIS_PENDING,
                   "Style-block symbol (Vue / scoped CSS). Pending cluster-H audit."),
    SymbolKindSpec("permission", AXIS_PENDING,
                   "Permission symbol (k8s / Solidity). Pending cluster-H audit."),
    SymbolKindSpec("keyframes", AXIS_PENDING,
                   "CSS @keyframes symbol. Pending cluster-H audit."),
    SymbolKindSpec("media", AXIS_PENDING,
                   "CSS @media symbol. Pending cluster-H audit."),
    SymbolKindSpec("font_face", AXIS_PENDING,
                   "CSS @font-face symbol. Pending cluster-H audit."),
    SymbolKindSpec("class_selector", AXIS_PENDING,
                   "CSS class selector symbol. Pending cluster-H audit."),
    SymbolKindSpec("id_selector", AXIS_PENDING,
                   "CSS id selector symbol. Pending cluster-H audit."),
    SymbolKindSpec("rule_set", AXIS_PENDING,
                   "CSS / shader rule-set symbol. Pending cluster-H audit."),
    SymbolKindSpec("subdirectory", AXIS_PENDING,
                   "Subdirectory pseudo-symbol. Pending cluster-H audit."),
    SymbolKindSpec("table", AXIS_PENDING,
                   "Table symbol (SQL / TOML / Markdown). Pending cluster-H audit."),
    SymbolKindSpec("table_array", AXIS_PENDING,
                   "TOML table-array symbol. Pending cluster-H audit."),
    SymbolKindSpec("link", AXIS_PENDING,
                   "Link symbol (markdown / yaml-anchor). Pending cluster-H audit."),
    SymbolKindSpec("label", AXIS_PENDING,
                   "Label symbol (assembly / k8s). Pending cluster-H audit."),
    SymbolKindSpec("command", AXIS_PENDING,
                   "Command symbol (shell / Cobra). Pending cluster-H audit."),
    SymbolKindSpec("environment", AXIS_PENDING,
                   "Environment symbol (LaTeX / shell). Pending cluster-H audit."),
    SymbolKindSpec("binding", AXIS_PENDING,
                   "Binding symbol (DSL / DI). Pending cluster-H audit."),
    SymbolKindSpec("id", AXIS_PENDING,
                   "Id symbol (k8s / DSL). Pending cluster-H audit."),
    SymbolKindSpec("source", AXIS_PENDING,
                   "Source symbol (data-flow / shell). Pending cluster-H audit."),
    SymbolKindSpec("port", AXIS_PENDING,
                   "Port symbol (k8s / VHDL). Pending cluster-H audit."),
    SymbolKindSpec("output", AXIS_PENDING,
                   "Output symbol (Terraform / shader). Pending cluster-H audit."),
    SymbolKindSpec("input", AXIS_PENDING,
                   "Input symbol (Terraform / shader). Pending cluster-H audit."),
    SymbolKindSpec("value", AXIS_PENDING,
                   "Value symbol (key-value DSLs). Pending cluster-H audit."),
    SymbolKindSpec("pattern", AXIS_PENDING,
                   "Pattern symbol (DSL / regex). Pending cluster-H audit."),
    SymbolKindSpec("subscript", AXIS_PENDING,
                   "Subscript symbol (Swift / Python __getitem__). Pending cluster-H audit."),
    SymbolKindSpec("signal", AXIS_PENDING,
                   "Signal symbol (VHDL / Verilog / Qt). Pending cluster-H audit."),
    SymbolKindSpec("message", AXIS_PENDING,
                   "Message symbol (proto / DSL). Pending cluster-H audit."),
    SymbolKindSpec("data", AXIS_PENDING,
                   "Data symbol (Terraform data block). Pending cluster-H audit."),
    SymbolKindSpec("resource", AXIS_PENDING,
                   "Resource symbol (Terraform / k8s). Pending cluster-H audit."),
    SymbolKindSpec("event", AXIS_PENDING,
                   "Event symbol (DSL / Solidity). Pending cluster-H audit."),
    SymbolKindSpec("protocol", AXIS_PENDING,
                   "Protocol symbol (Swift / Solidity / DSL). Pending cluster-H audit."),
    SymbolKindSpec("index", AXIS_PENDING,
                   "Index symbol (SQL / DSL). Pending cluster-H audit."),
    SymbolKindSpec("node", AXIS_PENDING,
                   "Node symbol (k8s / DSL). Pending cluster-H audit."),
    SymbolKindSpec("inductive", AXIS_PENDING,
                   "Inductive type (Coq / Lean). Pending cluster-H audit."),
    SymbolKindSpec("theorem", AXIS_PENDING,
                   "Theorem symbol (Coq / Lean). Pending cluster-H audit."),
    SymbolKindSpec("playbook", AXIS_PENDING,
                   "Ansible playbook symbol. Pending cluster-H audit."),
    SymbolKindSpec("structure", AXIS_ENDPOINT_SHAPE,
                   "Cluster C apex/peer: deprecated peer of `struct`. "
                   "No producer emits this kind (verified WI-rusit Wave 4); "
                   "registry entry remains through the Phase 4a deprecation "
                   "window per ADR-0027. Fold target: struct."),
    SymbolKindSpec("external_symbol", AXIS_PENDING,
                   "External-symbol pseudo-node. Pending cluster-H audit."),
    SymbolKindSpec("unresolved", AXIS_PENDING,
                   "Unresolved-symbol pseudo-node. Pending cluster-H audit."),
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
