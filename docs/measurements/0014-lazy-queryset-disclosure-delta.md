<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0014: What moving Django's lazy QuerySet combinators to a disclosure boundary did to taint precision

**Status:** Complete
**Date:** 2026-09-07
**Instrument:** [`scripts/measure-taint-precision.py collect`](../../scripts/measure-taint-precision.py) (held constant across both arms), the subject-arm ledger and the delta scripts in `~/hypergumbo_lab_notebook/fasap_dbquery_09072026/` (`PLAN.md` — the pre-registration, `preestimate.py`, `shape_scan.py`, `arm.sh`, `analyze.py`, `readback.txt` — the 44 excerpts, `delta_pretix.json`); the baseline ledger is measurement 0013's arm B in `~/hypergumbo_lab_notebook/mumov_qschain_09062026/B/`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `WI-fasap` (the change under test, Phase 6 PR 2 of the INV-linub class; the pre-registration is its first discussion entry), `INV-mumov` (0013, whose 28 vacuous situations are this record's positive control), `INV-nular` F3 (the deliberately-kept control this change releases)

## Frame

Machine-readable per ADR-0048 §A3. **This record is a marginal delta, not a
population measurement, and does not enter the 0006 series** (0003's shape, as
0013): the population is every situation one change REMOVED or ADDED on one
repository, read at source in full, so F2's allocation and F3's seed have
nothing to govern and the keys say so.

- unit: the SITUATION (claim, source function — INV-karud's collapse record), with the row count beside it (48 removed / 0 added rows = 44 gone / 0 new situations)
- allocation: CENSUS of the delta, NOT an M x R draw — all 44 vanished situations on the subject repository read at source and classed; the 18 survivors of the retag verified to carry an evaluation call site at a named line; one subject (pretix, the Phase 6 cohort's only Django repository) and one control (kserve, expected and observed 0 moved)
- seed: no draw was made, so none was seeded — the population is every situation the change touched
- cohort: pretix (subject) and kserve (control), the two repositories measurement 0013 ran on; qualification rule = the same cohort, so 0013's arm B is the baseline
- claim_set: `docs/example-claims/generic-taint-claims.yaml`, the seven generic claims, verbatim; 41 of the 44 vanished situations are on `untrusted-input-no-database`, 2 on `untrusted-input-no-host-fs`, 1 on `untrusted-input-no-subprocess`
- rubric: the four pre-registered classes for a vanished situation (PLAN.md): L = the QuerySet is the receiver of the write and is never read (ADR-0046 VACUOUS, KIND-MISDECLARED); I = evaluated in the composing scope by a shape the emitter declares (a defect); X = evaluated in scope by a shape outside the declared set (a cost, filed); D = handed to a scope the composing call does not name (the deferred class the shadow discloses). Truth under measurement 0001's rubric was NOT adjudicated for the D class, and the record does not claim those flows were false
- analyzer_sha: baseline ac37d556bf (dev; measurement 0013's arm B ledger, whose runtime files equal dev's — `git diff --stat ddb9a02404 ac37d556bf -- packages/` is empty); subject = branch `jgstern-agent/feat/db-compose-disclosure-boundary` at ac37d556bf plus the WI-fasap runtime diff, ASSERTED in `armC.log` before and after the run (`db_compose` overlay section 1, `_emit_orm_evaluation` 1, shadow 1; the implementation commit captures that working tree unchanged)
- language_scope: python is the only language the change can affect and the only language in scope; kserve's go content ran and is reported unmoved, not measured-at-zero

## The question

WI-fasap moves the twenty-one Django QuerySet combinators from `db_read`,
where they minted `untrusted_input` at a call that reads nothing, to
`db_compose`, a deferred-crossing boundary that mints nothing and shadows
`db_read`; and, in the same change, gives the implicit evaluation of a
QuerySet a call site (`django.db.models.__iter__` / `__aiter__` /
`__getitem__`, emitted by `py.py` at a `for`, a comprehension, a materialising
builtin and an index subscript) so the read is minted where it happens. Both
halves of ADR-0049 ruling 3 in one change. The question 0003's method asks of
it: **of the flows this change REMOVES, how many were the vacuous source it
set out to remove, and how many were real reads it deleted?**

## Method, pre-registered before the run

Written on `WI-fasap` and in `PLAN.md` before any subject number existed,
with four predictions:

- **P1** 0013's own positive control: its 28 L-class situations vanish, its 7
  R-TP situations remain.
- **P2** Of the 62 baseline situations resting only on lazy sources
  (`preestimate.py` over the baseline ledger), a text heuristic
  (`shape_scan.py`) classed 28 as write-receiver-only, 12 as evaluated in
  scope, 2 both, 20 unclear: the write-receiver ones vanish, the evaluated ones
  survive on the new call site, the unclear ones are read.
- **P3** kserve: 0 moved; no verdict category moves in either repository.
- **P4** No new situations except where an evaluation site exists in a
  function that had no lazy source before; expected small.

Ship rule: every vanished situation read at source and classed L / I / X / D;
an I-class vanished situation that is a true, useful finding under 0001 /
ADR-0046 means the PR does not merge until the emitter catches it.

One arm, cold, on an isolated `XDG_CACHE_HOME` with a `cache-clear` per
repository; the baseline is 0013's subject arm, because dev's runtime files
are the ones it ran on (asserted by an empty diff, not assumed).

## Population

| repo | arm | rows | situations | new | gone | kept |
|---|---|---:|---:|---:|---:|---:|
| kserve (control) | 0013-B → C | 63 → 63 | 58 → 58 | **0** | **0** | 58 |
| pretix | 0013-B → C | 147 → 99 | 139 → 95 | **0** | **44** | 95 |

No verdict category moved in either repository (all seven claims `violated`
on pretix in both arms; kserve's `untrusted-input-no-database` `inconclusive`
in both). **P3 and P4 hold.**

**P1 holds.** 0013's 40 new situations split mechanically into 27 resting only
on lazy sources and 13 carrying an executing read (`get` 11,
`get`+`select_related` 1, `all`+`filter`+`first`+`values_list` 1); the record
called them 28 and 12 because the last one carries both a real `first()` read
and a vacuous write-receiver flow. All 27 vanished; all 13 remain, the 28th on
its `first`.

**P2 holds, and the heuristic under-counted the survivors.** Of the 62
lazy-only baseline situations, 44 vanished and 18 survived — every one of the
18 on `__iter__` alone. The heuristic had predicted 14 survivors; four of its
20 "unclear" turned out to be evaluated in scope. Each survivor's evaluation
was verified as an emitted edge at a named line in the subject map
(`for t in Transaction.objects.filter(order=self)` orders.py:1226;
`for i in qs[:batch_size]` invoices.py:678 — the slice form;
`set(OrderPayment.objects....distinct())` orderlist.py:163 — a materialising
builtin; `list(MediumKeySet.objects.filter(...))` media.py:59;
`for op in qs` ticketoutputpdf/exporters.py:217, carrying both the host-fs
and the subprocess claim).

## Read-back — the 44 vanished pretix situations

| class | n | example | what happened to it |
|---|---:|---|---|
| **L — the QuerySet is the write's receiver, never read** | **33** | `WebHookCall.objects.filter(datetime__lte=...).delete()` (api/signals.py:73); `Voucher.objects.filter(pk=...).update(redeemed=F('redeemed')+1)` (models/orders.py:2764); `StaffSession.objects.annotate(...).filter(...).update(...)` (services/auth.py:36); `CheckinList.objects.filter(id__in=...).delete()` (control/views/subevents.py:1266) | **Removed.** The source observed nothing; ADR-0046 VACUOUS (KIND-MISDECLARED). This is the class the change exists to remove. |
| **I — evaluated in scope by a declared shape** | **0** | — | The ship rule's refutation condition did not fire. |
| **X — evaluated in scope by an undeclared shape** | **0** | — | — |
| **D — handed to a scope the composing call does not name** | **11** | `queue = OrderSyncQueue.objects.filter(...)...[:1000]; run_sync(queue)` (services/datasync.py:75–89, and `sync_single`); `self.fields['organizer'].queryset = Organizer.objects.all()` (control/forms/filter.py:941, :1107); `'allowed': self.objects.filter(redeemed=0)` into a template context (control/views/vouchers.py:619); `_find_order_for_invoice_id(Invoice.objects.filter(event=event), ...)` (banktransfer/tasks.py:146); `Checkin.objects.filter(...)` inside an `Exists()` annotation on a related manager (sendmail/tasks.py:76, :82; sendmail/signals.py:70; presale/views/widget.py:289); `render_pdf(self.event, qs, ...)` (badges/exporters.py:651, :655 — the host-fs and subprocess claims) | **No longer minted; disclosed.** The read happens in `run_sync`, in Django's form rendering, in the template, in the helper, in the database (a subquery), or in `render_pdf` — none of them a scope with a typed receiver. `db_compose`'s shadow over `db_read` is the disclosure for exactly this class. Truth under 0001 was not adjudicated: some are real reads reaching a sink through another function, and the record states the loss as a loss. |

Two residuals surfaced by the read-back, both outside this change's declared
set and neither a bug in it: pretix's soft-delete manager `Model.all`
(`Checkin.all.filter(...)`, `OrderPosition.all.filter(...)`, control/views/checkin.py:614,
models/orders.py:381–383) is not the `.objects` marker and is untyped, so a
`for ci in qs` over it emits nothing; and a related-manager chain
(`event.seats.select_related(...)`, `o.positions.annotate(...)`) is INV-mumov's
candidate 2, still untyped.

## Headline

| unit | vanished | vacuous (L) | deferred (D) | real reads deleted (I + X) |
|---|---:|---:|---:|---:|
| situation, pretix delta | 44 | **33** | **11** | **0** |

Read with 0013: the 28 vacuous situations chain typing added are gone, and so
are 5 older ones of the same shape WI-sozoj had minted since it shipped; the
seven useful `select_for_update(...).get()` → write flows remain; the
in-scope iteration reads that the F3 control was protecting for a year all
survive on their own call site. The cost is the D class: eleven situations
whose read the composing scope cannot see are disclosed rather than minted,
which is ADR-0049's stated trade ("decisiveness is traded for not inventing
flows") applied to the database.

## What this does not support

- No claim about the repository's precision (0012 remains the last
  population-level number); this is a marginal figure on one Django repository.
- No claim that the 11 D-class situations were false: they were not
  adjudicated for truth, only for where the read happens.
- No claim about the other Lazy members of ADR-0049's family (JPA's
  `getResultStream`, `sqlite3.Connection.iterdump`, the `NSURLSession` task
  rows): the Django licence does not transfer and each needs its own proof.
- kserve's zero is the control holding, not evidence of anything else.

## Consequences worth acting on

1. **The D class is the receiver-typing arc's population**, not a builtin to
   add: a QuerySet handed to another function, a form field or a paginator is
   read where no receiver is typed. Filed as the residual of WI-fasap with its
   shapes and this count as the re-open trigger.
2. **`Model.all` and other custom manager names** are a marker-coverage gap
   the `.objects` heuristic cannot see; pretix uses it for soft-deleted rows.
   Noted on the same residual.
3. **The remaining Lazy rows** in the family carry no licence from this
   measurement; filed as a parity sweep sequenced after the arc.
