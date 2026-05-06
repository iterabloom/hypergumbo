<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- AUTO-GENERATED — do not edit manually.
     Regenerate with: ./scripts/generate-concept-axes
     Sources of truth:
       packages/hypergumbo-core/src/hypergumbo_core/edge_types.py
       packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py
       packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py -->

# Concept Axes

Typing axes maintained in hypergumbo's behavior map. Each axis names a
dimension along which a multi-value field's values must be classified.
New axes are introduced via ADR following ADR-0024's four-part
declaration template (axis name, axiom, consumer pattern, enforcement);
ADR-0023 (Edge.type) is the worked example, and ADR-0027 (Symbol.kind)
is the second instantiation.

The current axes apply to `Edge.type`, `Symbol.kind`, and
`Edge.evidence_type`. Other multi-value fields (`supply_chain.tier`,
etc.) will be added here as their axes are formally declared.

## Why this doc exists

`docs/schema.json` carries the same axis information under the
`x-axis-of-values` extension keyword, but reading the schema by eye
doesn't surface the axis structure — values appear in registry order,
not grouped by axis. This doc is the human-readable view; the schema
remains the machine-readable source of truth for downstream consumers.

The Fundamental Concept Audit playbook
(`.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`)
nominates an axis from this list each release cycle for a deliberate
re-audit, complementing the commit-count cadence hook.

## Downstream consumers

If you consume hypergumbo's behavior-map JSON output and your code
filters or weights edges by `edge_type`, see
[`docs/migrating-edge-types.md`](migrating-edge-types.md) for the
rename table, the meta-key vocabulary, and migration patterns.

The values currently in the `endpoint_shape` section below (under
`Edge.type` axes and `Symbol.kind` axes both) are deprecation
candidates whose producers still emit them. Each is scheduled for
migration to a canonical relationship-axis (or language_construct-axis)
name plus an `edge.meta` (or `Symbol.meta["framework_role"]`) payload
— see the per-ADR migration guides for the per-value fold targets.
Each value's producer-side migration ships as its own per-family
subset, with bakeoff validation, then a `SCHEMA_VERSION` minor bump
removes the value from the registry. Values already removed at
`SCHEMA_VERSION` 0.4.0 do NOT appear here; this section is the
remaining backlog.

## `Edge.type` axes

### `relationship` — ADR-0023 compliant

Values that name the relationship the edge expresses between src and dst. Per ADR-0023, this is the only axis a new `edge_type` value should occupy.

- **`calls`** — Caller invokes callee.
- **`constrains`** — pip ``-c`` / ``--constraint`` file reference — constrains version selection without forcing install. Peer of ``includes`` (which models ``-r`` / ``--requirement``). Surfaced by WI-nubuv ext A in linkers/requirements.py.
- **`contains`** — Container symbol holds member symbol.
- **`data_flows_to`** — Data flow edge per ADR-0015 — value computed at src reaches dst.
- **`decorated_by`** — Symbol is decorated/annotated by another (e.g., Python decorator, Java annotation, C# attribute, Rust derive).
- **`defines_target`** — Config file defines a build/run/deploy target (Makefile rule, package.json script, pyproject entry point, Compose service, etc.).
- **`depends_on`** — Generic dependency relationship.
- **`depends_on_manifest`** — Dependency declared in a package or build manifest.
- **`dispatches_to`** — Caller dispatches to callee via runtime indirection (virtual method, function pointer, DI resolution, etc.).
- **`event_publishes`** — Producer publishes an event/message that the consumer receives via an async channel (event bus, queue, CRDT, etc.).
- **`extends`** — Class extends a superclass.
- **`implements`** — Class implements an interface.
- **`imports`** — Module imports another module or symbol.
- **`includes`** — File includes / sources / mixes-in another file's content (LaTeX \include, RST .. include::, Meson subdir, etc.).
- **`inherits`** — Class/contract inherits from a parent (used by languages where 'inherits' reads more naturally than 'extends').
- **`instantiates`** — Constructor or factory creates an instance.
- **`links`** — Generic linkage relationship.
- **`module_attr_ref`** — Reads an attribute on an imported module (e.g., os.environ).
- **`module_exports`** — Module exposes a symbol as part of its public surface (JS export, Python __all__, Rust pub, etc.).
- **`overrides`** — Method overrides a parent's same-named method (parallel to extends/implements; the override declaration itself).
- **`references`** — Symbol references another by name without invocation.
- **`sources`** — Sources another file (e.g., shell ``source``).
- **`subprocess_calls`** — Symbol invokes another symbol via a subprocess.
- **`uses`** — Generic symbol-usage relationship.
- **`wraps`** — Decorator or middleware wraps the target symbol.

### `endpoint_shape` — deprecation candidates per ADR-0023 §6

Values whose meaning is leaked into the type label even though it is captured by `src.kind` / `dst.kind` / language metadata. Migration plan in ADR-0023 §6 folds these back into relationship-shaped names with kind/language metadata on the endpoint nodes.

- **`abi_call`** — Solidity contract ABI call (cross-contract method invocation); likely fold to 'calls' + meta['protocol']='abi'.
- **`association`** — Ruby ActiveRecord association declaration (has_many, belongs_to, etc.); likely fold to 'references' + meta['construct']='association'.
- **`base_image`** — Dockerfile ``FROM`` base image reference.
- **`build_tag_alternative_of`** — Go build-tag-conditional alternative implementation of a symbol; likely fold to 'references' + meta['construct']='build_tag_alternative'.
- **`caller_invokes`** — Tauri-style cross-language invoke (caller → bound command); likely fold to 'calls' + meta['protocol']='ipc' (parallel to ipc_calls per audit-findings 0002).
- **`contains_routes`** — Controller / module containing route handlers; likely fold to 'contains' (already canonical) — pure dst-kind leakage.
- **`crypto_flow`** — Crypto-related dataflow (key/secret reaches sink); likely fold to 'data_flows_to' + meta['construct']='crypto'.
- **`depends`** — Package depends on another (Bitbake, requirements.txt); likely fold to 'depends_on' (already canonical) or 'depends_on_manifest' depending on declaration site.
- **`extends_template`** — Twig/Jinja template extends a parent template; likely fold to 'extends' + meta['construct']='template' or stay as canonical if templates' extension semantics differ enough.
- **`graphql_calls`** — GraphQL call (use 'calls' + protocol meta).
- **`grpc_calls`** — gRPC call (use 'calls' + protocol meta).
- **`http_calls`** — HTTP call (use 'calls' + protocol meta).
- **`includes_class`** — Puppet manifest includes a class declaration; likely fold to 'includes' (now canonical) + meta['construct']='puppet_class'.
- **`includes_template`** — Twig/Jinja template includes a partial; likely fold to 'includes' (now canonical) + meta['construct']='template'.
- **`invokes_callback`** — Erlang/Elixir/Ruby callback invocation (gen_server callback, framework lifecycle hook); likely fold to 'dispatches_to' or 'calls' + meta['mechanism']='callback'.
- **`kernel_launch`** — GPU kernel invocation.
- **`links_to`** — Markdown link from one document to another; likely fold to 'references' + meta['construct']='markdown_link'.
- **`notifies_resource`** — Puppet/Chef resource notify directive (trigger another resource on change); likely fold to 'event_publishes' + meta['channel_kind']='puppet_notify' (configuration-management pub-sub shape).
- **`renders`** — Controller renders a view template; likely fold to 'references' + meta['construct']='view_render' (parallel to renders_component for JSX).
- **`requires_resource`** — Puppet/Chef resource require directive (this resource depends on another); likely fold to 'depends_on' + meta['construct']='puppet_require'.
- **`script_src`** — HTML ``<script src=...>`` reference.
- **`signal_receiver`** — Django signal receiver registration; likely fold to 'event_publishes' + meta['channel_kind']='django_signal' (signals are pub-sub via Django's dispatch module).
- **`template_calls`** — Vue / template-engine method call from template into component logic; likely fold to 'calls' + meta['mechanism']='template'.
- **`uses_mixin`** — Sass/SCSS @include of a mixin; likely fold to 'references' + meta['construct']='sass_mixin'.
- **`uses_vocabulary`** — SPARQL/RDF query references a vocabulary/ontology; likely fold to 'references' + meta['construct']='rdf_vocabulary'.

### `pending_classification` — per-family audit pending per ADR-0023 §5

Values deferred to per-family audit. Some may be genuinely distinct relationships; others are protocol-conditional duplicates of a more general relationship. Verdicts arrive with each family's audit.

- **`implements_rpc`** — RPC implementation binding — pending per-family audit.
- **`openapi_implements`** — OpenAPI handler pattern — pending per-family audit.
- **`resolver_for_type`** — GraphQL resolver-type binding — pending per-family audit.
- **`resolver_implements`** — GraphQL resolver pattern — pending per-family audit.


## `Symbol.kind` axes

Per ADR-0027, `Symbol.kind` names the source-language syntactic
construct the symbol represents. Properties derivable from edges or
framework metadata are queried from those structures rather than
smuggled into the kind label. The audit at WI-dumiz-bikul classified
the 192 distinct `Symbol.kind` values currently in production into
eight clusters; this section lays out the three axes (Cluster A on
`language_construct`; Clusters D, E, plus `component_ref` from F on
`endpoint_shape` deprecation; Clusters B, C, G, H on
`pending_classification` until per-cluster audit-findings docs ship).

### `language_construct` — ADR-0027 compliant

Values that name the source-language syntactic construct the symbol represents. Per ADR-0027, this is the only axis a new `Symbol.kind` value should occupy.

- **`abstract`** — Abstract class / member declaration.
- **`alias`** — Generic alias declaration.
- **`arrow_function`** — Arrow-function expression (JS / TS).
- **`attribute`** — Attribute declaration (Python class attribute, etc.).
- **`call_site`** — Call-expression site as a syntactic construct. Cluster E sub-case (a) fold target per audit-findings 0010: the call expression is an AST node worth representing as a Symbol, distinct from the relationship captured by an Edge of edge_type='calls'. Producers that previously emitted kind='function_call' / 'subprocess_call' / 'db_query' / 'abi_call' now emit kind='call_site' with the prior specialisation moved to meta['call_kind'].
- **`class`** — Class declaration.
- **`component`** — Component declaration (Vue / Svelte / Astro / React).
- **`const`** — Const declaration (C / C++ / Rust / JS const).
- **`constant`** — Constant / final / let-immutable binding.
- **`constructor`** — Constructor / __init__ / init method.
- **`declaration`** — Generic declaration (catch-all for non-categorized syntactic forms).
- **`defined_type`** — Defined / nominal type declaration (Puppet / Coq).
- **`directive`** — Directive declaration (Vue / Angular / GraphQL).
- **`enum`** — Enum declaration.
- **`executable`** — Executable declaration (CMake `add_executable`, Meson `executable`). CANONICAL per audit-findings 0005.
- **`export`** — Export declaration (JS / TS / TOML / Rust).
- **`field`** — Field declaration on a struct / class / record.
- **`file`** — File-shape symbol — top-level file declaration in build / source DSLs. CANONICAL per audit-findings 0005.
- **`function`** — Top-level function definition.
- **`getter`** — Property getter accessor.
- **`instance`** — Typeclass / interface instance declaration.
- **`interface`** — Interface declaration.
- **`keyword`** — Keyword-shaped construct (configuration languages).
- **`library`** — Library declaration (CMake `add_library`, Meson `library`, Cargo `[lib]`, etc.). CANONICAL per audit-findings 0005.
- **`macro`** — Macro definition (Rust / C / Scheme).
- **`method`** — Method on a class / struct / interface.
- **`mixin`** — Mixin declaration (Ruby / Sass).
- **`module`** — Module declaration (the source-level construct).
- **`namespace`** — Namespace declaration (C++ / TypeScript / C#).
- **`object`** — Object / singleton declaration (Scala / Kotlin).
- **`package`** — Package declaration (CMake `find_package`, VHDL `package`, Go `package`, JS `package.json` synthesis, etc.). CANONICAL per audit-findings 0005.
- **`procedure`** — Procedure declaration (Pascal / Ada / SQL).
- **`program`** — Program declaration (Fortran `PROGRAM`, COBOL `PROGRAM-ID`, Pascal `program`). CANONICAL per audit-findings 0005.
- **`project`** — Project declaration (Meson `project()`, .csproj root, etc.). CANONICAL per audit-findings 0005.
- **`prop`** — Component prop declaration (Vue / React).
- **`property`** — Property declaration (Kotlin / Swift / C#).
- **`record`** — Record declaration (Java 14+, Erlang, Haskell).
- **`setter`** — Property setter accessor.
- **`simple_type`** — Simple-type declaration (XSD-shape).
- **`slot`** — Component slot declaration (Vue / Svelte).
- **`struct`** — Struct / record-type declaration.
- **`subroutine`** — Subroutine / sub declaration (Fortran / Perl).
- **`template`** — Template declaration (C++ / Vue / Handlebars).
- **`trait`** — Trait declaration (Rust / Scala / Groovy).
- **`type`** — Type declaration (TypeScript type, Haskell type, etc.).
- **`type_alias`** — Type alias declaration.
- **`typedef`** — C/C++ typedef declaration.
- **`union`** — Union / sum-type declaration.
- **`variable`** — Variable / let / mutable binding.
- **`view`** — View declaration (MVC / template languages).

### `endpoint_shape` — deprecation candidates per ADR-0027

Values whose meaning is leaked into the kind label even though it is captured by `Symbol.meta` (framework participation), `Edge` relationships (edge labels masquerading as kinds), or `dst.kind` queries (component refs). Migration plan in ADR-0027 §"Detailed analysis: per-cluster fold targets" folds these back into the canonical Cluster-A construct + `meta["framework_role"]` or drops them entirely as edge-only.

- **`abi_call`** — Cluster E sub-case (a) FOLD per audit-findings 0010 (reclassified from Cluster D in this PR — the Solidity ABI emit site names a call expression, not a framework role): the solidity_abi linker was reclassified to kind='call_site' + meta['call_kind']='abi'. Registry entry stays through the Phase 4a deprecation window.
- **`call`** — Cluster E DEPRECATE-NO-FOLD per audit-findings 0010: zero Symbol.kind=call producers (the value lives only on UsageContext.kind, a different field). Registry entry stays through the Phase 4a deprecation window.
- **`component_ref`** — Cluster F dst-kind leakage per audit-findings 0011: DEPRECATE-NO-FOLD (PRELIM_RESOLVED). Three producers (vue.py / svelte.py / astro.py) drop the per-reference Symbol; the companion imports Edge re-routes src to make_file_id and carries component_name + source_path in meta. Registry entry stays through the Phase 4a deprecation window.
- **`crypto_consumer`** — Crypto-flow consumer. Fold to function/method + meta['framework_role']='crypto_consumer'.
- **`crypto_producer`** — Crypto-flow producer. Fold to function/method + meta['framework_role']='crypto_producer'.
- **`db_query`** — Cluster E sub-case (a) FOLD per audit-findings 0010: the database_query linker was reclassified to kind='call_site' + meta['call_kind']='db_query'. Registry entry stays through the Phase 4a deprecation window.
- **`dispatcher`** — Generic dispatcher symbol. Fold to function/method + meta['framework_role']='dispatcher'.
- **`event_publisher`** — Symbol that publishes events. Fold to function/method + meta['framework_role']='event_publisher'.
- **`event_subscriber`** — Symbol that subscribes to events. Fold to function/method + meta['framework_role']='event_subscriber'.
- **`extends`** — Cluster E sub-case (b) DEPRECATE-NO-FOLD per audit-findings 0010: the extends_template Edge captures the relationship; no replacement Symbol kind. Two producers (twig.py, blade.py) dropped across PRs 2 and WI-kunag. Registry entry stays through the Phase 4a deprecation window.
- **`fn`** — Cluster C apex/peer: deprecated peer of `function`. No producer emits this kind (verified WI-rusit Wave 4); registry entry remains through the Phase 4a deprecation window per ADR-0027. Fold target: function.
- **`function_call`** — Cluster E sub-case (a) FOLD per audit-findings 0010: the Twig function-call producer (twig.py) was reclassified to kind='call_site'. Registry entry stays through the Phase 4a deprecation window.
- **`graphql_client`** — GraphQL client call site. Fold to function/method + meta['framework_role']='graphql_client'.
- **`graphql_resolver`** — GraphQL resolver. Fold to function/method + meta['framework_role']='graphql_resolver'.
- **`grpc_client`** — gRPC client call site (sibling of grpc_stub). Fold to function + meta['framework_role']='grpc_client'. Added 2026-05-06 (WI-nitil) — assignment-form producer at linkers/grpc.py:660 was missed by the original literal-grep audit. Registry entry stays through the Phase 4a deprecation window.
- **`grpc_server`** — gRPC server class. Fold to class + meta['framework_role']='grpc_server'.
- **`grpc_service`** — gRPC `service Foo {...}` proto declaration. Fold to interface + meta['framework_role']='grpc_service'. Added 2026-05-06 (WI-nitil) — assignment-form producer at linkers/grpc.py:655 was missed by the original literal-grep audit. Registry entry stays through the Phase 4a deprecation window.
- **`grpc_servicer`** — gRPC servicer class. Fold to class + meta['framework_role']='grpc_servicer'. Added 2026-05-06 (WI-nitil) — assignment-form producer at linkers/grpc.py:657 was missed by the original literal-grep audit. Registry entry stays through the Phase 4a deprecation window.
- **`grpc_stub`** — gRPC stub call site. Fold to function + meta['framework_role']='grpc_stub'.
- **`http_client`** — HTTP client call site. Fold to function/method + meta['framework_role']='http_client'.
- **`import`** — Cluster E sub-case (b) DEPRECATE-NO-FOLD per audit-findings 0010: the imports Edge captures the relationship; no replacement Symbol kind. Four producers (css.py, jsonnet.py, astro.py, r_lang.py) dropped across PRs 1, 2, and WI-kunag. Registry entry stays through the Phase 4a deprecation window.
- **`include`** — Cluster E sub-case (b) DEPRECATE-NO-FOLD per audit-findings 0010: the include-family Edges capture the relationship; no replacement Symbol kind. Five producers (puppet.py, scss.py, twig.py x2, make.py) dropped across PRs 1, 2, and WI-kunag. Registry entry stays through the Phase 4a deprecation window.
- **`inherit`** — Cluster E sub-case (b) FOLD-clean-drop per audit-findings 0010: the BitBake inherit-clause Symbol was dropped (relationship captured by the inherits Edge with src=bitbake:{file}, dst=bitbake:class:{cls}). Registry entry stays through the Phase 4a deprecation window.
- **`ipc`** — Generic IPC endpoint. Fold to function/method + meta['framework_role']='ipc'.
- **`ipc_bridge_caller`** — IPC bridge call endpoint. Fold to function/method + meta['framework_role']='ipc_bridge_caller'.
- **`ipc_caller`** — IPC call endpoint. Fold to function/method + meta['framework_role']='ipc_caller'.
- **`ipc_publisher`** — IPC publish endpoint. Fold to function/method + meta['framework_role']='ipc_publisher'.
- **`ipc_subscriber`** — IPC subscribe endpoint. Fold to function/method + meta['framework_role']='ipc_subscriber'.
- **`message_handler`** — Message-bus handler. Fold to function/method + meta['framework_role']='message_handler'.
- **`message_sender`** — Message-bus sender. Fold to function/method + meta['framework_role']='message_sender'.
- **`mq_publisher`** — Message-queue publisher. Fold to function/method + meta['framework_role']='mq_publisher'.
- **`mq_subscriber`** — Message-queue subscriber. Fold to function/method + meta['framework_role']='mq_subscriber'.
- **`objc_bridge`** — Objective-C bridge call. Fold to function/method + meta['framework_role']='objc_bridge'.
- **`openapi_operation`** — OpenAPI operation. Fold to function/method + meta['framework_role']='openapi_operation'.
- **`proc`** — Cluster C apex/peer: deprecated peer of `procedure`. No producer emits this kind (verified WI-rusit Wave 4); registry entry remains through the Phase 4a deprecation window per ADR-0027. Fold target: procedure.
- **`read`** — Cluster E DEPRECATE-NO-FOLD per audit-findings 0010: zero Symbol.kind=read producers (matches in pub/sub linkers are on internal dataclass fields YjsSite.kind / CryptoSite.kind / DispatchSite.kind, not Symbol.kind). Registry entry stays through the Phase 4a deprecation window.
- **`reference`** — Cluster E sub-case (b) per audit-findings 0010: UNRESOLVED — sole producer (json_config.py) is shape-2 edge-endpoint-dependent (references Edge has dst=symbol_id). Drop deferred to follow-on PR.
- **`route`** — Route declaration. Fold to function/method + meta['framework_role']='route'.
- **`route_include`** — Route include declaration. Fold to function/method + meta['framework_role']='route_include'.
- **`route_mount`** — Route mount declaration. Fold to function/method + meta['framework_role']='route_mount'.
- **`rpc`** — RPC method declaration. Fold to function/method + meta['framework_role']='rpc'.
- **`selector_ref`** — ObjC selector reference. Fold to reference + meta['framework_role']='selector_ref'.
- **`service`** — Service declaration (gRPC service, k8s service). Fold to interface/class + meta['framework_role']='service'.
- **`structure`** — Cluster C apex/peer: deprecated peer of `struct`. No producer emits this kind (verified WI-rusit Wave 4); registry entry remains through the Phase 4a deprecation window per ADR-0027. Fold target: struct.
- **`subprocess_call`** — Cluster E sub-case (a) FOLD per audit-findings 0010: the subprocess_cli linker was reclassified to kind='call_site' + meta['call_kind']='subprocess'. Registry entry stays through the Phase 4a deprecation window.
- **`var`** — Cluster C apex/peer: deprecated peer of `variable`. No producer emits this kind (verified WI-rusit Wave 4); registry entry remains through the Phase 4a deprecation window per ADR-0027. Fold target: variable.
- **`websocket_emitter`** — WebSocket emitter. Fold to function/method + meta['framework_role']='websocket_emitter'.
- **`websocket_endpoint`** — WebSocket endpoint. Fold to function/method + meta['framework_role']='websocket_endpoint'.
- **`websocket_listener`** — WebSocket listener. Fold to function/method + meta['framework_role']='websocket_listener'.
- **`write`** — Cluster E DEPRECATE-NO-FOLD per audit-findings 0010: symmetric counterpart of read; zero Symbol.kind=write producers. Registry entry stays through the Phase 4a deprecation window.

### `pending_classification` — per-cluster audit pending per ADR-0027 §"Migration"

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Includes the file-shape entities (Cluster B), apex/peer overloads (Cluster C), build/config-shape entities (Cluster G), and the long-tail domain vocabulary (Cluster H). Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`addtask`** — BitBake addtask symbol. Pending cluster-G audit.
- **`architecture`** — Architecture symbol (VHDL). Pending cluster-H audit.
- **`base`** — Base symbol (XML / OWL). Pending cluster-H audit.
- **`benchmark`** — Cargo `[[bench]]` target kind. Pending cluster-G audit.
- **`bin`** — Binary executable symbol. Pending cluster-B audit.
- **`binary`** — Cargo `[[bin]]` target kind. Pending cluster-G audit.
- **`binding`** — Binding symbol (DSL / DI). Pending cluster-H audit.
- **`block`** — Block symbol. Pending cluster-H audit.
- **`build-dependency`** — Build-dependency entry. Pending cluster-G audit.
- **`build_arg`** — Build argument symbol. Pending cluster-G audit.
- **`class_selector`** — CSS class selector symbol. Pending cluster-H audit.
- **`code_block`** — Code-block symbol (markdown). Pending cluster-H audit.
- **`command`** — Command symbol (shell / Cobra). Pending cluster-H audit.
- **`component_file`** — Component-as-file symbol. Pending cluster-B audit.
- **`composer_package`** — Composer package symbol. Pending cluster-B audit.
- **`conditional`** — Conditional-statement symbol. Pending cluster-H audit.
- **`config`** — Config symbol. Pending cluster-G audit.
- **`data`** — Data symbol (Terraform data block). Pending cluster-H audit.
- **`dependency`** — Dependency entry. Pending cluster-G audit.
- **`derivation`** — Nix derivation symbol. Pending cluster-G audit.
- **`dev-dependency`** — Dev-dependency entry. Pending cluster-G audit.
- **`devDependency`** — JS devDependency entry. Pending cluster-G audit.
- **`diagram`** — Diagram symbol (mermaid / graphviz). Pending cluster-H audit.
- **`editable`** — Editable install symbol. Pending cluster-G audit.
- **`entity`** — Entity symbol (DSL). Pending cluster-H audit.
- **`entry`** — Entry symbol. Pending cluster-H audit.
- **`env_var`** — Environment variable symbol. Pending cluster-G audit.
- **`environment`** — Environment symbol (LaTeX / shell). Pending cluster-H audit.
- **`event`** — Event symbol (DSL / Solidity). Pending cluster-H audit.
- **`example`** — Cargo `[[example]]` target kind. Pending cluster-G audit.
- **`export_entry`** — Generic export entry. Pending cluster-B audit.
- **`exposed_port`** — Container exposed-port symbol. Pending cluster-G audit.
- **`external_symbol`** — External-symbol pseudo-node. Pending cluster-H audit.
- **`font_face`** — CSS @font-face symbol. Pending cluster-H audit.
- **`for_loop`** — For-loop symbol (control-flow). Pending cluster-H audit.
- **`fragment`** — Fragment symbol (GraphQL / template). Pending cluster-H audit.
- **`handler`** — Ansible playbook handler. Pending cluster-G audit.
- **`heading`** — Heading symbol (markdown / docs). Pending cluster-H audit.
- **`helper`** — Handlebars block helper (non-builtin). Pending cluster-H audit.
- **`id`** — Id symbol (k8s / DSL). Pending cluster-H audit.
- **`id_selector`** — CSS id selector symbol. Pending cluster-H audit.
- **`index`** — Index symbol (SQL / DSL). Pending cluster-H audit.
- **`inductive`** — Inductive type (Coq / Lean). Pending cluster-H audit.
- **`input`** — Input symbol (Terraform / shader). Pending cluster-H audit.
- **`keyframes`** — CSS @keyframes symbol. Pending cluster-H audit.
- **`label`** — Label symbol (assembly / k8s). Pending cluster-H audit.
- **`library_export`** — Library-export entry. Pending cluster-B audit.
- **`link`** — Link symbol (markdown / yaml-anchor). Pending cluster-H audit.
- **`local`** — Local symbol (Terraform local). Pending cluster-H audit.
- **`main_entry`** — Main-entry pseudo-symbol. Pending cluster-B audit.
- **`media`** — CSS @media symbol. Pending cluster-H audit.
- **`message`** — Message symbol (proto / DSL). Pending cluster-H audit.
- **`model`** — Model symbol (DSL). Pending cluster-H audit.
- **`module_file`** — Module-as-file symbol. Pending cluster-B audit.
- **`node`** — Node symbol (k8s / DSL). Pending cluster-H audit.
- **`npm_package`** — NPM package symbol. Pending cluster-B audit.
- **`output`** — Output symbol (Terraform / shader). Pending cluster-H audit.
- **`paragraph`** — Paragraph symbol (markdown / docs). Pending cluster-H audit.
- **`partial`** — Partial symbol (template). Pending cluster-H audit.
- **`participant`** — Participant symbol (mermaid). Pending cluster-H audit.
- **`pattern`** — Pattern symbol (DSL / regex). Pending cluster-H audit.
- **`pattern_rule`** — Make pattern-rule target. Pending cluster-G audit.
- **`permission`** — Permission symbol (k8s / Solidity). Pending cluster-H audit.
- **`playbook`** — Ansible playbook symbol. Pending cluster-H audit.
- **`plot`** — Plot symbol (notebook / R). Pending cluster-H audit.
- **`port`** — Port symbol (k8s / VHDL). Pending cluster-H audit.
- **`prefix`** — Prefix symbol (URI / namespace). Pending cluster-H audit.
- **`private`** — WGSL `var<private>` address space. Pending cluster-H audit.
- **`protocol`** — Protocol symbol (Swift / Solidity / DSL). Pending cluster-H audit.
- **`provider`** — Provider symbol (Terraform / DI). Pending cluster-H audit.
- **`python_task`** — BitBake Python task symbol. Pending cluster-G audit.
- **`query`** — Query symbol (GraphQL / SQL operation). Pending cluster-H audit.
- **`recipe`** — Make recipe symbol. Pending cluster-G audit.
- **`requirement`** — Requirement / pip requirement. Pending cluster-G audit.
- **`resource`** — Resource symbol (Terraform / k8s). Pending cluster-H audit.
- **`rule_set`** — CSS / shader rule-set symbol. Pending cluster-H audit.
- **`script`** — Shell-script-shape symbol. Pending cluster-B audit.
- **`section`** — Section symbol (markdown / config). Pending cluster-H audit.
- **`setting`** — Setting / option symbol. Pending cluster-G audit.
- **`signal`** — Signal symbol (VHDL / Verilog / Qt). Pending cluster-H audit.
- **`source`** — Source symbol (data-flow / shell). Pending cluster-H audit.
- **`special_target`** — Make special-target symbol. Pending cluster-G audit.
- **`stage`** — Build / pipeline stage. Pending cluster-G audit.
- **`state`** — State symbol (state-machine DSL). Pending cluster-H audit.
- **`storage`** — WGSL `var<storage>` address space. Pending cluster-H audit.
- **`style_block`** — Style-block symbol (Vue / scoped CSS). Pending cluster-H audit.
- **`subdirectory`** — Subdirectory pseudo-symbol. Pending cluster-H audit.
- **`subscript`** — Subscript symbol (Swift / Python __getitem__). Pending cluster-H audit.
- **`table`** — Table symbol (SQL / TOML / Markdown). Pending cluster-H audit.
- **`table_array`** — TOML table-array symbol. Pending cluster-H audit.
- **`target`** — Build-target symbol. Pending cluster-G audit.
- **`task`** — Generic task symbol. Pending cluster-G audit.
- **`test`** — Test-case symbol. Pending cluster-G audit.
- **`test_case`** — Test-case symbol (alternate label). Pending cluster-G audit.
- **`theorem`** — Theorem symbol (Coq / Lean). Pending cluster-H audit.
- **`trigger`** — Pipeline / DB trigger symbol. Pending cluster-G audit.
- **`tsconfig`** — TypeScript tsconfig symbol. Pending cluster-B audit.
- **`uniform`** — Shader uniform binding (GLSL / WGSL). Pending cluster-H audit.
- **`unresolved`** — Unresolved-symbol pseudo-node. Pending cluster-H audit.
- **`url_requirement`** — URL-requirement install symbol. Pending cluster-G audit.
- **`value`** — Value symbol (key-value DSLs). Pending cluster-H audit.
- **`varying`** — GLSL varying qualifier (legacy interpolation). Pending cluster-H audit.
- **`wasm_import`** — WebAssembly import symbol. Pending cluster-B audit.
- **`wasm_module`** — WebAssembly module symbol. Pending cluster-B audit.
- **`work_item`** — Work-item symbol. Pending cluster-G audit.
- **`workgroup`** — WGSL `var<workgroup>` address space. Pending cluster-H audit.
- **`workspace`** — Cargo `[workspace]` table kind. Pending cluster-G audit.
- **`yield`** — Yield-statement symbol. Pending cluster-H audit.


## `Edge.evidence_type` axes

Per ADR-0028, `Edge.evidence_type` names the inference pathway by which
the analyzer concluded this edge exists. Properties of the dst's
resolvedness move to the new sibling field `Edge.is_resolved`;
framework-specific dispatch conventions and call-construct surface
forms move to `Edge.meta`. The audit at WI-turin-pajuk classified the
~210 distinct `Edge.evidence_type` values currently in production into
four clusters; this section lays out the three axes (Cluster A on
`inference_pathway`; Clusters B / C / D on `endpoint_shape`
deprecation; long-tail or new clusters on `pending_classification`
until per-cluster audit-findings docs ship).

### `inference_pathway` — ADR-0028 compliant

Values that name the inference pathway by which the analyzer concluded this edge exists. Per ADR-0028, this is the only axis a new `Edge.evidence_type` value should occupy. Resolution status moves to the new sibling `Edge.is_resolved`; framework-specific dispatch conventions move to `Edge.meta`.

- **`ast_annotation`** — Edge inferred from a type/decorator annotation in source AST.
- **`ast_attribute`** — Edge inferred from an attribute access in source AST.
- **`ast_call`** — Edge inferred from a generic call expression in source AST.
- **`ast_call_direct`** — Edge inferred from a direct (non-method) call site.
- **`ast_call_extension`** — Edge inferred from an extension-method call (Kotlin / Swift / C#).
- **`ast_call_inherited`** — Edge inferred from a call on an inherited member.
- **`ast_call_inherited_field`** — Edge inferred from access to an inherited field.
- **`ast_call_inherited_method`** — Edge inferred from a call on an inherited method.
- **`ast_call_static`** — Edge inferred from a static (class-level) method call.
- **`ast_call_this`** — Edge inferred from a `this`/`self` receiver call.
- **`ast_call_this_property`** — Edge inferred from a `this.property` / `self.attr` resolved access.
- **`ast_call_type_inferred`** — Edge inferred from a call site where the receiver type was inferred.
- **`ast_cite`** — Edge inferred from a citation/cross-reference link in source.
- **`ast_decorator`** — Edge inferred from a decorator/annotation node in source AST.
- **`ast_extends`** — Edge inferred from an `extends` clause in source AST.
- **`ast_implements`** — Edge inferred from an `implements` clause in source AST.
- **`ast_import`** — Edge inferred from an import statement in source AST.
- **`ast_include`** — Edge inferred from an include directive in source AST (C/C++).
- **`ast_method_inferred`** — Edge inferred from a method call where dispatch was inferred.
- **`ast_method_this`** — Edge inferred from a `this`/`self` method call.
- **`ast_method_this_property`** — Edge inferred from a `this.prop` / `self.attr` reference.
- **`ast_method_type_inferred`** — Edge inferred from a method call with type-inferred receiver.
- **`ast_new`** — Edge inferred from a `new` constructor expression.
- **`ast_package`** — Edge inferred from a package declaration.
- **`ast_perform`** — Edge inferred from a `perform`/effect-handler invocation (OCaml/Eff).
- **`ast_ref`** — Edge inferred from a generic name reference in source AST.
- **`ast_static_call`** — Edge inferred from a static method call (qualifier-resolved).
- **`ast_type_ref`** — Edge inferred from a type reference (annotation, generic, etc.).
- **`async_spawn`** — Edge inferred from an async spawn / task-creation construct.
- **`behaviour`** — Edge inferred from an Erlang `-behaviour(...)` attribute.
- **`behaviour_callback`** — Edge inferred from an Erlang behaviour callback definition.
- **`bridging_header_import`** — Edge inferred from an Objective-C bridging-header import.
- **`build_dependency`** — Edge inferred from a build-system dependency declaration.
- **`build_target_main`** — Edge inferred from a build target's main entry point.
- **`callable_reference`** — Edge inferred from a callable reference (Kotlin `::fn`, etc.).
- **`callback_argument_reference`** — Edge inferred from a callback function passed as an argument.
- **`canonical_name`** — Edge inferred from canonical-name resolution.
- **`cgo_call`** — Edge inferred from a Go cgo C-function call.
- **`closure_wrapper`** — Edge inferred from a closure/lambda wrapper construct.
- **`cmake_target_link`** — Edge inferred from a CMake `target_link_libraries` call.
- **`constructor_reference`** — Edge inferred from a constructor reference (Java `::new`, etc.).
- **`designated_init_fptr`** — Edge inferred from a designated-initializer function pointer (C99).
- **`dispatch_pattern`** — Edge inferred from a generic dispatch-pattern recognition.
- **`dispatch_table_initializer`** — Edge inferred from a dispatch-table initializer entry.
- **`dispatch_table_reference`** — Edge inferred from a reference into a dispatch table.
- **`dockerfile_copy_from`** — Edge inferred from a Dockerfile `COPY --from=...` directive.
- **`dockerfile_from`** — Edge inferred from a Dockerfile `FROM` directive.
- **`enclosing_scope`** — Edge inferred from an enclosing-scope relationship.
- **`eta_expansion`** — Edge inferred from an eta-expansion (point-free → pointed).
- **`extends`** — Edge inferred from a generic extends/inheritance relationship.
- **`function_pointer`** — Edge inferred from a function-pointer assignment or use.
- **`function_pointer_arg`** — Edge inferred from a function pointer passed as an argument.
- **`function_reference`** — Edge inferred from a function reference (not a call).
- **`function_reference_arg`** — Edge inferred from a function reference passed as an argument.
- **`grpc_stub_resolution`** — Edge inferred from a gRPC stub-method resolution lookup. Cluster B canonical for `grpc_unresolved_resolution` (ADR-0028 §Phase 3 Cluster B / WI-nunal).
- **`hash_field_reference`** — Edge inferred from a hash/dict field reference.
- **`hg_annotation`** — Edge inferred from a hypergumbo-emitted analyzer annotation.
- **`import`** — Edge inferred from a generic import construct.
- **`import_declaration`** — Edge inferred from an import declaration node.
- **`import_directive`** — Edge inferred from an import directive (C# `using`, etc.).
- **`import_statement`** — Edge inferred from an import statement node.
- **`import_static`** — Edge inferred from a Java `import static` declaration.
- **`import_to_manifest`** — Edge inferred from a manifest-driven import resolution.
- **`include`** — Edge inferred from a generic include construct.
- **`include_directive`** — Edge inferred from a `#include` directive (C / C++).
- **`instance`** — Edge inferred from a typeclass / trait instance declaration.
- **`interface_dispatch`** — Edge inferred from interface-method dispatch resolution.
- **`jsx_element`** — Edge inferred from a JSX element reference.
- **`link`** — Edge inferred from an OTP link/monitor relationship.
- **`luajit_ffi_lookup`** — Edge inferred from a LuaJIT FFI symbol lookup. Cluster B canonical for `luajit_ffi_unresolved` (ADR-0028 §Phase 3 Cluster B / WI-nunal).
- **`make_prerequisite`** — Edge inferred from a Make/CMake prerequisite declaration.
- **`message_send`** — Edge inferred from a message-send construct (Erlang `!`, Smalltalk).
- **`method_reference`** — Edge inferred from a method reference (Java `::method`, etc.).
- **`module_attribute_reference`** — Edge inferred from a module-level attribute reference.
- **`module_export_heuristic`** — Edge inferred from a module-export heuristic recognition.
- **`module_identifier_reference`** — Edge inferred from a module-qualified identifier reference.
- **`module_source`** — Edge inferred from a module's source-file relationship.
- **`naming_convention`** — Edge inferred from a language-level naming convention.
- **`notify`** — Edge inferred from a notification/signal construct.
- **`object_field_reference`** — Edge inferred from an object-field reference.
- **`open`** — Edge inferred from an `open` directive (OCaml / F#).
- **`open_import`** — Edge inferred from a Go open-import (qualified-but-unbound).
- **`recipe_dependency`** — Edge inferred from a Bazel/Buck recipe-dependency declaration.
- **`reference`** — Edge inferred from a generic name-reference.
- **`require`** — Edge inferred from a `require` construct (Ruby / Node).
- **`require_alias_call`** — Edge inferred from a `require(...)` aliased to a local name.
- **`require_dynamic`** — Edge inferred from a dynamic `require(...)` call.
- **`require_statement`** — Edge inferred from a top-level `require` statement.
- **`require_static`** — Edge inferred from a static `require(...)` invocation.
- **`schema_relation`** — Edge inferred from a schema-declared relation.
- **`scip_occurrence_ref`** — Edge inferred from a SCIP occurrence cross-reference.
- **`scip_relationship`** — Edge inferred from a SCIP-emitted symbol-relationship record.
- **`signal_constraint`** — Edge inferred from an HDL signal-constraint declaration.
- **`source_statement`** — Edge inferred from a generic source-level statement.
- **`span_overlap`** — Edge inferred from text-span overlap between symbols.
- **`sql_foreign_key`** — Edge inferred from a SQL `FOREIGN KEY` constraint.
- **`stack_construction`** — Edge inferred from a stack-frame construction site.
- **`static`** — Edge inferred from a static-linkage declaration.
- **`struct_field_reference`** — Edge inferred from a struct-field reference.
- **`subdir_include`** — Edge inferred from a subdirectory-include in a build file.
- **`trait_impl`** — Edge inferred from a Rust `impl Trait for Type` block.
- **`tree_sitter`** — Edge inferred from a tree-sitter query match.
- **`type_hierarchy`** — Edge inferred from a type-hierarchy traversal.
- **`typeclass_instance`** — Edge inferred from a typeclass-instance declaration (Haskell, Scala).
- **`use`** — Edge inferred from a `use` directive (Rust, PHP).
- **`use-package`** — Edge inferred from a Common Lisp `use-package` form (hyphenated identifier per CL convention).
- **`use_declaration`** — Edge inferred from a `use` declaration node.
- **`use_directive`** — Edge inferred from a `use` directive (qualifier-bound).
- **`using_directive`** — Edge inferred from a `using` directive (C# / C++).
- **`variable_match`** — Edge inferred from a variable-name match across sites.
- **`verilog_instantiation`** — Edge inferred from a Verilog module instantiation.
- **`vhdl_architecture`** — Edge inferred from a VHDL architecture declaration.

### `endpoint_shape` — deprecation candidates per ADR-0028

Values whose meaning is leaked into the evidence label even though it is captured by `Edge.is_resolved` (Cluster B `*_unresolved` resolution-status leakage), `Edge.meta` (Cluster C framework-dispatch conventions; Cluster D call-construct surface forms). Migration plan in ADR-0028 §"Detailed analysis: per-cluster fold targets" folds these back into a canonical inference label plus the appropriate sibling.

- **`abi_name_match`** — Cluster C fold: canonical inference + `meta['detection_pattern']='abi_name_match'`.
- **`activerecord_association`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='activerecord_association'`.
- **`airflow_framework_dispatch`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='airflow'`.
- **`ambiguous_method_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='ambiguous'`.
- **`ast_annotation_unresolved`** — Cluster B fold: `ast_annotation` + `is_resolved=False`.
- **`ast_attribute_unresolved`** — Cluster B fold: `ast_attribute` + `is_resolved=False`.
- **`ast_call_unresolved_import`** — Cluster B fold: `ast_call_direct` (or producer-specific) + `is_resolved=False`.
- **`ast_decorator_unresolved`** — Cluster B fold: `ast_decorator` + `is_resolved=False`.
- **`ast_method_unresolved_global`** — Cluster B fold: `ast_method_inferred` + `is_resolved=False`.
- **`ast_method_unresolved_namespace`** — Cluster B fold: `ast_method_inferred` + `is_resolved=False`.
- **`bare_method_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='bare'`.
- **`call`** — Cluster D fold: `ast_call` (the apex; `call` is the generic peer).
- **`chained_call_unresolved`** — Cluster B fold: `method_call_field_chain` apex + `is_resolved=False`.
- **`chained_return_type_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='chained_return_type'`.
- **`constructor_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='constructor'`.
- **`context_bridge_wrapper`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='electron_context_bridge'`.
- **`controller_routes`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='controller_routes'`.
- **`cross_file_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='cross_file'`.
- **`cross_file_message_send`** — Cluster D fold: `message_send` + `meta['call_construct']='cross_file'`.
- **`crypto_api_pattern`** — Cluster C fold: canonical inference + `meta['detection_pattern']='crypto_api'`.
- **`cuda_kernel_launch`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='cuda_kernel_launch'`.
- **`di_binding`** — Cluster C placeholder for `f"di_binding:{source}"` colon-form emits at di_resolution.py:608. Phase 3 folds to canonical + `meta['framework_dispatch']` per binding source.
- **`django_channels_emit`** — Cluster C dynamic emit (websocket.py:572): `f"{pattern_type}_emit"`. Fold: canonical + `meta['framework_dispatch']='django_channels'`.
- **`django_channels_endpoint`** — Cluster C dynamic emit (websocket.py:613): `f"{pattern_type}_endpoint"`. Fold: canonical + `meta['framework_dispatch']='django_channels'`.
- **`django_orm_dispatch`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='django_orm'`.
- **`django_signal_receiver`** — Cluster C fold: canonical inference + `meta['framework_dispatch']='django_signal'`.
- **`django_signal_receiver_unresolved`** — Cluster B+C fold: canonical inference + `meta['framework_dispatch']='django_signal'` + `is_resolved=False`.
- **`event_name_match`** — Cluster C fold: canonical inference + `meta['detection_pattern']='event_name'`.
- **`external_receiver_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='external'`.
- **`fastapi_emit`** — Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='fastapi'`.
- **`fastapi_endpoint`** — Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='fastapi'`.
- **`function_application`** — Cluster D fold: `ast_call` + `meta['call_construct']='application'`.
- **`function_application_external`** — Cluster D fold: `ast_call` + `meta['call_construct']='application_external'`.
- **`function_call`** — Cluster D fold: `ast_call` apex (the high-frequency emitter).
- **`go_cobra_dispatch`** — Cluster C fold: canonical + `meta['framework_dispatch']='cobra'`.
- **`go_memberlist_delegate`** — Cluster C fold: canonical + `meta['framework_dispatch']='memberlist'`.
- **`graphql_operation_match`** — Cluster C fold: canonical + `meta['framework_dispatch']='graphql_operation'`.
- **`grpc_go_server_method`** — Cluster C fold: canonical + `meta['framework_dispatch']='grpc_go_server'`.
- **`grpc_rpc_definition`** — Cluster C fold: canonical + `meta['framework_dispatch']='grpc_rpc_definition'`.
- **`grpc_server_to_service`** — Cluster C fold: canonical + `meta['framework_dispatch']='grpc_server_to_service'`.
- **`grpc_service_match`** — Cluster C fold: canonical + `meta['framework_dispatch']='grpc_service_match'`.
- **`grpc_unresolved_resolution`** — Cluster B fold: new canonical (e.g. `grpc_stub_resolution`) + `is_resolved=False`.
- **`http_url_match`** — Cluster C fold: canonical + `meta['detection_pattern']='http_url'`.
- **`implicit_convention`** — Cluster C fold: canonical + `meta['detection_pattern']='implicit_convention'`.
- **`jackson_bean_dispatch`** — Cluster C fold: canonical + `meta['framework_dispatch']='jackson_bean'`.
- **`jni_naming_convention`** — Cluster C fold: canonical + `meta['detection_pattern']='jni_naming_convention'`.
- **`job_enqueue`** — Cluster C fold: canonical + `meta['framework_dispatch']='job_enqueue'`.
- **`kafka_streams_dispatch`** — Cluster C fold: canonical + `meta['framework_dispatch']='kafka_streams'`.
- **`local_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='local'`.
- **`luajit_ffi_unresolved`** — Cluster B fold: new canonical (e.g. `luajit_ffi_lookup`) + `is_resolved=False`.
- **`macro_body_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='macro_body'`.
- **`method_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'`.
- **`method_call_field_chain`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='field_chain'`.
- **`method_call_recovery`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='recovery'`.
- **`method_call_type_inferred`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='type_inferred'`.
- **`method_call_typed`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='typed'`.
- **`method_group`** — Cluster D fold: `ast_call` + `meta['call_construct']='method_group'` (C# delegate group).
- **`middleware_chain`** — Cluster C fold: canonical + `meta['framework_dispatch']='middleware_chain'`.
- **`native_emit`** — Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='native_websocket'`.
- **`native_endpoint`** — Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='native_websocket'`.
- **`nestjs_module_registration`** — Cluster C fold: `ast_decorator` + `meta['framework_dispatch']='nestjs_module'`.
- **`npm_package_import`** — Cluster C fold: canonical import inference + `meta['framework_dispatch']='npm_package'`.
- **`object_creation`** — Cluster D fold: `ast_call` + `meta['call_construct']='constructor'` (peer of constructor_call).
- **`openapi_operation_id_match`** — Cluster C fold: canonical + `meta['framework_dispatch']='openapi_operation_id'`.
- **`openapi_path_match`** — Cluster C fold: canonical + `meta['framework_dispatch']='openapi_path'`.
- **`orm_accessor_pattern`** — Cluster C fold: canonical + `meta['framework_dispatch']='orm_accessor'`.
- **`otp_genserver_dispatch`** — Cluster C fold: canonical + `meta['framework_dispatch']='otp_genserver'`.
- **`phoenix_event_match`** — Cluster C fold: `naming_convention` + `meta['detection_pattern']='phoenix_event'`.
- **`pipe_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='pipe'` (Elixir / F# pipe).
- **`pyo3_bridge`** — Cluster C fold: canonical + `meta['framework_dispatch']='pyo3_bridge'`.
- **`rails_block_callback`** — Cluster C fold: canonical + `meta['framework_dispatch']='rails_block_callback'`.
- **`rails_callback`** — Cluster C fold: canonical + `meta['framework_dispatch']='rails_callback'`.
- **`receiver_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='generic'`.
- **`registry_dispatch`** — Cluster C fold: canonical + `meta['framework_dispatch']='registry_dispatch'`.
- **`remote_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='remote'`.
- **`remote_call_external`** — Cluster D fold: `ast_call` + `meta['call_construct']='remote_external'`.
- **`resolver_field_match`** — Cluster C fold: canonical + `meta['framework_dispatch']='graphql_resolver_field'`.
- **`resolver_type_match`** — Cluster C fold: canonical + `meta['framework_dispatch']='graphql_resolver_type'`.
- **`route_mount`** — Cluster C fold: canonical + `meta['framework_dispatch']='route_mount'`.
- **`router_routes`** — Cluster C fold: canonical + `meta['framework_dispatch']='router_routes'`.
- **`ruby_c_extension`** — Cluster C fold: canonical + `meta['framework_dispatch']='ruby_c_extension'`.
- **`ruby_delegate`** — Cluster C fold: canonical + `meta['framework_dispatch']='ruby_delegate'`.
- **`ruby_ffi_attach`** — Cluster C fold: canonical + `meta['framework_dispatch']='ruby_ffi_attach'`.
- **`ruby_ffi_attach_unresolved`** — Cluster B fold: `ruby_ffi_attach` canonical + `is_resolved=False`.
- **`rust_trait_dispatch`** — Cluster C fold: canonical + `meta['framework_dispatch']='rust_trait_dispatch'`.
- **`script_src`** — Cluster C fold: canonical + `meta['framework_dispatch']='html_script_src'`.
- **`socketio_emit`** — Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='socketio'`.
- **`socketio_endpoint`** — Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='socketio'`.
- **`specta_wrapper_import`** — Cluster C fold: canonical + `meta['framework_dispatch']='specta_wrapper'`.
- **`stdlib_method_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='stdlib'`.
- **`subprocess_cli_match`** — Cluster C fold: canonical + `meta['detection_pattern']='subprocess_cli'`.
- **`table_name_match`** — Cluster C fold: canonical + `meta['detection_pattern']='table_name'`.
- **`tauri_emit_listen`** — Cluster C fold: canonical + `meta['framework_dispatch']='tauri_emit_listen'`.
- **`tauri_invoke`** — Cluster C fold: canonical + `meta['framework_dispatch']='tauri_invoke'`.
- **`trait_impl_unresolved`** — Cluster B fold: `trait_impl` canonical + `is_resolved=False`.
- **`typed_field_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['receiver']='typed_field'`.
- **`typed_receiver_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['resolution_quality']='typed_receiver'`.
- **`unexported_method_call`** — Cluster D fold: `ast_call` + `meta['call_construct']='method'` + `meta['visibility']='unexported'` (Go).
- **`unresolved_dotted_submodule_call`** — Cluster B fold: canonical call inference + `is_resolved=False`.
- **`unresolved_external_call`** — Cluster B fold: `ast_call_direct` + `is_resolved=False`.
- **`unresolved_imported_name_call`** — Cluster B fold: `ast_call_direct` + `is_resolved=False`.
- **`unresolved_method_call`** — Cluster B fold: `method_call` (post-collapse `ast_call`) + `is_resolved=False`.
- **`unresolved_module_call`** — Cluster B fold: canonical call inference + `is_resolved=False`.
- **`unresolved_variable_method_call`** — Cluster B fold: `method_call_type_inferred` apex + `is_resolved=False`.
- **`vue_component_import`** — Cluster C fold: canonical + `meta['framework_dispatch']='vue_component'`.
- **`vue_event_handler`** — Cluster C fold: canonical + `meta['framework_dispatch']='vue_event_handler'`.
- **`wasm_bindgen_import`** — Cluster C fold: canonical + `meta['framework_dispatch']='wasm_bindgen_import'`.
- **`wasm_instantiate`** — Cluster C fold: canonical + `meta['framework_dispatch']='wasm_instantiate'`.
- **`ws_emit`** — Cluster C dynamic emit (websocket.py:572). Fold: canonical + `meta['framework_dispatch']='ws'`.
- **`ws_endpoint`** — Cluster C dynamic emit (websocket.py:613). Fold: canonical + `meta['framework_dispatch']='ws'`.
- **`yjs_crdt_pattern`** — Cluster C fold: canonical + `meta['framework_dispatch']='yjs_crdt'`.

### `pending_classification` — per-cluster audit pending per ADR-0028

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`alias_resolution`** — JS module-resolution pathway via path alias (linkers/js_module.py). Pending cluster-A audit (could promote to AXIS_INFERENCE_PATHWAY canonical).
- **`ast_call_method`** — Python AST method-call inference (py.py). At-risk Cluster D peer of `ast_call_direct`: fold candidate to `ast_call_direct` + `meta['call_construct']='method'`. Pending cluster-D audit.
- **`cffi_call`** — Python cffi FFI call (linkers/pyffi.py). At-risk Cluster C: fold candidate to canonical inference + `meta['ffi_mechanism']='cffi'`. Pending cluster-C audit.
- **`ctypes_call`** — Python ctypes FFI call (linkers/pyffi.py). At-risk Cluster C: fold candidate to canonical inference + `meta['ffi_mechanism']='ctypes'`. Pending cluster-C audit.
- **`import_resolution`** — JS module-resolution pathway via direct import (linkers/js_module.py). Pending cluster-A audit (could promote to AXIS_INFERENCE_PATHWAY canonical).
