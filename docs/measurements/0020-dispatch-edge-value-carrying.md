<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0020: What refusing a class-sourced `dispatches_to` edge removes — and why the first design measured an empty population

**Status:** Complete
**Date:** 2026-09-08
**Instrument:** `~/hypergumbo_lab_notebook/putug_dispatch_09082026/` — `PLAN.md` (pre-registration, written before any arm ran, with the four refutation conditions fixed in advance), `arm.sh` (arms S/B on today's dev), `armH.sh` (the harness arms X/Y, asserting BOTH the harness and the fix by marker count), `diffarms.py` (content-keyed situation delta with the class-hop canary), `cmp_selfproof.py` (the INV-zuhig control), `RESULT-r2.md`, `RESULT-inert-on-dev.md`, `ADJUDICATION-22.txt`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `INV-putug` (the item under test), `WI-sihoh` (the finished rule this unblocks), `INV-zuhig` (the ruling that had to survive), `INV-linub` (the carrier)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: the SITUATION (an ADR-0046 source→sink situation as `verify-claims`
  reports it)
- allocation: CENSUS. Every situation that appears or disappears was read back;
  all 22 removals additionally went through a mechanical whole-body check. No
  sample.
- seed: none. The delta is 22 removed and 2 added; there is no draw to reproduce
- language_scope: python. Five linkers emit class-sourced dispatch
  (`django_orm_dispatch`, `airflow_framework_dispatch`, `jackson_dispatch`,
  `rust_trait_dispatch`, `_third_party_bases`) spanning python, java and rust,
  but only the python member has a corpus repository. The other four are
  covered by unit test only and are NOT claimed as measured.
- cohort: pretix (119 MB, the repository the defect was measured on and the
  only Django project in the Phase 6 cohort) + kserve (199 MB, the zero-move
  control of measurements 0013/0015/0016)
- claim_set: the seven generic taint claims, verbatim
- rubric: ADR-0046's two axes. CORRECT = a real value flow exists from the
  source's value to the named sink
- analyzer_sha: four arms, every one asserting its markers at BEGIN and END and
  aborting on disagreement. S = `taint.py 0e62e420…` (fix) / B = `ff1fa1e6…`
  (dev). X and Y additionally pin `py.py` to `359880a105`
  (`587bb80e…`) so WI-sihoh's withheld rule is present in BOTH — py.py
  staleness is COMMON MODE and the only inter-arm difference is `taint.py`
- canary: class-node hops traversed. Validated against measurement 0016 round 1,
  where it reads exactly **33**, and against a synthetic
  `Class --dispatches_to--> Class.save` path

## The first design measured an empty population, and that is the first result

Arms S/B, on today's dev:

| repo | BASE | SUBJ | removed | added | class-node hops |
|---|---|---|---|---|---|
| pretix | 147 | 147 | **0** | **0** | **0 → 0** |
| kserve | 62 | 62 | 0 | 0 | 0 → 0 |

Method mixes byte-identical. **The fix is inert on the shipped tree**, and the
reason is structural: INV-putug's defect needs a taint SINK INSIDE a Django
model method, and the only rule that puts one there — WI-sihoh's
`super().save()` typing — was WITHHELD from the shipped WI-gamas PR. No sink
inside the class, no hop to cross, nothing to remove. Class-node hops are **0
today against 33 in 0016 round 1**.

**The pre-registration was wrong and should have caught this.** INV-putug's own
text says the shape was "made visible at scale" by round 1's arm and that the
rule was withheld; the plan nonetheless predicted a DECREASE on a population
that same text described as absent.

## The harness arms, which price the fix on the population it exists for

X and Y pin `py.py` to `359880a105` in both arms — the withheld rule present as
a MEASUREMENT HARNESS that is **not shipped**. Arm X reproduces round 1 exactly:
**184 flows, 33 class-node hops**, against 0016's documented 184 and 33.

| | X (harness, no fix) | Y (harness + fix) |
|---|---|---|
| pretix situations | 183 | **163** |
| removed / added | — | **22 / 2** |
| class-node hops | **33** | **0** |
| kserve (control) | 62 | **62 — 0 moved** |

**Net −20 situations, which matches 0016's independent attribution of "20 of the
47 situations round 1 added were this shape" exactly.**

### The removals are exactly the targeted population

- All **22** removed situations crossed a class node in arm X. **Zero** removed
  situations did not — no collateral damage.
- **11** of the 33 class-hop flows were KEPT. They had a legitimate alternative
  route, and the accounting closes: 22 + 11 = 33.

The 11 kept are the informative half. `generate_invoice` genuinely takes the
tainted `order`, constructs `Invoice(order=order, …)` and hands it to
`build_invoice`, which calls `invoice.save()` — a REAL flow. Because
`build_invoice(invoice: Invoice)` is annotated, the typed-receiver rule gives an
honest `build_invoice → Invoice.save` edge, so the finding survives the fix by
its true route instead of the class hop. **The fix does not delete findings that
have an honest path; it deletes the dishonest path to them.**

### The 2 added are re-routings, not new noise

Both share a source symbol with a removal and differ only in the sink
(`subevents.py:870-1051` delete→save; `sendmail/tasks.py:56-166` save→create).
With the class hop gone the walk reports a different terminal for the same
source.

## Adjudication of all 22 removals: 0 of 22 are correct

Census, three ways:

1. **Mechanical, complete.** For every one of the 22, the source function's own
   body contains **no call at all** to the named sink method. 0 of 22 have one.
2. **Structural.** All 22 reached their sink ONLY through the class node —
   removing it removed them entirely, so no alternative route existed in the
   graph, while the 11 that had one survived.
3. **Read at source**, the two sites INV-putug named. `payment.py:712-723`:
   `get_invoice_address` builds `InvoiceAddress()` with NO arguments on the
   branch where the primary key is absent and never calls `.save()`.
   `subevents.py:434-500`: `SubEventItem(subevent=…, item=i)` does take the read
   value, but the walk lands on `SubEventItem.delete`, which this code never
   calls.

`InvoiceAddress` is the class node in most of the 22 — the no-argument
constructor shape.

## The refutation conditions, fixed in advance

- **R1** (a removal is a true positive): **does not fire.** 0 of 22 correct by a
  complete census, and the flows that had a real route were kept.
- **R2** (INV-zuhig moves): **does not fire.** Separate two-arm self-proof, every
  field identical — 18/18 same verdicts, 28 credited flows both, `findings_total`
  333 both, zero claims moved. Weaker evidence than it looks, since the change is
  inert on that repository; the X/Y arms are what carry the load.
- **R3** (kserve moves): **does not fire.** 0 moved.
- **R4** (more than 31 situations lost): **does not fire.** Net 20.

## Two instrument faults, found and recorded

1. **`diffarms.py` first keyed on `flow_id`, which the collect instrument writes
   as `<ARM>:<claim>:<index>`** — it embeds the arm letter, so every situation
   read as both removed and added. It printed "removed 148, added 148", an
   absurdity obvious enough to catch. **On a population where the true delta was
   small it would have produced a plausible wrong number instead.** Re-keyed on
   the content tuple a human can re-read at source.
2. **A canary check that looked like validation and was not.** 0016's on-disk
   arm S is ROUND 2, post-withholding — its own `arm.sh` asserts both super-rules
   absent — so its 0 hops proved nothing. The real control is round 1, where the
   canary reads exactly 33.

## What this does not establish

- Whether the walk crosses OTHER non-value edges. `contains` is exonerated by
  0016's 16,010 untraversed pairs; `extends` (2,860 pairs), `references` (299)
  and `decorated_by` are untested and stay open on INV-putug.
- Volume outside pretix. One Django repository, one language.
- Anything about the four non-python sibling linkers beyond unit tests.
- Any timing claim. pretix ran 2745 s in arm S and 1966 s in arm B; the fix adds
  a check only on dispatch edges, so warm OS page cache on the later run is the
  likelier explanation. Not investigated, not claimed.

## What it unblocks

WI-sihoh is a finished, measured, reverted rule (61 pretix `super().save()`
sites, 0 wrong-type on a whole-survey read-back) withheld because its yield
through this defect was 3 true against 20 false. Those 20 are the population
this fix removes. Re-landing it is a SEPARATE PR with its own measurement;
folding it in here would make neither number attributable.
