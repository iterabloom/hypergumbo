<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0014: Edge.evidence_type Cluster 28C — Framework-Specific Dispatch

- Date: 2026-05-05
- Status: All rows PRELIM_RESOLVED — Phase 3 producer migration complete across all 65 rows. Wave 5 of WI-runod (six PRs across Codeberg #3572 and selfh #162-#166) shipped the framework_dispatch / detection_pattern fold; the `linkers/websocket.py` f-string emit was rewritten to canonical inference + structured `meta["framework_dispatch"]` per the audit's f-string-source-targeted diagnostic. Empirical re-grep finds zero live `Edge.create(... evidence_type=<value> ...)` producers across all rows (3 stale-docstring matches at `controller_routes.py:38`, `middleware_chain.py:15`, `router_routes.py:66` are docstring references in already-migrated code). Values remain on `endpoint_shape` through the Phase 4a deprecation window per ADR-0028 §"Phase 4".
- Closes: WI-kagik-fumiz-moros-bunok-pahuh-rihov-gavak-bigok (Cluster 28C, ADR-0028 Phase 3) at the verdict-table layer. Producer-side migration ships piecewise as per-framework sub-PRs (Wave 5 of WI-runod schedule), each carrying its own `awaits_bakeoff_validation` tag.
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Fourth audit-findings doc on the `Edge.evidence_type` axis declared by [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md), companion to audit-findings 0004 (Cluster 28A canonical inference), 0008 (Cluster 28B resolution-status), and 0012 (Cluster 28D apex/peer call-construct).

## Context

[ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) §"Phase 3" Cluster 28C is the framework-specific dispatch cluster of `Edge.evidence_type`: 65 values that name **which framework's detection pattern fired** (or **which pattern shape was matched**), not the analyzer's inference pathway in the Cluster 28A sense. The cluster fails Test 1 (property derivability) and Test 4 (mechanism vs. category) of the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md): each value duplicates information that `Edge.meta["framework"]` / `meta["protocol"]` already carries (or could carry), and the framework-dispatch property is mechanism (HOW the framework dispatched), not category (WHAT inference fired).

ADR-0028 §"Resolution" rule 3 prescribes the fold: each Cluster 28C value collapses to a canonical inference label (existing Cluster 28A entry like `ast_call_direct`, `ast_decorator`, `ast_import`, `naming_convention`, `tree_sitter`) plus structured meta — either `meta["framework_dispatch"]=<framework_name>` or `meta["detection_pattern"]=<pattern_name>` per row. The choice between the two meta keys partitions the cluster on a single axis: framework-named dispatches (django, kafka, phoenix, …) carry `framework_dispatch`; pure pattern-detection sites (regex / naming / URL match) carry `detection_pattern`.

The 65 in-scope values are seeded on `AXIS_ENDPOINT_SHAPE` at registry lines 367–496 (`packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py`). The list:

```
abi_name_match, activerecord_association, airflow_framework_dispatch,
context_bridge_wrapper, controller_routes, crypto_api_pattern, cuda_kernel_launch,
di_binding, django_orm_dispatch, django_signal_receiver, django_channels_emit,
django_channels_endpoint, event_name_match, fastapi_emit, fastapi_endpoint,
go_cobra_dispatch, go_memberlist_delegate, graphql_operation_match,
grpc_go_server_method, grpc_rpc_definition, grpc_server_to_service,
grpc_service_match, http_url_match, implicit_convention, jackson_bean_dispatch,
jni_naming_convention, job_enqueue, kafka_streams_dispatch, middleware_chain,
native_emit, native_endpoint, nestjs_module_registration, npm_package_import,
openapi_operation_id_match, openapi_path_match, orm_accessor_pattern,
otp_genserver_dispatch, phoenix_event_match, pyo3_bridge, rails_block_callback,
rails_callback, registry_dispatch, resolver_field_match, resolver_type_match,
route_mount, router_routes, ruby_c_extension, ruby_delegate, ruby_ffi_attach,
rust_trait_dispatch, script_src, socketio_emit, socketio_endpoint,
specta_wrapper_import, subprocess_cli_match, table_name_match, tauri_emit_listen,
tauri_invoke, vue_component_import, vue_event_handler, wasm_bindgen_import,
wasm_instantiate, ws_emit, ws_endpoint, yjs_crdt_pattern
```

This audit answers three questions:

1. **Per-value verdicts.** All 65 values FOLD. Per-row fold target is the canonical inference label that names how the analyzer concluded the edge exists — typically `ast_call_direct` for call-site dispatches, `ast_decorator` for decorator-based registrations, `ast_import` / canonical-import for import patterns, `naming_convention` for regex/naming-based detection, and `tree_sitter` for AST-pattern-match detection. The cluster-28A audit-findings doc enumerates which canonical labels are available; per-row choice is left to the per-framework producer migration PR (audit notes the prescribed key but defers the precise canonical to producer-site context).
2. **Meta-key partition.** 56 rows fold to `meta["framework_dispatch"]=<framework_name>`; 9 rows fold to `meta["detection_pattern"]=<pattern_name>`. The split is per-row, governed by whether the value names a *framework* (django_orm, kafka_streams, phoenix, nestjs_module, …) or a *pattern shape* (URL match, naming convention, regex match, …).
3. **Recurrence-promotion threshold for `meta["framework_dispatch"]`** (ADR-0028 Open Question 2). After fold, `meta["framework_dispatch"]` will carry ~30 distinct values across ~30 producer modules — at the recurrence-promotion threshold per ADR-0024 §"Fold-residue discipline" rule 3. **This audit does not promote.** Phase 3 follow-on work tracks emission counts; if the threshold trips post-migration, a follow-on ADR promotes `framework_dispatch` to a dedicated `Edge.framework: str | None` field.

**No new axis ADR required.** The four-leakage-test pass for each of the 65 values fired uniformly on Tests 1 (property derivability) and 4 (mechanism vs. category). Each value names "how the framework dispatched / which detection pattern recognized it," not "how the analyzer inferred the edge."

## Methodology

Per [ADR-0028 §"Phase 3" Cluster 28C](../adr/0028-evidence-type-inference-pathway-only.md). Each value's verdict applies the four leakage tests from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md). Tests 1 (property derivability) and 4 (mechanism vs. category) are the load-bearing tests: the framework-dispatch property is fully derivable from `Edge.meta["framework"]` (Test 1), and the value names HOW dispatch happened (mechanism), not WHAT inference fired (category) (Test 4).

Producer-side migration: each emit site replaces `evidence_type="<value>"` with `evidence_type="<canonical_inference_label>"` plus structured `meta["framework_dispatch"]=<value>` (or `meta["detection_pattern"]=<value>` for the nine pattern-detection rows). The L3 producer-side coherence linter (`scripts/check-producer-axis-coherence`) catches drift at pre-commit.

This audit-findings doc is **doc-only at filing time**: no producer code is migrated in this PR. Per-framework sub-PRs (Wave 5 of WI-runod schedule) ship the producer migration grouped with the parallel ADR-0027 Cluster 27D framework-role fold for each framework's linker file (per audit-findings 0013). Each sub-PR carries its own `awaits_bakeoff_validation` tag per the validation-tagging discipline.

## Diagnostic findings

### 1. The meta-key partition: `framework_dispatch` vs `detection_pattern`

The 65 values split on a single discriminator: does the value name a *framework* (a recognizable third-party dispatch system: Django ORM, Kafka Streams, Phoenix, NestJS, Vue, Rails, gRPC, Tauri, Ruby FFI, Wasm-bindgen, …) or a *pattern shape* (a generic detection technique: URL match, naming convention, regex match, table-name lookup, subprocess argument inspection)?

- **`meta["framework_dispatch"]` (56 rows):** values whose name carries a framework identity. Examples: `django_orm_dispatch` → `framework_dispatch=django_orm`; `kafka_streams_dispatch` → `framework_dispatch=kafka_streams`; `nestjs_module_registration` → `framework_dispatch=nestjs_module`; `tauri_invoke` → `framework_dispatch=tauri_invoke`. The framework name becomes the meta value; the rest of the registry comment describes the canonical inference label.
- **`meta["detection_pattern"]` (9 rows):** values whose name carries a *pattern shape* rather than a framework. Examples: `event_name_match` → `detection_pattern=event_name`; `http_url_match` → `detection_pattern=http_url`; `jni_naming_convention` → `detection_pattern=jni_naming_convention`; `subprocess_cli_match` → `detection_pattern=subprocess_cli`; `phoenix_event_match` → `detection_pattern=phoenix_event` (a Phoenix-specific *naming* pattern, but the meta key reflects that the dispatch is detected via name-shape inspection, not a framework call). The full nine: `abi_name_match`, `crypto_api_pattern`, `event_name_match`, `http_url_match`, `implicit_convention`, `jni_naming_convention`, `phoenix_event_match`, `subprocess_cli_match`, `table_name_match`.

The partition is per-row hand-curated; the registry comments at `evidence_types.py:367–496` already record the prescribed meta key for each value.

### 2. Dynamic-emit subset: ten values produced via `linkers/websocket.py` f-strings

Ten values do **not** appear as literal `evidence_type="<value>"` strings in any producer file. Instead, they are produced by f-string expansion at two emit sites in `linkers/websocket.py`:

- `linkers/websocket.py:572` — `evidence_type=f"{pattern_type}_emit"` expands to `django_channels_emit`, `fastapi_emit`, `native_emit`, `socketio_emit`, `ws_emit`.
- `linkers/websocket.py:613` — `evidence_type=f"{pattern_type}_endpoint"` expands to `django_channels_endpoint`, `fastapi_endpoint`, `native_endpoint`, `socketio_endpoint`, `ws_endpoint`.

The literal-grep diagnostic test for these ten values returns empty *at filing time* (the dynamic emit is not a literal string), but the producer is still emitting them via f-string expansion. Per-row diagnostic_test for these rows uses the f-string source as the check rather than a literal-grep. The producer fold for these ten values lives in a single edit at `linkers/websocket.py`: rewrite the two f-strings to emit a canonical inference label + `meta["framework_dispatch"]=<pattern_type>` (with `pattern_type` already in scope at both sites). One PR migrates all ten.

This subset is the cleanest single-PR target in the cluster; recommended as the first per-framework sub-PR of Wave 5.

### 3. Multi-row producers: linker files that own multiple Cluster 28C values

Six producer files emit multiple Cluster 28C values and so will own multi-row migrations:

- `linkers/grpc.py` — owns `grpc_go_server_method`, `grpc_rpc_definition`, `grpc_server_to_service`, `grpc_service_match` (4 rows).
- `linkers/openapi.py` — owns `openapi_operation_id_match`, `openapi_path_match` (2 rows; plus `route` Symbol.kind from audit-findings 0013).
- `linkers/tauri_ipc.py` — owns `specta_wrapper_import`, `tauri_emit_listen`, `tauri_invoke` (3 rows; plus four Cluster 27D framework-roles from audit-findings 0013).
- `linkers/wasm_bindgen.py` — owns `wasm_bindgen_import`, `wasm_instantiate` (2 rows).
- `linkers/graphql_resolver.py` — owns `resolver_field_match`, `resolver_type_match` (2 rows; plus `graphql_resolver` Symbol.kind from audit-findings 0013).
- `linkers/ruby_ffi.py` — owns `ruby_c_extension`, `ruby_ffi_attach` (2 rows).
- `linkers/websocket.py` — owns the ten dynamic-emit values (see Diagnostic finding 2).
- `hypergumbo_lang_mainstream/ruby.py` — owns `activerecord_association`, `job_enqueue`, `rails_block_callback`, `rails_callback`, `ruby_delegate` (5 rows).

Per-framework PR-groups (Wave 5 of WI-runod) align to these producer-file owners. Each PR migrates one linker file's full row set, folding both Symbol.kind framework-role values (audit-findings 0013) and Edge.evidence_type framework-dispatch values (this audit) emitted by that file.

### 4. Single-row producers: 30+ frameworks with one row each

The remaining ~40 rows have one producer file each. Examples: `airflow_framework_dispatch.py` owns `airflow_framework_dispatch`; `django_orm_dispatch.py` owns `django_orm_dispatch`; `kafka_streams_dispatch.py` owns `kafka_streams_dispatch`; `jackson_dispatch.py` owns `jackson_bean_dispatch`; `phoenix_ipc.py` owns `phoenix_event_match`; `rust_trait_dispatch.py` owns `rust_trait_dispatch`; `vue_component.py` owns `vue_component_import`. These single-row producers can ship as either: (a) one PR per file, ~40 PRs, very granular; or (b) batched per framework family (e.g., one PR for all django files, one for all wasm/specta files). Wave 5 per-framework PR-groups in WI-runod prefer (b) for amortized review cost.

### 5. Three rows whose registry comment names a specific canonical label

Most Cluster 28C registry comments use the generic phrase "Cluster 28C fold: canonical inference + …". Three rows name a specific canonical inference label:

- `nestjs_module_registration` → `ast_decorator` (registered via NestJS `@Module` decorator).
- `npm_package_import` → "canonical import inference" — a Cluster 28A `ast_import` derivative.
- `phoenix_event_match` → `naming_convention` (Phoenix event names follow a regex pattern; not an AST construct).

For the remaining 62 rows the canonical inference label is left to the per-framework migration PR; the audit's verdict prescribes the meta key but not the precise canonical. This is consistent with audit-findings 0011/0012's pattern of leaving precise per-site fold details to the producer migration.

### 6. `route_mount` is dual-axis: also a Symbol.kind Cluster 27D row

`route_mount` appears in both this cluster (Edge.evidence_type Cluster 28C) AND in audit-findings 0013 Cluster 27D as a `Symbol.kind` framework-role value. The two registry entries are independent (different axes); the per-framework PR-group that migrates `route_mount` on one axis migrates it on the other simultaneously. Same shape applies to several other route/grpc-family values that share names across the two axes — the single-PR cross-axis fold preserves consistency between the Symbol.kind framework_role and the Edge.evidence_type framework_dispatch for any given linker.

### 7. Recurrence-promotion threshold (Open Question 2)

Per ADR-0028 §"Open Question 2" and ADR-0024 §"Fold-residue discipline" rule 3, after Cluster 28C fold the `meta["framework_dispatch"]` key will carry ~30 distinct values across ~30 producer modules. This is **at** the recurrence-promotion threshold (≥30 distinct values across ≥30 producers). If post-migration steady-state monitoring confirms the threshold has tripped, a follow-on ADR promotes `framework_dispatch` from a `meta` key to a dedicated `Edge.framework: str | None` field. This audit does not promote — the open question stays open until Phase 3 ships and post-migration emission counts stabilize. Tracking lives at the parent ADR-0028 issue (see ADR-0028 §"Open questions" #2). The parallel `meta["detection_pattern"]` key with ~9 distinct values is well below the threshold and is not at risk of promotion.

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.evidence_type
verdicts:
  - value: abi_name_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"abi_name_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection. Fold: canonical inference + meta['detection_pattern']='abi_name_match'. Producer: linkers/solidity_abi.py."
  - value: activerecord_association
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"activerecord_association\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Rails ActiveRecord association). Fold: canonical inference + meta['framework_dispatch']='activerecord_association'. Producer: hypergumbo_lang_mainstream/ruby.py."
  - value: airflow_framework_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"airflow_framework_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Airflow DAG / operator). Fold: canonical inference + meta['framework_dispatch']='airflow'. Producer: linkers/airflow_framework_dispatch.py."
  - value: context_bridge_wrapper
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"context_bridge_wrapper\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Electron contextBridge IPC). Fold: canonical inference + meta['framework_dispatch']='electron_context_bridge'. Producer: linkers/ipc.py."
  - value: controller_routes
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"controller_routes\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (controller-pattern routes). Fold: canonical inference + meta['framework_dispatch']='controller_routes'. Producer: linkers/controller_routes.py."
  - value: crypto_api_pattern
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"crypto_api_pattern\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (crypto API call shape). Fold: canonical inference + meta['detection_pattern']='crypto_api'. Producer: linkers/crypto_flow.py."
  - value: cuda_kernel_launch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"cuda_kernel_launch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (CUDA kernel launch via <<<...>>>). Fold: canonical inference + meta['framework_dispatch']='cuda_kernel_launch'. Producer: hypergumbo_lang_common/cuda.py."
  - value: di_binding
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=[\"\\047]di_binding[\"\\047:]' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (DI container binding). Registry placeholder for f-string emit `f\"di_binding:{source}\"` at linkers/di_resolution.py:608. Fold: canonical inference + meta['framework_dispatch']=<binding_source> per-row. Producer: linkers/di_resolution.py."
  - value: django_orm_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"django_orm_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Django ORM queryset). Fold: canonical inference + meta['framework_dispatch']='django_orm'. Producer: linkers/django_orm_dispatch.py."
  - value: django_signal_receiver
    verdict: FOLD
    fold_target: ast_decorator
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"django_signal_receiver\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Django signal receiver via @receiver). Fold: canonical inference + meta['framework_dispatch']='django_signal'. Producer: hypergumbo_lang_mainstream/py.py."
  - value: django_channels_emit
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_emit' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (Django Channels emit). Dynamic emit via f-string at linkers/websocket.py:572 (`f\"{pattern_type}_emit\"`). Fold: canonical inference + meta['framework_dispatch']='django_channels'. Producer migration rewrites the f-string to emit a canonical evidence_type + structured meta."
  - value: django_channels_endpoint
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_endpoint' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (Django Channels endpoint). Dynamic emit via f-string at linkers/websocket.py:613. Fold: canonical inference + meta['framework_dispatch']='django_channels'."
  - value: event_name_match
    verdict: FOLD
    fold_target: naming_convention
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"event_name_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (event-name regex match). Fold: canonical inference + meta['detection_pattern']='event_name'. Producer: linkers/event_sourcing.py."
  - value: fastapi_emit
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_emit' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (FastAPI WebSocket emit). Dynamic emit via f-string at linkers/websocket.py:572. Fold: canonical inference + meta['framework_dispatch']='fastapi'."
  - value: fastapi_endpoint
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_endpoint' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (FastAPI WebSocket endpoint). Dynamic emit via f-string at linkers/websocket.py:613. Fold: canonical inference + meta['framework_dispatch']='fastapi'."
  - value: go_cobra_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"go_cobra_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Go Cobra command). Fold: canonical inference + meta['framework_dispatch']='cobra'. Producer: linkers/go_cobra.py."
  - value: go_memberlist_delegate
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"go_memberlist_delegate\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Hashicorp memberlist delegate). Fold: canonical inference + meta['framework_dispatch']='memberlist'. Producer: linkers/go_memberlist.py."
  - value: graphql_operation_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"graphql_operation_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (GraphQL operation match). Fold: canonical inference + meta['framework_dispatch']='graphql_operation'. Producer: linkers/graphql.py."
  - value: grpc_go_server_method
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"grpc_go_server_method\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Go gRPC server method). Fold: canonical inference + meta['framework_dispatch']='grpc_go_server'. Producer: linkers/grpc.py."
  - value: grpc_rpc_definition
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"grpc_rpc_definition\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (gRPC RPC definition in .proto). Fold: canonical inference + meta['framework_dispatch']='grpc_rpc_definition'. Producer: linkers/grpc.py."
  - value: grpc_server_to_service
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"grpc_server_to_service\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (gRPC server → service binding). Fold: canonical inference + meta['framework_dispatch']='grpc_server_to_service'. Producer: linkers/grpc.py."
  - value: grpc_service_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"grpc_service_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (gRPC service match). Fold: canonical inference + meta['framework_dispatch']='grpc_service_match'. Producer: linkers/grpc.py."
  - value: http_url_match
    verdict: FOLD
    fold_target: naming_convention
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"http_url_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (HTTP URL string match). Fold: canonical inference + meta['detection_pattern']='http_url'. Producer: linkers/http.py."
  - value: implicit_convention
    verdict: FOLD
    fold_target: naming_convention
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"implicit_convention\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (view-template implicit naming). Fold: canonical inference + meta['detection_pattern']='implicit_convention'. Producer: linkers/view_template.py."
  - value: jackson_bean_dispatch
    verdict: FOLD
    fold_target: ast_decorator
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"jackson_bean_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Jackson @JsonProperty / bean serialization). Fold: canonical inference + meta['framework_dispatch']='jackson_bean'. Producer: linkers/jackson_dispatch.py."
  - value: jni_naming_convention
    verdict: FOLD
    fold_target: naming_convention
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"jni_naming_convention\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (JNI Java_<class>_<method> naming). Fold: canonical inference + meta['detection_pattern']='jni_naming_convention'. Producer: linkers/jni.py."
  - value: job_enqueue
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"job_enqueue\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Sidekiq / ActiveJob perform_async). Fold: canonical inference + meta['framework_dispatch']='job_enqueue'. Producer: hypergumbo_lang_mainstream/ruby.py."
  - value: kafka_streams_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"kafka_streams_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Kafka Streams topology). Fold: canonical inference + meta['framework_dispatch']='kafka_streams'. Producer: linkers/kafka_streams_dispatch.py."
  - value: middleware_chain
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"middleware_chain\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Express / Koa middleware .use chain). Fold: canonical inference + meta['framework_dispatch']='middleware_chain'. Producers: linkers/middleware_chain.py, hypergumbo_lang_mainstream/js_ts.py."
  - value: native_emit
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_emit' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (native browser WebSocket .send()). Dynamic emit via f-string at linkers/websocket.py:572. Fold: canonical inference + meta['framework_dispatch']='native_websocket'."
  - value: native_endpoint
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_endpoint' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (native browser WebSocket onmessage). Dynamic emit via f-string at linkers/websocket.py:613. Fold: canonical inference + meta['framework_dispatch']='native_websocket'."
  - value: nestjs_module_registration
    verdict: FOLD
    fold_target: ast_decorator
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"nestjs_module_registration\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (NestJS @Module decorator). Fold: ast_decorator + meta['framework_dispatch']='nestjs_module'. Producer: linkers/di_resolution.py."
  - value: npm_package_import
    verdict: FOLD
    fold_target: ast_import
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"npm_package_import\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (npm package import). Fold: canonical import inference + meta['framework_dispatch']='npm_package'. Producer: linkers/js_module.py."
  - value: openapi_operation_id_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"openapi_operation_id_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (OpenAPI operationId match). Fold: canonical inference + meta['framework_dispatch']='openapi_operation_id'. Producer: linkers/openapi.py."
  - value: openapi_path_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"openapi_path_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (OpenAPI path-template match). Fold: canonical inference + meta['framework_dispatch']='openapi_path'. Producer: linkers/openapi.py."
  - value: orm_accessor_pattern
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"orm_accessor_pattern\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (generic ORM accessor — SQLAlchemy / Django ORM / TypeORM). Fold: canonical inference + meta['framework_dispatch']='orm_accessor'. Producer: linkers/orm.py."
  - value: otp_genserver_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"otp_genserver_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Erlang/Elixir OTP GenServer handle_*). Fold: canonical inference + meta['framework_dispatch']='otp_genserver'. Producer: linkers/otp.py."
  - value: phoenix_event_match
    verdict: FOLD
    fold_target: naming_convention
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"phoenix_event_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (Phoenix event-name regex). Fold: naming_convention + meta['detection_pattern']='phoenix_event'. Producer: linkers/phoenix_ipc.py."
  - value: pyo3_bridge
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"pyo3_bridge\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (PyO3 Rust ↔ Python bridge). Fold: canonical inference + meta['framework_dispatch']='pyo3_bridge'. Producer: linkers/pyffi.py."
  - value: rails_block_callback
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"rails_block_callback\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Rails before_action / after_action block form). Fold: canonical inference + meta['framework_dispatch']='rails_block_callback'. Producer: hypergumbo_lang_mainstream/ruby.py."
  - value: rails_callback
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"rails_callback\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Rails before_action / after_action symbol form). Fold: canonical inference + meta['framework_dispatch']='rails_callback'. Producer: hypergumbo_lang_mainstream/ruby.py."
  - value: registry_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"registry_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (decorator-registry pattern). Fold: canonical inference + meta['framework_dispatch']='registry_dispatch'. Producer: linkers/decorator_dispatch.py."
  - value: resolver_field_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"resolver_field_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (GraphQL resolver field). Fold: canonical inference + meta['framework_dispatch']='graphql_resolver_field'. Producer: linkers/graphql_resolver.py."
  - value: resolver_type_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"resolver_type_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (GraphQL resolver type). Fold: canonical inference + meta['framework_dispatch']='graphql_resolver_type'. Producer: linkers/graphql_resolver.py."
  - value: route_mount
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"route_mount\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (route mount). Fold: canonical inference + meta['framework_dispatch']='route_mount'. Producer: hypergumbo_lang_mainstream/go.py. Cross-axis: also a Symbol.kind framework_role row in audit-findings 0013."
  - value: router_routes
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"router_routes\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (router-pattern routes). Fold: canonical inference + meta['framework_dispatch']='router_routes'. Producer: linkers/router_routes.py."
  - value: ruby_c_extension
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ruby_c_extension\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Ruby C extension via rb_define_method). Fold: canonical inference + meta['framework_dispatch']='ruby_c_extension'. Producer: linkers/ruby_ffi.py."
  - value: ruby_delegate
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ruby_delegate\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Ruby delegate / Forwardable). Fold: canonical inference + meta['framework_dispatch']='ruby_delegate'. Producer: hypergumbo_lang_mainstream/ruby.py."
  - value: ruby_ffi_attach
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ruby_ffi_attach\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Ruby FFI attach_function). Fold: canonical inference + meta['framework_dispatch']='ruby_ffi_attach'. Producer: linkers/ruby_ffi.py."
  - value: rust_trait_dispatch
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"rust_trait_dispatch\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Rust trait method dispatch). Fold: canonical inference + meta['framework_dispatch']='rust_trait_dispatch'. Producer: linkers/rust_trait_dispatch.py."
  - value: script_src
    verdict: FOLD
    fold_target: ast_import
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"script_src\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (HTML <script src=…>). Fold: canonical import inference + meta['framework_dispatch']='html_script_src'. Producer: hypergumbo_lang_mainstream/html.py."
  - value: socketio_emit
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_emit' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (Socket.IO emit). Dynamic emit via f-string at linkers/websocket.py:572. Fold: canonical inference + meta['framework_dispatch']='socketio'."
  - value: socketio_endpoint
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_endpoint' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (Socket.IO endpoint). Dynamic emit via f-string at linkers/websocket.py:613. Fold: canonical inference + meta['framework_dispatch']='socketio'."
  - value: specta_wrapper_import
    verdict: FOLD
    fold_target: ast_import
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"specta_wrapper_import\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Tauri Specta wrapper import). Fold: canonical import inference + meta['framework_dispatch']='specta_wrapper'. Producer: linkers/tauri_ipc.py."
  - value: subprocess_cli_match
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"subprocess_cli_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (subprocess argv binary-name match). Fold: canonical inference + meta['detection_pattern']='subprocess_cli'. Producer: linkers/subprocess_cli.py."
  - value: table_name_match
    verdict: FOLD
    fold_target: naming_convention
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"table_name_match\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Pattern-detection (DB table-name string match). Fold: canonical inference + meta['detection_pattern']='table_name'. Producer: linkers/database_query.py."
  - value: tauri_emit_listen
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"tauri_emit_listen\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Tauri emit/listen IPC). Fold: canonical inference + meta['framework_dispatch']='tauri_emit_listen'. Producer: linkers/tauri_ipc.py."
  - value: tauri_invoke
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"tauri_invoke\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Tauri invoke command). Fold: canonical inference + meta['framework_dispatch']='tauri_invoke'. Producer: linkers/tauri_ipc.py."
  - value: vue_component_import
    verdict: FOLD
    fold_target: ast_import
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"vue_component_import\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Vue component import in <template>). Fold: canonical import inference + meta['framework_dispatch']='vue_component'. Producer: linkers/vue_component.py."
  - value: vue_event_handler
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"vue_event_handler\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Vue @event handler binding). Fold: canonical inference + meta['framework_dispatch']='vue_event_handler'. Producer: linkers/vue_template_method.py."
  - value: wasm_bindgen_import
    verdict: FOLD
    fold_target: ast_import
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"wasm_bindgen_import\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (wasm-bindgen extern import). Fold: canonical import inference + meta['framework_dispatch']='wasm_bindgen_import'. Producer: linkers/wasm_bindgen.py."
  - value: wasm_instantiate
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"wasm_instantiate\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (WebAssembly.instantiate). Fold: canonical inference + meta['framework_dispatch']='wasm_instantiate'. Producer: linkers/wasm_bindgen.py."
  - value: ws_emit
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_emit' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (`ws` Node.js WebSocket emit). Dynamic emit via f-string at linkers/websocket.py:572. Fold: canonical inference + meta['framework_dispatch']='ws'."
  - value: ws_endpoint
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -n 'pattern_type}_endpoint' packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py"
      expect: empty
    rationale: "Framework dispatch (`ws` Node.js WebSocket endpoint). Dynamic emit via f-string at linkers/websocket.py:613. Fold: canonical inference + meta['framework_dispatch']='ws'."
  - value: yjs_crdt_pattern
    verdict: FOLD
    fold_target: ast_call_direct
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"yjs_crdt_pattern\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Framework dispatch (Yjs CRDT update pattern). Fold: canonical inference + meta['framework_dispatch']='yjs_crdt'. Producer: linkers/yjs_crdt.py."
```

## Migration impact

- **Producer-side this PR:** zero — doc-only filing. Per-framework Phase 3 sub-PRs (Wave 5 of WI-runod schedule) ship the producer migration grouped with the parallel ADR-0027 Cluster 27D framework-role fold (audit-findings 0013) for each framework's linker file.
- **Linker-side this PR:** zero. The L3 producer-side coherence linter (`scripts/check-producer-axis-coherence`) already enforces axis discipline; per-framework PRs use it as the gate.
- **Registry-side this PR:** zero. The 65 Cluster 28C registry entries stay on `AXIS_ENDPOINT_SHAPE` through the Phase 4a deprecation window per ADR-0028 §"Phase 4". Phase 4b prunes them piecewise as each framework's `awaits_bakeoff_validation` clears; expect 5-7 sub-ships across the framework-family clusters.
- **Schema-side:** no change. The open enum on `Edge.evidence_type` already accommodates the additive change. The new `meta["framework_dispatch"]` and `meta["detection_pattern"]` keys are documented in the `Edge.meta` open-form section of the schema; no per-key registration required at audit-filing time. The recurrence-promotion threshold for `framework_dispatch` (Open Question 2) is tracked at the parent ADR.
- **Consumer-side:** no immediate change. Consumer enumerations of `Edge.evidence_type == "<framework_dispatch_value>"` migrate to `Edge.evidence_type in {<canonical>} and Edge.meta.get("framework_dispatch") == "<value>"` in Phase 4 follow-on. This audit does not gate the consumer migration; per-framework sub-PRs may opportunistically migrate consumer call sites that are local to the framework's scope.
- **Cross-axis coupling:** Wave 5 of WI-runod ships per-framework PR-groups that fold both axes simultaneously. The same linker files emit both Edge.evidence_type framework_dispatch values (this audit) and Symbol.kind framework_role values (audit-findings 0013). Coordinating both folds in one PR per framework halves producer churn vs. shipping the two axes' migrations as separate sweeps.

## Related

- [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) — declares the `Edge.evidence_type` axis this audit applies. §"Detailed analysis" Cluster 28C and §"Phase 3" Cluster 28C are the load-bearing references; §"Resolution" rule 3 names `meta["framework_dispatch"]` / `meta["detection_pattern"]` as the fold-residue convention; §"Open question 2" tracks the recurrence-promotion threshold for `framework_dispatch`.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0028 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy. §"Fold-residue discipline" rule 3 names the recurrence-promotion threshold relevant for `meta["framework_dispatch"]`.
- [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` precedent for framework-dispatch-leakage cleanup. ADR-0023's dispatch / publish / IPC family deprecations are the structural template this audit's `Edge.evidence_type` resolution applies on the parallel axis. Audit-findings 0001 and 0002 are the worked examples.
- Audit-findings 0004 — Cluster 28A canonical inference pathways. The registry seed for the canonical labels each Cluster 28C row folds into.
- Audit-findings 0008 — Cluster 28B resolution-status leakage. Three of its PRELIM_RESOLVED rows fold to Cluster 28D peer values that audit-findings 0012 then re-folds to `ast_call`; this audit's framework-dispatch fold preserves any `is_resolved=False` status set by the canary Phase 3 sub-PR.
- Audit-findings 0012 — Cluster 28D apex/peer call-construct fold. The structural template for cross-cluster fold-residue accounting; this audit's `meta["framework_dispatch"]` is parallel to that audit's `meta["call_construct"]`.
- Audit-findings 0013 — Cluster 27D framework roles on `Symbol.kind`; the cross-axis companion. Wave 5 per-framework sub-PRs migrate both axes at once.
- WI-runod cross-axis schedule (discussion entry 2026-05-05) — Wave 5 framework-dispatch coordinated pair; this audit closes the verdict-table layer of the Edge.evidence_type half.
