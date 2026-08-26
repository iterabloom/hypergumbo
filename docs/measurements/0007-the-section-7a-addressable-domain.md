<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0007 — the ADR-0017 §7a addressable domain is zero on this corpus

**Question.** ADR-0017 §7a would give the DDG forward walk authority to *remove*
a flow. Pricing that removal as "drop every finding labelled `ddg_mixed`" gave
+6.3 precision points on measurement 0006 — and tripped WI-kabif's own
pre-registered tripwire, which says in the item's words: *"Addressable domain is
therefore about 18 percent of flows. If a future measurement reports a much
larger number, suspect the measurement."* It reported 26.8%. This measures what
`ddg_mixed` actually contains.

**Answer.** Of 153 `ddg_mixed` evidence rows across 11 repositories, **zero**
rest on a walk that ran and established no dependence. 90.8% rest on a walk that
**never ran**, and the remaining 9.2% on a walk that ran and **lost track of the
value**. Every removal §7a would authorise under this pricing is
removal-on-ignorance. The tripwire was right, and the +6.3 figure is not an
upper bound in need of refinement — it was priced on the wrong field.

## Why the field was wrong

`analysis_method` answers *which analysis produced this finding*. It was also
being asked *what did the walk conclude*, and it cannot: the call site collapses
the walk's three-valued return with `is True`, and the label is then chosen on
`fn_has_ddg` — *did the DDG cover the source function*. So everything that is
not a confirmation lands on `ddg_mixed`.

INV-zidur named two facts hiding there (the walk returned `False` vs `None`).
Re-derived at the call site there are **three**, and the third is the one that
matters: every guard above the walk can fail with `fn_has_ddg` still true, and
the finding is stamped `ddg_mixed` anyway. `ddg_mixed` means *the DDG covered
the function* — not *the walk looked and found nothing*.

`TaintFlowFinding.walk_verdict` now records the walk's own result.
`analysis_method`'s published vocabulary is unchanged, so every `ddg_mixed` in
measurement 0006 and in `docs/VERIFY-CLAIMS-SCOPE.md` still says what it said.

## Method

Census, not a sample. `verify-claims --format json` with the six generic claims
(`docs/example-claims/generic-taint-claims.yaml`), on the 11 of measurement
0006's 16 repositories that carry at least one `ddg_mixed` situation. Unit is the
**evidence row**; no verdict reached the 100-row evidence cap, so no count is
truncated.

**Three arms, and the last two are controls on the instrument rather than on the
tool.** Arm A established the verdict split. Arm B added `walk_blocked_by` and
re-ran the whole cohort: the verdict table is byte-identical to A, so the
instrument does not perturb what it measures. Arm C wired INV-lupav's
`forfeit_refutation` gate to the §3a walk — which the refutation-gate contract
test *required* once this change started consuming the walk's `False` rather
than collapsing it — and re-ran again: byte-identical to B, per repository as
well as in total. That is the expected result and worth stating as one, since a
gate that downgrades an unearned `False` to `None` can only move rows from
`unconfirmed` to `escaped`, and `unconfirmed` was already zero.

## Result

| repo | confirmed | unconfirmed | escaped | not_attempted | unavailable | rows |
|---|---:|---:|---:|---:|---:|---:|
| ArkLib | 1 | 0 | 0 | 2 | 4 | 7 |
| beads | 7 | 0 | 8 | 74 | 24 | 113 |
| cert-manager | 0 | 0 | 0 | 1 | 8 | 9 |
| cilium | 5 | 0 | 1 | 26 | 29 | 61 |
| gocryptfs | 0 | 0 | 0 | 7 | 5 | 12 |
| jaeger | 1 | 0 | 3 | 11 | 9 | 24 |
| kamaraflow | 0 | 0 | 2 | 2 | 3 | 7 |
| mobx | 0 | 0 | 0 | 4 | 9 | 13 |
| plausible | 0 | 0 | 0 | 5 | 24 | 29 |
| session-desktop | 0 | 0 | 0 | 6 | 8 | 14 |
| spacedrive | 0 | 0 | 0 | 1 | 8 | 9 |
| **TOTAL** | **14** | **0** | **14** | **139** | **131** | **298** |

`ddg_mixed` = `unconfirmed` + `escaped` + `not_attempted` = **153 rows**, split
**0 / 14 (9.2%) / 139 (90.8%)**. `unconfirmed` is zero in *every* repository,
not merely in aggregate.

## Why the walk did not run

The first guard that stopped it, over the 139 `not_attempted` rows. These
**partition** the population — a remedy for the top blocker promotes flows to the
*next* one, not to the walk, so the categories must not be added up as if each
were independently addressable.

| blocker | rows | share |
|---|---:|---:|
| `cross_function` | 106 | 76.3% |
| `source_not_tracked` | 24 | 17.3% |
| `sink_before_source` | 9 | 6.5% |

§3a is intraprocedural by construction, and three quarters of the population it
never reached is exactly that limit. WI-kabif predicted it in advance from a
different cohort — *"69 percent of flows have source and sink in different
functions, which intraprocedural DDG cannot connect by construction"* — and this
is 76.3% measured a second way.

## What this settles, and what it does not

**Settles:** §7a's removal authority cannot be priced on `analysis_method`, and
on this corpus it has no evidence-backed domain at all. WI-kabif's design call —
build §7a or retire it — is answered *neither*: it is blocked on §4 function
summaries (its own discussion's blocker 5), not on labelling and not on the
removal rule. `sink_before_source` at 6.5% is a floor rather than a real bound —
WI-pohib already records that the test is lexical, so a sink in a closure that
executes later reads as sink-first.

**A third line of work terminates in the same place.** INV-fumod's
resource-selection barrier — an I/O primitive's return does not inherit its
argument's taint — was implemented and run over this same cohort as a fourth
arm. It is **byte-identical to arm C on all 11 repositories**: correct on a
fixture with a discriminating control, and inert here. The reason is the same
§4 gap. Take jaeger's refuted situation: `with open(args.output, 'w') as f:
f.write(summary)`, where `summary` came from `parse_diff_file(args.diff)` — the
external read happens *inside a first-party callee*, so no catalogue rule can
see the crossing. `cross_function` is not only why the walk does not run; it is
also why a correct intraprocedural precision rule has nothing to bite on.

**Does not settle:** anything about *recall*, which this does not measure.
Anything about the 131 `unavailable` rows, where no DDG existed at all. Whether
the 14 `escaped` rows would refute if INV-busis's escape sites were closed —
that is the one population where closing escapes could move the number, and it
is 9.2% of `ddg_mixed`, 4.7% of all rows. And the cohort is 0006's, so it
inherits 0006's repository selection; it is a census *within* those repositories
rather than a fresh sample, and the row unit is not 0006's canonical situation
unit.
