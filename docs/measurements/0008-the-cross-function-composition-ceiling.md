<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0008 — the §4a cross-function composition ceiling

**Question.** Measurement 0007 established that 76.3% of never-ran §3a walks
bail at `sink_node != source_fn`, and named ADR-0017 §4a function summaries as
what would lift it. That is the size of the BLOCKER. This measures the size of
the REMEDY: of the flows the production binary blocks at `cross_function`, how
many could a summary-composed walk actually reach — and if it reached them,
would anything a user reads change?

**Answer.** At the composition ceiling, **18.3% of cross-function findings**
(778 of 4,245) satisfy the necessary conditions for a single-hop composed walk,
with a per-repository median of 22.3% and a range of 0%–100%. But the payoff
splits hard by direction, and the useful half is not the half WI-kabif asks
about:

* **For REFUTATION — the payoff is bounded by zero, and §4a does not change
  that.** Removal authority can only act on `unconfirmed` (the walk ran and
  established no dependence). Across the same 11 repositories, of the 28 walks
  that ran, **14 confirmed, 14 escaped, and 0 were unconfirmed** — zero in
  every repository. Composition creates *running* walks; on the observed split
  it would create confirmations and escapes, not the class removal needs.
* **For CONFIRMATION — the effect is real, and it INFLATES the output rather
  than shrinking it.** `analysis_method` is part of the collapse key and
  adjudicated flows bypass collapse entirely (`taint.py:641`), so every
  newly-confirmed flow becomes **its own row** while the `ddg_mixed` group it
  left behind survives with one fewer member. At the ceiling that is up to 778
  new `precise` rows on a cohort that currently emits 298 rows in total.

So the honest report is that §4a is **not** the gate on WI-kabif's removal arm
that 0007's framing implied. It is a labelling capability with an unpriced
row-inflation cost, and the two should be decided separately.

## Population

Census, not a sample. The 11 repositories of measurement 0007 (themselves 0006's
16 filtered to those carrying at least one `ddg_mixed` situation), the same six
generic claims (`docs/example-claims/generic-taint-claims.yaml`), on dev
`daafb0abb1`. Two units are reported throughout and never mixed:

* **findings** — flows as constructed, before `collapse_unadjudicated_flows`.
  This is the unit the walk operates on.
* **evidence rows** — what survives collapse and reaches the reader. This is
  0007's unit and the unit any payoff claim must be quoted in.

The ratio between them is **40:1** pooled and wildly uneven (beads 2,650 → 58;
cert-manager 30 → 0). A percentage quoted at the findings level and read as a
user-visible effect would be overstated by roughly that factor.

## Method

`scripts/measure-cross-function-reach.py`. For every finding the production
binary stamps `walk_blocked_by == "cross_function"`, it records the call-graph
route and evaluates the conditions a composed walk needs:

| condition | why it is necessary |
|---|---|
| `hops == 1` | the source function calls the sink's function directly — the only shape single-hop composition addresses |
| `sink_in_ddg` | the sink's function has reaching-def coverage; without it a composed walk has nothing to walk |
| `all_in_ddg` | every function on the route has coverage — the condition for composing a whole chain |

**These are necessary, not sufficient.** Sufficiency additionally requires
knowing that the tainted value is an *argument* at the call, which is precisely
`param_to_calls` — a field that exists only in ADR-0017's text and nowhere in
`packages/` (WI-famig). Every figure here is therefore a ceiling, and the
achievable number is strictly below it.

**Three independent reconciliations against 0007**, which is the strongest check
available here and the reason the instrument is believed:

1. Cross-function evidence rows total **106**; 0007 independently reports
   `cross_function` = 106 of 139 `not_attempted`. Exact.
2. The per-repository verdict census reproduces 0007's result table
   **row for row**: 14 confirmed / 0 unconfirmed / 14 escaped / 139
   not_attempted / 131 unavailable, 298 rows.
3. Per-repository, cross-function rows plus the other two blockers reconcile to
   0007's `not_attempted` column (beads 58 + 16 = 74; jaeger 9 + 2 = 11; …).

## Result — findings level

| repo | xf findings | 1 hop | 1hop+ddg | sink_ddg | all_ddg | ceiling |
|---|---:|---:|---:|---:|---:|---:|
| ArkLib | 8 | 8 | 8 | 8 | 8 | 100.0% |
| beads | 2650 | 663 | 565 | 2422 | 1790 | 21.3% |
| cert-manager | 30 | 12 | 11 | 25 | 23 | 36.7% |
| cilium | 953 | 114 | 76 | 531 | 198 | 8.0% |
| gocryptfs | 400 | 101 | 93 | 345 | 331 | 23.2% |
| jaeger | 58 | 22 | 20 | 56 | 36 | 34.5% |
| kamaraflow | 0 | — | — | — | — | EXCLUDED |
| mobx | 2 | 2 | 0 | 0 | 0 | 0.0% |
| plausible | 7 | 7 | 5 | 5 | 5 | 71.4% |
| session-desktop | 7 | 1 | 0 | 4 | 4 | 0.0% |
| spacedrive | 130 | 0 | 0 | 82 | 0 | 0.0% |
| **POOLED CENSUS** | **4245** | **930** | **778** | **3478** | **2395** | **18.3%** |

**kamaraflow is EXCLUDED, not zero.** It produces no cross-function findings at
all — all 6 of its blocked walks stop at `source_not_tracked`. Its zero measures
a different blocker, so reporting it as a 0% ceiling would be a category error.

**The pooled figure is a beads statistic wearing a cohort's name.** beads alone
is 62.4% of pooled findings, and the per-repo ceiling ranges 0%–100% with a
median of 22.3%. Per INV-duvup, the pooled number is a pooled census and not the
estimator.

## Result — by language, and this cohort is a Go measurement

| language | xf findings | 1hop+ddg | ceiling |
|---|---:|---:|---:|
| go | 4000 | 729 | 18.2% |
| rust | 130 | 0 | **0.0%** |
| python | 82 | 38 | 46.3% |
| javascript | 24 | 11 | 45.8% |
| typescript | 9 | 0 | **0.0%** |

Go is **94.2%** of all cross-function findings, so the pooled 18.3% is
substantially the Go figure. Python and JavaScript show more than double the
ceiling on volumes too small to carry weight. Rust and TypeScript return a
structural zero — rust's 130 findings include none at one hop and none with a
fully-covered route, despite 82 having a covered sink.

## Result — hop distribution

| hops | findings | share |
|---:|---:|---:|
| 1 | 930 | 21.9% |
| 2 | 1062 | 25.0% |
| 3 | 707 | 16.7% |
| 4 | 527 | 12.4% |
| 5+ | 1019 | 24.0% |

**Depth is not neutral, and the deep tail should not be read as headroom.**
jaeger's 5-hop route runs from a `_test.go` helper through `internal/tenancy`
into `examples/hotrod` — implausible as a real call chain. Each additional hop
multiplies exposure to call-graph false edges, so composing along long routes
would carry taint down fabricated paths. The `all_in_ddg` figure of 56.4%
pooled is a ceiling that should NOT be pursued for that reason; 18.3% is the
number to act on.

## Pre-registration, scored

Filed at `p5_famig_08262026/DECISION-RULE-PREREGISTRATION.md` before the cohort
result was read (with the provenance of that claim itself corrected in place —
cert-manager had finished unread eleven seconds before the file was written).

**The expectation was 25–50% of cross-function rows becoming running walks,
central estimate 35%. It scored badly, and it was underspecified in a way worth
recording**: it never named a composition DEPTH. The single-hop ceiling is
18.3% (below the band); the all-hops ceiling is 56.4% (above it). The
pre-registration does not adjudicate between them — but its own invalidation
section says "the 1-hop figure is the one to trust", and under that reading the
prediction was too optimistic.

**The tripwire did not fire.** It said to suspect a much LARGER number; the
number is smaller, in the direction of less payoff, and three independent
reconciliations against 0007 corroborate the population.

**The pre-registered hypothesis — that §4a's value is in the CONFIRM direction,
not the REFUTE direction — is supported, with a consequence it did not
predict.** It anticipated a labelling gain. It did not anticipate that the gain
arrives as *new rows*: because adjudicated flows bypass collapse, a `ddg_mixed`
situation of 40 members with 10 confirmations becomes 11 rows, not 1 improved
one. That is a UX cost that has never been priced and does not appear anywhere
in WI-kabif's framing.

**The decision rule, applied.** It required, for the refutation case, either
>0 verdict changes or >10% of evidence rows at the ceiling. Neither is
reachable: the class removal acts on (`unconfirmed`) is 0 of 28 running walks,
so composition's refutation payoff is bounded at zero by the same evidence that
made 0007's §7a domain zero. **§4a does not unblock WI-kabif's removal arm.**

## What this does not support

* **Not a claim that §4a is worthless.** It is a claim about the removal arm.
  The confirmation payoff is real (up to 778 earned `precise` labels) and is a
  separate decision with a separate cost.
* **Not a measurement of what composition would actually achieve** — only of
  the conditions under which it could be attempted. `param_to_calls` does not
  exist, so the sufficient condition is unmeasured and untestable today.
* **Not a random sample.** Eleven repositories inherited from 0006 via 0007,
  94% Go by finding volume, one repository contributing 62% of the pooled
  count.
* **The 0-of-28 unconfirmed rate is a small denominator.** It is consistent
  across every repository and across 0007's independent run, but 28 walks is
  not a large base, and the extrapolation from "unconfirmed is 0 among walks
  that run today" to "unconfirmed would be ~0 among walks composition makes
  run" is an inference, not a measurement. It is the single number most worth
  re-checking if §4a is ever built.
