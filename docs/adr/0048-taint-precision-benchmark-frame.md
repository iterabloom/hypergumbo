<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0048: The Taint-Precision Benchmark — Equal Allocation, Declared Frame, Pinned SHA

- Status: **Accepted**
- Date: 2026-08-28
- Supersedes: —
- Superseded by: —
- Related: ADR-0046 (two-axis precision — the metric this frames; it names INV-duvup as "the series this unblocks"). ADR-0017 (the analysis being measured). Measurement records [0001](../measurements/0001-taint-flow-precision.md) (the rubric, cited verbatim by every measurement since), [0005](../measurements/0005-taint-precision-after-vocabulary-split.md) (the concentration failure), [0006](../measurements/0006-taint-precision-under-the-ratified-frame.md) (the first measurement under this frame; the series restarts there). Tracker items: INV-duvup (the defect), WI-sivuz (the decision bands, pre-registered separately).

**Decision provenance.** The frame recorded here was **ratified by the owner on
2026-08-25** and has governed one measurement already. This ADR does not make a
new ruling; it moves a ruling that lived only in a lab-notebook file
(`frame_08252026/FRAME-PROPOSAL.md`) into the repository, so that it is
citable, version-controlled beside the code it measures, and — for the first
time — **enforced**. Three additions are new and are marked as such in
§"What this ADR adds".

## Context

### The defect

INV-duvup: three consecutive precision measurements each disclaim comparability
with the one before, so the question the whole taint campaign exists to answer
— *is precision improving?* — is structurally unanswerable. Two mechanisms:

**The estimator changed silently.** 0001 is population-weighted; 0005 is a raw
pooled census. Neither headline says which it uses, or that it differs from the
last one.

**Concentration was diagnosed once and then recreated.** 0004 dropped pretix
*because* it supplied 77% of that measurement's population; 0005 then let caddy
supply 75% of its rows. On 0005's own adjudications, pooling choice alone moved
the row rate across a **2.8x range** with no adjudication changed:

| estimator | per situation | per row |
| --- | --- | --- |
| pooled census (the 0005 headline) | 21.6% | 10.0% |
| unweighted mean over all 5 repos | 10.7% | 5.9% |
| unweighted mean, 2 substantive repos | 26.8% | 14.8% |
| caddy excluded entirely | 19.0% | 16.7% |

A rate that swings 2.8x on an undeclared choice is not a measurement of the
tool.

### The insight that makes the estimator question go away

The ratified frame does **not** pick an estimator. It removes the choice:

> **Sample exactly M situations from each of R qualifying repositories.**

Under equal allocation the pooled rate and the unweighted per-repo mean are
*the same number by construction*. No repo can dominate, and there is no
weighting scheme left to argue about. It also disposes of the other half of the
0005 problem — a repository contributing two records can no longer be reported
as "0.0% precision", because a repository that cannot supply M is not in the
cohort at all.

Measurement 0006 is the first application: **M=7 situations x R=16
repositories = 112 adjudications**, every repository contributing exactly
seven. Both estimators give 44/112 = 39.3%. The 2.8x span is zero by design.

## Decision

### The frame (ratified 2026-08-25, binding from measurement 0006)

- **F1 — Unit.** The *situation* is canonical; the row rate is reported beside
  it **every time**, never instead. A situation is the post-INV-karud collapse
  record. Cost stated: per-situation precision treats a situation standing for
  100 pairs identically to one standing for 1, which is why the row rate is
  always published alongside.
- **F2 — Allocation.** Exactly M situations from each of R qualifying
  repositories. `M x R` is the adjudication budget and the only real cost knob.
- **F3 — Sampling within a repository.** Simple random sample under a **seed
  declared in the record before any drawing**, never "the first M".
- **F4 — Cohort rule, fixed before screening.** Qualification is by supply
  (>= M situations) and by language balance: at least 3 languages, no language
  more than half the cohort. Ties broken by the declared seed, never by
  inspection.
- **F5 — Claim set.** The seven generic claims, unchanged.
- **F6 — Rubric.** Measurement 0001's, **cited verbatim, never rewritten** —
  the one component that escapes the contamination problem, because it was
  written before any of these numbers existed.
- **F7 — Adjudication protocol.** 0005's blind pass plus adversarial pass,
  because it demonstrably caught errors and moved the headline twice, in
  opposite directions.
- **F8 — The headline must declare its own frame.** Every measurement states
  unit, allocation, seed, cohort, claim set and rubric version. A headline that
  does not name its frame is what made 0001/0004/0005 incomparable.
- **F9 — Shortfall fallback.** If fewer than R repositories qualify at the
  chosen M, **hold R and reduce M**, floor M=5; if R cannot be reached even at
  M=5, **stop and report** — do not relax language balance and do not shrink R.
  Rationale: the frame exists to control concentration, and R is the variable
  that controls it, so shrinking R to protect M trades away the thing being
  fixed. *(Pre-registered in the ratified document as PROPOSED; adopted here.)*

## What this ADR adds

Three requirements the ratified frame does not carry. Each is recorded as new.

### A1 — The analyzer SHA is part of the frame

Eighteen commits changed taint behaviour between the 2026-08-25 screen and the
2026-08-27 figures. Attribution across that boundary is **not recoverable** —
not by reasoning, not by re-reading the record. A precision series whose
instrument changes between points measures the instrument and the subject at
once and cannot separate them.

So every measurement records the SHA of the tree it was produced by. This is
the cheapest possible discipline and its absence has already cost one
attribution.

**0006 does not carry one and cannot be given one honestly.** No artifact of
that run records the tree it ran against; a SHA reconstructed from dates would
be a guess wearing a fact's clothing, which is the failure this ADR exists to
prevent. It is recorded as `unrecorded` — an explicit, greppable admission
rather than a silent absence — and the gate below refuses that value for any
new measurement.

### A2 — The language scope is declared, and an exclusion is not a zero

A cohort drawn from fifteen catalogued languages in which five produce **zero
taint flows for a structural reason** is a three-language cohort wearing a
fifteen-language label — INV-duvup's own "two-repo cohort wearing a five-repo
label", one level up.

INV-linub's exposure table predicts exactly which: languages at >=87%
method-kind sinks (java, scala, kotlin, objc, swift) produced 13 repositories
and **zero** flows on an independent screen, while languages at <=53% produced
828. Repairing that is recall work, which the WI-sivuz band stops while useful
precision is below it. So the frame does not repair it — **it declares it**:

> Every measurement names the languages in scope and the languages **excluded
> by name**, and for each exclusion the reason and its tracker item. An
> excluded language is EXCLUDED, not measured-at-zero. Pooling a structural
> zero into the headline is the same undeclared-estimator choice this ADR
> exists to forbid.

This makes the exclusion reversible and visible: when the band lifts and
INV-linub closes, those languages enter the cohort as a **declared scope
change** with a named before and after — which is the comparability the series
is for.

### A3 — F8 becomes a gate, not a rule

F8 is a rule with nothing enforcing it, and a rule that only a careful author
obeys is how 0001/0004/0005 drifted in the first place. `scripts/check-measurement-frame`
requires every measurement record from 0009 onward to carry a machine-readable
`## Frame` block naming unit, allocation, seed, cohort, claim set, rubric,
analyzer SHA and language scope. It runs in CI.

Records 0006-0008 predate the gate and are grandfathered by number in one
explicit list in the script, each with its reason. A new measurement cannot
join that list without editing the gate and saying why — which is the point.

## Consequences

**The series has a defined restart point.** 0006 is measurement N=1 under this
frame. Comparisons across the 0001/0004/0005 boundary remain unsupported and
are not made supportable by this ADR; what changes is that every measurement
from here is comparable to every other one.

**The adjudication budget is explicit.** `M x R` is a cost, and F9 spends it on
R rather than M when the two compete, because concentration is the thing being
controlled.

**A measurement can now fail to publish.** F9's "stop and report" and the A3
gate both make it possible for a measurement to be blocked on its own frame.
That is intended: a number that cannot state its frame is the failure mode this
ADR is named for.

**What this does not do.** It does not make 0006's figure comparable to 0005's;
it does not choose between correctness precision and useful precision (ADR-0046
settled that both are published); and it does not repair the zero-flow
languages, which stay excluded-and-declared until INV-linub closes.
