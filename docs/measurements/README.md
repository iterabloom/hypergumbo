<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement records

This directory holds **measurement records**: the per-item verdict
tables produced when hypergumbo's own output is adjudicated against
ground truth read from source code.

## Why this is a sibling of `docs/audits/`, not part of it

[`docs/audits/README.md`](../audits/README.md) scopes itself
explicitly: its format is "shaped for **axis-conformance audits**" —
per-value verdicts from the CANONICAL / FOLD / DEPRECATE-NO-FOLD
trichotomy — and it says that audits with a different verdict shape
"should propose a sibling format rather than shoehorn into this one."

A precision measurement has a different verdict shape. Its unit is a
reported *finding*, not a vocabulary *value*; its verdicts are TRUE
POSITIVE / FALSE POSITIVE / UNADJUDICABLE; and its output is a rate
with a population attached rather than a migration plan. So it files
here.

## What belongs here

A measurement record, not a lab note and not a status page. Concretely:

- The **question**, stated so that a different answer would change a
  decision.
- The **population**: what was measured, how it was obtained, and
  whether it is a census or a sample. A rate without a named
  denominator is not a measurement.
- The **rubric**, fixed before the labelling and reproduced verbatim,
  together with any revision made after labelling started and the
  reason for it.
- The **verdict table**, one row per adjudicated item.
- The **disagreement rate** between independent passes, reported
  beside the headline number rather than folded into it.
- What the number does **not** support. Precision is not accuracy;
  a census of five repositories is not a random sample of software.

Numbers that describe the state of a tracker item still live on the
tracker item. What lives here is the evidence a reader would need to
disbelieve the number.

## Format

One markdown file per measurement, named `<NNNN>-<topic>.md`, numbered
independently of the ADR and audit-findings series.

## Index

| ID | Title | Instrument | Result |
|----|-------|------------|--------|
| [0001](0001-taint-flow-precision.md) | Taint-flow precision on real repositories | `scripts/measure-taint-precision.py` | ≈41% population-weighted (28 TP of 85 adjudicated across 6 repos); 19 further flows unadjudicable |
| [0002](0002-catalogue-reach-multilanguage.md) | Catalogue reach on Go, JavaScript and Java | `scripts/measure-catalogue-reach.py` | go **84.8% production-faithful** (Go arm corrected — the original 77.1% "all 45 method-kind unattributed" was a fixture artefact and its receiver-typing conclusion is withdrawn), javascript 97.3%, java 90.8% (static form only), python 97.7% |
| [0003](0003-instantiates-taint-delta.md) | What the construction-edge widening did to taint precision | `scripts/measure-taint-precision.py` | **2.9% marginal precision** (1 TP of 35 added flows, 0 removed, census of the delta across the same 6 repos); the change is correct and the trade is bad — 24 of 34 FPs are sink calls with no arguments at all |
| [0004](0004-taint-precision-per-situation.md) | Per-situation taint precision, and what the row denominator was hiding | `verify-claims --format json`, hand-adjudicated | **32.2% per situation vs 11.1% per row** (19 TP of 59 situations / 33 TP of 297 rows, census of 0001's cohort at one commit, two units); false positives concentrate in the largest groups — caddy's `cmdRun` is 76 rows, all false, collapsing to 3. Single-pass non-blind, ties resolved to FP, so both rates are floors |
| [0005](0005-taint-precision-after-vocabulary-split.md) | Taint precision after the fail-open fixes and the `env_read` split | `scripts/measure-taint-precision.py` (extended to report both units) | **21.6% per situation / 10.0% per row** (8 TP of 37 situations / 17 TP of 170 rows, census of 0004's cohort at `ba01153095`). Blind second pass by four independent adjudicators: **0.0% disagreement over all 37** — and the adjudication and adversarial passes that followed it moved the headline **twice, in opposite directions**. The population fell 297 -> 170 rows and **every row of that is one scope decision**: `host_description` ships one claim where `host_secret` has three, and adding the two probe claims restores 0004's count exactly, repo for repo |
| [0006](0006-taint-precision-under-the-ratified-frame.md) | Taint-flow precision under the ratified frame | `verify-claims --format json`, two independent 16-repo adjudication panels | **33.9% per situation / 31.5% per row** (38 TP of 112 situations / 106 TP of 336 rows, 16 repositories, 10 languages, seeded draw). Series position **N=1** — it does not continue 0001–0005 (INV-duvup). Two blind panels agreed **94.6%**, and a subsequent refutation pass killed **7 of 44** panel-agreed true positives, three of them unanimous — agreement is not correctness. Per INV-nular a finding can be true value-flow AND vacuous, so 33.9% is an **upper bound** on useful precision. **Useful precision re-derived under ADR-0046 (WI-gibom): 24.1%** (27/112) — **11** KIND-MISDECLARED, **0** CONFIGURED-ACTION, so the band `≤25% useful` is tripped. The ≤25.0% previously published here survives as a bound and fails as a derivation: its stated basis (five shellcheck TPs) supports 29.5%. One standing sensitivity — `io_lib:format` declared `logging` while its own catalogue note says it *"Returns iolist, not direct I/O"* — would take it to **21.4%**. Two further sensitivities first published here were **withdrawn**: the spec already defines `env_read` as an ambient configuration read *"(environment variables, system properties, argv — values that may carry a credential)"*, so argv and application config are not misdeclarations |
| [0007](0007-the-section-7a-addressable-domain.md) | The ADR-0017 §7a addressable domain | `verify-claims --format json`, census over 0006's cohort | **Zero.** Of 153 `ddg_mixed` evidence rows across 11 repositories, **0** rest on a walk that ran and established no dependence — 90.8% never ran, 9.2% lost track — and `unconfirmed` is 0 in *every* repository. Every removal §7a would authorise is removal-on-ignorance; the +6.3pt price was computed on the wrong field. Blocked walks partition **cross_function 76.3% / source_not_tracked 17.3% / sink_before_source 6.5%** |
| [0008](0008-the-cross-function-composition-ceiling.md) | The §4a cross-function composition ceiling | `scripts/measure-cross-function-reach.py` | **18.3% pooled ceiling** (778 of 4,245 cross-function findings meet the necessary conditions for a single-hop composed walk; per-repo median 22.3%, range 0–100%, beads alone 62.4% of the pool, 94% Go by volume). The payoff splits by direction: **refutation is bounded at zero** (0 of 28 running walks are `unconfirmed`, the only class removal can act on), while **confirmation INFLATES output** — adjudicated flows bypass collapse, so up to 778 new `precise` rows land on a cohort emitting 298 |
| [0009](0009-deferred-crossing-reachability.md) | Reachability of the deferred-crossing family | `measure-catalogue-reach.py` + per-idiom fixtures | **72.6% of the measured family fires in real idiom** (45 of 62; L1 gate 134/135 with a module hint, 37/135 without). The synthetic probe alone says 92.3% and **disagrees with the real idiom on 24% of rows in both directions** — its method spelling is `require('WebSocket')`. `flask.Flask.run`, the filed premise for "maybe it's all inert", fires in both forms a Flask program is written in and misses only the untyped receiver; python is 22/22. Where it IS inert one mechanism covers go and java (`WI-lalot`: a receiver typed from a library RETURN VALUE is not typed at all, killing all 7 go framework launch rows) and a second covers javascript (`INV-misup`, re-derived independently). Register: **135 deferred + 14 misdeclared of 335** own rows, against the census's 131 of 332 — and `ipc_recv` is 67 rows, not the 64 that document totals |
