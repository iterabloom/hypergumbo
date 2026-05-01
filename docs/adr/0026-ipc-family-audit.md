<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0026: IPC Family Classifications

Date: 2026-05-01
Status: Accepted
Supersedes: ADR-0023 §5 (audit deferral) for the IPC family
Closes: WI-fugun-butil (IPC family audit)
Sibling: ADR-0025 (dispatch and publish family classifications) — same
methodology, applied to a different family

## Context

ADR-0023 §6's deprecation list left `ipc_calls` and `ipc_event` at
`endpoint_shape` "pending review per IPC family conventions" and
similarly punted `message_send` / `message_receive` /
`websocket_message` / `websocket_connection` / `message_queue` to
their respective protocol-specific reviews. Each of those values
encodes a *protocol* or *framework* (Tauri / Electron / Phoenix /
WebSocket / RabbitMQ-or-Kafka-flavored) plus a *direction* (send vs
receive vs connect) — the same shape ADR-0025 found for the dispatch
and publish families.

This ADR resolves the IPC-shaped subset by applying the same audit
methodology ADR-0025 used (the four leakage tests, per-pair verdicts,
fold-target tables). The producer migration that follows is a
sibling tracker item; this ADR settles the canonical names so Phase 3
can land for IPC.

### What the audit looked at

Every emit site for every IPC-shaped `edge_type`. The members
inventoried (with the production-side linker for each):

| Value                  | Producing linker                                   | Edge shape (src → dst) | Mediation |
|------------------------|----------------------------------------------------|------------------------|-----------|
| `ipc_calls`            | `linkers/tauri_ipc.py` (Rust ↔ JS via Tauri)       | caller → callee        | Tauri's `invoke` API |
| `ipc_event`            | `linkers/tauri_ipc.py` (Tauri's emit/listen)       | publisher → listener   | named Tauri event |
| `message_send`         | `linkers/ipc.py` (Electron), `linkers/phoenix_ipc.py` | sender → receiver    | named channel / event |
| `message_receive`      | same — *converse-direction edge*                   | receiver → sender      | same |
| `websocket_message`    | `linkers/websocket.py`                             | sender_file → recv_file | WebSocket event |
| `websocket_connection` | `linkers/websocket.py`                             | file → endpoint_symbol | (declarative — declares connectivity, not an exchange) |
| `message_queue`        | `linkers/message_queue.py` (RabbitMQ / Kafka / etc.) | publisher → subscriber | named topic on a queue |

The grep is the authoritative inventory; the audit consumes whatever
sites currently emit, not the description above (which can drift).

### Why this looks like ADR-0025 ground

Both audits found the same leak shape: the value name encodes a
*protocol* / *framework* / *channel kind* alongside (or instead of)
the relationship the edge expresses. The fix is the same — fold the
value to the canonical relationship and put the differentiating
fact in `meta`. This audit's verdicts are therefore mostly
mechanical applications of ADR-0025's pattern, with two exceptions
the four-test pass surfaced.

## Methodology

For each member, decide:

1. **CANONICAL** — the value names a genuinely distinct relationship
   not captured by an existing axis member. Goes on the `relationship`
   axis.

2. **FOLD** — protocol-conditional or framework-specific alias of an
   existing canonical. The mechanism (`protocol`, `channel_kind`)
   becomes meta information. Goes on the `endpoint_shape` axis until
   Phase 4 prunes it.

3. **DEPRECATE-NO-FOLD** — emit shape doesn't match the relationship
   the name suggests; the producer site is doing something other than
   what the name implies and Phase 3 picks a different replacement.
   Goes on `endpoint_shape` with a Phase-3 producer-rewrite plan.

The four leakage tests (per the audit playbook §3) inform each
verdict; the table cites the test that fired most diagnostically.

## Decision

### IPC family verdicts

| Value                  | Verdict           | Canonical fold target | Diagnostic test | Rationale (one-line) |
|------------------------|-------------------|------------------------|-----------------|----------------------|
| `ipc_calls`            | FOLD              | `calls`                | Test 4 (mechanism vs. category) | Tauri's `invoke` IS a call; "ipc" is the mechanism. `meta["protocol"]="ipc"`. |
| `ipc_event`            | FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | Tauri's emit/listen IS publish; "ipc" is the channel kind. `meta["channel_kind"]="ipc"`. |
| `message_send`         | FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | Electron / Phoenix sender→receiver is the publish-family shape; "ipc" is the channel kind. `meta["channel_kind"]="ipc"`. |
| `message_receive`      | DEPRECATE-NO-FOLD | (Phase-3 producer decision) | Test 2 (apex/peer overloading) | Same shape problem as `event_subscribes` from ADR-0025: emit direction is converse (receiver→sender) of what `event_publishes` already captures, so the edge is either redundant or wants a `references`-shaped reverse-pointer. Phase 3 picks the rewrite (likely drop, since hypergumbo's slice can compute reverse paths from forward edges). |
| `websocket_message`    | FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | Sender_file → recv_file via channel = publish-family shape; "websocket" is the channel kind. `meta["channel_kind"]="websocket"`. |
| `websocket_connection` | FOLD              | `references`           | Test 3 (construct vs. relationship) | Different shape from the others: file → endpoint_symbol declares connectivity, doesn't carry a message. `meta["construct"]="websocket_endpoint"`. |
| `message_queue`        | FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | Publisher → subscriber via topic = publish-family shape; "queue" is the channel kind. `meta["channel_kind"]="queue"`. **Note:** identical fold target as `enqueues` from ADR-0025 — both are queue-shaped pub-sub. |

**Net:** zero canonical (the IPC family is entirely fold candidates;
the canonicals it folds to — `calls`, `event_publishes`,
`references` — already exist), six folds, one DEPRECATE-NO-FOLD.

## Diagnostic findings worth naming

Three findings the per-pair audit surfaced:

**1. Overlap with ADR-0025's `enqueues`.** `message_queue` (this
audit) and `enqueues` (ADR-0025) both fold to
`event_publishes + meta["channel_kind"]="queue"`. They were emitted
by different linkers (`linkers/message_queue.py` for `message_queue`;
`hypergumbo_lang_mainstream/ruby.py` for `enqueues`'s ActiveJob case)
but they describe the same relationship. The fold consolidates them
correctly; future analyzers detecting queue-flavored pub-sub should
emit the canonical form directly.

**2. `message_receive` parallels `event_subscribes`.** Same shape
problem — the producer emits a converse-direction edge under a name
that suggests a separate relationship. The Phase 3 producer for the
IPC subset (sibling to `WI-vasik-jofiv` for the publish/dispatch
subset) makes the same kind of judgment call: rewrite to a different
relationship (e.g., `references` from receiver→sender as a
"message-source" pointer) or drop the edge entirely (slice can
compute reverse paths). Recommendation: drop, since the forward
`event_publishes` edge already captures the relationship and
hypergumbo's traversal handles inverse direction natively.

**3. `channel_kind` is becoming a closed enumeration de facto.** This
ADR adds `ipc`, `websocket`, `queue` as `channel_kind` values; ADR-0025
established `crdt`, `message_bus`, `queue`, `event_bus`, plus the
`mechanism` keys (`annotation`, `delegate`, `di`, `di_registration`,
`route_registration`). Together with `protocol` / `bridge_kind` /
`construct`, `meta` is acquiring multiple closed enumerations. The
BRIDGE_KINDS frozenset in `hypergumbo_core.edge_types` (added by
WI-mifor-vabul) is the precedent. A follow-on item should formalize
the `channel_kind` / `protocol` / `mechanism` / `construct` closed
enumerations the same way (out of scope for this ADR — file as
follow-up).

## Registry updates landing with this ADR

All seven IPC-family values were already classified `endpoint_shape`
per ADR-0023 §6's first cut. The audit ratifies that classification
(no axis reclassifications needed); the per-value descriptions in
the registry are updated to point to ADR-0026's fold targets. No new
enum values added, no removed; **`SCHEMA_VERSION` does not bump**
(description-only changes don't affect the JSON Schema enum).

## Migration impact on Phase 3 (WI-mokam-jalig)

The IPC subset of Phase 3 producer migration now has concrete rename
targets. The migration table:

| Old emit               | New emit              | New `meta` |
|------------------------|-----------------------|------------|
| `ipc_calls`            | `calls`               | `protocol="ipc"` |
| `ipc_event`            | `event_publishes`     | `channel_kind="ipc"` |
| `message_send`         | `event_publishes`     | `channel_kind="ipc"` |
| `message_receive`      | (Phase-3 decision)    | (Phase-3 decision) |
| `websocket_message`    | `event_publishes`     | `channel_kind="websocket"` |
| `websocket_connection` | `references`          | `construct="websocket_endpoint"` |
| `message_queue`        | `event_publishes`     | `channel_kind="queue"` |

A Phase-3 producer-migration item for IPC is filed alongside this
ADR (sibling to `WI-vasik-jofiv` for the publish/dispatch subset).
Once it lands, WI-mokam-jalig's "every endpoint_shape value has zero
production emit sites" acceptance closes (modulo Phase 4's
deprecation removal at `WI-vomoj-suhaz`).

## Consequences

### Positive

- **Phase 3 IPC subset unblocked**: producer migration has concrete
  canonical rename targets and clear `meta` keys.
- **Cross-ADR consolidation**: confirms the publish-family fold is
  protocol-agnostic. `event_publishes + meta["channel_kind"]=...`
  works for event buses, CRDTs, queues, IPC, WebSocket, and any
  future async producer→consumer mediation.
- **Methodology reuse**: third audit using the same four-test pass.
  The pattern is now mechanical enough that future families
  (resolver/openapi/RPC, the next pending_classification group) can
  follow the same template.

### Negative

- **None requiring schema bump** — verdicts are descriptive
  classifications, not new types or removals.
- **`message_receive` Phase-3 decision is a judgment call**, same
  shape as ADR-0025's `event_subscribes`. Two prior audits have now
  surfaced this shape; if a third does, it's worth generalizing the
  pattern explicitly in an ADR amendment.

### Risks

- **Bigger fold than ADR-0025**: this audit folds three different
  protocols (Tauri / Electron / Phoenix) into the same `channel_kind`
  meta value (`"ipc"`). Downstream consumers wanting protocol-specific
  filtering will need a finer disambiguator (e.g., `meta["framework"]`
  carrying `"tauri"` / `"electron"` / `"phoenix"`). Phase 3 producer
  migration should add `meta["framework"]` where the linker knows it,
  to preserve the information. Mitigation: Phase 3 reviewer asks
  "could a downstream consumer want to filter by framework?" for each
  rename and adds the meta key when yes.

## Related

- **Sibling pattern**: ADR-0025 (dispatch and publish family
  classifications) — same audit methodology, same fold-table shape.
- **Generalizes**: ADR-0023 §5's deferral to per-family audit; this
  ADR completes the deferral for the IPC family.
- **Pending sibling families**: the `pending_classification` resolver
  values (`resolver_implements`, `resolver_for_type`,
  `openapi_implements`, `implements_rpc`) remain — needs its own
  audit (out of scope; file as follow-up if/when Phase 3 reaches
  those producers).
- **Unblocks**: the IPC subset of `WI-mokam-jalig` (ADR-0023 §6
  Phase 3 — Unify producers).
