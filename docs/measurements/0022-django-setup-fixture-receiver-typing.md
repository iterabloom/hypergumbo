<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0022: What Django test-fixture receiver typing buys, on the only arm that can see it

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/zamud_setupfields_09092026/` — `PLAN.md` (pre-registration, written before the fix was built and therefore before any number it predicts could exist), `pc1_scan.py` + `RECONCILIATION.md` (population existence, three rounds, and why the pre-registered stop condition fired), `SCOPE_INIT.md` (a filed build instruction declined on data), `arm.sh` / `runall.sh` (arms S/B, marker asserts at BEGIN and END, mandatory tree restore), `analyze.py` (situation delta, both source modes), `diffsurvey.py` + `DEDUP_FINDING.md` (edge-level reach), `refute.py` + `REFUTATION.md` (independent ast index), `adjudicate.py` (census, two classifier variants), `INSTRUMENT_DEFECTS.md`, `RESULT.md`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-zamud` (the rule under test), `INV-mumov` (the class), `INV-linub` (the carrier), `WI-bifob` (the exclusion that forces the arm split)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: the SITUATION (an ADR-0046 source→sink situation as `verify-claims`
  reports it). A secondary EDGE-level endpoint (D4) is reported separately and
  never pooled with it.
- allocation: CENSUS of all 224 new situations for the precision figure. The
  BASELINE comparison is a seeded random sample (seed 20260909, n=120 of 613)
  scored by the same classifier.
- seed: 20260909, for the baseline sample and for the classifier-validation
  sample only. The headline census has no draw to reproduce.
- language_scope: python, Django only. The rule is gated on
  `DjangoRelationIndex`; nothing outside Django can reach it. Every other
  catalogued language is EXCLUDED, not measured-at-zero.
- cohort: pretix (119 MB, the only Django APPLICATION in the Phase 6 cohort)
  + kserve (199 MB, the non-Django zero-move control of 0013/0015/0016/0021).
  A second Django repository exists in the corpus and was NOT used (WI-kibud).
- claim_set: the seven generic taint claims, verbatim.
- rubric: measurement 0001's. CORRECT = a real value flow exists from the
  source's value to the NAMED sink.
- analyzer_sha: two arms. S = `73a5fdb02f` (dev + WI-zamud), B = `560dee9426`
  (dev as shipped, the subject's own parent). `py.py` is the only file that
  differs. Markers asserted at BEGIN and END of each arm, with control markers
  present in BOTH arms so a wrong FILE is distinguishable from a wrong ARM.
- **source mode: TWO ARMS, REPORTED SEPARATELY, NEVER POOLED.** `prod` is the
  default (WI-bifob excludes test/fixture-sourced flows); `all` passes
  `--include-non-production-sources`. Every site in this rule's population is
  under `src/tests/`, so on the `prod` arm "the rule works" and "the rule does
  nothing" are the same number. The `prod` arm is run and reported to
  DEMONSTRATE that blindness, not to be averaged in. The primary endpoint is
  the `all` arm.

## Result

    kserve   prod    58 ->  58    NEW 0  GONE 0     control unmoved
    kserve   all    277 -> 277    NEW 0  GONE 0     control unmoved
    pretix   prod   149 -> 149    NEW 0  GONE 0     predicted, and held
    pretix   all    613 -> 837    NEW 224 GONE 0

    TRUE POSITIVE    60   30.8%    95% CI 24.7% .. 37.6%
    FALSE POSITIVE  135   69.2%
    VACUOUS          29
    WRONG-TYPE        0

    edge reach: django.db.models CALL SITES 10,422 -> 11,869  (+1,447, 0 gone)
                distinct (src,dst) pairs      6,105 -> 6,861  (+756)

**The pre-registered ship criterion was ≥ 50% correct. Observed 30.8%. It
FAILED, and the entire 95% confidence interval lies below the bar.** That is
stated first because it is the result; the rest of this record explains what
the number measures, and does not restate it as a pass.

## What the number measures

All 224 new situations carry `analysis_method = structural` and
`walk_verdict = "unavailable"` — the tool's own statement that NO DATAFLOW WALK
RAN. 152 of 224 are `hops = 0`, where the whole "flow" is that one test
function contains both an ORM read and an ORM write. The dominant false
positive is a trailing `assert` on `<manager>.get(...)` paired with a
`create(...)` earlier in the same function; it has nothing to do with receiver
typing. The true positives are one clean shape —
`Order.objects.create(..., sales_channel=<...>.sales_channels.get(...), ...)`,
a db_read whose value is lexically an argument to a db_write.

The bar itself was mis-specified, and measurement rather than argument
establishes it. The arm's own ambient precision was unknown when the bar was
set; scored here with the same classifier on a seeded sample of the situations
the arm already had:

    WI-zamud's new population (census)   60/195 = 30.8%   [24.7 .. 37.6]
    the arm's pre-existing population    23/ 95 = 24.2%   [16.7 .. 33.7]
    difference +6.6 pp,  z = 1.16,  p = 0.25 — NOT significant

The new population is statistically indistinguishable from the arm it joins.
The 50% bar was carried over from DEFAULT-arm precedents (0013 87.5%, 0015
66.7%, 0016 72.7%, 0021 56.3%) and applied to a non-production arm on which no
measurement had ever been taken — because until this record's instrument change
no measurement could reach that arm at all.

## Positive control: the population exists

The pre-registered stop condition FIRED — an independent scan counted 3,026
chain sites against the item's filed 1,155 — and was reconciled over three
rounds rather than waved through. The item's census counts
`<X>.<accessor>.<orm method>(`, which requires an accessor hop; the first scan
counted any method on a typed field. With scope matched, file counts agreed
exactly (767 = 767) and the inherited share to within four points (62.5% vs
58.8%); the residual is short-name base merging in the looser instrument. The
conservative filed number is the one quoted. Full detail in `RECONCILIATION.md`.

A finding the item did not have: its own 1,155 **undercounts** it, because the
accessor-hop shape structurally excludes ~1,110 direct instance writes
(`self.order.save()`) on the very same fields.

## Refutation

`refute.py` builds an independent ast index of pretix — its own class,
relation and setUp-binding maps, sharing no code with `py.py`.

    692  setUp field + owned accessor — OK
     78  accessor not owned BY THE SCAN — scan-unresolvable
      0  VIOLATIONS

Three defects in the SCAN were found by reading flagged sites at source and
fixed there rather than reported as analyzer violations (8 → 1 → 0 violations;
169 → 78 unresolvable): a fixture built from another fixture, merged through
project bases; a forward `ManyToManyField` as a manager on its declaring class;
and multi-hop chain flattening. All five residual accessor names were confirmed
to be real declared `related_name`s in pretix source, so the analyzer is right
on all 78 and the scan is blind. A refutation instrument that cries wolf is
worse than none.

## Confounds and disclosures

1. **CORRECTED 2026-09-09, after publication: the "110 disappeared edges"
   were an artifact of THIS RECORD'S OWN INSTRUMENT, not a property of the IR.**
   The first version of this section said `calls` edges are "deduplicated by
   `(src, dst)` with the LINE discarded" and filed that as a pre-existing
   defect. **That is wrong.** `ir.deduplicate_edges` preserves every collapsed
   call site in `meta["call_lines"]` — 18,075 of pretix's `calls` edges carry
   it, recording 59,806 sites, 41,731 of them beyond their survivor — and the
   ABSENCE of the key is a documented contract meaning "exactly one site, at
   `edge.line`". `diffsurvey.py` keyed on `edge.line` and never read
   `call_lines`, so it saw a collision where the IR had recorded both sites.

   Re-derived by reading the field the contract intends:

       django.db.models CALL SITES   B 10,422 -> S 11,869   NEW 1,447  GONE 0

   So the edge-level result is nearly DOUBLE what was first published (+1,447
   sites, not +756 pairs) and there were never any removals to explain. The
   `(src, dst)` pair count of +756 is retained above because it is a real
   figure of a different unit, not because the two are alternatives.

   The only genuine bound is `_CALL_LINES_CAP = 50`, which 36 pretix edges
   reach and which `ir.py` documents at the point it applies.
2. **The precision figure is machine-assisted.** The classifier was
   hand-verified 6/6 against source on a seeded sample and is insensitive to a
   receiver-contamination variant (identical 60/135 under both). It is not a
   panel adjudication.
3. **One subject repository.** Every number here rests on pretix (WI-kibud).
4. **The baseline comparison is a sample, the headline a census.** They are
   different allocations and are labelled as such.
5. `__init__` was NOT added to the setUp family despite the item's filed build
   note, on data: 1 class, 1 field, 0 chain sites on pretix (`SCOPE_INIT.md`).
