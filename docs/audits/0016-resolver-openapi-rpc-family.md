<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0016: Resolver / OpenAPI / RPC Family Classifications

- Date: 2026-07-20 (verdicts) · 2026-07-21 (folded + pruned, WI-pusuv Option B)
- Status: All RESOLVED (producer-migrated + registry entries pruned; the `pending_classification` axis of `Edge.edge_type` is now empty)
- Closes: WI-sumik (resolver/OpenAPI/RPC `pending_classification` audit) — the audit deliverable; the FOLD migrations shipped as Batches A/B + the consolidated Phase-4b prune (WI-pusuv Option B)
- Sibling: [audit-findings 0001](0001-dispatch-publish-family.md) / [0002](0002-ipc-family.md) — same methodology, other `Edge.edge_type` families
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md).

## Context

Four `Edge.edge_type` values sit on the `pending_classification`
axis: `resolver_implements`, `resolver_for_type`, `openapi_implements`,
and `implements_rpc`. Every other edge-type family got its ADR-0024
per-family audit (dispatch/publish → audit-findings 0001; IPC →
audit-findings 0002; framework dispatch → the evidence-type clusters);
this "resolver/OpenAPI/RPC family" was punted at `edge_types.py`
(the family "awaits its own audit") and never filed. This document is
that audit — the missing entry the 0002 doc's closing note already
flagged as a "pending sibling family."

### What the audit looked at

Every emit site for each value (verified across all `packages/`
including `hypergumbo-lang-*`; literal-kwarg producers only, no
helper/f-string/dict-subscript indirection). Inventory at audit time:

| Value                 | Producing linker (file:line)               | Edge shape (src → dst)                 | Contract |
|-----------------------|--------------------------------------------|----------------------------------------|----------|
| `resolver_implements` | `linkers/graphql_resolver.py:522`          | resolver `function` → schema `field`   | GraphQL schema field |
| `resolver_for_type`   | `linkers/graphql_resolver.py:546`          | resolver `function` → `type`           | GraphQL type (declaration-time association) |
| `openapi_implements`  | `linkers/openapi.py:363,390`               | spec operation → route handler (**inverse dir.**) | OpenAPI operation |
| `implements_rpc`      | `linkers/grpc.py:622`                       | Go `method` → proto RPC route          | proto RPC (Go interface impl) |

### Why this looks like a family

Two of the four express "concrete code implements a declared contract"
in the impl→contract direction (`resolver_implements`, `implements_rpc`)
— differing only by framework (GraphQL / gRPC), the framework name
leaking into the label exactly as `http_calls` / `grpc_calls` /
`graphql_calls` did before their WI-vumum-juvil fold to `calls` +
`meta['protocol']`; these fold to `implements`. The other two fold to
`references`: `resolver_for_type` is a type-level *association*, not
contract satisfaction; and `openapi_implements` (per the 2026-07-20
ruling) emits the *inverse* direction (spec→handler) deliberately, so it
is a declarative spec-artifact reference rather than impl→contract
satisfaction. The family thus folds heterogeneously — 2 `implements`,
2 `references` — mirroring how audit-findings 0002's IPC family split
`message_send`→`event_publishes` from `websocket_connection`→`references`.

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the
four-leakage-test procedure are defined in
[ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md).
This document applies that methodology.

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: resolver_implements
    verdict: FOLD
    fold_target: implements
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"resolver_implements\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: empty
    rationale: "A GraphQL resolver function satisfies a declared schema field: impl→contract, canonical 'implements' + meta['protocol']='graphql'. The only differentiator (GraphQL-ness) is already in dst.kind='field' + meta['framework_dispatch']. Test 4 (mechanism vs. category): 'resolver' names the framework role, not the relationship."
  - value: resolver_for_type
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"resolver_for_type\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: empty
    rationale: "'@Resolver(() => User)' associates a resolver WITH a type — it does not implement the whole type. Coarse declaration-time association → references + meta['ref_construct']='graphql_resolver_type'. Test 3 (construct vs. relationship) + Test 2 (apex/peer): folding it to 'implements' alongside its sibling would overload one relationship name onto two."
  - value: openapi_implements
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"openapi_implements\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: empty
    rationale: "OpenAPI spec operation → route handler (openapi.py:363,390). RULED 2026-07-20 (4-lens investigation, REVISED from the provisional 'implements'): the spec→handler direction is DELIBERATE (openapi.py:39,42 — it powers the linker's documented forward-slice-from-spec-to-implementation), so folding to 'implements' (impl→contract) would require a producer direction flip that BREAKS that traversal AND misclassifies it into the structural INHERITANCE_EDGE_TYPES set — for zero gain (these values have zero consumers). Direction-preserving → references + meta['ref_construct']='openapi_operation' (a declarative spec artifact referencing its realizing handler; the audit-findings 0002 websocket_connection precedent). Test 3."
  - value: implements_rpc
    verdict: FOLD
    fold_target: implements
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"implements_rpc\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: empty
    rationale: "A Go method on a struct embedding UnimplementedXxxServer literally IS a Go interface implementation (detected via base_classes / Unimplemented* embedding): impl→contract, canonical 'implements' + meta['protocol']='grpc'. Test 1 (property-derivability): the proto-interface differentiator is already meta. CONSUMER-COUPLING WRINKLE (see Diagnostic findings): a naive rename silently demotes gRPC taint/reachability."
```

**Net:** zero CANONICAL, four FOLD, zero DEPRECATE-NO-FOLD — the same
shape audit-findings 0002 (IPC) reached. After the 2026-07-20 4-lens
ruling pass the family folds **heterogeneously**: **two → `implements`**
(`resolver_implements`, `implements_rpc` — genuine impl→contract
satisfaction) and **two → `references`** (`resolver_for_type` type
association, and `openapi_implements` — revised from `implements`
because its deliberate spec→handler direction folds direction-preservingly
to `references`, not to the impl→contract `implements`). The canonicals
folded to (`implements`, `references`) already exist. All four rows are now
RESOLVED: the producers were migrated (Batch A — the three clean folds; Batch B
— `implements_rpc`, preserving its call-like taint/io/ranking/slice coupling
via `edge_types.is_grpc_rpc_implementation` per finding 3) and the four dead
registry entries pruned in the consolidated Phase-4b PR (SCHEMA_VERSION
0.17.0 → 0.18.0), draining the `pending_classification` axis to empty. No
verdict is CANONICAL, so
the ADR-0024 "CANONICAL requires a re-evaluation trigger" rule does
not bind; the trigger to record should a future maintainer keep any
as canonical: "a second non-framework producer emits the same
impl→contract shape without a queryable framework/protocol
discriminator on either endpoint."

## Diagnostic findings worth naming

**1. The family is one relationship with one exception.** Three of
four are "concrete code implements a declared contract" differing only
by framework — textbook FOLD-to-one-canonical. When these migrate,
the `implements` registry description ("Class implements an
interface") should generalize to "concrete implementation satisfies a
declared contract (interface, GraphQL schema field, OpenAPI operation,
proto RPC)." `resolver_for_type` is the odd member — a type-level
association (`references`), not contract satisfaction.

**2. `openapi_implements` is directionally inverted — RULED FOLD →
`references`, direction-preserving, NO flip (2026-07-20, 4-lens
investigation).** It is the sole family member emitting contract→impl
(spec→handler), the inverse of the other three and of canonical
`implements` (impl→contract). The spec→handler direction is a
*deliberate* design choice — `openapi.py:39,42` document it as powering
"slice traversal from spec to implementation," the linker's flagship
purpose. Folding to `implements` would force a producer flip that breaks
that forward-slice-from-spec *and* moves the edge into the structural
`INHERITANCE_EDGE_TYPES` set (forward-BFS-skipped, reverse-only), for
**zero** offsetting gain: `openapi_implements` has no consumer (unlike
`implements_rpc`, which is taint/io/ranking-coupled). So it folds to
`references` + `meta['ref_construct']='openapi_operation'` — a
declarative spec artifact referencing its realizing handler, exactly the
audit-findings 0002 `websocket_connection` divergence (a declarative
connectivity edge that folded to `references` while its family siblings
folded elsewhere). The family therefore folds heterogeneously (see Net).

**3. `implements_rpc` carries consumer coupling a rename must
preserve.** It is the only family member with special consumer
treatment — traceable/call-like in `taint.py:1120`
(`TAINT_CALL_EDGE_TYPES`), `io_boundary.py:1146,1649`
(`_TRACEABLE_EDGE_TYPES`), and weight 1.0 in `ranking.py:203`, whereas
canonical `implements` is in none of those and ranks 0.5. A naive
rename would silently **demote gRPC reachability + taint propagation**.
The fold must add the folded form (gated on `meta['protocol']='grpc'`)
back into those three consumer sets, or accept and document the
demotion. `edge_types.py:540-546` already flags this coupling and warns
the drift linter watches consumers that don't follow the rename.

## Migration impact (shipped)

The four values were producer-migrated then pruned (WI-pusuv Option B).
The concrete rename targets, as shipped:

| Old emit              | New emit     | New `meta`                         | Note |
|-----------------------|--------------|------------------------------------|------|
| `resolver_implements` | `implements` | `protocol="graphql"`               | — |
| `resolver_for_type`   | `references` | `ref_construct="graphql_resolver_type"` | — |
| `openapi_implements`  | `references` | `ref_construct="openapi_operation"` | direction-preserving, NO flip (finding 2, ruled 2026-07-20) |
| `implements_rpc`      | `implements` | `protocol="grpc"`                  | preserve traceable/taint/ranking coupling (finding 3) |

## Related

- **Parent axis declaration**: [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` axis whose values this audit catalogues.
- **Methodology**: [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md).
- **Sibling audits**: [audit-findings 0001](0001-dispatch-publish-family.md), [0002](0002-ipc-family.md) — the dispatch/publish and IPC families; 0002's closing note flagged this family as pending. [audit-findings 0017](0017-endpoint-shape-long-tail.md) — the sibling long-tail endpoint_shape audit filed in the same pass.
- **Migration tracker**: WI-sumik (this audit), WI-kivip (the endpoint_shape fold-tail umbrella), WI-pusuv (the downstream access_mode census the fold-tail drain unblocks).
