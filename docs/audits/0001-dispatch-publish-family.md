<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0001: Dispatch and Publish Family Classifications

- Date: 2026-04-30
- Status: All rows RESOLVED at relocation (2026-05-02)
- Closes: WI-kofuh-fovar (dispatch family audit), WI-kusif-gamup (publish family audit)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the
  audit-findings format defined in
  [`docs/audits/README.md`](README.md).

> **Reclassification note.** This document was originally filed as
> ADR-0025 in `docs/adr/`. It was relocated to the audit-findings
> series because it records per-value verdicts under existing law
> (ADR-0023 for the axis, ADR-0024 for the methodology), not a new
> architecture decision. The bucket boundary is documented in
> [`docs/adr/README.md`](../adr/README.md).

## Context

ADR-0023 §5 deferred two `Edge.edge_type` families — dispatch and
publish — to per-family audit because the family members were not
obviously a single relationship under different protocols. Some
members might be genuinely distinct relationships, others
protocol-conditional aliases of a shared canonical. The per-family
audit was meant to settle each member's verdict before Phase 3
producer migration touched the emit sites.

This document records the audit's verdicts.

### What the audit looked at

Every emit site for every family-adjacent `edge_type` in the
codebase. The emit-site grep was the authoritative inventory; the
sets named in the WI descriptions were a starting point that got
extended to include `registers_routes` once the exhaustive sweep ran.

For each value, the audit recorded:

- The src/dst kind shape produced by the emit site (caller→callee
  vs declaration→target vs subscriber→enclosing, etc.).
- The semantic question the value is meant to answer ("dispatch
  this call at runtime" vs "declare a binding statically" vs
  "register an event handler").
- Whether sibling members in the family answer the same question via
  a different protocol/mechanism, or genuinely answer a different
  question.

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
  # --- Dispatch family ---
  - value: dispatches_to
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.edge_types import EDGE_TYPES; assert any(s.name == \"dispatches_to\" and s.axis == \"relationship\" for s in EDGE_TYPES)'"
      expect: exit_code:0
    rationale: "Apex of single-target dispatch via runtime indirection. Test 4 (mechanism vs. category) confirms; 'dispatch' is the relationship, not a mechanism qualifier."
  - value: routes_to
    verdict: FOLD
    fold_target: dispatches_to
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"routes_to\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "HTTP routing IS dispatch via path matching; 'route' is the dispatch mechanism, not a separate relationship. Producers emit dispatches_to + meta['dispatch_kind']='route'. Test 4 (mechanism vs. category)."
  - value: delegates_to
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"delegates_to\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Class-level delegation is a declaration-time binding, not a runtime dispatch — the emit site is the class body, not a call site. Test 3 (construct vs. relationship)."
  - value: message_dispatch
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"message_dispatch\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Misnamed: emit shape is publisher→subscriber, fits publish-family not dispatch-family. Producers emit event_publishes + meta['channel_kind']='message_bus'. Test 2 (apex/peer overloading)."
  - value: annotated_dispatches
    verdict: FOLD
    fold_target: dispatches_to
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"annotated_dispatches\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Annotation IS the dispatch mechanism; producers emit dispatches_to + meta['mechanism']='annotation'. Test 4 (mechanism vs. category)."
  - value: uses_dispatch_table
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"uses_dispatch_table\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Edge shape is code→data (the dispatch-table symbol), not dispatcher→target. Producers emit references + meta['construct']='dispatch_table'. Test 3 (construct vs. relationship)."
  - value: di_registers
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"di_registers\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "DI registration is a declaration-time binding ('module declares X provides Y'), not runtime dispatch. Producers emit references + meta['mechanism']='di_registration'. Test 3 (construct vs. relationship)."
  - value: di_resolves
    verdict: FOLD
    fold_target: dispatches_to
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"di_resolves\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Runtime DI resolution dispatches an interface to its implementation — DI is the mechanism. Producers emit dispatches_to + meta['mechanism']='di'. Test 4 (mechanism vs. category)."
  - value: registers_routes
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"registers_routes\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Router→route declaration is structural ('this router declares this route'), parallel to di_registers. Producers emit references + meta['mechanism']='route_registration'. Test 3 (construct vs. relationship)."
  # --- Publish family ---
  - value: event_publishes
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.edge_types import EDGE_TYPES; assert any(s.name == \"event_publishes\" and s.axis == \"relationship\" for s in EDGE_TYPES)'"
      expect: exit_code:0
    rationale: "Apex of producer→consumer over an async channel. Channel kind (queue, bus, CRDT) is meta information."
  - value: event_subscribes
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"event_subscribes\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Production emit shape was 'subscriber → enclosing function' (a structural-containment query) under a name that suggests pub-sub semantics. Producer rewritten in Phase 3; the value carries no replacement. Test 2 (apex/peer overloading)."
  - value: crdt_publishes
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"crdt_publishes\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "CRDT is a channel kind; producers emit event_publishes + meta['channel_kind']='crdt'. Test 4 (mechanism vs. category)."
  - value: annotated_publishes
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"annotated_publishes\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Annotation is the publish mechanism; producers emit event_publishes + meta['mechanism']='annotation'. Test 4 (mechanism vs. category)."
  - value: emits
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"emits\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Emit shape is 'function → event_symbol' (a function references the event it emits), not pub→sub. Producers emit references + meta['construct']='event_emit'. Test 3 (construct vs. relationship)."
  - value: enqueues
    verdict: FOLD
    fold_target: event_publishes
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"enqueues\"' packages/hypergumbo-core/src/hypergumbo_core/"
      expect: empty
    rationale: "Queue is a channel kind for async producer→consumer (Ruby ActiveJob SomeJob.perform_later is the canonical example); producers emit event_publishes + meta['channel_kind']='queue'. Test 4 (mechanism vs. category)."
```

**Net:** Two canonicals (`dispatches_to`, `event_publishes`), one
deprecate-no-fold (`event_subscribes`), twelve folds split across
three existing canonicals (`dispatches_to` for runtime dispatch,
`references` for declaration-time bindings, `event_publishes` for
producer→consumer). All thirteen rows at RESOLVED at relocation
time: Phase 4b producer migration shipped at `SCHEMA_VERSION 0.4.0`
and the registry was pruned of every endpoint_shape value in this
audit's scope.

## Diagnostic findings worth naming

Two findings emerged from the audit that are worth recording even
though they don't change the verdicts:

**1. Cross-family misnaming.** `message_dispatch` carried the word
"dispatch" in its name but its production emit shape was
publisher→subscriber, fitting the publish family. The audit caught
this only because the four-test pass at per-pair level forced
inspection of the emit-site src/dst shape rather than relying on
the name. A name-only audit would have classified it under
dispatch.

**2. Subscriber-shape inconsistency.** `event_subscribes` had two
incompatible emit shapes across the codebase: production
(`event_sourcing.py`) emitted `subscriber → enclosing function`
(structural containment), while every test fixture in `test_slice.py`
used `subscriber → handler` (publish-family-shape matching the name).
This inconsistency was itself the leak — producer code and test code
had diverging mental models of what the edge meant. Phase 3 producer
migration picked "drop the producer; the information is recoverable
from `Symbol.span`" as the resolution.

## Migration impact (historical)

Phase 3 producer migration (WI-mokam-jalig) used the table below as
its concrete rename targets. All rewrites have shipped and the
registry has been pruned (Phase 4b, `SCHEMA_VERSION 0.4.0`).

| Old emit                  | New emit              | New `meta` |
|---------------------------|-----------------------|------------|
| `routes_to`               | `dispatches_to`       | `dispatch_kind="route"` |
| `delegates_to`            | `references`          | `mechanism="delegate"` |
| `message_dispatch`        | `event_publishes`     | `channel_kind="message_bus"` |
| `annotated_dispatches`    | `dispatches_to`       | `mechanism="annotation"` |
| `uses_dispatch_table`     | `references`          | `construct="dispatch_table"` |
| `di_registers`            | `references`          | `mechanism="di_registration"` |
| `di_resolves`             | `dispatches_to`       | `mechanism="di"` |
| `registers_routes`        | `references`          | `mechanism="route_registration"` |
| `crdt_publishes`          | `event_publishes`     | `channel_kind="crdt"` |
| `annotated_publishes`     | `event_publishes`     | `mechanism="annotation"` |
| `emits`                   | `references`          | `construct="event_emit"` |
| `enqueues`                | `event_publishes`     | `channel_kind="queue"` |
| `event_subscribes`        | (dropped)             | n/a |

## Related

- **Parent axis declaration**: [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` axis whose values this audit catalogues.
- **Methodology**: [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md) — the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test procedure.
- **Sibling audit**: [audit-findings 0002](0002-ipc-family.md) — same methodology applied to the IPC family.
- **Migration tracker**: WI-mokam-jalig (Phase 3 producer migration), WI-vomoj-suhaz (Phase 4b registry pruning).
- **Sweep finding**: WI-tavas-voror's emitted-but-unregistered triage; the audit consumed those findings and fold-classified the surfaced values (notably `registers_routes`, which the original family lists missed).
