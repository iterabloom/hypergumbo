<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0013: Symbol.kind Cluster 27D — Framework Roles

- Date: 2026-05-05 (filed); 2026-05-06 corrections (WI-nitil); 2026-05-18 amendment (INV-mopif — `http_client` fold-target harmonisation)
- Status: All RESOLVED — Phase 3 producer migration complete across all 33 in-scope rows. Wave 5 of WI-runod (six PRs across Codeberg #3572 and selfh #162-#166) shipped the framework_role fold for active producers; WI-nitil corrected four assignment-form misses (gRPC + MQ) and added three previously-unregistered values (`grpc_service`, `grpc_servicer`, `grpc_client`); the `phoenix_ipc.py` f-string-form Symbol.kind producer (selfh PR #174) closed the final f-string blind spot. Empirical re-grep finds zero live `Symbol(kind=<value>)` producers across all rows. Values subsequently advanced PRELIM_RESOLVED → RESOLVED at SCHEMA_VERSION 0.6.0 when the `endpoint_shape` parking axis was retired and the 71 values removed from `SYMBOL_KINDS` (ADR-0027 §"Phase 4" closure).
- 2026-05-06 corrections (WI-nitil): the original literal-grep diagnostic test (`grep -rn 'kind=["\047]<value>["\047]'`) only catches kwarg-form producers like `Symbol(kind="mq_publisher", ...)` and missed assignment-form producers like `kind = "mq_publisher" if ... else "mq_subscriber"` followed by `Symbol(kind=kind, ...)`. Re-sweep with a broader pattern (`kind\s*=\s*["\047]<value>["\047]`) found four assignment-form producers — `grpc_server` and `grpc_stub` at `linkers/grpc.py:660,663`, `mq_publisher` and `mq_subscriber` at `linkers/message_queue.py:410`. Those four rows are corrected to UNRESOLVED below. The other five originally-PRELIM_RESOLVED rows (`ipc_subscriber`, `websocket_emitter`, `websocket_listener`, `rpc`, `service`) re-verified as zero-producer; they retain PRELIM_RESOLVED. Three additional values emitted by the same `linkers/grpc.py` block but absent from the registry — `grpc_service`, `grpc_servicer`, `grpc_client` — are added as new verdict rows below. The L3 producer-coherence linter only flags literal-string kwargs (`Symbol(kind="literal", ...)`) and so cannot catch the assignment-form gap; closing that gap is tracked as a follow-on linter improvement.
- **2026-07-06 correction (WI-rilal / WI-zipis): the 2026-05-06 re-sweep's "zero-producer" verdict for `rpc` and `service` was itself a false negative of the very class it corrected.** Both the literal-grep AND the broader assignment-form grep miss the **positional-helper** form — `proto.py` `_make_proto_symbol(..., "rpc"/"service")`, `thrift.py` `make_symbol(..., "service")`, `smithy.py` `_extract_shape(..., "service")` — where a raw literal is passed *positionally* into a helper whose parameter reaches the `Symbol(kind=...)` constructor. A fresh diverse BROAD cohort (kong `.proto`) surfaced live `kind='rpc'` / `'service'` on real repos. The authoritative diagnostic for this form is the WI-zipis L3 producer-coherence **descend sweep** (module-local helper-sink discovery + positional binding); the literal grep in each row below stays as-is but is INSUFFICIENT for helper-routed producers. The `rpc` and `service` rows were doc-`RESOLVED` but producer-`UNRESOLVED`; their fold is now **executed** — `rpc`→`method` (a service member, per §Methodology's member→method rule), `service`→`interface`, each with `meta['framework_role']`, across proto/thrift/smithy.
- Closes: WI-habut-diziv-jahuv-gimub-kipus-rosaj-nukol-gujil (Cluster 27D, ADR-0027 Phase 3) at the verdict-table layer. Producer-side migration ships piecewise as per-framework sub-PRs (Wave 5 of WI-runod schedule), each carrying its own `awaits_bakeoff_validation` tag.
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Eighth audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster 27A canonical), 0005 (Cluster 27B file-shape), 0006 (Cluster 27G build/config), 0007 (Cluster 27H domain long-tail), 0009 (Cluster 27C apex/peer), 0010 (Cluster 27E edge-label leakage), and 0011 (Cluster 27F component refs).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Detailed analysis: per-cluster fold targets" Cluster 27D is the framework-role cluster of `Symbol.kind`: ~30 values that name **the symbol's participation in a framework pattern** rather than what the symbol *is* in its source language. ADR-0027 §"Resolution" prescribes a uniform fold: each value collapses to a canonical Cluster 27A construct (`function` or `method` for callables, `interface`/`class` for declarations, `reference` for cross-symbol pointers) plus `meta["framework_role"]=<value>`. ADR-0023 caught the same shape on `Edge.edge_type` for its dispatch / publish / IPC families; this audit applies the parallel `Symbol.kind` resolution.

The 30 values ADR-0027 Phase 1 seeded on `AXIS_ENDPOINT_SHAPE` (registry lines 186–251):

```
event_publisher, event_subscriber, ipc_publisher, ipc_subscriber, ipc_caller,
ipc_bridge_caller, ipc, objc_bridge, crypto_producer, crypto_consumer,
message_sender, message_handler, mq_publisher, mq_subscriber, grpc_server,
grpc_stub, websocket_endpoint, websocket_emitter, websocket_listener,
dispatcher, graphql_resolver, graphql_client, http_client, route_mount, route,
route_include, openapi_operation, abi_call, selector_ref, rpc, service
```

`abi_call` is intentionally absent from this audit's verdict list. Audit-findings 0010 sub-case (a) reclassified it from Cluster 27D into Cluster 27E and folded it to `kind="call_site"` + `meta["call_kind"]="abi"` — the Solidity ABI emit site names a call expression, not a framework participation. The registry entry stays on `endpoint_shape` through the Phase 4a deprecation window per audit-findings 0010; this doc does not re-file the verdict.

This audit answers two questions:

1. **Per-value verdicts.** All 29 in-scope values FOLD. The fold target is `function` or `method` for callables (the dominant case), `interface` or `class` for declarations (`service`), or `reference` for cross-symbol pointers (`selector_ref`). Per-row choice between `function` and `method` is decided at producer migration time based on the symbol's actual construct site (member of a class → `method`; free function → `function`).
2. **Filing-time status per row.** Per-framework Phase 3 sub-PRs ship one at a time, so most rows arrive at this audit with active producers (UNRESOLVED). Five values have zero producers in the codebase (after the 2026-05-06 WI-nitil broader-grep re-sweep) — `ipc_subscriber`, `websocket_emitter`, `websocket_listener`, `rpc`, `service`. These are registry placeholders without active emit sites; their producer migration is trivially complete and the rows ship PRELIM_RESOLVED. Four rows originally listed as zero-producer (`mq_publisher`, `mq_subscriber`, `grpc_server`, `grpc_stub`) were misclassified at filing because the literal-grep diagnostic missed assignment-form producers; corrected to UNRESOLVED below.

**No new axis ADR required.** The four-leakage-test pass for each of the 29 in-scope values fired uniformly on Test 1 (property derivability) and Test 4 (mechanism vs. category). Each value encodes either "this symbol participates in framework pattern X" (Test 4: mechanism, not category) or "this symbol has an outgoing/incoming edge of type Y" (Test 1: derivable from `(edge_type, dst.kind)` queries per the ADR-0023 pattern). Both are exactly the leak that `meta["framework_role"]` absorbs.

## Methodology

Per [ADR-0027 §"Phase 3" Cluster 27D](../adr/0027-symbol-kind-language-construct-only.md). Each value's verdict applies the four leakage tests from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md). Tests 1 (property derivability) and 4 (mechanism vs. category) are the load-bearing tests for this cluster: every value names a framework-participation property of the symbol, not a syntactic-construct kind, and the participation is derivable from the symbol's edges plus framework metadata.

The audit's empirical scope is `Symbol(kind=...)` emissions only. The producer count below is the count of `Symbol(kind="<value>", ...)` literal-grep matches across `packages/`, excluding test files and the registry module.

Producer-side migration: each emit site replaces `kind="<value>"` with `kind="function"` (or `"method"`, depending on the site's construct) plus `meta["framework_role"]=<value>`. Three rows take a different canonical: `service` folds to `interface` or `class`; `selector_ref` folds to `reference`; the rest fold to `function`/`method`. The L3 producer-side coherence linter (`scripts/check-producer-axis-coherence`) catches drift at pre-commit.

This audit-findings doc is **doc-only at filing time**: no producer code is migrated in this PR. Per-framework sub-PRs (Wave 5 of WI-runod schedule) ship the producer migration grouped with the parallel ADR-0028 Cluster 28C framework-dispatch fold for each framework's linker file (per audit-findings 0014). Each sub-PR carries its own `awaits_bakeoff_validation` tag per the validation-tagging discipline.

## Diagnostic findings

### 1. The cluster decomposes into six framework families

The 29 in-scope values cluster naturally into six framework families, each owned by one or two producer linker files:

- **Event / messaging** (~5 values: `event_publisher`, `event_subscriber`, `message_sender`, `message_handler`, `dispatcher`). Producers: `linkers/annotation_convention.py`, `linkers/message_dispatch.py`, `linkers/tauri_ipc.py`, `linkers/yjs_crdt.py`.
- **IPC** (~5 values: `ipc_publisher`, `ipc_subscriber`, `ipc_caller`, `ipc_bridge_caller`, `ipc`). Producers: `linkers/ipc.py`, `linkers/tauri_ipc.py`.
- **Crypto flow** (2 values: `crypto_producer`, `crypto_consumer`). Producer: `linkers/crypto_flow.py`.
- **gRPC / RPC / service** (4 in-scope + 3 unregistered: `grpc_server`, `grpc_stub`, `rpc`, `service`, plus assignment-form-only `grpc_service`, `grpc_servicer`, `grpc_client`). Producers: `linkers/grpc.py` lines 654-663 emit all five gRPC values via assignment form (`kind = "grpc_..."`), then pass `kind=kind` to the `Symbol` constructor. The original literal-grep diagnostic only matched kwarg form `Symbol(kind="grpc_server", ...)` and missed all five. `rpc` and `service` remain registry placeholders with no producers.
- **WebSocket** (3 values: `websocket_endpoint`, `websocket_emitter`, `websocket_listener`). Producer: `linkers/websocket.py` for `websocket_endpoint` only; the emitter and listener variants are registry placeholders.
- **HTTP / GraphQL / OpenAPI / route** (8 values: `graphql_resolver`, `graphql_client`, `http_client`, `route_mount`, `route`, `route_include`, `openapi_operation`, plus `mq_publisher` / `mq_subscriber` as message-queue placeholders). Producers: `linkers/graphql.py`, `linkers/graphql_resolver.py`, `linkers/http.py`, `linkers/openapi.py`, `linkers/route_handler.py`, `linkers/annotation_convention.py`, plus `route` emits scattered across mainstream language analyzers (`go.py`, `js_ts.py`, `php.py`, `play_routes.py`, `py.py`, `ruby.py`, `swift.py`, `elixir.py`).
- **ObjC / cross-language bridge** (2 values: `objc_bridge`, `selector_ref`). Producer: `linkers/swift_objc.py`.

Per-framework PR-groups (Wave 5 of WI-runod) align to these families. Each PR migrates one family's producer file(s), folding both the Symbol.kind framework-role values *and* the parallel Edge.evidence_type framework-dispatch values (audit-findings 0014) emitted by the same linker.

### 2. `service` and `selector_ref` use non-default canonical kinds

Twenty-six of the 29 in-scope values fold to `function` or `method` (the choice picked at the producer site by inspecting whether the symbol is a class member). Three values use a different canonical:

- **`service`** → `interface` or `class`. A service declaration (gRPC `service Foo {…}`, Kubernetes `Service`) is a type/interface, not a callable. Per-row choice between `interface` and `class` is producer-site-dependent.
- **`selector_ref`** → `reference`. The `_ref` suffix names the *use* of a selector at a call site, not the selector definition itself. Same shape as audit-findings 0011 §"Diagnostic findings" #3 caught for `component_ref` — but here the parallel resolution from audit-findings 0010 (DEPRECATE-NO-FOLD on `reference`) does **not** apply, because the registry's `reference` Symbol kind is on Cluster 27A `language_construct` (an actual cross-symbol pointer in source languages like Rust `&foo`), not the Cluster 27E edge-label-shadow `reference` that audit-findings 0010 deprecated. The two `reference` values are distinct registry entries with distinct semantics.
- **`abi_call`** (excluded) → already resolved per audit-findings 0010 sub-case (a): `kind="call_site"` + `meta["call_kind"]="abi"`. Not re-filed here.

### 3. Five values have zero producers (after 2026-05-06 broader-grep re-sweep)

Five registry entries have no producers (literal- or assignment-form) in `packages/` outside of tests:

```
ipc_subscriber, websocket_emitter, websocket_listener, rpc, service
```

These are forward-looking registry placeholders — values seeded by ADR-0027 Phase 1 because the per-cluster scan found them in earlier code revisions or because the cluster's symmetry suggested them, but no producer currently emits them. For these rows the producer migration is trivially complete (zero edits needed). They ship at status PRELIM_RESOLVED; the registry entry remains on `endpoint_shape` through the Phase 4a deprecation window per ADR-0027 §"Phase 4". Phase 4b prunes the registry entry along with the rest of Cluster 27D after bakeoff validation clears.

**Filing-time miscount, corrected 2026-05-06 (WI-nitil).** The original list was nine rows: it added `mq_publisher`, `mq_subscriber`, `grpc_server`, `grpc_stub` because the literal-grep diagnostic test (`grep -rn 'kind=["\047]<value>["\047]'`) found no kwarg-form matches. All four have assignment-form producers (`linkers/message_queue.py:410` for the mq pair, `linkers/grpc.py:660,663` for the grpc pair) where the `Symbol(kind=...)` constructor receives a variable, not a literal. The broader pattern `kind\s*=\s*["\047]<value>["\047]` catches them. Their rows below are now UNRESOLVED; producer migration ships in this same WI-nitil PR. Three additional values — `grpc_service`, `grpc_servicer`, `grpc_client` — were emitted by the same `linkers/grpc.py` block but absent from the registry entirely; verdict rows for them are added below and they migrate in the same PR.

If a future analyzer adds a producer for one of the five remaining placeholders, the row's status reverts to UNRESOLVED until that producer migrates. The diagnostic test now uses the broader assignment-aware pattern.

### 4. The `route` emit-site list is the largest blast radius in this cluster

The bare value `kind="route"` is emitted from twelve files: `linkers/annotation_convention.py`, `linkers/grpc.py`, `linkers/http.py`, `linkers/openapi.py`, `linkers/route_handler.py`, `cli.py`, `entrypoints.py`, `framework_patterns.py`, plus the mainstream language analyzers (`go.py`, `js_ts.py`, `php.py`, `play_routes.py`, `py.py`, `ruby.py`, `swift.py`, `elixir.py`). This is the highest-blast-radius value in the cluster, and its per-framework PR-group fans out across many files. The Wave 5 schedule recommends shipping it as either: (a) a single coordinated PR touching all twelve files, or (b) split per-framework (HTTP route handlers, gRPC routes, OpenAPI routes, etc.) — preference (b) per WI-runod's "smallest blast radius first" principle.

### 5. `meta["framework_role"]` recurrence-promotion threshold

Per ADR-0027 §"Phase 4" and ADR-0024 §"Fold-residue discipline" rule 3, `meta["framework_role"]` is the load-bearing fold-residue key for this cluster (29 distinct values across ~15 producer linkers after migration). The recurrence-promotion threshold (≥30 distinct values across ≥30 producers) is borderline. ADR-0027 Open Question 4 ("Coordination with Symbol.meta schema") tracks whether `framework_role` should subsequently promote to a dedicated `Symbol.framework_role: str | None` field. This audit does not promote it — Phase 3 follow-on work tracks meta-key emission counts and files a follow-on ADR if the threshold fires post-migration.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: event_publisher
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]event_publisher[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='event_publisher'. Producers: linkers/annotation_convention.py, linkers/tauri_ipc.py, linkers/yjs_crdt.py."
  - value: event_subscriber
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]event_subscriber[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='event_subscriber'. Producers: linkers/annotation_convention.py, linkers/tauri_ipc.py, linkers/yjs_crdt.py."
  - value: ipc_publisher
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]ipc_publisher[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='ipc_publisher'. Producer: linkers/tauri_ipc.py."
  - value: ipc_subscriber
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]ipc_subscriber[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='ipc_subscriber'. Registry placeholder — no producer emits this value at filing time; producer migration trivially complete."
  - value: ipc_caller
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]ipc_caller[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='ipc_caller'. Producer: linkers/tauri_ipc.py."
  - value: ipc_bridge_caller
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]ipc_bridge_caller[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='ipc_bridge_caller'. Producer: linkers/ipc.py."
  - value: ipc
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]ipc[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (generic IPC endpoint). Fold: kind=function/method + meta['framework_role']='ipc'. Producer: linkers/ipc.py."
  - value: objc_bridge
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]objc_bridge[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (Objective-C bridge call). Fold: kind=function/method + meta['framework_role']='objc_bridge'. Producer: linkers/swift_objc.py."
  - value: crypto_producer
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]crypto_producer[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (crypto-flow producer). Fold: kind=function/method + meta['framework_role']='crypto_producer'. Producer: linkers/crypto_flow.py."
  - value: crypto_consumer
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]crypto_consumer[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (crypto-flow consumer). Fold: kind=function/method + meta['framework_role']='crypto_consumer'. Producer: linkers/crypto_flow.py."
  - value: message_sender
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]message_sender[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='message_sender'. Producer: linkers/message_dispatch.py."
  - value: message_handler
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]message_handler[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='message_handler'. Producer: linkers/message_dispatch.py."
  - value: mq_publisher
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]mq_publisher[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='mq_publisher'. Producer: linkers/message_queue.py:410 (assignment-form `kind = ...if pattern.type == 'publish' else ...`). Originally PRELIM_RESOLVED at 2026-05-05 filing; corrected to UNRESOLVED 2026-05-06 (WI-nitil) — the literal-grep diagnostic missed assignment-form producers."
  - value: mq_subscriber
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]mq_subscriber[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='mq_subscriber'. Producer: linkers/message_queue.py:410 (assignment-form ternary, sibling of mq_publisher). Originally PRELIM_RESOLVED at 2026-05-05 filing; corrected to UNRESOLVED 2026-05-06 (WI-nitil)."
  - value: grpc_server
    verdict: FOLD
    fold_target: class
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]grpc_server[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (gRPC server class). Fold target is class (not function/method) — the producer at linkers/grpc.py:663 names a server-side service-implementation class, not a method. Fold: kind=class + meta['framework_role']='grpc_server'. Originally PRELIM_RESOLVED at 2026-05-05 filing; corrected to UNRESOLVED 2026-05-06 (WI-nitil) — the literal-grep diagnostic missed the assignment-form producer at linkers/grpc.py:663."
  - value: grpc_stub
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]grpc_stub[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (gRPC stub call site). Fold: kind=function + meta['framework_role']='grpc_stub'. Producer: linkers/grpc.py:660 (assignment-form ternary `kind = 'grpc_stub' if pattern.type == 'stub' else 'grpc_client'`). Originally PRELIM_RESOLVED at 2026-05-05 filing; corrected to UNRESOLVED 2026-05-06 (WI-nitil)."
  - value: grpc_service
    verdict: FOLD
    fold_target: interface
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]grpc_service[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (gRPC `service Foo {...}` proto declaration). Fold target is interface (a service definition is a type, not a callable). Fold: kind=interface + meta['framework_role']='grpc_service'. Producer: linkers/grpc.py:655. Added 2026-05-06 (WI-nitil) — assignment-form producer absent from the original audit's literal-grep scope and absent from the registry; not registered separately because the value migrates to the canonical `interface` kind in the same PR."
  - value: grpc_servicer
    verdict: FOLD
    fold_target: class
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]grpc_servicer[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (gRPC servicer class — Python `class FooServicer(...)`). Fold target is class. Fold: kind=class + meta['framework_role']='grpc_servicer'. Producer: linkers/grpc.py:657. Added 2026-05-06 (WI-nitil) — assignment-form producer absent from the original audit and registry; folds to canonical `class` in the same PR."
  - value: grpc_client
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'kind\\s*=\\s*[\"\\047]grpc_client[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (gRPC client call site, sibling of grpc_stub). Fold: kind=function + meta['framework_role']='grpc_client'. Producer: linkers/grpc.py:660 (assignment-form ternary). Added 2026-05-06 (WI-nitil) — assignment-form producer absent from the original audit and registry; folds to canonical `function` in the same PR."
  - value: websocket_endpoint
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]websocket_endpoint[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='websocket_endpoint'. Producer: linkers/websocket.py."
  - value: websocket_emitter
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]websocket_emitter[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='websocket_emitter'. Registry placeholder — no producer emits this value at filing time."
  - value: websocket_listener
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]websocket_listener[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='websocket_listener'. Registry placeholder — no producer emits this value at filing time."
  - value: dispatcher
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]dispatcher[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (generic dispatcher symbol). Fold: kind=function/method + meta['framework_role']='dispatcher'. Producer: linkers/annotation_convention.py."
  - value: graphql_resolver
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]graphql_resolver[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='graphql_resolver'. Producer: linkers/graphql_resolver.py."
  - value: graphql_client
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]graphql_client[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='graphql_client'. Producer: linkers/graphql.py."
  - value: http_client
    verdict: FOLD
    fold_target: call_site
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]http_client[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (HTTP client call site). Fold: kind=call_site + meta['call_kind']='http' + meta['framework_role']='http_client'. Producer: linkers/http.py. **2026-05-18 amendment (INV-mopif)**: the original 2026-05-05 verdict listed fold_target=function, which was internally inconsistent with audit-findings 0010 sub-case (a) — the latter had already added call_site to AXIS_LANGUAGE_CONSTRUCT (symbol_kinds.py:156) precisely for the 'call expression as syntactic construct' shape and migrated abi_call / function_call / subprocess_call / db_query onto it. The function fold gave http_client Symbols names like 'GET event.request' (non-identifier) and made hypergumbo dead-code-maybe flag live fetch() calls as dead functions (the production service-worker.js:54 case INV-mopif filed). Harmonised fold preserves both meta keys: call_kind names the syntactic-construct specialisation (sibling of db_query/subprocess/abi); framework_role names the framework-participation residue (audit-0013 convention). The two carry orthogonal information."
  - value: route_mount
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]route_mount[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='route_mount'. Producer: hypergumbo_lang_mainstream/go.py."
  - value: route
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]route[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (route declaration). Fold: kind=function/method + meta['framework_role']='route'. Highest blast radius in cluster: producers across linkers/annotation_convention.py, linkers/grpc.py, linkers/http.py, linkers/openapi.py, linkers/route_handler.py, cli.py, entrypoints.py, framework_patterns.py, plus mainstream language analyzers (go.py, js_ts.py, php.py, play_routes.py, py.py, ruby.py, swift.py, elixir.py)."
  - value: route_include
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]route_include[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='route_include'. Producer: hypergumbo_lang_mainstream/play_routes.py."
  - value: openapi_operation
    verdict: FOLD
    fold_target: function
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]openapi_operation[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation. Fold: kind=function/method + meta['framework_role']='openapi_operation'. Producer: linkers/openapi.py."
  - value: selector_ref
    verdict: FOLD
    fold_target: reference
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]selector_ref[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (ObjC selector reference at a call site). The _ref suffix names the use, not the definition. Fold: kind=reference + meta['framework_role']='selector_ref'. Producer: linkers/swift_objc.py. Note: the registry's reference Symbol kind on Cluster 27A language_construct is a distinct entry from the Cluster 27E edge-label-shadow reference deprecated in audit-findings 0010 sub-case (b); this fold targets the Cluster 27A canonical."
  - value: rpc
    verdict: FOLD
    fold_target: method
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]rpc[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (RPC method declaration, a member of a service). Fold: kind=method + meta['framework_role']='rpc'. 2026-07-06 correction (WI-rilal / WI-zipis): the original 'registry placeholder — no producer' claim was FALSE — proto.py emitted kind='rpc' via the positional helper _make_proto_symbol the grep diagnostics missed (same blind-spot class this audit documents for 'message'). fold_target resolved to method per the §Methodology member->method rule. Producer migration EXECUTED (proto.py: rpc->method + meta['framework_role']='rpc'). The literal-grep above stays empty — it never matched the positional form — so the WI-zipis descend sweep is the authoritative check."
  - value: service
    verdict: FOLD
    fold_target: interface
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]service[\"\\047]' packages/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Framework participation (gRPC / thrift / smithy service declaration). Fold target is interface (a service is a type declaration, not a callable). Fold: kind=interface + meta['framework_role']='service'. 2026-07-06 correction (WI-rilal / WI-zipis): the original 'registry placeholder — no producer' claim was FALSE — proto.py, thrift.py, and smithy.py all emitted kind='service' via positional helpers (_make_proto_symbol / make_symbol / _extract_shape) invisible to the literal- and assignment-form greps. Producer migration EXECUTED across all three (service->interface + meta['framework_role']='service'). The literal-grep above stays empty — it never matched the positional form — so the WI-zipis descend sweep is the authoritative check."
```

`abi_call` is intentionally absent — its verdict and disposition are recorded in audit-findings 0010 sub-case (a) (`FOLD → call_site + meta['call_kind']='abi'`). Tracking it here would dual-file the verdict.

## Migration impact

- **Producer-side this PR:** zero — doc-only filing. Per-framework Phase 3 sub-PRs (Wave 5 of WI-runod schedule) ship the producer migration grouped with the parallel ADR-0028 Cluster 28C framework-dispatch fold (audit-findings 0014) for each framework's linker file.
- **Linker-side this PR:** zero. The L3 producer-side coherence linter (`scripts/check-producer-axis-coherence`) already enforces axis discipline; per-framework PRs use it as the gate.
- **Registry-side this PR:** zero. The 30 Cluster 27D registry entries (29 in-scope + `abi_call`) stay on `AXIS_ENDPOINT_SHAPE` through the Phase 4a deprecation window per ADR-0027 §"Phase 4". Phase 4b prunes them piecewise as each framework's `awaits_bakeoff_validation` clears.
- **Schema-side:** no change. The open enum on `Symbol.kind` already accommodates the additive change. The new `meta["framework_role"]` key is documented in the `Symbol.meta` open-form section of the schema; no per-key registration required at audit-filing time.
- **Consumer-side:** no immediate change. Consumer enumerations of `Symbol.kind == "<framework_role>"` migrate to `Symbol.kind in {"function","method"} and Symbol.meta.get("framework_role") == "<value>"` in a Phase 4 follow-on (ADR-0027 §"Phase 4b"). This audit does not gate the consumer migration; per-framework sub-PRs may opportunistically migrate consumer call sites that are local to the framework's scope.
- **Cross-axis coupling:** Wave 5 of WI-runod ships per-framework PR-groups that fold both axes simultaneously. The same linker files emit both Symbol.kind framework_role values (this audit) and Edge.evidence_type framework_dispatch values (audit-findings 0014). Coordinating both folds in one PR per framework halves producer churn vs. shipping the two axes' migrations as separate sweeps.

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — declares the `Symbol.kind` axis this audit applies. §"Detailed analysis" Cluster 27D and §"Phase 3" Cluster 27D are the load-bearing references; §"Resolution" rule 2 names `meta["framework_role"]` as the fold-residue convention.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0027 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy. §"Fold-residue discipline" rule 3 names the recurrence-promotion threshold relevant for `meta["framework_role"]`.
- [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` precedent for framework-role-leakage cleanup. ADR-0023's dispatch / publish / IPC family deprecations are the structural template this audit's `Symbol.kind` resolution applies on the parallel axis.
- Audit-findings 0010 — Cluster 27E sub-case (a) for `abi_call` (excluded from this audit's verdicts; resolved as `kind="call_site"` + `meta["call_kind"]="abi"`).
- Audit-findings 0011 — Cluster 27F component-ref pattern, parallel `_ref` suffix shape; the structural template for `selector_ref`'s fold-to-`reference`.
- Audit-findings 0014 — Cluster 28C framework-dispatch on `Edge.evidence_type`; the cross-axis companion. Wave 5 per-framework sub-PRs migrate both axes at once.
- WI-runod cross-axis schedule (discussion entry 2026-05-05) — Wave 5 framework-dispatch coordinated pair; this audit closes the verdict-table layer of the Symbol.kind half.
