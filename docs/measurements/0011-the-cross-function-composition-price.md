<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0011 — what a composed walk costs in rows

**Question.** Measurement 0008 closed by naming an unpriced cost: adjudicated
flows bypass `collapse_unadjudicated_flows`, so every flow ADR-0017 §4a
composition confirms leaves the group it was collapsed into and becomes its own
row. WI-famig then recorded an ordering deadlock — the load-bearing question it
still wants answered ("would `unconfirmed` stay ~0 among walks composition
*makes* run?") needs a composition prototype, and the item forbids building the
prototype before the row cost is priced. **So: how many more rows would a user
read?**

**Answer.** At the composition ceiling the cohort's emitted rows go from
**3,166 to 4,470 — plus 1,304, a factor of 1.41**. At a 50% sufficiency rate
it is ×1.22, at 25% it is ×1.11. The price is a curve, not a point, because
the achievable rate is unmeasured and strictly below the ceiling.

Three results matter more than the headline:

* **0008's relative framing was computed against the wrong denominator.** It
  read "up to 778 new rows on a cohort that emits 298 rows in total", which
  implies roughly ×3.6. But 298 is measurement 0007's count of rows *carrying a
  walk verdict*, not the cohort's emitted rows, which are **3,166**. The
  absolute count was the right order; the ratio was overstated by about 2.5×.
* **The cost is not one row per confirmation, and the group-size distribution
  is why.** 1,549 composable findings produce only **+1,304** rows, because a
  group whose members are *all* composable vanishes rather than surviving one
  member lighter. 783 of 2,977 groups are touched; their sizes run from 1 to
  **104**, and 125 of them have ten or more members.
* **§4a would invalidate a premise the collapse design states quantitatively.**
  `collapse_unadjudicated_flows`' docstring justifies letting `ddg` bypass
  collapse on the grounds that it "is 6 of 359 census flows" — 1.7% — so
  "collapsing it would trade earned precision for 1.7% of the noise". That
  premise **reproduces today at 1.43%** (189 of 13,199 findings). At the
  ceiling composition takes it to **13.17%, a 9× increase**. The argument for
  the bypass is a statement about a proportion, and §4a moves the proportion.

So §4a is not only "a labelling capability with an unpriced cost" (0008's
phrasing). It is a labelling capability that **forces a collapse-design
decision**, and the two cannot be taken separately.

## Frame

Machine-readable per ADR-0048 §A3. **This record is not a precision measurement
and does not enter the 0006 series** — nothing is adjudicated and no finding is
labelled true or false. It is a mechanical count of what the production collapse
function would emit under a stated hypothetical, so three of the eight keys
declare why the corresponding frame rule has nothing to govern rather than
naming a value.

- unit: the EMITTED ROW (what survives `collapse_unadjudicated_flows`). The finding is reported alongside it throughout and never mixed — the ratio is 4.2:1 pooled and reaches 58:1 on cilium
- allocation: CENSUS, not an M x R draw — every finding the production binary constructs in all 11 cohort repositories, 13,199 of them
- seed: no draw was made, so none was seeded — every finding in scope is measured
- cohort: measurement 0007's 11 repositories (itself 0006's 16 filtered to those carrying a `ddg_mixed` situation), unchanged so this record composes with 0008; kamaraflow reports EXCLUDED rather than zero, per its own instrument control
- claim_set: `docs/example-claims/generic-taint-claims.yaml`, the same six generic claims 0007 and 0008 used
- rubric: none is applicable and that is a declaration, not a gap — no item is judged. The counting rule is stated in full under "Method" and is arithmetic over the production collapse key, reproduced verbatim from `taint.py`
- analyzer_sha: c2686a6131 — established by CONTENT as well as by name: this tree carries the widened `axis_drift` collector (882e1206d5) and the rust grouped-use fix (7273edb844), both merged the same day and both upstream of the finding population measured here
- language_scope: whatever the 11 repositories contain — go dominates, as 0008 established (94.2% of its findings). NOT a language census: no language is measured at zero here, because a language absent from the cohort is absent from the denominator too

## Population

Census. 13,199 findings as constructed across the 11 repositories, collapsing to
3,166 emitted rows. Of the findings, **10,445 are blocked at `cross_function`**
and **1,549** of those meet 0008's necessary conditions for a single-hop
composed walk (`hops == 1` and the sink's function carries reaching-def
coverage).

## Method

`scripts/measure-row-inflation.py`. It replaces `TaintFlowFinding`,
`_reconstruct_path` and `propagate_taint_ddg` with recording wrappers — the same
hooking `scripts/measure-cross-function-reach.py` uses, so the two compose — and
records for every constructed finding the six fields of the production collapse
key, read off the CONSTRUCTED object rather than from kwargs so `__post_init__`
defaults are included:

```
(taint_label, source_symbol, sink_zone, sanitized, source_boundary, analysis_method)
```

Findings whose `analysis_method` is in `UNADJUDICATED_METHODS`
(`structural`, `ddg_mixed`) are grouped on that key and contribute one row per
group; everything else — `ddg` — contributes one row each, because
`collapse_unadjudicated_flows` appends it untouched. For a group of size `|G|`
with `p` members promoted to `ddg` by composition:

```
rows before = 1
rows after  = p + (1 if p < |G| else 0)
```

Under partial sufficiency at rate `r`, promoting each composable member
independently, the exact expectation for a group with `c` composable members is
`c*r + (1 if |G| > c else 1 - r**c)`. Reported at r = 1.00 / 0.50 / 0.25.

**The hypothetical is deliberately generous to §4a.** It assumes every
composable finding that composes is *confirmed*. On 0008's observed split a
running walk is as likely to `escape` as to confirm, and an escaped walk stays
`ddg_mixed`, so the true row cost at any given sufficiency rate is at most this
and probably about half it. That direction is stated because it is the direction
that makes §4a look *worse*, and an estimate that flatters the thing being
priced is the one to distrust.

## Control: the simulated collapse is checked against the real one

A model of collapse that merely *looks* right is the failure this control
exists to prevent, and the simulation makes one assumption production does not:
it pools all findings globally, while `verify-claims` calls
`collapse_unadjudicated_flows` two to four times on disjoint subsets. If any
group straddled a call boundary the simulation would under-count rows and
overstate inflation. Measured by spying on the production function:

| repo | collapse calls | real findings → rows | simulated |
|---|---:|---|---|
| jaeger | 4 | 290 → 133 | 290 → 133 |
| gocryptfs | 3 | 745 → 119 | 745 → 119 |
| cert-manager | 2 | 82 → 32 | 82 → 32 |
| ArkLib | 2 | 15 → 6 | 15 → 6 |

Exact on all four, including the repo with the largest inflation ratio. The
pooling assumption is validated rather than assumed.

## Result — the price

| repo | findings | rows today | xf findings | composable | ceiling | rows @r=1 | delta | x |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| beads | 7011 | 2024 | 5513 | 1138 | 20.6% | 2979 | +955 | 1.47 |
| cilium | 4479 | 606 | 3817 | 221 | 5.8% | 803 | +197 | 1.32 |
| jaeger | 290 | 133 | 196 | 31 | 15.8% | 150 | +17 | 1.13 |
| gocryptfs | 745 | 119 | 647 | 127 | 19.6% | 229 | +110 | 1.92 |
| plausible | 191 | 102 | 7 | 5 | 71.4% | 104 | +2 | 1.02 |
| spacedrive | 264 | 101 | 197 | 0 | 0.0% | 101 | +0 | 1.00 |
| cert-manager | 82 | 32 | 51 | 19 | 37.3% | 49 | +17 | 1.53 |
| mobx | 44 | 22 | 2 | 0 | 0.0% | 22 | +0 | 1.00 |
| session-desktop | 78 | 21 | 7 | 0 | 0.0% | 21 | +0 | 1.00 |
| ArkLib | 15 | 6 | 8 | 8 | 100.0% | 12 | +6 | 2.00 |
| **POOLED** | **13199** | **3166** | **10445** | **1549** | **14.8%** | **4470** | **+1304** | **1.41** |

kamaraflow is **EXCLUDED**, not zero: it constructs 29 findings and none is
blocked at `cross_function`, so its zero measures a different blocker (0008
reached the same verdict by the same control).

Per repository the price is wildly uneven and **anti-correlated with size**.
The two repositories that dominate the finding count inflate least in
proportional terms (beads ×1.47 on 2,024 rows, cilium ×1.32 on 606), while the
smallest inflates most (ArkLib ×2.00 on 6 rows). Pooling hides that; the pooled
×1.41 is substantially a beads statistic, as INV-duvup warns.

| rate | rows | delta | factor |
|---|---:|---:|---:|
| r = 1.00 (ceiling) | 4,470 | +1,304 | ×1.412 |
| r = 0.50 | 3,850 | +684 | ×1.216 |
| r = 0.25 | 3,514 | +348 | ×1.110 |

## Result — 0008's ceiling has drifted, and its per-repo table is stale

Re-deriving 0008's own headline on this tree, six days and roughly ninety
commits later:

| repo | 0008 xf | today xf | growth | 0008 ceiling | today ceiling |
|---|---:|---:|---:|---:|---:|
| beads | 2650 | 5513 | 2.1x | 21.3% | 20.6% |
| cilium | 953 | 3817 | 4.0x | 8.0% | 5.8% |
| gocryptfs | 400 | 647 | 1.6x | 23.2% | 19.6% |
| spacedrive | 130 | 197 | 1.5x | 0.0% | 0.0% |
| jaeger | 58 | 196 | 3.4x | 34.5% | 15.8% |
| cert-manager | 30 | 51 | 1.7x | 36.7% | 37.3% |
| ArkLib | 8 | 8 | 1.0x | 100.0% | 100.0% |
| plausible | 7 | 7 | 1.0x | 71.4% | 71.4% |
| session-desktop | 7 | 7 | 1.0x | 0.0% | 0.0% |
| mobx | 2 | 2 | 1.0x | 0.0% | 0.0% |
| **POOLED** | **4245** | **10445** | **2.5x** | **18.3%** | **14.8%** |

The pooled ceiling falls **18.3% → 14.8%**: the cross-function population grew
2.5× while the composable subset grew only 2.0×. Five repositories are
byte-identical (ArkLib, plausible, session-desktop, mobx, and kamaraflow's
exclusion), five moved, and jaeger's ceiling **halved**.

**This is not a defect and it is not drift in the pejorative sense** — it is
six days of taint and analyzer work landing. It is recorded because a ceiling
quoted from 0008 today would be wrong by 3.5 points, and because the shape of
the change is informative: **findings grew far faster than rows**. jaeger's
findings went 111 → 290 while the rows carrying a walk verdict went 24 → 26.
Collapse absorbed almost all of it. That is the same mechanism this record
prices, observed from the other side.

## Result — the group-size distribution, which is what makes the cost non-linear

783 of 2,977 groups contain at least one composable member. Their sizes:

| members | 1 | 2 | 3 | 4 | 5 | 6–9 | 10–19 | 20–49 | 50+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| touched groups | 141 | 98 | 104 | 98 | 48 | 169 | 82 | 27 | 8 |

The eight largest carry 52, 54, 57, 58, 81, 86, 88, 94 and 104 members. A
promotion out of a 104-member group costs one new row and leaves the group
standing; a promotion out of a 1-member group costs nothing at all, because the
group vanishes as the row moves. That is why 1,549 promotions yield +1,304 rows
rather than +1,549, and why any estimate that multiplies a confirmation count by
one row is wrong in both directions at once.

## What this does not support

* **It is not a measurement of whether composition would confirm anything.**
  Sufficiency requires `param_to_calls`, which does not exist; every figure here
  is conditional on a rate this record cannot observe. The r-curve is offered
  precisely so no single number pretends otherwise.
* **It does not answer WI-famig's load-bearing question.** Whether `unconfirmed`
  stays ~0 among walks composition *makes* run is still unmeasured, and still
  needs a prototype. What this record does is remove the stated reason not to
  build one.
* **It is not a claim that the inflation is bad.** A row a user reads is not
  automatically noise: 1,304 rows that each carry a *confirmed* data dependence
  may be worth more than the 1 collapsed row they left. This record prices the
  change; whether the trade is good is a judgement about what the output is for.
* **The pooled figure is a beads statistic.** beads supplies 53% of findings and
  73% of the composable set. The per-repo table is the one to read.

## What follows

The deadlock WI-famig recorded is broken in one direction only: the row cost is
now priced, so "do not build the prototype before pricing the cost" no longer
blocks. It is replaced by a sharper question that did not exist before this
record — **§4a and the collapse key cannot be decided separately**, because the
bypass that makes composition expensive is justified by a proportion composition
changes 9×. Three options, none costed here:

1. Build §4a and accept ×1.11–×1.41. The rows are more precise; there are more
   of them.
2. Build §4a and revisit the `ddg` bypass, so a composed confirmation collapses
   with its siblings and carries the confirmation as a field. Cheaper in rows,
   and it re-opens a ruling INV-karud settled on a premise that no longer holds.
3. Do not build §4a. The refutation payoff is bounded at zero (0008) and the
   confirmation payoff is a labelling improvement on at most 14.8% of
   cross-function findings.
