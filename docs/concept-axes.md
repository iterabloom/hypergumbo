<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- AUTO-GENERATED — do not edit manually.
     Regenerate with: ./scripts/generate-concept-axes
     Source of truth: packages/hypergumbo-core/src/hypergumbo_core/edge_types.py -->

# Concept Axes

Typing axes maintained in hypergumbo's behavior map. Each axis names a
dimension along which a multi-value field's values must be classified.
New axes are introduced via ADR following ADR-0024's four-part
declaration template (axis name, axiom, consumer pattern, enforcement);
ADR-0023 (Edge.type) is the worked example.

The current axes apply to `Edge.type`. Other multi-value fields
(`Symbol.kind`, `supply_chain.tier`, etc.) will be added here as their
axes are formally declared.

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
Producers no longer emit any of the values currently in the
`endpoint_shape` section below; those values stay in the schema's
enum during the dual-validity window so existing consumers don't
break, but they are scheduled for removal.

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
- **`annotated_dispatches`** — Annotation-driven dispatch; per ADR-0025 fold to 'dispatches_to' + meta['mechanism']='annotation'.
- **`annotated_publishes`** — Annotation-driven publish; per ADR-0025 fold to 'event_publishes' + meta['mechanism']='annotation'.
- **`association`** — Ruby ActiveRecord association declaration (has_many, belongs_to, etc.); likely fold to 'references' + meta['construct']='association'.
- **`base_image`** — Dockerfile ``FROM`` base image reference.
- **`bridge_invokes`** — Generic bridge-mediated invocation (use 'calls' + bridge meta).
- **`build_tag_alternative_of`** — Go build-tag-conditional alternative implementation of a symbol; likely fold to 'references' + meta['construct']='build_tag_alternative'.
- **`caller_invokes`** — Tauri-style cross-language invoke (caller → bound command); likely fold to 'calls' + meta['protocol']='ipc' (parallel to ipc_calls per ADR-0026).
- **`cgo_bridge`** — Go cgo FFI bridge (use 'calls' + bridge meta).
- **`contains_routes`** — Controller / module containing route handlers; likely fold to 'contains' (already canonical) — pure dst-kind leakage.
- **`crdt_publishes`** — CRDT-backed event log publish; per ADR-0025 fold to 'event_publishes' + meta['channel_kind']='crdt'.
- **`crypto_flow`** — Crypto-related dataflow (key/secret reaches sink); likely fold to 'data_flows_to' + meta['construct']='crypto'.
- **`delegates_to`** — Class-level method delegation declaration (e.g., Ruby delegate); per ADR-0025 fold to 'references' + meta['mechanism']='delegate' (declaration-time, not dispatch).
- **`depends`** — Package depends on another (Bitbake, requirements.txt); likely fold to 'depends_on' (already canonical) or 'depends_on_manifest' depending on declaration site.
- **`di_registers`** — DI container registration declaration; per ADR-0025 fold to 'references' + meta['mechanism']='di_registration' (declaration-time, not runtime dispatch).
- **`di_resolves`** — DI container runtime resolution; per ADR-0025 fold to 'dispatches_to' + meta['mechanism']='di'.
- **`emits`** — Function references an event symbol it emits; per ADR-0025 fold to 'references' + meta['construct']='event_emit' (emit shape is function→event_symbol, not pub→sub).
- **`enqueues`** — Producer pushes a job to a queue (e.g., Ruby ActiveJob perform_later); per ADR-0025 fold to 'event_publishes' + meta['channel_kind']='queue'.
- **`event_subscribes`** — DEPRECATE-NO-FOLD per ADR-0025: production emit shape is subscriber→enclosing-function (structural containment) while the name suggests pub-sub. Phase 3 producer rewrite decides the canonical replacement (likely 'references' or 'contains' with reversed direction).
- **`extends_template`** — Twig/Jinja template extends a parent template; likely fold to 'extends' + meta['construct']='template' or stay as canonical if templates' extension semantics differ enough.
- **`ffi_bridge`** — Generic FFI bridge (use 'calls' + bridge meta).
- **`graphql_calls`** — GraphQL call (use 'calls' + protocol meta).
- **`grpc_calls`** — gRPC call (use 'calls' + protocol meta).
- **`http_calls`** — HTTP call (use 'calls' + protocol meta).
- **`imports_component`** — Imports targeting a UI component (Vue/Svelte/React); per ADR-0023 §6, fold into 'imports' + dst.kind == 'component'.
- **`imports_module`** — Imports targeting a module/file specifically (use 'imports').
- **`includes_class`** — Puppet manifest includes a class declaration; likely fold to 'includes' (now canonical) + meta['construct']='puppet_class'.
- **`includes_template`** — Twig/Jinja template includes a partial; likely fold to 'includes' (now canonical) + meta['construct']='template'.
- **`invokes_callback`** — Erlang/Elixir/Ruby callback invocation (gen_server callback, framework lifecycle hook); likely fold to 'dispatches_to' or 'calls' + meta['mechanism']='callback'.
- **`ipc_calls`** — Tauri-style IPC call (Rust↔JS via invoke); per ADR-0026 fold to 'calls' + meta['protocol']='ipc'.
- **`ipc_event`** — Tauri-style IPC emit/listen; per ADR-0026 fold to 'event_publishes' + meta['channel_kind']='ipc'.
- **`kernel_launch`** — GPU kernel invocation.
- **`links_to`** — Markdown link from one document to another; likely fold to 'references' + meta['construct']='markdown_link'.
- **`message_dispatch`** — Misnamed: emit shape is publisher→subscriber, not dispatcher→target. Per ADR-0025 fold to 'event_publishes' + meta['channel_kind']='message_bus' (cross-family fold).
- **`message_queue`** — RabbitMQ/Kafka publisher→subscriber via topic; per ADR-0026 fold to 'event_publishes' + meta['channel_kind']='queue' (same fold target as 'enqueues' from ADR-0025).
- **`message_receive`** — Converse-direction edge of message_send; per ADR-0026 DEPRECATE-NO-FOLD (Phase 3 producer rewrite picks the canonical replacement — likely drop, since slice can compute reverse paths from the forward event_publishes edge).
- **`message_send`** — Electron / Phoenix sender→receiver via named channel; per ADR-0026 fold to 'event_publishes' + meta['channel_kind']='ipc'.
- **`model_reference`** — ORM reference to a model class; per ADR-0023 §6, fold into 'references' + dst.kind == 'model'.
- **`napi_bridge`** — Node-API native bridge (use 'calls' + bridge meta).
- **`native_bridge`** — JNI/FFI bridge to native code (use 'calls' + bridge meta).
- **`notifies_resource`** — Puppet/Chef resource notify directive (trigger another resource on change); likely fold to 'event_publishes' + meta['channel_kind']='puppet_notify' (configuration-management pub-sub shape).
- **`query_references`** — Query reference to a database object (table, column, view); per ADR-0023 §6, fold into 'references' + dst.kind == 'query'.
- **`registers_routes`** — Router declares a route; per ADR-0025 fold to 'references' + meta['mechanism']='route_registration' (parallel to di_registers).
- **`renders`** — Controller renders a view template; likely fold to 'references' + meta['construct']='view_render' (parallel to renders_component for JSX).
- **`renders_component`** — JSX/template render of a UI component; per ADR-0023 §6 review, likely 'references' with meta['construct'] == 'jsx'.
- **`requires_resource`** — Puppet/Chef resource require directive (this resource depends on another); likely fold to 'depends_on' + meta['construct']='puppet_require'.
- **`routes_to`** — HTTP/router route to handler; per ADR-0025 fold to 'dispatches_to' + meta['dispatch_kind']='route'.
- **`script_src`** — HTML ``<script src=...>`` reference.
- **`signal_receiver`** — Django signal receiver registration; likely fold to 'event_publishes' + meta['channel_kind']='django_signal' (signals are pub-sub via Django's dispatch module).
- **`template_calls`** — Vue / template-engine method call from template into component logic; likely fold to 'calls' + meta['mechanism']='template'.
- **`type_ref`** — TypeScript reference to a type symbol; per ADR-0023 §6, fold into 'references' + dst.kind == 'type'.
- **`uses_dispatch_table`** — Function references a dispatch-table data symbol; per ADR-0025 fold to 'references' + meta['construct']='dispatch_table'.
- **`uses_mixin`** — Sass/SCSS @include of a mixin; likely fold to 'references' + meta['construct']='sass_mixin'.
- **`uses_vocabulary`** — SPARQL/RDF query references a vocabulary/ontology; likely fold to 'references' + meta['construct']='rdf_vocabulary'.
- **`wasm_bridge`** — WebAssembly bridge invocation (use 'calls' + bridge meta).
- **`wasm_load`** — WebAssembly module load.
- **`websocket_connection`** — WebSocket file→endpoint declarative connectivity reference; per ADR-0026 fold to 'references' + meta['construct']='websocket_endpoint'.
- **`websocket_message`** — WebSocket sender_file→recv_file via channel; per ADR-0026 fold to 'event_publishes' + meta['channel_kind']='websocket'.

### `pending_classification` — per-family audit pending per ADR-0023 §5

Values deferred to per-family audit. Some may be genuinely distinct relationships; others are protocol-conditional duplicates of a more general relationship. Verdicts arrive with each family's audit.

- **`implements_rpc`** — RPC implementation binding — pending per-family audit.
- **`openapi_implements`** — OpenAPI handler pattern — pending per-family audit.
- **`resolver_for_type`** — GraphQL resolver-type binding — pending per-family audit.
- **`resolver_implements`** — GraphQL resolver pattern — pending per-family audit.
