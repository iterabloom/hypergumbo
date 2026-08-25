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
