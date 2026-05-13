<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Migrating downstream consumers off deprecated `edge_type` values

This guide is for code that consumes hypergumbo's behavior-map JSON
output and filters or weights edges by their `edge_type`. If your
code does not look at edges, or only looks at the small set of
relationship-shape names (`calls`, `imports`, `references`,
`contains`, `instantiates`, `inherits`, `implements`, `extends`,
`decorated_by`, ...), you can stop reading; nothing you depend on
has been deprecated.

If your code references any of the names in the rename tables
below, this guide tells you what to switch to.

## What changed, in one paragraph

Hypergumbo previously used `edge_type` to encode three different
things at once: the *relationship* the edge expresses, the *kind* of
endpoint involved, and the *protocol/mechanism/framework* mediating
it. ADR-0023 settled the principle that `edge_type` names only the
relationship; endpoint kind lives on `src.kind` / `dst.kind` /
`src.language` / `dst.language`, and protocol / mechanism /
framework live in `edge.meta`. Producers were rewritten to emit the
canonical relationship name plus a meta key carrying the
differentiating fact. The deprecated names remain valid in the
schema enum for one minor version (the dual-validity window) so
external consumers can adapt; they hard-fail in the version after.

## Deprecation timeline

| Schema version | What ships                                            |
|----------------|-------------------------------------------------------|
| 0.3.0          | Deprecation announcement: `x-deprecated` annotation lists every deprecation candidate; values still valid in the enum. Production analyzers no longer emit any of them. |
| 0.3.1          | Registry-completeness sweep: 25 previously-emitted-but-unregistered values added to the schema enum (7 canonical, 18 endpoint_shape candidates). Same dual-validity rules apply. |
| **0.4.0**      | **Phase 4b shipped — removal**: 33 deprecation candidates removed from the schema enum (the rename-table values listed below). Behavior maps emitted at 0.4.0+ never contain these; behavior maps containing them stop validating against the 0.4.0 schema. The 25 endpoint_shape values added in 0.3.1 all stay in the schema (under `x-deprecated`); 3 of them (the protocol-call family) had their producer migration land post-0.4.0 in a follow-on release but await a sibling Phase 4b' ship for schema removal, and the remaining 22 still emit pending per-family Phase 3. |

If you read your behavior maps with a permissive parser (one that
ignores the schema), nothing breaks at 0.4.0 — but your filters
will stop matching edges that no longer exist by those names. The
rename table is what you need to update.

## The rename table

The deprecated value column lists everything you may need to
migrate off. The "new query" column shows the canonical
`(edge_type, meta-key)` shape producers now emit.

### Endpoint-kind leakage (per ADR-0023 §6)

| Old `edge_type`      | New query                                                          |
|----------------------|--------------------------------------------------------------------|
| `imports_module`     | `edge_type == "imports"` and `dst.kind in {"module_file", "file", "npm_package"}` |
| `imports_component`  | `edge_type == "imports"` and `dst.kind == "component"`             |
| `model_reference`    | `edge_type == "references"` and `dst.kind == "model"`              |
| `type_ref`           | `edge_type == "references"` and `dst.kind == "type"`               |
| `query_references`   | `edge_type == "references"` and `dst.kind == "query"`              |
| `renders_component`  | `edge_type == "references"` and `meta["construct"] == "jsx"`       |

### Bridge / FFI family (per ADR-0023 §6)

The bridge family folds to `calls` because every bridge is, at
runtime, a function invocation across a language boundary. The
`bridge_kind` meta key carries the mechanism. `wasm_load` is the
exception: a file→module load is import-shaped, not call-shaped.

| Old `edge_type`     | New query                                                         |
|---------------------|-------------------------------------------------------------------|
| `cgo_bridge`        | `edge_type == "calls"` and `meta["bridge_kind"] == "cgo"`         |
| `ffi_bridge`        | `edge_type == "calls"` and `meta["bridge_kind"] == "ffi"`         |
| `napi_bridge`       | `edge_type == "calls"` and `meta["bridge_kind"] == "napi"`        |
| `wasm_bridge`       | `edge_type == "calls"` and `meta["bridge_kind"] == "wasm"`        |
| `native_bridge`     | `edge_type == "calls"` and `meta["bridge_kind"] == "native"`      |
| `bridge_invokes`    | `edge_type == "calls"` and `meta["bridge_kind"] == "context_bridge"` |
| `wasm_load`         | `edge_type == "imports"` and `dst.kind == "wasm_module"`          |

### IPC family

Inter-process communication (Tauri, Electron, Phoenix Channels,
WebSocket, message queues) folds to `calls` for invoke-shaped
exchanges and to `event_publishes` for publish-shaped ones. The
`channel_kind` meta key carries the protocol.

| Old `edge_type`        | New query                                                                |
|------------------------|--------------------------------------------------------------------------|
| `ipc_calls`            | `edge_type == "calls"` and `meta["protocol"] == "ipc"`                   |
| `ipc_event`            | `edge_type == "event_publishes"` and `meta["channel_kind"] == "ipc"`     |
| `message_send`         | `edge_type == "event_publishes"` and `meta["channel_kind"] == "ipc"`     |
| `websocket_message`    | `edge_type == "event_publishes"` and `meta["channel_kind"] == "websocket"` |
| `websocket_connection` | `edge_type == "references"` and `meta["construct"] == "websocket_endpoint"` |
| `message_queue`        | `edge_type == "event_publishes"` and `meta["channel_kind"] == "queue"`   |
| `message_receive`      | **Dropped.** No replacement edge — see "Dropped edges" below.            |

### Protocol-call family (per ADR-0023 §6)

Protocol-call edges fold to `calls` because every protocol invocation is,
at runtime, a function call across a network or process boundary. The
`protocol` meta key carries the wire protocol. Producer migration shipped
in a follow-on release; the registry entries remain `x-deprecated` until a
sibling Phase 4b' ship prunes them.

| Old `edge_type` | New query                                                   |
|-----------------|-------------------------------------------------------------|
| `http_calls`    | `edge_type == "calls"` and `meta["protocol"] == "http"`     |
| `grpc_calls`    | `edge_type == "calls"` and `meta["protocol"] == "grpc"`     |
| `graphql_calls` | `edge_type == "calls"` and `meta["protocol"] == "graphql"`  |

### Dispatch / publish family

| Old `edge_type`        | New query                                                                  |
|------------------------|----------------------------------------------------------------------------|
| `routes_to`            | `edge_type == "dispatches_to"` and `meta["dispatch_kind"] == "route"`      |
| `delegates_to`         | `edge_type == "references"` and `meta["mechanism"] == "delegate"`          |
| `annotated_dispatches` | `edge_type == "dispatches_to"` and `meta["mechanism"] == "annotation"`     |
| `uses_dispatch_table`  | `edge_type == "references"` and `meta["construct"] == "dispatch_table"`    |
| `di_registers`         | `edge_type == "references"` and `meta["mechanism"] == "di_registration"`   |
| `di_resolves`          | `edge_type == "dispatches_to"` and `meta["mechanism"] == "di"`             |
| `registers_routes`     | `edge_type == "references"` and `meta["mechanism"] == "route_registration"` |
| `message_dispatch`     | `edge_type == "event_publishes"` and `meta["channel_kind"] == "message_bus"` |
| `crdt_publishes`       | `edge_type == "event_publishes"` and `meta["channel_kind"] == "crdt"`      |
| `annotated_publishes`  | `edge_type == "event_publishes"` and `meta["mechanism"] == "annotation"`   |
| `emits`                | `edge_type == "references"` and `meta["construct"] == "event_emit"`        |
| `enqueues`             | `edge_type == "event_publishes"` and `meta["channel_kind"] == "queue"`     |
| `event_subscribes`     | **Dropped.** No replacement edge — see "Dropped edges" below.              |

### Dropped edges

Two edges no longer get emitted at all:

- **`message_receive`** (Electron / Phoenix Channels) — used to be
  emitted as a converse-direction edge from `receiver` → `sender`.
  Its forward counterpart is `event_publishes` (`sender` →
  `receiver`); the reverse direction is recoverable from any edge
  by inverting `src` / `dst`. If your consumer needs "who sent to
  me," walk `event_publishes` edges with the receiver as `dst`.

- **`event_subscribes`** (event-sourcing linker) — used to be
  emitted as `subscriber_symbol` → `enclosing_method`, expressing
  the structural fact "this subscriber is registered inside this
  method." That information is recoverable from `Symbol.span`: a
  subscriber's span fits inside its enclosing method's span. If
  your consumer needs the enclosing method, look up the
  subscriber's `path` and find the symbol whose span contains it.

## The meta-key vocabulary

After the migration, six meta keys carry the differentiating facts
the deprecated names used to encode. Each is a closed enumeration:

| Meta key        | Allowed values                                                              | What it answers                              |
|-----------------|-----------------------------------------------------------------------------|---------------------------------------------|
| `bridge_kind`   | `cgo`, `ffi`, `napi`, `wasm`, `native`, `context_bridge`                    | Which FFI / native-bridge mechanism mediates the call |
| `channel_kind`  | `ipc`, `websocket`, `queue`, `message_bus`, `crdt`                          | Which async-channel kind mediates the publish |
| `mechanism`     | `annotation`, `delegate`, `di`, `di_registration`, `route_registration`     | The framework convention that produced the binding |
| `construct`     | `jsx`, `dispatch_table`, `websocket_endpoint`, `event_emit`                 | The syntactic-construct the edge represents |
| `dispatch_kind` | `route`                                                                     | The dispatch sub-flavor (likely to grow with future migrations) |
| `protocol`      | `ipc`                                                                       | The wire-protocol mediating the call (likely to grow) |

`bridge_kind` is closed enough to be exposed as a frozenset
(`hypergumbo_core.edge_types.BRIDGE_KINDS`) for in-process
consumers; the rest are documented-but-not-yet-frozenset enums.

## Migration patterns by example

**"Find all import edges, regardless of what's being imported"**

```python
# Before
imports = [e for e in edges if e["type"] in
    ("imports", "imports_module", "imports_component", "wasm_load")]

# After
imports = [e for e in edges if e["type"] == "imports"]
```

**"Find component imports specifically"**

```python
# Before
component_imports = [e for e in edges if e["type"] == "imports_component"]

# After (requires looking up dst.kind on the destination symbol)
component_imports = [
    e for e in edges
    if e["type"] == "imports"
    and node_index[e["dst"]]["kind"] == "component"
]
```

**"Find every cross-language function call (FFI, JNI, NAPI, WASM, IPC, etc.)"**

```python
# Before — the canonical "all bridge variants" set kept going stale
BRIDGE_TYPES = {
    "cgo_bridge", "ffi_bridge", "napi_bridge", "wasm_bridge",
    "native_bridge", "bridge_invokes",
    "ipc_calls",  # often forgotten
}
bridges = [e for e in edges if e["type"] in BRIDGE_TYPES]

# After — single canonical + a closed meta enumeration
KNOWN_BRIDGE_KINDS = frozenset({
    "cgo", "ffi", "napi", "wasm", "native", "context_bridge"
})
bridges = [
    e for e in edges
    if e["type"] == "calls"
    and (e.get("meta") or {}).get("bridge_kind") in KNOWN_BRIDGE_KINDS
]
# OR for the broader "any cross-process invocation" query:
cross_process = [
    e for e in edges
    if e["type"] == "calls"
    and (e.get("meta") or {}).get("protocol") == "ipc"
]
```

**"Find every async pub-sub edge (WebSocket / message queue / CRDT / IPC events / Django signals / ...)"**

```python
# Before — the family was scattered across many names
PUBSUB_TYPES = {
    "ipc_event", "message_send", "websocket_message",
    "message_queue", "crdt_publishes", "annotated_publishes",
    "enqueues", "message_dispatch",
}
pubsub = [e for e in edges if e["type"] in PUBSUB_TYPES]

# After
pubsub = [e for e in edges if e["type"] == "event_publishes"]
# Filter further by channel_kind if you need a specific protocol:
ws_only = [
    e for e in edges
    if e["type"] == "event_publishes"
    and (e.get("meta") or {}).get("channel_kind") == "websocket"
]
```

**"Find every dispatch / routing edge"**

```python
# Before
DISPATCH_TYPES = {"dispatches_to", "routes_to", "annotated_dispatches",
                  "di_resolves", "delegates_to"}
dispatches = [e for e in edges if e["type"] in DISPATCH_TYPES]

# After — delegates_to was reclassified as references (delegation is
# declaration-time, not runtime dispatch); the rest fold to dispatches_to
dispatches = [e for e in edges if e["type"] == "dispatches_to"]
# delegate-shaped queries:
delegates = [
    e for e in edges
    if e["type"] == "references"
    and (e.get("meta") or {}).get("mechanism") == "delegate"
]
```

## What's NOT migrated yet

After the 0.4.0 ship plus the post-release protocol-call producer
migration, **26 values still emit** under their current names — 22
in the `endpoint_shape` axis (deprecation candidates with fold
targets named) and 4 in the `pending_classification` axis (awaiting
per-family audit). They split into two groups by what clears the
next ship. (The protocol-call family — `http_calls` / `grpc_calls`
/ `graphql_calls` — had its producers migrated in that follow-on
release; its three registry entries stay `x-deprecated` until a
sibling Phase 4b' prunes them, but they no longer emit. See the
rename table above.)

### Pending-classification (awaits per-family audit)

Four `edge_type` values are classified `pending_classification` in
the registry — the registry's deliberate "we haven't decided yet"
state. They will be folded once their per-family audit completes
(analogous to audit-findings 0001 for dispatch/publish and audit-findings 0002 for IPC).
Consumer queries that filter on these values still work today; the
names may rename in a future minor version.

| Current name           | Family awaiting audit                            |
|------------------------|--------------------------------------------------|
| `resolver_implements`  | GraphQL resolver pattern                         |
| `resolver_for_type`    | GraphQL resolver-type binding                    |
| `openapi_implements`   | OpenAPI handler pattern                          |
| `implements_rpc`       | RPC implementation binding                       |

### Long-tail individual values (smaller subsequent migrations)

Eighteen endpoint_shape values added during the registry-completeness
sweep have heterogeneous fold targets (some to `calls` + meta, some
to `references` + dst.kind, some to `event_publishes` + channel_kind,
some to `depends_on` + construct). Each entry's plausible fold target
lives in its description in
`hypergumbo_core.edge_types.EDGE_TYPES`. They will ship in smaller
subsequent micro-phases — likely per-language or per-pattern.

These are: `abi_call`, `association`, `base_image`,
`build_tag_alternative_of`, `caller_invokes`, `contains_routes`,
`crypto_flow`, `depends`, `extends_template`, `includes_class`,
`includes_template`, `invokes_callback`, `kernel_launch`, `links_to`,
`notifies_resource`, `renders`, `requires_resource`, `script_src`,
`signal_receiver`, `template_calls`, `uses_mixin`, `uses_vocabulary`.

Treat them all as working-but-may-rename — same posture as the
pending-classification values above. The deprecation timeline at the
top of this doc applies to each group: `x-deprecated` annotation
present today; eventual removal in a future `SCHEMA_VERSION` minor
bump after that group's bakeoff validation clears.

## Companion docs

- `docs/concept-axes.md` — auto-generated by-axis view of the
  current canonical registry. The relationship-axis section is the
  authoritative list of post-migration canonical names; the
  endpoint_shape section enumerates everything in the deprecation
  window.
- `docs/schema.json` — the JSON Schema; its `Edge.type` field has
  the `enum` of valid values plus an `x-deprecated` extension key
  listing the deprecation candidates.
- ADR-0023 (under `docs/adr/`) and audit-findings 0001 + 0002 (under
  `docs/audits/`) — the design decision behind the typing principle and
  the per-family classifications. Read these only if you want the rationale; the
  rename tables above are sufficient for migration.
