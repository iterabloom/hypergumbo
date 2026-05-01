<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0025: Dispatch and Publish Family Classifications

Date: 2026-04-30
Status: Accepted
Supersedes: ADR-0023 §5 (audit deferral) for the dispatch and publish families
Closes: WI-kofuh-fovar (dispatch family audit), WI-kusif-gamup (publish family audit)

## Context

ADR-0023 §5 deferred two `Edge.edge_type` families to per-family
audit because the family members were not obviously a single
relationship under different protocols — some might be genuinely
distinct relationships, others protocol-conditional aliases of a
shared canonical. The per-family audit was meant to settle each
member's verdict before Phase 3 producer migration touched the
emit sites.

This ADR records the audits' verdicts. The methodology follows the
fundamental-concept audit playbook
(`.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`),
applying the four leakage tests at the per-pair level rather than
the field level.

### What the audit looked at

Every emit site for every family-adjacent `edge_type` in the
codebase (the emit-site grep is the authoritative inventory; the
sets named in the WI descriptions were a starting point that got
extended to include `registers_routes` once the exhaustive sweep
ran). For each value, the audit recorded:

- The src/dst kind shape produced by the emit site (caller→callee
  vs declaration→target vs subscriber→enclosing, etc.).
- The semantic question the value is meant to answer ("dispatch
  this call at runtime" vs "declare a binding statically" vs
  "register an event handler").
- Whether sibling members in the family answer the same question
  via a different protocol/mechanism, or genuinely answer a
  different question.

## Methodology

For each member, decide one of three verdicts:

1. **CANONICAL** — names a genuinely distinct relationship; the
   value names what the edge expresses between src and dst, with
   no endpoint or mechanism leakage. Goes on the `relationship`
   axis of `EDGE_TYPES`.

2. **FOLD** — protocol-conditional or framework-specific alias of
   an existing canonical. The value's mechanism / protocol /
   channel-kind is endpoint or meta information, not a separate
   relationship. Migration: rename to the canonical at producer
   sites, move the differentiating fact to `edge.meta`. Goes on
   the `endpoint_shape` axis until Phase 4 prunes it.

3. **DEPRECATE-NO-FOLD** — the value's emit shape doesn't match
   the relationship the name suggests; the producer site is doing
   something else (typically a structural-containment query) under
   a misleading name. Migration: producer site rewrites with a
   different relationship label or stops emitting. Goes on the
   `endpoint_shape` axis with a Phase 3 producer-rewrite plan.

The four leakage tests (per the audit playbook §3) inform each
verdict; the table below cites the test that fired most
diagnostically.

## Decision

### Dispatch family verdicts

| Value                  | Verdict     | Canonical fold target | Diagnostic test | Rationale (one-line) |
|------------------------|-------------|------------------------|-----------------|----------------------|
| `dispatches_to`        | CANONICAL   | (apex)                 | n/a             | Apex of single-target dispatch via runtime indirection. |
| `routes_to`            | FOLD        | `dispatches_to`        | Test 4 (mechanism vs. category) | HTTP routing IS dispatch via path matching; "route" is the dispatch mechanism, not a separate relationship. `meta["dispatch_kind"]="route"`. |
| `delegates_to`         | FOLD        | `references`           | Test 3 (construct vs. relationship) | Class-level delegation is a *declaration-time* binding, not a *runtime* dispatch — the emit site is the class body, not a call site. |
| `message_dispatch`     | FOLD        | `event_publishes`      | Test 2 (apex/peer overloading) | Misnamed: emit shape is publisher→subscriber, fits publish-family not dispatch-family. `meta["channel_kind"]="message_bus"`. |
| `annotated_dispatches` | FOLD        | `dispatches_to`        | Test 4 (mechanism vs. category) | Annotation IS the dispatch mechanism. `meta["mechanism"]="annotation"`. |
| `uses_dispatch_table`  | FOLD        | `references`           | Test 3 (construct vs. relationship) | Edge shape is code→data (the dispatch-table symbol), not dispatcher→target. `meta["construct"]="dispatch_table"`. |
| `di_registers`         | FOLD        | `references`           | Test 3 (construct vs. relationship) | DI registration is a declaration-time binding ("module declares X provides Y"), not runtime dispatch. `meta["mechanism"]="di_registration"`. |
| `di_resolves`          | FOLD        | `dispatches_to`        | Test 4 (mechanism vs. category) | Runtime DI resolution dispatches an interface to its implementation — DI is the mechanism. `meta["mechanism"]="di"`. |
| `registers_routes`     | FOLD        | `references`           | Test 3 (construct vs. relationship) | Router→route declaration is structural ("this router declares this route"), parallel to `di_registers`. |

**Net:** one canonical (`dispatches_to`), zero deprecate-no-fold,
eight folds split between two existing canonicals
(`dispatches_to` for runtime dispatch, `references` for
declaration-time bindings) with one cross-family fold to
`event_publishes`.

### Publish family verdicts

| Value                | Verdict           | Canonical fold target | Diagnostic test | Rationale (one-line) |
|----------------------|-------------------|------------------------|-----------------|----------------------|
| `event_publishes`    | CANONICAL         | (apex)                 | n/a             | Apex of producer→consumer over an async channel. |
| `event_subscribes`   | DEPRECATE-NO-FOLD | (TBD by Phase 3)       | Test 2 (apex/peer overloading) | Production emit shape is `subscriber → enclosing function` (a structural-containment query) under a name that suggests pub-sub semantics. The name and the shape don't match; producer site needs rewriting in Phase 3 (probably to `references` from sub→enclosing, or `contains` from enclosing→sub with reversed direction). |
| `crdt_publishes`     | FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | CRDT is a channel kind. `meta["channel_kind"]="crdt"`. |
| `annotated_publishes`| FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | Annotation is the publish mechanism. `meta["mechanism"]="annotation"`. |
| `emits`              | FOLD              | `references`           | Test 3 (construct vs. relationship) | Emit shape is `function → event_symbol` (a function references the event it emits), not pub→sub. |
| `enqueues`           | FOLD              | `event_publishes`      | Test 4 (mechanism vs. category) | Queue is a channel kind for async producer→consumer; Ruby ActiveJob `SomeJob.perform_later` is the canonical example. `meta["channel_kind"]="queue"`. |

**Net:** one canonical (`event_publishes`), one deprecate-no-fold
(`event_subscribes` — emit shape doesn't match name), four folds
to two existing canonicals.

## Diagnostic findings worth naming

Two findings emerged from the audit that are worth recording even
though they don't change the verdicts:

**1. Cross-family misnaming.** `message_dispatch` carries the word
"dispatch" in its name but its production emit shape is
publisher→subscriber, fitting the publish family. The audit caught
this only because the four-test pass at per-pair level forced
inspection of the emit-site src/dst shape rather than relying on
the name. A name-only audit would have classified it under
dispatch.

**2. Subscriber-shape inconsistency.** `event_subscribes` has two
incompatible emit shapes across the codebase: production
(`event_sourcing.py:866`) emits `subscriber → enclosing function`
(structural containment), while every test fixture in
`test_slice.py` uses `subscriber → handler` (publish-family-shape
matching the name). This inconsistency is itself the leak —
producer code and test code have diverging mental models of what
the edge means. Phase 3 producer migration must pick one and
update either the producer or the tests; the audit recommends
migrating the producer (the smaller-blast-radius change).

## Registry updates landing with this ADR

For values currently classified `pending_classification` in the
canonical registry (`packages/hypergumbo-core/src/hypergumbo_core/edge_types.py`):

- `dispatches_to`: pending → **relationship** (CANONICAL)
- `event_publishes`: pending → **relationship** (CANONICAL)
- `routes_to`: pending → **endpoint_shape** (FOLD to `dispatches_to`)
- `di_resolves`: pending → **endpoint_shape** (FOLD to `dispatches_to`)

Resolver values (`resolver_implements`, `resolver_for_type`,
`openapi_implements`, `implements_rpc`) remain at
`pending_classification` — they're not in scope for this ADR's
families and need their own audit (out of scope; tracker item to
be filed separately if/when Phase 3 reaches the resolver
producers).

For values previously emitted but absent from the registry
(surfaced by the comprehensive sweep at PR #3464 close-out and
verified against current emit sites), all are added with the
classification their fold target implies:

- `delegates_to` → endpoint_shape (FOLD to references)
- `message_dispatch` → endpoint_shape (FOLD to event_publishes)
- `annotated_dispatches` → endpoint_shape (FOLD to dispatches_to)
- `uses_dispatch_table` → endpoint_shape (FOLD to references)
- `di_registers` → endpoint_shape (FOLD to references)
- `registers_routes` → endpoint_shape (FOLD to references)
- `event_subscribes` → endpoint_shape (DEPRECATE-NO-FOLD; producer rewrite TBD)
- `crdt_publishes` → endpoint_shape (FOLD to event_publishes)
- `annotated_publishes` → endpoint_shape (FOLD to event_publishes)
- `emits` → endpoint_shape (FOLD to references)
- `enqueues` → endpoint_shape (FOLD to event_publishes)

`SCHEMA_VERSION` bumps minor (additive: 11 new enum values + 4
axis reclassifications).

## Migration impact on Phase 3 (WI-mokam-jalig)

Phase 3 producer migration now has concrete rename targets for
both families. The migration table:

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
| `event_subscribes` (TBD)  | (Phase-3 decision)    | (Phase-3 decision) |

`event_subscribes` is the one entry Phase 3 must make a producer
decision on rather than mechanically renaming.

## Consequences

### Positive

- **Phase 3 unblocked**: producer migration sites for the dispatch
  and publish families have concrete canonical rename targets and
  clear `meta` shapes for the differentiating facts.
- **One concrete bug surfaced**: `event_subscribes`'s shape/name
  mismatch is now documented and tracked through Phase 3.
- **Cross-family error caught**: `message_dispatch`'s misclassification
  under "dispatch" was invisible at the name level; the audit's
  emit-site inspection pulled it out.
- **Both audits complete in one PR**: shared methodology section
  amortizes the explanation cost.

### Negative

- **Schema bump**: minor version (additive — eleven new enum
  values + four axis reclassifications). Standard cost.
- **The `meta` schema gains four new keys** (`dispatch_kind`,
  `mechanism`, `channel_kind`, `construct`) used by the Phase 3
  rename. None of these are mandatory; they're added per-edge as
  the producer migrations land.

### Risks

- **The `event_subscribes` Phase-3 decision is a judgement call**,
  not a mechanical rename. If the producer team picks "drop the
  edge entirely" (since the information is recoverable from
  Symbol.span), there will be a small loss of graph navigability.
  Mitigation: audit the consumer queries that follow
  `event_subscribes` edges before Phase 3 lands.
- **Sweep-discovered values** (`registers_routes`, others) hint
  that the family-membership lists aren't exhaustive at
  declaration time. Future analyzer additions may add more
  publish/dispatch-shaped values; the cadence-hook audit catches
  them periodically.

## Related

- **Generalizes**: ADR-0023 §5's deferral to per-family audit; this
  ADR completes that deferral for both named families.
- **Pattern**: ADR-0024's axis-declaration template is the abstract
  shape; this ADR is a per-family classification within an
  existing axis (Edge.edge_type), not a new axis declaration.
- **Surfaced sweep finding**: WI-tavas-voror's emitted-but-unregistered
  triage. The audit consumes those findings and fold-classifies
  them; coordinating updates to that item's discussion thread.
- **Unblocks**: WI-mokam-jalig (ADR-0023 §6 Phase 3 — Unify
  producers) for the dispatch and publish families.
