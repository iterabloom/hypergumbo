<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Recovery-linker supersession precision — hypergumbo self-survey

| | |
|---|---|
| **Axis** | ADR-0057 §14.1 supersession grant (`RegisteredLinker.supersedes_consumed_stub`) |
| **Date** | 2026-09-20 |
| **Artifact** | `hypergumbo survey .` on this repository, single-backend Python (no SCIP backend in `analysis_runs`; zero `scip` origin tokens on 208,161 edges) |
| **Instrument** | full enumeration of each pass's emitted edges + hand audit of each against its source line |
| **Outcome** | Per-pass verdict on whether a derivation stamp may demote the stub it names. |

```yaml
kind: linker_supersession_precision
artifact: hypergumbo-self-survey-2026-09-20
date: 2026-09-20
passes:
  - pass: inherited-calls-linker
    edges_emitted: 329
    audited: 70
    wrong_in_audit: 0
    structurally_consistent: 329
    verdict: GRANTED
  - pass: method-call-recovery-linker
    edges_emitted: 64
    audited: 64
    wrong_in_audit: 16
    structurally_consistent: null
    verdict: REFUSED
```

## Why a grant is needed at all

§14.1 lets a pass that consumed an unresolved stub demote it by NAMING it in
`Edge.derived_from`. The stub is a producer's positive answer — "this callee is
outside the repo" — and demoting it ranks that answer below the consuming
pass's own. A pass that is wrong a quarter of the time would therefore bury the
right answer a quarter of the time, with a `superseded_by` stamp inviting the
consumer to trust it. The permission is declared per pass and granted on
measurement, which is ADR-0057 §5's discipline applied to a different
permission.

## `inherited-calls-linker` — GRANTED

329 edges emitted. Every one resolves a method call through a receiver type or
an enclosing class, walking the declared inheritance graph.

| probe | result |
|---|---|
| hand-audited against source | **70 of 329 (21%)** — 30 stratified across the three confidence bands, 30 simple random, 14 adversarially chosen for the highest in-repo homonym count (up to 51 same-short-name symbols) |
| wrong in the audit | **0 of 70** |
| target's class is the hinted receiver class, or a declared ancestor of it in the artifact's own `extends` / `implements` / `inherits` / `includes` graph | **329 of 329** — 184 direct (conf 0.85), 82 an ancestor of `enclosing_class` (conf 0.90), 63 an ancestor of the hinted receiver (conf 0.70). Zero unrelated, zero not-found |
| guards in force | `_SITE1_STRICT_LANGS` ambiguity refusal, the INV-guviv stdlib-shadow guard, no Step-3 fallback for Python |

**Not asserted:** that the 259 unaudited edges are correct. They are screened
against the inheritance graph, not read. The 30-draw simple random sample
bounds that population's wrong rate at **≤10% at 95%** (rule of three); the
point estimate including the adversarial draws is 0.

## `method-call-recovery-linker` — REFUSED

64 edges emitted, after the WI-rulik gate removed 28 that overrode a stated
module. All 64 hand-read.

| probe | result |
|---|---|
| wrong | **16 of 64 (25%)** |
| mechanism | the stub abstained on its module, more than one class hint has a method of that short name, and the line-proximity tiebreaker picks the wrong one |
| receiver evidence consulted | **none available** — `_declared_receiver_type` reads `receiver_type_hint` and **0 of 90** audited Python stubs carried one. The filter drops a hint that *contradicts* a stamped type and abstains when nothing is stamped: correct polarity, never armed |

### The shipped quality signal does not separate the wrong ones

`disambiguation_fallback` is stamped when more than one class hint matched, and
the edge's confidence is capped at 0.5. If that flag tracked correctness it
would license a conditional grant. It does not:

| | `disambiguation_fallback` set | not set | wrong rate |
|---|---:|---:|---:|
| **wrong** | 5 | 11 | — |
| **right** | 13 | 35 | — |
| *wrong rate* | 5/18 = **28%** | 11/46 = **24%** | |

Gating the grant on the flag would refuse 28% of the wrong edges and 27% of the
right ones — within noise of refusing at random. There is no shipped signal to
condition on, so the grant is refused outright rather than conditioned on a
number with no derivation.

**What would change the verdict:** receiver-type evidence that does not exist
yet. Every one of the 16 has a receiver whose type the repository states
somewhere; the Python producer does not stamp `receiver_type_hint` for these
shapes (local assigned from a constructor, annotated parameter, comprehension
target, attribute chain). That drain is WI-fihun. Supplying the evidence is
what recovers these edges; refusing to guess is only what stops them lying.

## Reading

A grant here is not a claim that a pass is correct — `inherited-calls-linker`
is granted on 21% audited and 100% structurally consistent, not on proof. It is
a claim that the pass is right often enough that ranking a producer's stub
below its resolution is better than not, and that the claim was measured rather
than assumed. The measurement is repo-specific: one Python-heavy corpus whose
own `to_dict` / `id` / `type` / `get` naming is unusually collision-prone. A
second corpus could move either verdict.
