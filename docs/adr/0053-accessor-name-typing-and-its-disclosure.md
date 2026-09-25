<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0053: A declared relation-accessor name types the module slot, and says that it did

Date: 2026-09-10
Status: Accepted
Amends: WI-gulaz's implementation docstring clause "an untyped root" (its FILED refutation condition is NOT overturned — see "What is and is not amended")

## Decision

**When a receiver expression is `<root>.<accessor>` and `<accessor>` is a relation
accessor THIS project's Django models declare, the ORM module slot is filled even
though nothing is known about `<root>` — and every edge so filled carries
`resolution_quality="accessor_name"`, which `verify-claims` reads back so a clean
security verdict stays qualified rather than going silent.**

The two halves are one decision. Filling the slot without the disclosure would be
a recall gain bought with a quieter all-clear, which is the one direction this
project treats as unsafe.

## Context

`DjangoRelationIndex` reaches its accessor map only through `instance_class`, so
the rule required a root whose class had already resolved. A bare parameter
(`event.seats.filter(...)`) or an unresolved dotted path
(`request.event.seats`) never consulted the index at all, and the receiver fell
to the `external` sentinel — which ADR-0051 defines as *unreachable to the
catalogue*, i.e. a false negative by construction.

## What is and is not amended

WI-gulaz pre-registered a refutation condition. Its **filed** condition — an
accessor-like attribute on a receiver that **does not own it** stays untyped —
**stands, and is still enforced**. What is amended is one clause of the
*implementation docstring*, "an untyped root", which conflated two categorically
different things:

- **evidence against** — a resolved class that does not declare the name, a
  local bound to a non-model, a call the resolver already declined. Still
  refuses. `_refuted_by_a_known_owner` is that predicate.
- **silence** — a bare parameter the walker never had an input for. No longer
  refuses.

A gate may be tightened on silence, never opened on it; this opens on *absence
of evidence against*, not on absence of evidence. `make().seats` still refuses,
and that half of the original test is deliberately kept green.

## Evidence

Records: `~/hypergumbo_lab_notebook/mumov_tip_09102026/` (`ABLATION_PLAN.md`,
`ABLATION_RESULT.md`, `VERIFY_PLAN.md`, `VERIFY_RESULT.md` — both plans written
before any number existed).

**Recall, on pretix.** +2,943 ORM-slot call edges, **0 lost**. 2,867 promoted, of
which 2,856 (99.6%) had `external` among their prior slots; 58 created — protocol
edges that could not be emitted at all while the receiver had no type. The 11
promotions that never touched `external`, and all 58 created edges, were
adjudicated **exhaustively** against source: all correct.

**Is it keyed on Django or on names?** A shuffled-index ablation replaced the real
accessor set with 20 size- and frequency-matched WRONG sets. True index +2,943;
shuffled mean +37.3. **Ratio 78.9** against a kill threshold of **10** fixed
before the number existed; worst case 62.6.

That floor is itself mostly correct: 80% of it is one name, `fees`, which the
shuffle drew by accident and which is a genuine `@property`-wrapped related
manager the index does not see (filed as WI-valav). The headline stays 78.9 —
recomputing a pre-registered statistic on a decomposition that flatters it is not
a result.

**Direction, on the security path.** Four arms of `verify-claims`: 45 findings
generated, **0 lost** (5 apparent losses were re-identifications — the same flow
reported with a superset of source primitives), and **both shuffled controls moved
exactly 0 findings**. `sanitizer_scope` byte-identical across all four arms, so
the feared suppression channel never activates. No verdict changed.

**The limit, stated because it is the safety-relevant one.** That measurement
could NOT test whether the rule loosens an all-clear. `CAVEAT_UNTYPED_RECEIVER`
only qualifies CLEAN verdicts and is scoped to ADR-0016 boundary claims; all seven
generic claims are ADR-0017 taint claims and all come back violated on pretix.
That is structural — the boundary most sensitive to Django receiver typing is
`db_read`, which pretix crosses constantly — so no claims file fixes it there. The
question is answered by fixture instead
(`test_verify_claims_accessor_name_receiver_caveat.py`), where a correct and an
incorrect typing can both be constructed on purpose.

## The disclosure

`CAVEAT_ACCESSOR_NAME_RECEIVER` is the complement of `CAVEAT_UNTYPED_RECEIVER`
over one population. A site moves from that map to this one exactly when the
analyzer learns to type it, so the qualification is not dropped, it is made more
precise: *"I could not type N receivers"* becomes *"I typed N receivers from a
declared accessor name, not from a typed root"*, plus a commensurable denominator
("N of M method-call receivers in this analysis").

**The catalogue lookup is deliberately NOT scoped to the module the `dst` names.**
That module is the inference, and the inference is the thing that might be wrong.
If the name-typing is mistaken, `thing.seats` is not a manager at all — it could
be a dict, where `.get` is no I/O, or a `requests.Session`, where `.get` is a
`net_send`. Scoping to the believed module would assume the answer, and would also
make the caveat unreachable, since a `db_read` claim is violated by the very chain
that would qualify it.

## Alternatives rejected

**Refuse the inference.** Costs 2,943 correctly-slotted edges to avoid an error the
ablation prices at roughly 1 in 79.

**Ship it as a low-confidence edge.** `Edge.confidence` is inert on the security
path — zero reads in `taint.py` and `verify_claims.py` — so this would disclose
nothing. A disclosure nothing reads is not a disclosure.

**Stamp `type_inferred` and say nothing.** The status quo ante of this ADR's second
half, and the reason it has one: it is indistinguishable from a receiver typed off
a resolved class, so the two cannot be told apart downstream and the all-clear
quietly loosens.

## Consequences

- A Django repository gains ORM module-slot coverage on receivers whose root is a
  parameter or an unresolved path.
- A repository with **no** Django models is unaffected — `related_managers` is
  empty, so `declares_accessor_anywhere` answers False for every name. This is a
  property of the code, pinned by test, not a probability.
- Clean boundary verdicts in such repositories acquire a new caveat kind. This is
  the intended cost: the verdict was previously silent about these calls.
