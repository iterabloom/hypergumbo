<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0021: What the Django `super().save()` write chokepoint costs and buys on a walk that is no longer lying

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/sihoh_superwrite_09092026/` — `PLAN.md` (pre-registration, written before any arm ran, amended once — also before any arm ran — to add PC1b), `arm.sh` / `runall.sh` (arms S/B with marker asserts at BEGIN and END and a mandatory tree restore), `analyze.py` (situation delta), `diffarms.py` (content-keyed pair delta + class-hop canary), `pc1b.py` (population-existence check), `refute.py` + `write_scan.py` (RC3, independent bare-name index), `readback_full.py` (whole-function readback), `ADJUDICATION.md`, `RESULT.md`, `DEADLOCK.md`, `D2_SUBSTITUTION.txt`, `INSTRUMENT_CANARY.txt`, `PC2_EXPECTED.txt`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-sihoh` (the rule under test), `INV-putug` (the walk fix this validates on a live population), `INV-mumov` (the class), `INV-linub` (the carrier), `WI-kutal` (the routing defect situation 9 exposed)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: the SITUATION (an ADR-0046 source→sink situation as `verify-claims`
  reports it). The pair-level delta is reported alongside because the two
  disagree in an informative way; see "the 23 removals".
- allocation: CENSUS. All 16 new situations were read back against source in
  full function bodies, not token-filtered excerpts. No sample.
- seed: none. The delta is 16 new and 0 gone; there is no draw to reproduce.
- language_scope: python, Django only. The rule is gated on
  `DjangoRelationIndex.model_ids`; nothing outside Django can reach it.
- cohort: pretix (119 MB, the only Django APPLICATION in the Phase 6 cohort)
  + kserve (199 MB, the zero-move control of 0013/0015/0016). A second Django
  repository exists in the corpus and was NOT used; see "external validity".
- claim_set: the seven generic taint claims, verbatim
- rubric: measurement 0001's. CORRECT = a real value flow exists from the
  source's value to the named sink.
- analyzer_sha: two arms. S = `14d01fde7f` (dev + the rule), B = `8127dc8245`
  (dev as shipped). Both carry INV-putug's walk fix; the rule is the ONLY
  inter-arm difference. Markers asserted at BEGIN and END of each arm, with a
  control marker present in both so a wrong FILE is distinguishable from a
  wrong ARM.
- canary: class-node hops traversed. Validated BEFORE use by replaying it
  against measurement 0016 round 1, where it reads exactly **33**.

## Result

    kserve   situations 58 -> 58    NEW 0  GONE 0        (control unmoved)
    pretix   situations 133 -> 149  NEW 16 GONE 0

    TRUE POSITIVE    9   56.3%     (8 / 50.0% under the strict reading of #9)
    FALSE POSITIVE   7   43.8%
    VACUOUS          0
    WRONG-TYPE       0

Every new situation is on `untrusted-input-no-database`; sinks 15 `save` /
1 `delete`. No claim verdict moved. The per-site table is in `ADJUDICATION.md`.

The rule SHIPS against the pre-registered criterion (≥ 50% correct, zero
wrong-type, PC1 holds), and it ships AT the bar rather than above it: on the
strict reading this is exactly 50.0%, below every shipped precedent in this
family (0013 87.5%, 0015 66.7%, 0016 72.7%). The case rests on the direction,
not the rate — the ORM write chokepoint of an entire Django codebase was
invisible, which is a false NEGATIVE on a write primitive.

## The controls, and why the zero is evidence

`super().save()` / `super().delete()` inside a model override is where the
write actually is on a real Django project: of the 306 pretix `save`/`delete`
sites whose receiver resolves to a model, 215 (70%) call a `save` the project
OVERRIDES, and 56 of its 66 `save` overrides then call `super().save()`.

  * **PC2 (capability).** 61 `super()` sites typed, **0 violations** across all
    136 marker edges on a whole-survey read-back against an independent
    bare-name index. Three routes agree on 61: the analyzer, a stdlib-only AST
    scan sharing no code with it, and a hand calculation recorded before the
    arm reported. The 14 `super().save` sites in NON-model classes stayed
    `external`.
  * **PC1 (the walk).** Class-node hops traversed in arm S: **0**. Round 1 read
    **33** with this same rule present and INV-putug's defect live.
  * **PC1b (is that zero evidence?).** Added to the pre-registration before any
    arm ran, because an empty population produces the same zero as a working
    fix. The subject survey carries **896** `dispatches_to` edges out of a type
    node across 440 classes, and **67** of the newly typed sinks sit inside a
    class carrying one. The shape existed and the walk declined to cross it.

PC1 + PC1b together are the first observation of INV-putug's fix on a
population that exists: 0020 could only price it with harness arms pinning
`py.py`, because the sink it needed was the rule withheld behind it.

## The 23 pair-level removals are re-keys, not losses

The content-keyed pair delta reads 147 → 163 with 23 removed and 39 added,
while the situation delta reads 0 GONE. Checked rather than assumed: 0 of the
23 belong to a situation that vanished. Each still exists in S reporting a
different sink, the representative having moved onto the newly available
`save`. `analyze.py` independently reports exactly 23 "kept situations with a
changed source-primitive/sink set". This is the artefact 0013 hit and the
reason the two units are both reported.

## Direction check: no suppression, and the substitution is disclosed

A change in this family can SUPPRESS — more resolved callees let
`_use_site_terminates` return True, a False earns `sanitized`, and a sanitized
flow is dropped (#214). `collect` records no `sanitized` count, and a
suppressed flow leaves the ledger by construction, so counting a field that
does not exist would have returned 0 and read as "no suppression". Four
substituted observables were named BEFORE the arms reported; all moved up or
stayed flat (`flow_count` 148→164, `findings_total` 724→750, per-method all up
or flat, 0 GONE; kserve identical in both arms). The declared-sanitizer arm was
therefore not run, and this is the reason rather than an omission.

## The pre-registered prediction was wrong

§6 of the pre-registration predicted "roughly 3 TP / 0 FP", on the reasoning
that round 1's 28 FPs were 20 (the `dispatches_to` mechanism) + 8 ordinary
misses already present in the other rules. The second half is false: seven of
round 1's eight named ordinary misses are `super()`-sink or lock-read sites
that exist only when this rule is present. They were this rule's FPs all along.
Round 1 said the 28 were "two populations"; it never said the 8 belonged to the
other rules, and conflating those two statements is what produced the bad
prediction.

The half under test HELD: the 20-FP mechanism is gone, and all seven of round
1's remaining named FPs reappear with the same verdict, reached independently
from source.

Round 1's "R2 marginal = 23 situations" was a SUBTRACTION of two arm totals
(47 − 24), not a direct A/B of the rule. This measurement isolates it directly,
so the two marginals are not comparable and no improvement in TP count is
claimed.

## External validity — the honest limit

Every precision number in this family (0013, 0015, 0016, 0021) rests on ONE
Django application. kserve shows the change does not leak outside Django; it
cannot show these rates generalise. A second Django repository (Django itself,
86 MB) exists in the corpus and carries real population. It was not used here
because the pre-registration and WI-sihoh's re-open trigger both name "the same
two arms", and changing the subject set mid-run would answer a different
question. It is now cheap to add: the harness ran 5.2× faster than round 1 on
identical work (pretix collect 1,937 s → 370 s), an independent replication of
WI-bavuz on a workload built for something else.
