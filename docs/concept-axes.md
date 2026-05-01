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

## `Edge.type` axes

### `relationship` — ADR-0023 compliant

Values that name the relationship the edge expresses between src and dst. Per ADR-0023, this is the only axis a new `edge_type` value should occupy.

- **`calls`** — Caller invokes callee.
- **`contains`** — Container symbol holds member symbol.
- **`depends_on`** — Generic dependency relationship.
- **`depends_on_manifest`** — Dependency declared in a package or build manifest.
- **`dispatches_to`** — Caller dispatches to callee via runtime indirection (virtual method, function pointer, DI resolution, etc.).
- **`event_publishes`** — Producer publishes an event/message that the consumer receives via an async channel (event bus, queue, CRDT, etc.).
- **`extends`** — Class extends a superclass.
- **`implements`** — Class implements an interface.
- **`imports`** — Module imports another module or symbol.
- **`instantiates`** — Constructor or factory creates an instance.
- **`links`** — Generic linkage relationship.
- **`module_attr_ref`** — Reads an attribute on an imported module (e.g., os.environ).
- **`references`** — Symbol references another by name without invocation.
- **`sources`** — Sources another file (e.g., shell ``source``).
- **`subprocess_calls`** — Symbol invokes another symbol via a subprocess.
- **`uses`** — Generic symbol-usage relationship.
- **`wraps`** — Decorator or middleware wraps the target symbol.

### `endpoint_shape` — deprecation candidates per ADR-0023 §6

Values whose meaning is leaked into the type label even though it is captured by `src.kind` / `dst.kind` / language metadata. Migration plan in ADR-0023 §6 folds these back into relationship-shaped names with kind/language metadata on the endpoint nodes.

- **`annotated_dispatches`** — Annotation-driven dispatch; per ADR-0025 fold to 'dispatches_to' + meta['mechanism']='annotation'.
- **`annotated_publishes`** — Annotation-driven publish; per ADR-0025 fold to 'event_publishes' + meta['mechanism']='annotation'.
- **`base_image`** — Dockerfile ``FROM`` base image reference.
- **`bridge_invokes`** — Generic bridge-mediated invocation (use 'calls' + bridge meta).
- **`cgo_bridge`** — Go cgo FFI bridge (use 'calls' + bridge meta).
- **`crdt_publishes`** — CRDT-backed event log publish; per ADR-0025 fold to 'event_publishes' + meta['channel_kind']='crdt'.
- **`delegates_to`** — Class-level method delegation declaration (e.g., Ruby delegate); per ADR-0025 fold to 'references' + meta['mechanism']='delegate' (declaration-time, not dispatch).
- **`di_registers`** — DI container registration declaration; per ADR-0025 fold to 'references' + meta['mechanism']='di_registration' (declaration-time, not runtime dispatch).
- **`di_resolves`** — DI container runtime resolution; per ADR-0025 fold to 'dispatches_to' + meta['mechanism']='di'.
- **`emits`** — Function references an event symbol it emits; per ADR-0025 fold to 'references' + meta['construct']='event_emit' (emit shape is function→event_symbol, not pub→sub).
- **`enqueues`** — Producer pushes a job to a queue (e.g., Ruby ActiveJob perform_later); per ADR-0025 fold to 'event_publishes' + meta['channel_kind']='queue'.
- **`event_subscribes`** — DEPRECATE-NO-FOLD per ADR-0025: production emit shape is subscriber→enclosing-function (structural containment) while the name suggests pub-sub. Phase 3 producer rewrite decides the canonical replacement (likely 'references' or 'contains' with reversed direction).
- **`ffi_bridge`** — Generic FFI bridge (use 'calls' + bridge meta).
- **`graphql_calls`** — GraphQL call (use 'calls' + protocol meta).
- **`grpc_calls`** — gRPC call (use 'calls' + protocol meta).
- **`http_calls`** — HTTP call (use 'calls' + protocol meta).
- **`imports_component`** — Imports targeting a UI component (Vue/Svelte/React); per ADR-0023 §6, fold into 'imports' + dst.kind == 'component'.
- **`imports_module`** — Imports targeting a module/file specifically (use 'imports').
- **`ipc_calls`** — Inter-process call (use 'calls' + protocol meta).
- **`ipc_event`** — Inter-process event dispatch.
- **`kernel_launch`** — GPU kernel invocation.
- **`message_dispatch`** — Misnamed: emit shape is publisher→subscriber, not dispatcher→target. Per ADR-0025 fold to 'event_publishes' + meta['channel_kind']='message_bus' (cross-family fold).
- **`message_queue`** — Message queue endpoint reference.
- **`message_receive`** — Message consumed from a queue/topic.
- **`message_send`** — Message produced to a queue/topic.
- **`model_reference`** — ORM reference to a model class; per ADR-0023 §6, fold into 'references' + dst.kind == 'model'.
- **`napi_bridge`** — Node-API native bridge (use 'calls' + bridge meta).
- **`native_bridge`** — JNI/FFI bridge to native code (use 'calls' + bridge meta).
- **`query_references`** — Query reference to a database object (table, column, view); per ADR-0023 §6, fold into 'references' + dst.kind == 'query'.
- **`registers_routes`** — Router declares a route; per ADR-0025 fold to 'references' + meta['mechanism']='route_registration' (parallel to di_registers).
- **`renders_component`** — JSX/template render of a UI component; per ADR-0023 §6 review, likely 'references' with meta['construct'] == 'jsx'.
- **`routes_to`** — HTTP/router route to handler; per ADR-0025 fold to 'dispatches_to' + meta['dispatch_kind']='route'.
- **`script_src`** — HTML ``<script src=...>`` reference.
- **`type_ref`** — TypeScript reference to a type symbol; per ADR-0023 §6, fold into 'references' + dst.kind == 'type'.
- **`uses_dispatch_table`** — Function references a dispatch-table data symbol; per ADR-0025 fold to 'references' + meta['construct']='dispatch_table'.
- **`wasm_bridge`** — WebAssembly bridge invocation (use 'calls' + bridge meta).
- **`wasm_load`** — WebAssembly module load.
- **`websocket_connection`** — WebSocket connection establishment.
- **`websocket_message`** — WebSocket message exchange.

### `pending_classification` — per-family audit pending per ADR-0023 §5

Values deferred to per-family audit. Some may be genuinely distinct relationships; others are protocol-conditional duplicates of a more general relationship. Verdicts arrive with each family's audit.

- **`implements_rpc`** — RPC implementation binding — pending per-family audit.
- **`openapi_implements`** — OpenAPI handler pattern — pending per-family audit.
- **`resolver_for_type`** — GraphQL resolver-type binding — pending per-family audit.
- **`resolver_implements`** — GraphQL resolver pattern — pending per-family audit.
