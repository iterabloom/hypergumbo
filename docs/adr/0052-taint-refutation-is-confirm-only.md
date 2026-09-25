<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0052: Taint refutation is confirm-only in practice — the escape-closing goal is retired

Date: 2026-09-09
Status: Accepted
Supersedes: ADR-0017 §3a (the removal-COVERAGE ambition only; §3a's removal capability itself stays in force)

> Scope note, because the neighbouring section is easy to over-read: this ADR does
> NOT supersede ADR-0017 §7a. §7a describes field-sensitivity lite as a
> *propagation* feature (taint follows `.field` access) and that design is
> untouched. What is declined is INV-busis's option (b), which proposed
> repurposing §7a as an escape-CLOSING device so §3a could refute more often.
> §7a stays unwired, and whether to wire it for recall is still open.

## Decision

**hypergumbo's taint analysis confirms flows; it does not, in practice, remove them.
The programme of closing ADR-0017 §3a's escape sites so that refutation fires at
scale is RETIRED.** The capability is untouched — §3a still removes a flow whose
walk returns `unconfirmed`, and `INCLUSION_DECIDED_BY` still reads
`call_graph_reachability_minus_ddg_refutation` — but the coverage that removal
would need in order to fire is no longer a goal, and the shortfall is published to
users as a declared limitation rather than left to be inferred from a zero.

Concretely:

1. **Not pursued:** escape reclassification undertaken to make §3a refute more
   often — including INV-busis's option (b), which proposed field-sensitivity
   lite (ADR-0017 §7a) as the means. §7a stays unwired (`is_field_tainted`, zero
   production callers). Whether §7a is worth wiring for *recall* — finding flows
   that field access currently loses — is a SEPARATE question left open.
2. **Not adopted:** the Joern model (assume an unknown callee propagates taint;
   let the catalogue NARROW rather than enable). It is fail-open by construction.
3. **Not adopted:** narrowing refutation's domain to flows whose every escape is a
   catalogued terminating call. Sound, and inert — it addressed 0 of 51 walks when
   priced.
4. **Shipped in its place:** the per-run walk-verdict breakdown in
   `dataflow_coverage` (`count_walk_verdicts`), so `flows_removed_by_walk: 0` is
   readable rather than ambiguous.

## Context

### The measured blocker

For §3a to license removing a flow, every point at which the walk loses a tainted
value must be attributable to a callee whose summary says the taint terminates.
It is not: **66.7% of blocked-walk sites have no callee recorded at the line at
all** (48/72 unique on pretix; 90.6% excluding `source_undefined`; 75/127 pooled
over four repositories). The value is used at a line that defines nothing the DDG
tracked and invokes nothing — a field write, a container subscript, a closure
capture. There is no callee to look up, so no function summary can ever help.

### Option (b) was priced, and it is zero

INV-busis filed three options and said "do not pick one from the armchair — (b)'s
size is unmeasured, and that measurement is the next step." It was then measured.
[Measurement 0007](../measurements/0007-the-section-7a-addressable-domain.md): of
**153 `ddg_mixed` evidence rows across 11 repositories, ZERO** rest on a walk that
ran and established no dependence. 90.8% rest on a walk that never ran; the
remaining 9.2% on a walk that ran and lost track of the value. Every removal §7a
would authorise under that pricing is removal-on-ignorance.

### Closing escapes deletes findings, and the tool cannot check the deletion

A declared-sanitizer arm (2026-09-07, alertmanager / beads / kserve) established
the direction of the whole escape-closing family for the first time. With
sanitizers declared, the barrier arm gains a population (beads 0 → 122 barrier
walks), and a ceiling arm that closes every escape at once moves findings:
beads goes `violated 6 / sanitized 351 / evidence 230` → `violated 6 / sanitized
352 / evidence 225`. **The only reachable effect on findings is SUPPRESSION.**

That magnitude is an artefact of the declaration — the arm only had a population
because nine ordinary laundering functions (`fmt.Sprintf`, `errors.New`, …) were
declared sanitizers, and `fmt.Sprintf` does not neutralise untrusted input.
hypergumbo cannot check that claim; `--taint-sanitizers` exists precisely so a
project can make it about itself. So the machinery options (b) and (d) would buy
is machinery for deleting findings on unverifiable authority. For a tool whose
job is to surface what a reader would otherwise miss, that is the wrong trade.

### What made the decision safe to take

ADR-0017 §3a's removal authority is implemented and tested (WI-kabif, PR #716):
a refuted flow is removed on the production path with a non-vacuity floor, and
INV-sadah is satisfied on its own filed repro. Retiring the goal therefore
forfeits *coverage growth*, not a capability. Nothing is ripped out, and a
repository whose escapes do close still gets its removal.

## Consequences

### The limitation must be visible, so it ships as a disclosure

Retiring a goal without publishing its consequence is how a limitation becomes a
silent one. `dataflow_coverage` already emitted `flows_removed_by_walk`, but a
reader could not interpret a zero: `findings_by_analysis_method` rolls three walk
verdicts into `ddg_mixed` and a fourth into `structural`, and only `unconfirmed`
can remove a flow. "The walk adjudicated every flow and refuted none" and "the
walk never got to look at any of them" had identical evidence and opposite
meanings — the same defect `dataflow_scope` was built to prevent for
"0 precise findings", left standing on its own field.

`count_walk_verdicts` publishes the breakdown (`confirmed` / `unconfirmed` /
`escaped` / `not_attempted` / `unavailable`, plus `mixed` for a collapsed row
whose members disagreed and `unrecorded`), in both JSON and text, on every run.
A collapsed row is NEVER attributed to its first member: measured on beads,
63.9% of groups holding a `sink_before_source` member are not unanimous, so
counting the scalar would publish a clean, plausible, wrong breakdown in the
flattering direction.

Measured on hypergumbo's own repository the first time it ran: **`unavailable`
224 of 224** — the DDG built 177,518 edges and covered none of the flows' source
functions. The previous output had no way to state that.

### What a consumer should now assume

- A surviving finding was INCLUDED by call-graph reachability. A `ddg` label
  corroborates it; it does not decide it (INV-sadah's standing misreading).
- `flows_removed_by_walk: 0` is the normal case and is not evidence that the
  analysis examined the flows.
- Precision improvements will come from emission and receiver typing (the
  INV-linub family), not from teaching the walk to refute more.

### Costs accepted

- False positives that a field-sensitive or alias-aware analysis could refute
  stay in the output. This is the ADR-0017 §7b trade taken deliberately at the
  §3a layer as well: overapproximation is preferred to underapproximation.
- The `escaped` / `not_attempted` populations stay large and are reported rather
  than reduced.

### Re-open trigger

This decision is revisited if **either** holds: (1) a corpus measurement reports
a non-trivial `unconfirmed` population — walks that ran, exhausted every route,
and found no dependence — since that is the cell measurement 0007 found empty and
the cell that makes removal worth widening; or (2) hypergumbo gains a way to
VERIFY that a declared sanitizer sanitizes, which removes the fail-open objection
to options (b) and (d).

## References

- [ADR-0017](0017-taint-zone-dataflow.md) §3a, §4, §7a, §7b
- [Measurement 0007 — the §7a addressable domain is zero on this corpus](../measurements/0007-the-section-7a-addressable-domain.md)
- INV-busis (the measured blocker, the option set, and the owner ruling of 2026-09-09)
- INV-sadah (satisfied 2026-09-06), WI-kabif (done, PR #716 — §3a removal authority)
