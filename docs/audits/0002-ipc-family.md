<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0002: IPC Family Classifications

- Date: 2026-05-01
- Status: All rows RESOLVED at relocation (2026-05-02)
- Closes: WI-fugun-butil (IPC family audit)
- Sibling: [audit-findings 0001](0001-dispatch-publish-family.md) — same methodology, applied to a different family
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the
  audit-findings format defined in
  [`docs/audits/README.md`](README.md).

> **Reclassification note.** This document was originally filed as
> ADR-0026 in `docs/adr/`. It was relocated to the audit-findings
> series because it records per-value verdicts under existing law
> (ADR-0023 for the axis, ADR-0024 for the methodology), not a new
> architecture decision. The bucket boundary is documented in
> [`docs/adr/README.md`](../adr/README.md).

## Context

ADR-0023 §6's deprecation list left `ipc_calls` and `ipc_event` at
`endpoint_shape` "pending review per IPC family conventions" and
similarly punted `message_send` / `message_receive` /
`websocket_message` / `websocket_connection` / `message_queue` to
their respective protocol-specific reviews. Each of those values
encoded a *protocol* or *framework* (Tauri / Electron / Phoenix /
WebSocket / RabbitMQ-or-Kafka-flavored) plus a *direction*
(send vs receive vs connect) — the same shape audit-findings 0001
found for the dispatch and publish families.

This document resolves the IPC-shaped subset by applying the same
audit methodology, settling the canonical names so Phase 3 producer
migration could land for IPC.

### What the audit looked at

Every emit site for every IPC-shaped `edge_type`. Inventory at
audit time (with the production-side linker for each):

| Value                  | Producing linker                                   | Edge shape (src → dst) | Mediation |
|------------------------|----------------------------------------------------|------------------------|-----------|
| `ipc_calls`            | `linkers/tauri_ipc.py` (Rust ↔ JS via Tauri)       | caller → callee        | Tauri's `invoke` API |
| `ipc_event`            | `linkers/tauri_ipc.py` (Tauri's emit/listen)       | publisher → listener   | named Tauri event |
| `message_send`         | `linkers/ipc.py` (Electron), `linkers/phoenix_ipc.py` | sender → receiver    | named channel / event |
| `message_receive`      | same — *converse-direction edge*                   | receiver → sender      | same |
| `websocket_message`    | `linkers/websocket.py`                             | sender_file → recv_file | WebSocket event |
| `websocket_connection` | `linkers/websocket.py`                             | file → endpoint_symbol | declarative — declares connectivity, not an exchange |
| `message_queue`        | `linkers/message_queue.py` (RabbitMQ / Kafka / etc.) | publisher → subscriber | named topic on a queue |

### Why this looks like audit-findings 0001 ground

Both audits found the same leak shape: the value name encodes a
*protocol* / *framework* / *channel kind* alongside (or instead of)
the relationship the edge expresses. The fix is the same — fold the
value to the canonical relationship and put the differentiating
fact in `meta`. This audit's verdicts are therefore mostly mechanical
applications of the pattern audit-findings 0001 established, with
one exception (`message_receive`) the four-test pass surfaced.

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the
four-leakage-test diagnostic procedure are defined in
[ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md).
This document applies that methodology; consult ADR-0024 for the
verdict scheme's definition.

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: ipc_calls
    verdict: FOLD
    fold_target: calls
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"ipc_calls\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Tauri's invoke IS a call; 'ipc' is the mechanism. Producers emit calls + meta['protocol']='ipc'. Test 4 (mechanism vs. category)."
  - value: ipc_event
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"ipc_event\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Tauri's emit/listen IS publish; 'ipc' is the channel kind. Producers emit event_publishes + meta['channel_kind']='ipc'. Test 4 (mechanism vs. category)."
  - value: message_send
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"message_send\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Electron / Phoenix sender→receiver is the publish-family shape; 'ipc' is the channel kind. Producers emit event_publishes + meta['channel_kind']='ipc'. Test 4 (mechanism vs. category)."
  - value: message_receive
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"message_receive\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Same shape problem as event_subscribes (audit-findings 0001): emit direction is converse (receiver→sender) of what event_publishes already captures, so the edge was either redundant or wanted a references-shaped reverse-pointer. Phase 3 dropped the producer; hypergumbo's slice can compute reverse paths from forward edges. Test 2 (apex/peer overloading)."
  - value: websocket_message
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"websocket_message\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Sender_file → recv_file via channel = publish-family shape; 'websocket' is the channel kind. Producers emit event_publishes + meta['channel_kind']='websocket'. Test 4 (mechanism vs. category)."
  - value: websocket_connection
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"websocket_connection\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Different shape from the other IPC values: file → endpoint_symbol declares connectivity, doesn't carry a message. Producers emit references + meta['construct']='websocket_endpoint'. Test 3 (construct vs. relationship)."
  - value: message_queue
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"message_queue\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Publisher → subscriber via topic = publish-family shape; 'queue' is the channel kind. Producers emit event_publishes + meta['channel_kind']='queue'. Identical fold target as enqueues from audit-findings 0001 — both are queue-shaped pub-sub. Test 4 (mechanism vs. category)."
```

**Net:** Zero canonicals (the IPC family is entirely fold candidates;
the canonicals it folds to — `calls`, `event_publishes`, `references`
— already exist), six folds, one DEPRECATE-NO-FOLD. All seven rows at
RESOLVED at relocation time: Phase 4b producer migration shipped at
`SCHEMA_VERSION 0.4.0` and the registry was pruned of every
endpoint_shape value in this audit's scope.

## Diagnostic findings worth naming

Three findings the per-pair audit surfaced:

**1. Overlap with audit-findings 0001's `enqueues`.** `message_queue`
(this audit) and `enqueues` (audit-findings 0001) both fold to
`event_publishes + meta["channel_kind"]="queue"`. They were emitted by
different linkers (`linkers/message_queue.py` for `message_queue`;
`hypergumbo_lang_mainstream/ruby.py` for `enqueues`'s ActiveJob case)
but they describe the same relationship. The fold consolidated them
correctly; future analyzers detecting queue-flavored pub-sub should
emit the canonical form directly.

**2. `message_receive` parallels `event_subscribes`.** Same shape
problem — the producer emitted a converse-direction edge under a
name that suggested a separate relationship. Two prior audits have
surfaced this shape; if a third does, it's worth generalizing the
pattern explicitly in an ADR amendment. (Phase 3 picked "drop, since
the forward `event_publishes` edge already captures the relationship
and hypergumbo's traversal handles inverse direction natively.")

**3. `channel_kind` became a closed enumeration de facto.** This
audit added `ipc`, `websocket`, `queue` as `channel_kind` values;
audit-findings 0001 established `crdt`, `message_bus`, `queue`,
`event_bus`, plus the `mechanism` keys (`annotation`, `delegate`,
`di`, `di_registration`, `route_registration`). Together with
`protocol` / `bridge_kind` / `construct`, `meta` was acquiring
multiple closed enumerations. The `BRIDGE_KINDS` frozenset in
`hypergumbo_core.edge_types` (added by WI-mifor-vabul) was the
precedent. ADR-0024's new fold-residue-discipline section's
recurrence-promotion threshold rule (N=3 distinct axis values OR
N=2 producer modules) operationalizes when these meta keys should
get promoted to dedicated host-dataclass fields.

## Migration impact (historical)

Phase 3 producer migration's IPC subset used the table below as its
concrete rename targets. All rewrites have shipped and the registry
has been pruned (Phase 4b, `SCHEMA_VERSION 0.4.0`).

| Old emit               | New emit              | New `meta` |
|------------------------|-----------------------|------------|
| `ipc_calls`            | `calls`               | `protocol="ipc"` |
| `ipc_event`            | `event_publishes`     | `channel_kind="ipc"` |
| `message_send`         | `event_publishes`     | `channel_kind="ipc"` |
| `message_receive`      | (dropped)             | n/a |
| `websocket_message`    | `event_publishes`     | `channel_kind="websocket"` |
| `websocket_connection` | `references`          | `construct="websocket_endpoint"` |
| `message_queue`        | `event_publishes`     | `channel_kind="queue"` |

## Related

- **Parent axis declaration**: [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` axis whose values this audit catalogues.
- **Methodology**: [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md) — the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test procedure.
- **Sibling audit**: [audit-findings 0001](0001-dispatch-publish-family.md) — same methodology applied to the dispatch and publish families.
- **Migration tracker**: WI-mokam-jalig (Phase 3 producer migration), WI-vomoj-suhaz (Phase 4b registry pruning).
- **Pending sibling families**: the `pending_classification` resolver values (`resolver_implements`, `resolver_for_type`, `openapi_implements`, `implements_rpc`) remain in the registry — they need their own audit (out of scope for this document; will file as audit-findings 0003 when Phase 3 reaches those producers).
