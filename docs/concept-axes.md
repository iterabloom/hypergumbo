<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- AUTO-GENERATED — do not edit manually.
     Regenerate with: ./scripts/generate-concept-axes
     Sources of truth:
       packages/hypergumbo-core/src/hypergumbo_core/edge_types.py
       packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py -->

# Concept Axes

Typing axes maintained in hypergumbo's behavior map. Each axis names a
dimension along which a multi-value field's values must be classified.
New axes are introduced via ADR following ADR-0024's four-part
declaration template (axis name, axiom, consumer pattern, enforcement);
ADR-0023 (Edge.type) is the worked example, and ADR-0027 (Symbol.kind)
is the second instantiation.

The current axes apply to `Edge.type` and `Symbol.kind`. Other
multi-value fields (`supply_chain.tier`, `Edge.evidence_type` per
ADR-0028 draft) will be added here as their axes are formally declared.

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
- **`class`** — Class declaration.
- **`component`** — Component declaration (Vue / Svelte / Astro / React).
- **`const`** — Const declaration (C / C++ / Rust / JS const).
- **`constant`** — Constant / final / let-immutable binding.
- **`constructor`** — Constructor / __init__ / init method.
- **`declaration`** — Generic declaration (catch-all for non-categorized syntactic forms).
- **`defined_type`** — Defined / nominal type declaration (Puppet / Coq).
- **`directive`** — Directive declaration (Vue / Angular / GraphQL).
- **`enum`** — Enum declaration.
- **`export`** — Export declaration (JS / TS / TOML / Rust).
- **`extends`** — Extends clause as a syntactic form (Java, Solidity).
- **`field`** — Field declaration on a struct / class / record.
- **`fn`** — Function declaration (Rust fn, Elixir def).
- **`function`** — Top-level function definition.
- **`getter`** — Property getter accessor.
- **`import`** — Import declaration as a syntactic-form symbol.
- **`include`** — Include declaration (Ruby include, C #include, Make include).
- **`inherit`** — Inherit clause as a syntactic form (BitBake, OOP DSLs).
- **`instance`** — Typeclass / interface instance declaration.
- **`interface`** — Interface declaration.
- **`keyword`** — Keyword-shaped construct (configuration languages).
- **`macro`** — Macro definition (Rust / C / Scheme).
- **`method`** — Method on a class / struct / interface.
- **`mixin`** — Mixin declaration (Ruby / Sass).
- **`module`** — Module declaration (the source-level construct).
- **`namespace`** — Namespace declaration (C++ / TypeScript / C#).
- **`object`** — Object / singleton declaration (Scala / Kotlin).
- **`proc`** — Procedure / proc declaration (Tcl / Nim / Ruby Proc).
- **`procedure`** — Procedure declaration (Pascal / Ada / SQL).
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
- **`var`** — Var declaration (Go / JS var).
- **`variable`** — Variable / let / mutable binding.
- **`view`** — View declaration (MVC / template languages).

### `endpoint_shape` — deprecation candidates per ADR-0027

Values whose meaning is leaked into the kind label even though it is captured by `Symbol.meta` (framework participation), `Edge` relationships (edge labels masquerading as kinds), or `dst.kind` queries (component refs). Migration plan in ADR-0027 §"Detailed analysis: per-cluster fold targets" folds these back into the canonical Cluster-A construct + `meta["framework_role"]` or drops them entirely as edge-only.

- **`abi_call`** — Solidity ABI call site. Fold to function/method + meta['framework_role']='abi_call'.
- **`call`** — Call site. Fold to call_site (new canonical) or drop in favor of edge.
- **`component_ref`** — Inline component reference. Fold to reference + dst.kind == 'component'.
- **`crypto_consumer`** — Crypto-flow consumer. Fold to function/method + meta['framework_role']='crypto_consumer'.
- **`crypto_producer`** — Crypto-flow producer. Fold to function/method + meta['framework_role']='crypto_producer'.
- **`db_query`** — DB query site. Fold to call_site + meta['framework_role']='db_query' or drop.
- **`dispatcher`** — Generic dispatcher symbol. Fold to function/method + meta['framework_role']='dispatcher'.
- **`event_publisher`** — Symbol that publishes events. Fold to function/method + meta['framework_role']='event_publisher'.
- **`event_subscriber`** — Symbol that subscribes to events. Fold to function/method + meta['framework_role']='event_subscriber'.
- **`function_call`** — Function-call site. Fold to call_site or drop.
- **`graphql_client`** — GraphQL client call site. Fold to function/method + meta['framework_role']='graphql_client'.
- **`graphql_resolver`** — GraphQL resolver. Fold to function/method + meta['framework_role']='graphql_resolver'.
- **`grpc_server`** — gRPC server method. Fold to function/method + meta['framework_role']='grpc_server'.
- **`grpc_stub`** — gRPC stub method. Fold to function/method + meta['framework_role']='grpc_stub'.
- **`http_client`** — HTTP client call site. Fold to function/method + meta['framework_role']='http_client'.
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
- **`read`** — Read access (relationship); drop — already on Edge.
- **`reference`** — Generic reference; drop or rename to reference_site.
- **`route`** — Route declaration. Fold to function/method + meta['framework_role']='route'.
- **`route_include`** — Route include declaration. Fold to function/method + meta['framework_role']='route_include'.
- **`route_mount`** — Route mount declaration. Fold to function/method + meta['framework_role']='route_mount'.
- **`rpc`** — RPC method declaration. Fold to function/method + meta['framework_role']='rpc'.
- **`selector_ref`** — ObjC selector reference. Fold to reference + meta['framework_role']='selector_ref'.
- **`service`** — Service declaration (gRPC service, k8s service). Fold to interface/class + meta['framework_role']='service'.
- **`subprocess_call`** — Subprocess-call site. Fold to call_site or drop.
- **`websocket_emitter`** — WebSocket emitter. Fold to function/method + meta['framework_role']='websocket_emitter'.
- **`websocket_endpoint`** — WebSocket endpoint. Fold to function/method + meta['framework_role']='websocket_endpoint'.
- **`websocket_listener`** — WebSocket listener. Fold to function/method + meta['framework_role']='websocket_listener'.
- **`write`** — Write access (relationship); drop — already on Edge.

### `pending_classification` — per-cluster audit pending per ADR-0027 §"Migration"

Values deferred to per-cluster audit-findings docs at `docs/audits/<NN>-<topic>.md`. Includes the file-shape entities (Cluster B), apex/peer overloads (Cluster C), build/config-shape entities (Cluster G), and the long-tail domain vocabulary (Cluster H). Each cluster's audit decides between fold-to-Cluster-A vs separate-axis declaration vs producer-side drop.

- **`addtask`** — BitBake addtask symbol. Pending cluster-G audit.
- **`architecture`** — Architecture symbol (VHDL). Pending cluster-H audit.
- **`base`** — Base symbol (XML / OWL). Pending cluster-H audit.
- **`bin`** — Binary executable symbol. Pending cluster-B audit.
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
- **`executable`** — Executable-shape symbol. Pending cluster-B audit.
- **`export_entry`** — Generic export entry. Pending cluster-B audit.
- **`exposed_port`** — Container exposed-port symbol. Pending cluster-G audit.
- **`external_symbol`** — External-symbol pseudo-node. Pending cluster-H audit.
- **`file`** — File-shape symbol. Pending cluster-B audit.
- **`font_face`** — CSS @font-face symbol. Pending cluster-H audit.
- **`for_loop`** — For-loop symbol (control-flow). Pending cluster-H audit.
- **`fragment`** — Fragment symbol (GraphQL / template). Pending cluster-H audit.
- **`heading`** — Heading symbol (markdown / docs). Pending cluster-H audit.
- **`id`** — Id symbol (k8s / DSL). Pending cluster-H audit.
- **`id_selector`** — CSS id selector symbol. Pending cluster-H audit.
- **`index`** — Index symbol (SQL / DSL). Pending cluster-H audit.
- **`inductive`** — Inductive type (Coq / Lean). Pending cluster-H audit.
- **`input`** — Input symbol (Terraform / shader). Pending cluster-H audit.
- **`keyframes`** — CSS @keyframes symbol. Pending cluster-H audit.
- **`label`** — Label symbol (assembly / k8s). Pending cluster-H audit.
- **`library`** — Library-shape symbol. Pending cluster-B audit.
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
- **`package`** — Package-shape symbol. Pending cluster-B audit.
- **`paragraph`** — Paragraph symbol (markdown / docs). Pending cluster-H audit.
- **`partial`** — Partial symbol (template). Pending cluster-H audit.
- **`participant`** — Participant symbol (mermaid). Pending cluster-H audit.
- **`pattern`** — Pattern symbol (DSL / regex). Pending cluster-H audit.
- **`permission`** — Permission symbol (k8s / Solidity). Pending cluster-H audit.
- **`playbook`** — Ansible playbook symbol. Pending cluster-H audit.
- **`plot`** — Plot symbol (notebook / R). Pending cluster-H audit.
- **`port`** — Port symbol (k8s / VHDL). Pending cluster-H audit.
- **`prefix`** — Prefix symbol (URI / namespace). Pending cluster-H audit.
- **`program`** — Program-shape symbol. Pending cluster-B audit.
- **`project`** — Project-shape symbol. Pending cluster-B audit.
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
- **`structure`** — Apex/peer of struct emitted by some analyzers. Cluster C fold target: collapse to 'struct'. Pending cluster-C audit.
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
- **`unresolved`** — Unresolved-symbol pseudo-node. Pending cluster-H audit.
- **`url_requirement`** — URL-requirement install symbol. Pending cluster-G audit.
- **`value`** — Value symbol (key-value DSLs). Pending cluster-H audit.
- **`wasm_import`** — WebAssembly import symbol. Pending cluster-B audit.
- **`wasm_module`** — WebAssembly module symbol. Pending cluster-B audit.
- **`work_item`** — Work-item symbol. Pending cluster-G audit.
- **`yield`** — Yield-statement symbol. Pending cluster-H audit.
