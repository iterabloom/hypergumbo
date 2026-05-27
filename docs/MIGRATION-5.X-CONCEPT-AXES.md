<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Migrating downstream consumers — Symbol.kind + Edge.evidence_type concept-axes closure (5.x)

This guide is for code that consumes hypergumbo's behavior-map JSON
and filters or weights symbols by `Symbol.kind` or edges by
`Edge.evidence_type`, or reads `origin` / `pass_id` fields. If your
code only consumes structure (`Symbol.name`, `Edge.src`, `Edge.dst`,
the relationship-shape `edge_type` like `calls` / `imports` /
`extends`), you can stop reading.

Parts 1–5 cover the **closure** of the ADR-0027 and ADR-0028
programs: 71 `Symbol.kind` values and 111 `Edge.evidence_type`
values retire from their registries between schema 0.5.8 and 0.8.0.
Part 6 covers post-closure breaking changes (0.9.0 → 0.10.0),
including the `origin` type change and `pass_id` suffix removal.
The companion doc [`migrating-edge-types.md`](migrating-edge-types.md)
covers an earlier program (ADR-0023) for `Edge.edge_type` and is
unrelated to this migration.

## What changed in one paragraph

`Symbol.kind` previously smuggled three different things through
one field: the source-language syntactic construct, the
framework-imposed role, and miscellaneous endpoint qualifiers.
ADR-0027 settled that **`Symbol.kind` names only the source-language
syntactic construct**; framework roles, ecosystem qualifiers, and
construct-shape facts move to `Symbol.meta`. Symmetrically,
`Edge.evidence_type` previously smuggled the inference pathway,
the dst's resolvedness, the framework's dispatch convention, and
the call-construct's surface form through one field. ADR-0028
settled that **`Edge.evidence_type` names only the inference
pathway**; resolvedness moves to a new sibling field
`Edge.is_resolved: bool`, framework dispatch moves to
`Edge.meta["framework_dispatch"]`, and call-construct surface
form moves to `Edge.meta["call_construct"]`.

The retiring values land on a canonical kind / evidence_type plus
a meta key. The tables below show the per-value mapping.

## Schema version timeline

| Version | What ships |
|---|---|
| 0.5.8 | (released, baseline) |
| 0.6.0 | **Breaking.** `Symbol.kind` endpoint_shape closure: 71 values removed from `SYMBOL_KINDS`. Behavior maps emitted at 0.6.0+ never contain these values; behavior maps containing them stop validating against the 0.6.0 schema. |
| 0.7.0 | **Breaking.** `Edge.evidence_type` endpoint_shape closure: 111 values removed from `EVIDENCE_TYPES`. Same dual-validity discipline. |
| 0.7.1 | Additive. 6 new evidence_type values land as AXIS_PENDING; `error_set` registered on `Symbol.kind` (Zig). |
| 0.7.2 | Additive. `Edge.dst_ref` field lands as optional sibling of `Edge.dst`. |
| 0.8.0 | Canonicalization. Producers prefer `Edge.dst_ref` over the legacy colon-split `Edge.dst`; consumers should do the same. |

If you read behavior maps with a permissive JSON parser (one that
ignores the schema), nothing crashes at 0.6.0 / 0.7.0 — but your
filters will stop matching symbols and edges that no longer exist
by those names. The rename tables below are what you need to
update.

## Part 1 — `Symbol.kind` retirements (71 values)

71 values retire from `SYMBOL_KINDS` at schema 0.6.0. Of these,
**56 are FOLD** (renamed onto a canonical kind + meta key);
**15 are DEPRECATE-NO-FOLD** (no canonical home — the consumer
should switch to a different query shape entirely).

In addition, ~84 values previously parked in
`pending_classification` were promoted to `language_construct` in
the registry — these are registry-internal promotions and don't
appear in your filtered output as anything new; you do not need
to adjust queries for those.

### Cluster 27B — File-shape / package-shape

10 FOLD, 1 DEPRECATE-NO-FOLD. Audit: [0005](audits/0005-symbol-kind-cluster-b-file-shape.md).

| Old `Symbol.kind` | Folds to | Carry in `meta` |
|---|---|---|
| `module_file` | `file` | `meta["module_system"]="esm"` |
| `component_file` | `file` | `meta["component_framework"]="vue"` |
| `npm_package` | `package` | `meta["package_ecosystem"]="npm"` |
| `composer_package` | `package` | `meta["package_ecosystem"]="composer"` |
| `main_entry` | `file` | `meta["entry_role"]="main"` |
| `library_export` | `export` | `meta["export_scope"]="library"` |
| `export_entry` | `export` | `meta["export_source"]="package_exports_map"` |
| `wasm_module` | `module` | `meta["compilation_target"]="wasm"` |
| `wasm_import` | `import` | `meta["compilation_target"]="wasm"` |
| `script` | `file` | `meta["entry_role"]="script"` |

**Deprecated without fold:**

- `tsconfig` — Was a single-purpose marker for `tsconfig.json`. Replace queries with `is_config_file=True AND path.endswith("tsconfig.json")` or `meta["config_format"]="tsconfig"`.

### Cluster 27G — Build / config-shape

5 FOLD, 4 DEPRECATE-NO-FOLD. Audit: [0006](audits/0006-symbol-kind-cluster-g-build-config-shape.md).

| Old `Symbol.kind` | Folds to | Carry in `meta` |
|---|---|---|
| `test_case` | `test` | `meta["test_dialect"]="robot"` |
| `editable` | `requirement` | `meta["install_mode"]="editable"` |
| `url_requirement` | `requirement` | `meta["install_source"]="url"` |
| `devDependency` | `dependency` | `meta["dependency_scope"]="dev"` |
| `python_task` | `task` | `meta["task_implementation"]="python"` |

**Deprecated without fold:**

- `config` — Prisma was emitting `kind="config"` for every block type. Real construct lives in `meta["block_type"]`; query on that instead.
- `dev-dependency`, `build-dependency` — Dead registry vocabulary; no analyzer or linker ever emitted them.
- `work_item` — Tracker-internal kind that leaked; not user-facing.

### Cluster 27H — Domain-specific long-tail

0 FOLD, 3 DEPRECATE-NO-FOLD, 57 CANONICAL-promoted. Audit: [0007](audits/0007-symbol-kind-cluster-h-long-tail.md).

**Deprecated without fold:**

- `heading` — Dead vocabulary; Markdown headings emit as `kind="section"` already.
- `model` — Was an ID-string-only synthetic emit from the Prisma analyzer.
- `unresolved` — Registry seed error; no producer ever emitted `Symbol(kind="unresolved")`.

### Cluster 27C — Apex/peer overloads

4 FOLD, 0 DEPRECATE-NO-FOLD. Audit: [0009](audits/0009-symbol-kind-cluster-c-apex-peer.md).

| Old `Symbol.kind` | Folds to |
|---|---|
| `fn` | `function` |
| `var` | `variable` |
| `proc` | `procedure` |
| `structure` | `struct` |

Pure renames — no meta key needed. The old names were just peer
spellings of the canonical construct.

### Cluster 27E — Edge labels masquerading as kinds

4 FOLD, 6 DEPRECATE-NO-FOLD. Audit: [0010](audits/0010-symbol-kind-cluster-e-edge-label-kinds.md).

| Old `Symbol.kind` | Folds to | Carry in `meta` |
|---|---|---|
| `function_call` | `call_site` | — |
| `subprocess_call` | `call_site` | `meta["call_kind"]="subprocess"` |
| `db_query` | `call_site` | `meta["call_kind"]="db_query"` |
| `abi_call` | `call_site` | `meta["call_kind"]="abi"` |

**Deprecated without fold:**

- `call`, `read`, `write` — Zero Symbol producers; these were edge-label names that leaked into the kind registry.
- `inherit`, `include`, `extends` — Relationship-shape names; query the corresponding `Edge` (`inherits` / `includes_class` / `extends_template`) instead.

### Cluster 27F — Component / UI references

0 FOLD, 1 DEPRECATE-NO-FOLD. Audit: [0011](audits/0011-symbol-kind-cluster-f-component-refs.md).

**Deprecated without fold:**

- `component_ref` — Was dst-kind leakage; the `imports` Edge captures the relationship without a synthetic Symbol kind.

### Cluster 27D — Framework roles

33 FOLD, 0 DEPRECATE-NO-FOLD. Audit: [0013](audits/0013-symbol-kind-cluster-d-framework-roles.md). The largest single cluster; all fold onto the underlying language construct (`function` / `class` / `interface`) plus `meta["framework_role"]=<old_name>`.

| Old `Symbol.kind` | Folds to | Carry in `meta` |
|---|---|---|
| `event_publisher` | `function` | `meta["framework_role"]="event_publisher"` |
| `event_subscriber` | `function` | `meta["framework_role"]="event_subscriber"` |
| `ipc_publisher` | `function` | `meta["framework_role"]="ipc_publisher"` |
| `ipc_subscriber` | `function` | `meta["framework_role"]="ipc_subscriber"` |
| `ipc_caller` | `function` | `meta["framework_role"]="ipc_caller"` |
| `ipc_bridge_caller` | `function` | `meta["framework_role"]="ipc_bridge_caller"` |
| `ipc` | `function` | `meta["framework_role"]="ipc"` |
| `objc_bridge` | `function` | `meta["framework_role"]="objc_bridge"` |
| `crypto_producer` | `function` | `meta["framework_role"]="crypto_producer"` |
| `crypto_consumer` | `function` | `meta["framework_role"]="crypto_consumer"` |
| `message_sender` | `function` | `meta["framework_role"]="message_sender"` |
| `message_handler` | `function` | `meta["framework_role"]="message_handler"` |
| `mq_publisher` | `function` | `meta["framework_role"]="mq_publisher"` |
| `mq_subscriber` | `function` | `meta["framework_role"]="mq_subscriber"` |
| `grpc_server` | `class` | `meta["framework_role"]="grpc_server"` |
| `grpc_stub` | `function` | `meta["framework_role"]="grpc_stub"` |
| `grpc_service` | `interface` | `meta["framework_role"]="grpc_service"` |
| `grpc_servicer` | `class` | `meta["framework_role"]="grpc_servicer"` |
| `grpc_client` | `function` | `meta["framework_role"]="grpc_client"` |
| `websocket_endpoint` | `function` | `meta["framework_role"]="websocket_endpoint"` |
| `websocket_emitter` | `function` | `meta["framework_role"]="websocket_emitter"` |
| `websocket_listener` | `function` | `meta["framework_role"]="websocket_listener"` |
| `dispatcher` | `function` | `meta["framework_role"]="dispatcher"` |
| `graphql_resolver` | `function` | `meta["framework_role"]="graphql_resolver"` |
| `graphql_client` | `function` | `meta["framework_role"]="graphql_client"` |
| `http_client` | `function` | `meta["framework_role"]="http_client"` |
| `route_mount` | `function` | `meta["framework_role"]="route_mount"` |
| `route` | `function` | `meta["framework_role"]="route"` |
| `route_include` | `function` | `meta["framework_role"]="route_include"` |
| `openapi_operation` | `function` | `meta["framework_role"]="openapi_operation"` |
| `selector_ref` | `reference` | `meta["framework_role"]="selector_ref"` |
| `rpc` | `function` | `meta["framework_role"]="rpc"` |
| `service` | `interface` | — |

### Producer-side fold: CUDA + Android XML

Two producer-side fixes against registry-absent values surfaced by
the ADR-0027 audit. Both replace ad-hoc kind strings with the
canonical kind + `meta` discriminator.

| Old emit | Now emits | Carry in `meta` |
|---|---|---|
| CUDA `__global__` / `__device__` / `__host__` functions | `function` | `meta["cuda_execution_space"]="global" \| "device" \| "host" \| "host_device"` |
| Android XML `<activity>` / `<service>` / `<receiver>` / `<provider>` | `component` | `meta["component_type"]="activity" \| "service" \| "receiver" \| "provider"` |

Note: Android XML `<provider>` deliberately routes through
`meta["component_type"]="provider"` (rather than canonical kind
`provider`) to keep it disjoint from Apex / Salesforce `[Provider]`,
which is the canonical `provider` kind.

## Part 2 — `Edge.evidence_type` retirements (111 values)

All 111 retiring values are **FOLD**. The fold target is one of the
canonical inference labels (`ast_call`, `ast_call_direct`,
`ast_decorator`, `ast_import`, `naming_convention`,
`method_call`, etc.).

Three structural patterns appear, organized by audit cluster:

### Cluster 28B — Resolution-status leakage

18 FOLD. Audit: [0008](audits/0008-evidence-type-cluster-b-resolution-status.md). These values smuggled "unresolved" into the inference label. The fold target is the resolved-form sibling, and **the dst's resolvedness now lives on `Edge.is_resolved: bool`**. So if your old query was `evidence_type == "ast_call_unresolved_import"`, your new query is `evidence_type == "ast_call_direct" AND is_resolved == False`.

| Old `evidence_type` | Folds to | (plus `Edge.is_resolved=False`) |
|---|---|---|
| `ast_annotation_unresolved` | `ast_annotation` | ✓ |
| `ast_attribute_unresolved` | `ast_attribute` | ✓ |
| `ast_call_unresolved_import` | `ast_call_direct` | ✓ |
| `ast_decorator_unresolved` | `ast_decorator` | ✓ |
| `ast_method_unresolved_global` | `ast_method_inferred` | ✓ |
| `ast_method_unresolved_namespace` | `ast_method_inferred` | ✓ |
| `chained_call_unresolved` | `method_call_field_chain` | ✓ |
| `django_signal_receiver_unresolved` | `django_signal_receiver` | ✓ |
| `grpc_unresolved_resolution` | `grpc_stub_resolution` | ✓ |
| `luajit_ffi_unresolved` | `luajit_ffi_lookup` | ✓ |
| `ruby_ffi_attach_unresolved` | `ruby_ffi_attach` | ✓ |
| `trait_impl_unresolved` | `trait_impl` | ✓ |
| `unresolved_dotted_submodule_call` | `ast_call_direct` | ✓ |
| `unresolved_external_call` | `ast_call_direct` | ✓ |
| `unresolved_imported_name_call` | `ast_call_direct` | ✓ |
| `unresolved_method_call` | `method_call` | ✓ |
| `unresolved_module_call` | `ast_call_direct` | ✓ |
| `unresolved_variable_method_call` | `method_call_type_inferred` | ✓ |

### Cluster 28D — Call-construct overloads

28 FOLD. Audit: [0012](audits/0012-evidence-type-cluster-d-call-construct.md). These values smuggled the call-construct surface form (function call vs. method call vs. constructor vs. pipe etc.) into the inference label. The fold target is the apex `ast_call`; surface-form info moves to `meta["call_construct"]` where it remains distinguishable.

| Old `evidence_type` | Folds to | Carry in `meta` |
|---|---|---|
| `ambiguous_method_call` | `ast_call` | — |
| `bare_method_call` | `ast_call` | — |
| `call` | `ast_call` | — |
| `chained_return_type_call` | `ast_call` | `meta["call_construct"]="chained_return_type"` |
| `constructor_call` | `ast_call` | `meta["call_construct"]="constructor"` |
| `cross_file_call` | `ast_call` | `meta["call_construct"]="cross_file"` |
| `cross_file_message_send` | `message_send` | `meta["call_construct"]="cross_file"` |
| `external_receiver_call` | `ast_call` | — |
| `function_application` | `ast_call` | `meta["call_construct"]="application"` |
| `function_application_external` | `ast_call` | `meta["call_construct"]="application_external"` |
| `function_call` | `ast_call` | `meta["call_construct"]="function"` |
| `local_call` | `ast_call` | `meta["call_construct"]="local"` |
| `macro_body_call` | `ast_call` | `meta["call_construct"]="macro_body"` |
| `method_call` | `ast_call` | `meta["call_construct"]="method"` |
| `method_call_field_chain` | `ast_call` | — |
| `method_call_recovery` | `ast_call` | — |
| `method_call_typed` | `ast_call` | — |
| `method_call_type_inferred` | `ast_call` | — |
| `method_group` | `ast_call` | `meta["call_construct"]="method_group"` |
| `object_creation` | `ast_call` | `meta["call_construct"]="constructor"` |
| `pipe_call` | `ast_call` | `meta["call_construct"]="pipe"` |
| `receiver_call` | `ast_call` | — |
| `remote_call` | `ast_call` | `meta["call_construct"]="remote"` |
| `remote_call_external` | `ast_call` | `meta["call_construct"]="remote_external"` |
| `stdlib_method_call` | `ast_call` | — |
| `typed_field_call` | `ast_call` | — |
| `typed_receiver_call` | `ast_call` | — |
| `unexported_method_call` | `ast_call` | — |

### Cluster 28C — Framework-specific dispatch

65 FOLD. Audit: [0014](audits/0014-evidence-type-cluster-c-framework-dispatch.md). These values smuggled the framework dispatch convention (Django ORM, Rails callback, Tauri invoke, etc.) into the inference label. The fold target is the canonical inference label for the call shape; the framework label moves to `meta["framework_dispatch"]` (or `meta["detection_pattern"]` for naming-convention shapes).

| Old `evidence_type` | Folds to | Carry in `meta` |
|---|---|---|
| `abi_name_match` | `ast_call_direct` | `meta["detection_pattern"]="abi_name_match"` |
| `activerecord_association` | `ast_call_direct` | `meta["framework_dispatch"]="activerecord_association"` |
| `airflow_framework_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="airflow"` |
| `context_bridge_wrapper` | `ast_call_direct` | `meta["framework_dispatch"]="electron_context_bridge"` |
| `controller_routes` | `ast_call_direct` | `meta["framework_dispatch"]="controller_routes"` |
| `crypto_api_pattern` | `ast_call_direct` | `meta["detection_pattern"]="crypto_api"` |
| `cuda_kernel_launch` | `ast_call_direct` | `meta["framework_dispatch"]="cuda_kernel_launch"` |
| `di_binding` | `ast_call_direct` | — |
| `django_orm_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="django_orm"` |
| `django_signal_receiver` | `ast_decorator` | `meta["framework_dispatch"]="django_signal"` |
| `django_channels_emit` | `ast_call_direct` | `meta["framework_dispatch"]="django_channels"` |
| `django_channels_endpoint` | `ast_call_direct` | `meta["framework_dispatch"]="django_channels"` |
| `event_name_match` | `naming_convention` | `meta["detection_pattern"]="event_name"` |
| `fastapi_emit` | `ast_call_direct` | `meta["framework_dispatch"]="fastapi"` |
| `fastapi_endpoint` | `ast_call_direct` | `meta["framework_dispatch"]="fastapi"` |
| `go_cobra_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="cobra"` |
| `go_memberlist_delegate` | `ast_call_direct` | `meta["framework_dispatch"]="memberlist"` |
| `graphql_operation_match` | `ast_call_direct` | `meta["framework_dispatch"]="graphql_operation"` |
| `grpc_go_server_method` | `ast_call_direct` | `meta["framework_dispatch"]="grpc_go_server"` |
| `grpc_rpc_definition` | `ast_call_direct` | `meta["framework_dispatch"]="grpc_rpc_definition"` |
| `grpc_server_to_service` | `ast_call_direct` | `meta["framework_dispatch"]="grpc_server_to_service"` |
| `grpc_service_match` | `ast_call_direct` | `meta["framework_dispatch"]="grpc_service_match"` |
| `http_url_match` | `naming_convention` | `meta["detection_pattern"]="http_url"` |
| `implicit_convention` | `naming_convention` | `meta["detection_pattern"]="implicit_convention"` |
| `jackson_bean_dispatch` | `ast_decorator` | `meta["framework_dispatch"]="jackson_bean"` |
| `jni_naming_convention` | `naming_convention` | `meta["detection_pattern"]="jni_naming_convention"` |
| `job_enqueue` | `ast_call_direct` | `meta["framework_dispatch"]="job_enqueue"` |
| `kafka_streams_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="kafka_streams"` |
| `middleware_chain` | `ast_call_direct` | `meta["framework_dispatch"]="middleware_chain"` |
| `native_emit` | `ast_call_direct` | `meta["framework_dispatch"]="native_websocket"` |
| `native_endpoint` | `ast_call_direct` | `meta["framework_dispatch"]="native_websocket"` |
| `nestjs_module_registration` | `ast_decorator` | `meta["framework_dispatch"]="nestjs_module"` |
| `npm_package_import` | `ast_import` | `meta["framework_dispatch"]="npm_package"` |
| `openapi_operation_id_match` | `ast_call_direct` | `meta["framework_dispatch"]="openapi_operation_id"` |
| `openapi_path_match` | `ast_call_direct` | `meta["framework_dispatch"]="openapi_path"` |
| `orm_accessor_pattern` | `ast_call_direct` | `meta["framework_dispatch"]="orm_accessor"` |
| `otp_genserver_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="otp_genserver"` |
| `phoenix_event_match` | `naming_convention` | `meta["detection_pattern"]="phoenix_event"` |
| `pyo3_bridge` | `ast_call_direct` | `meta["framework_dispatch"]="pyo3_bridge"` |
| `rails_block_callback` | `ast_call_direct` | `meta["framework_dispatch"]="rails_block_callback"` |
| `rails_callback` | `ast_call_direct` | `meta["framework_dispatch"]="rails_callback"` |
| `registry_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="registry_dispatch"` |
| `resolver_field_match` | `ast_call_direct` | `meta["framework_dispatch"]="graphql_resolver_field"` |
| `resolver_type_match` | `ast_call_direct` | `meta["framework_dispatch"]="graphql_resolver_type"` |
| `route_mount` | `ast_call_direct` | `meta["framework_dispatch"]="route_mount"` |
| `router_routes` | `ast_call_direct` | `meta["framework_dispatch"]="router_routes"` |
| `ruby_c_extension` | `ast_call_direct` | `meta["framework_dispatch"]="ruby_c_extension"` |
| `ruby_delegate` | `ast_call_direct` | `meta["framework_dispatch"]="ruby_delegate"` |
| `ruby_ffi_attach` | `ast_call_direct` | `meta["framework_dispatch"]="ruby_ffi_attach"` |
| `rust_trait_dispatch` | `ast_call_direct` | `meta["framework_dispatch"]="rust_trait_dispatch"` |
| `script_src` | `ast_import` | `meta["framework_dispatch"]="html_script_src"` |
| `socketio_emit` | `ast_call_direct` | `meta["framework_dispatch"]="socketio"` |
| `socketio_endpoint` | `ast_call_direct` | `meta["framework_dispatch"]="socketio"` |
| `specta_wrapper_import` | `ast_import` | `meta["framework_dispatch"]="specta_wrapper"` |
| `subprocess_cli_match` | `ast_call_direct` | `meta["detection_pattern"]="subprocess_cli"` |
| `table_name_match` | `naming_convention` | `meta["detection_pattern"]="table_name"` |
| `tauri_emit_listen` | `ast_call_direct` | `meta["framework_dispatch"]="tauri_emit_listen"` |
| `tauri_invoke` | `ast_call_direct` | `meta["framework_dispatch"]="tauri_invoke"` |
| `vue_component_import` | `ast_import` | `meta["framework_dispatch"]="vue_component"` |
| `vue_event_handler` | `ast_call_direct` | `meta["framework_dispatch"]="vue_event_handler"` |
| `wasm_bindgen_import` | `ast_import` | `meta["framework_dispatch"]="wasm_bindgen_import"` |
| `wasm_instantiate` | `ast_call_direct` | `meta["framework_dispatch"]="wasm_instantiate"` |
| `ws_emit` | `ast_call_direct` | `meta["framework_dispatch"]="ws"` |
| `ws_endpoint` | `ast_call_direct` | `meta["framework_dispatch"]="ws"` |
| `yjs_crdt_pattern` | `ast_call_direct` | `meta["framework_dispatch"]="yjs_crdt"` |

## Part 3 — `Edge.dst_ref` adoption (0.7.2 → 0.8.0)

A new structured external-target sibling field. Before 0.7.2,
`Edge.dst` carried a colon-joined string like
`"python:requests:0-0:get:unresolved"` and consumers parsed it
heuristically. From 0.8.0, **prefer `Edge.dst_ref` over the
colon-split heuristic**.

```python
@dataclass(frozen=True)
class ExternalRef:
    lang: str           # e.g. "python", "rust"
    module_path: str    # e.g. "requests", "tokio::net"
    name: str           # the imported symbol name
```

**Aliasing rule.** For aliased imports (`from numpy import zeros as z`),
`name` carries the **imported symbol** (`zeros`), not the local
alias (`z`). This matters for cross-codebase analysis where the
alias is local.

**Defensive loading.** `Edge.from_dict()` reads `d.get("dst_ref")`,
so cached JSON from earlier releases loads cleanly with
`dst_ref=None`. Code that prefers `dst_ref` should null-guard:

```python
ref = edge.dst_ref
if ref is None:
    # Fall back to legacy heuristic
    lang, module, _, name, *_ = edge.dst.split(":", 4)
else:
    lang, module, name = ref.lang, ref.module_path, ref.name
```

**Producers that emit `dst_ref` at 0.8.0:** Java, Go, Elixir,
JS/TS, C++, Rust, Ruby, Python. Other languages emit
`dst_ref=None` until they ship corresponding ImportScope
adoption.

**Consumers that prefer `dst_ref` at 0.8.0:** `io_boundary` chain
composition, `ir.create_boundary_nodes`.

## Part 4 — `io-boundaries --json` envelope schema (1.0)

A separate wire contract from the behavior-map envelope.
`IO_BOUNDARIES_SCHEMA_VERSION = "1.0"` ships as the inaugural
version. Locked top-level keys:

- `schema_version`
- `total_io_edges`
- `boundaries`
- `unsupported_languages`

Bumping rules live in the `io_boundary.py` module docstring; a
test fails loudly on silent drift.

## Part 5 — `disambiguation_fallback` discipline (read-side contract)

Not a schema change, but worth knowing if you weight edges by
confidence. As of this release, **13 linkers adopt the
disambiguation-fallback contract**: when a linker resolves a simple
name to a structurally ambiguous target, the resulting edge carries
`Edge.confidence <= 0.5` AND `Edge.meta["disambiguation_fallback"]=True`.

A static linter pins the contract at every linker emission site.
If you filter on confidence ≥ 0.5 you'll now reliably exclude
ambiguous-resolution edges; if you want them, query on
`meta.get("disambiguation_fallback")`.

## Part 6 — Post-closure schema changes (0.9.0 → 0.10.0)

The concept-axes program concluded at 0.8.0. Subsequent schema
bumps in the same 5.x release cycle are unrelated to the axes
closure but affect JSON consumers:

| Version | What ships |
|---|---|
| 0.9.0 | Self-analysis validates clean. `line=0` fix on module_exports edges; missing top-level keys added. |
| 0.9.1 | Additive. `Edge.derived_from: list[str]` (linker derivation provenance). `pass_id` suffix removal (`-v1` / `-ts-v1` / `-ast-v1` gone — **breaking** if you match on pass IDs). `behavior_map["features"]` and `behavior_map["reproducibility_context"]` added. |
| 0.10.0 | **Breaking.** `Symbol.origin` and `Edge.origin` change from `str` to `list[str]`. Multi-source attribution: when multiple passes contribute, all are credited. |

**`origin` migration.** If your code reads `symbol.origin` or
`edge.origin` as a string, switch to list iteration:

```python
# Before (0.9.1 and earlier):
if edge["origin"] == "python":
    ...

# After (0.10.0+):
if "python" in edge["origin"]:
    ...
```

**`pass_id` migration.** If your code matches on pass IDs like
`"python-v1"` or `"js_ts-ts-v1"`, strip the suffix:
`"python-v1"` → `"python"`, `"js_ts-ts-v1"` → `"js_ts"`.

**`Edge.derived_from`.** New optional field (list of Symbol ID
strings). Present on linker-produced edges; absent or empty on
analyzer-produced edges. No migration needed — it's additive.

## See also

- [ADR-0027](adr/0027-symbol-kind-language-construct-only.md) — Symbol.kind axiom and migration program
- [ADR-0028](adr/0028-evidence-type-inference-pathway-only.md) — Edge.evidence_type axiom and migration program
- [ADR-0024](adr/0024-axis-declaration-template.md) — Axis declaration template; defines the CANONICAL / FOLD / DEPRECATE-NO-FOLD verdict methodology
- [migrating-edge-types.md](migrating-edge-types.md) — Sibling migration guide for the earlier ADR-0023 `Edge.edge_type` program (independent of this one)
- [docs/audits/](audits/) — Per-cluster audit findings; each row of the tables above traces back to a verdict in one of audits 0005–0014
- [hypergumbo-spec.md](hypergumbo-spec.md) — Current `Symbol.kind` and `Edge.evidence_type` axiom statements
- [RELEASE-NOTES-5.X.md](RELEASE-NOTES-5.X.md) — User-facing summary of all 5.x changes including this migration
