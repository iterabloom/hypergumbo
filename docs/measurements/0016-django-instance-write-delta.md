<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0016: What typing the Django ORM instance write did to taint precision

**Status:** Complete
**Date:** 2026-09-07
**Instrument:** [`scripts/measure-taint-precision.py collect`](../../scripts/measure-taint-precision.py) (held constant across both arms) and the scripts in `~/hypergumbo_lab_notebook/mumov_instwrite_09072026/` — `PLAN.md` (the pre-registration, written before any number existed), `fx/` + `dump.py` (the premise check), `write_scan.py` / `write_scan.txt` (the population sizing), `arm.sh` + `run_baseline.sh` / `arm_b.sh` (the two arms, each asserting its own tree), `analyze.py` / `readback.txt` / `delta_*.json` (the delta and the read-back), `refute.py` / `refute.json` / `RC3_RESULT.md` (the refutation check on the production survey), `RESULTS_ROUND1.md` (the withheld rule's own measurement), `classhop_check.txt`, `positive_control.txt`, `capability.txt`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `WI-gamas` (the change under test, Phase 6 PR 4 of the INV-linub class), `INV-mumov` (the L3 half this slice serves), `WI-kutal` (the same-file misresolution that caps the lineage rule), `WI-nibum` / `WI-zamud` / `WI-lurob` (the receiver shapes deliberately left)

## Frame

Machine-readable per ADR-0048 §A3. **This record is a marginal delta, not a
population measurement, and does not enter the 0006 series** (0003's shape, as
0013, 0014 and 0015): the population is every situation one change ADDED or
REMOVED on one repository, read at source in full, so F2's allocation and F3's
seed have nothing to govern and the keys say so.

- unit: the SITUATION (claim, source function — INV-karud's collapse record), with the row count beside it
- allocation: CENSUS of the delta, NOT an M x R draw — every new and every vanished situation on the subject repository read at source and classed; one subject (pretix, the Phase 6 cohort's only Django repository) and one control (kserve, expected and observed 0 moved)
- seed: no draw was made, so none was seeded — the population is every situation the change touched
- cohort: pretix (subject) and kserve (control), the repositories 0013, 0014 and 0015 ran on
- claim_set: `docs/example-claims/generic-taint-claims.yaml`, the seven generic claims, verbatim
- rubric: measurement 0001's rubric with ADR-0046's two numbers — TP when the value read at the source is the RECEIVER or an ARGUMENT of a `db_write` call on the reported path; FP when it is not (a value that only gates a branch, a lock read whose result is discarded, a read that happens after the write, a structural pairing the walk did not carry); VACUOUS = a TP whose only carrier is a lazy QuerySet or a branch condition (ADR-0046 KIND-MISDECLARED); WRONG-TYPE = the receiver is not a Django model at all (the pre-registered refutation cell)
- analyzer_sha: baseline = dev `946ad85633`, checked out IN PLACE over the branch tree by `run_baseline.sh`; subject = branch `jgstern-agent/feat/django-instance-write-lineage` at `03788a4cdf`. BOTH arms assert their own marker counts at BEGIN and END (`armS2.log`, `armB_wrapper.log`): the subject requires `_is_django_model_instance` present and the withheld rule's two definitions absent, the baseline requires all three absent, and both require `_class_directly_extends_django_model` present as a positive control that the assertion reads the right file.
- language_scope: python is the only language the change can affect and the only language in scope; kserve's go content ran and is reported unmoved, not measured-at-zero

## The question

WI-sozoj gave the Python analyzer one rule for the Django instance write:
`self.save()` / `self.delete()` in a class whose DIRECT base is the dotted
`models.Model`. This change moves that gate to the model lineage the relation
index already computes and widens the receiver from `self` to any receiver the
index can resolve to a model instance. The question 0003's method asks of a
recall change: **of the flows this change ADDS, how many are real, and how many
of the real ones are useful?**

## What the premise check found first

`fx/` through production `analyze_python` at dev `946ad85633`, with two controls
in the same run, before any code was written:

| site | receiver | dev |
|---|---|---|
| `self.delete()` in a DIRECT `models.Model` subclass | `self` | `django.db.models.delete` — CONTROL, WI-sozoj works |
| `self.save()` where the lineage defines `save` | `self` | resolves to the project method — correct |
| `d.delete()` after `d = Direct.objects.create(...)` | typed local | `external` |
| `p.save()` after `p = Payment()` | typed local, hint already stamped | `external` |
| `x.save()` on an untyped parameter | unknown | `external` — correct |

An INSTRUMENT DEFECT was found and corrected before any number was quoted: the
first probe silently dropped every RESOLVED edge and would have reported four of
these as "no edge emitted", which is the emission clause INV-mumov already had
refuted. `dump.py` prints every `calls` edge unfiltered and is the instrument of
record.

## Capability: what the gate was actually worth before

Counted on the full production survey of pretix in both arms:

| | typed `save`/`delete` sites | `external` placeholders |
|---|---|---|
| dev `946ad85633` | 2 | 1,972 |
| this change | **75** | 1,924 |

WI-sozoj's direct-dotted-base gate recognised **two** ORM instance writes in a
repository that contains about 2,900 `save`/`delete` call sites.

## Results

### The delta

| | rows | situations | new | gone | kept with a changed set |
|---|---|---|---|---|---|
| kserve (control) | 63 → 63 | 58 → 58 | 0 | 0 | 0 |
| pretix (subject) | 132 → 148 | 122 → 133 | **11** | **0** | 34 |

No claim verdict moved: every claim is `violated` in both arms on pretix, and
kserve's `untrusted-input-no-database` is `inconclusive` in both. All 11 new
situations are on `untrusted-input-no-database` and every one of their sinks is
`django.db.models.save`.

### Every new situation, read at source

| | TP | FP | vacuous | wrong-type |
|---|---|---|---|---|
| 11 new situations | **8** | 3 | 0 | 0 |

**Correct 72.7%. Useful 72.7%.** The full table with the reason for each is in
`ADJUDICATION.md`; the eight true ones are a fetched row written back through
its own `save` (`CachedFile`, `StaffSession`, `QuestionAnswer`) or a fetched row
recorded as a `LogEntry`'s content object, which is a database write of the row
that was read.

The three false ones each name work already filed, and none is a defect in the
typing:

* `ReauthView.post` — the only flow runs into `StopStaffSession.get`, an
  unrelated class's method claimed by short name at `self.get(request, …)`.
  That is WI-kutal. The genuine write here is on a LOOP VARIABLE, which WI-nibum
  still leaves untyped.
* `_get_unknown_transactions` — the read builds a de-duplication set that gates
  whether new rows are created; the row written is built from external data.
  The rubric refuses a control-only read, correctly.
* `QuestionsStep.post` — the ledger pairs a `first()` read with a write inside a
  method called BEFORE it and never passed the value.

### The positive control

`PLAN.md` §5 predicted, from 0015's own read-back, that typing the instance
write would move that measurement's five structural false positives onto their
real carrier. Of the 27 situations 0015 added, **22 gained a Django write sink
here** (16 `save`, 6 `delete`), and **19 of the 22 gained it on a direct path**
— the real carrier — with 3 arriving through the class-hop artefact described
below. The control fired.

### The artefact is gone with the rule that caused it

    class-node hops traversed by all flows, round-1 subject arm (with the super rule)   33
    class-node hops traversed by all flows, SHIPPED subject arm                          0

That zero is the causal check on the attribution in the next section: the
`dispatches_to` pairing entered with the withheld rule's sinks and left with
them.

## The refutation conditions

All three were written in `PLAN.md` before the code existed.

**RC1 — a `ModelForm` or serializer `save()` typed as an ORM write.** DID NOT
FIRE. A form declares `forms.CharField`, so it is not in `model_ids` and is not
a model to the independent scan either; a typed form site would have surfaced as
a violation below, and none did.

**RC3 — survey-wide read-back.** Every edge to
`python:django.db.models:0-0:<save|delete>` stamped `framework_dispatch:
django_orm` was joined to source and its receiver resolved by an INDEPENDENT
bare-name scan. **75 marker edges, 0 violations, 0 wrong-type.** By the receiver the
independent scan resolves: 45 a local bound from `Model(...)`, 22 a local bound
from `Model.objects[.qs].get/create/first(…)`, 3 `self`, 2 an annotated
parameter, 1 a local rebound in the body, and 2 the scan could not type. The two
were hand-checked at source (`api/middleware.py:81` and `:98`, bound by
`call, created = ApiCall.objects.select_for_update(…).get_or_create(…)`) and are
correct: an unresolvable cell is not a pass.

Two defects in the refutation instrument were found by reading its first four
flags at source, and both made the SCAN wrong while every flagged site was
correct analyzer behaviour: its model test recognised only `models.Model` and
project bases, so a class over a THIRD-PARTY Django abstract base
(`User(AbstractBaseUser)`, `OAuthApplication(AbstractApplication)`) was
invisible to it; and its binding test answered a parameter annotation before
looking for a later rebinding, so `def f(job: int)` followed by
`job = BankImportJob.objects.get(pk=job)` reported `T=int`. Both are recorded in
`RC3_RESULT.md`. The consequence for the population sizing is stated plainly:
`write_scan.py` carries the first defect, so its "306 sites whose receiver
resolves to a model" is a FLOOR, and the direction of that error is an
understated population, never an overstated effect.

## The rule that was built, measured and withheld

The pre-registered design had a third rule: `super().save()` /
`super().delete()` inside a model's own override. It is where the write actually
is on a real Django project — of the 306 pretix sites whose receiver resolves to
a model, 215 call a `save` the project OVERRIDES, and 56 of its 66 `save`
overrides then call `super().save()` — and typing it is CORRECT at the site: the
refutation check read 61 such sites back against source with zero wrong-type.

It is withheld anyway, and the reason is downstream of the analyzer. Putting a
sink inside a model method makes it reachable from any function that merely
mentions the class, because the ADR-0017 walk crosses the django-orm-dispatch
linker's `dispatches_to` edge as if it carried a value:

| | |
|---|---|
| class-node hops traversed by all flows in the round-1 subject arm | 33 |
| of those, on a pair carrying `dispatches_to` | 33 |
| `class → method` pairs carrying ONLY `contains` (never traversed) | 16,010 |
| situations where every flow crosses a class node, baseline | 0 of 122 |
| situations where every flow crosses a class node, round-1 subject | 31 of 169 |

Two readings at source show the pairing is not merely unproven but wrong. At
`pretix/base/payment.py:717` the `instantiates` edge is `InvoiceAddress()`, a
NO-ARGUMENT constructor on the branch where the primary key is absent, so the
value is not passed into it at all. At
`pretix/control/views/subevents.py:485` the constructor does take the read
value, but the sink the walk then reaches is `SubEventItem.delete`, a method
that code never calls. The walk can terminate at any ORM method of a class a
function mentions.

The measured trade inside this PR was exact, so the choice did not need a
judgement call:

| shipped | new situations | TP | FP | correct |
|---|---|---|---|---|
| the lineage and receiver rules (shipped) | 11 | 8 | 3 | 72.7% |
| plus the `super()` rule (round 1) | 47 | 19 | 28 | 40.4% |

Three true findings for twenty false ones. The rule returns when the walk stops
crossing that edge; both are filed with this record as the evidence.

## What this does not establish

- **One Django repository.** pretix is the cohort's only Django project, so
  every number here is one repository's, and kserve's zero is a control against
  a harness fault, not a second sample.
- **The withheld rule's true precision is unknown.** Its 3-against-20 is
  measured THROUGH a walk defect. What it would buy on a fixed walk is not
  established here and must not be quoted from this record.
- **The lineage rule's population is capped by a filed defect, not sized.**
  WI-kutal (a `self.method()` claimed by an unrelated same-file class of the
  same name) took 2 of pretix's 37 `self.save()` / `self.delete()` sites. That
  is one population's number, not the item's volume.
- **`setUp` fields, loop variables and forward-FK fields are still untyped** and
  remain filed as WI-zamud, WI-nibum and WI-lurob. This record does not claim
  the receiver-typing question is closed.
