<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0015: What typing Django's reverse-relation managers did to taint precision

**Status:** Complete
**Date:** 2026-09-07
**Instrument:** [`scripts/measure-taint-precision.py collect`](../../scripts/measure-taint-precision.py) (held constant across both arms), the subject-arm ledger and the delta scripts in `~/hypergumbo_lab_notebook/gulaz_relmgr_09072026/` (`PLAN.md` — the pre-registration, `shape_scan.py` / `shape_scan.txt` — the population sizing, `arm.sh`, `analyze.py`, `readback.txt` — the 27 excerpts, `refute.py` / `refute.json` — the refutation check on the production survey, `delta_pretix.json`); the baseline ledger is measurement 0014's arm C in `~/hypergumbo_lab_notebook/fasap_dbquery_09072026/C/`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `WI-gulaz` (the change under test, Phase 6 PR 3 of the INV-linub class), `INV-mumov` (whose 2026-09-06 census sized the reverse-relation manager as candidate 2), `WI-fasap` (0014, whose arm C is this record's baseline)

## Frame

Machine-readable per ADR-0048 §A3. **This record is a marginal delta, not a
population measurement, and does not enter the 0006 series** (0003's shape,
as 0013 and 0014): the population is every situation one change ADDED or
REMOVED on one repository, read at source in full, so F2's allocation and
F3's seed have nothing to govern and the keys say so.

- unit: the SITUATION (claim, source function — INV-karud's collapse record), with the row count beside it (33 added / 0 removed rows = 27 new / 0 gone situations)
- allocation: CENSUS of the delta, NOT an M x R draw — all 27 new situations on the subject repository read at source and classed; the 24 kept situations whose source-primitive or sink set changed tabulated (not adjudicated: every one was `violated` in both arms and no key was lost); one subject (pretix, the Phase 6 cohort's only Django repository) and one control (kserve, expected and observed 0 moved)
- seed: no draw was made, so none was seeded — the population is every situation the change touched
- cohort: pretix (subject) and kserve (control), the two repositories measurements 0013 and 0014 ran on; qualification rule = the same cohort, so 0014's arm C is the baseline
- claim_set: `docs/example-claims/generic-taint-claims.yaml`, the seven generic claims, verbatim; all 27 new situations are on `untrusted-input-no-database`
- rubric: measurement 0001's rubric with ADR-0046's two numbers, applied per situation to the flows the ledger reports — TP when the value read at the new source is the RECEIVER or an ARGUMENT of a `db_write` call on the reported path (a fetched row written back through its own manager or method counts, as 0013's R-TP class did); FP when it is not (a value that only gates a branch, an argument to a check, a read after the write, or a write whose real carrier is an untyped `.save()` on a loop variable); VACUOUS = a TP whose only carrier is a lazy QuerySet or a branch condition (ADR-0046 KIND-MISDECLARED); WRONG-TYPE = the receiver is not the model the relation index says (the pre-registered refutation, PLAN.md)
- analyzer_sha: baseline 2fc749f655 (dev; measurement 0014's arm C ledger ran on dd3b7cf0ee, and `git diff --stat dd3b7cf0ee 2fc749f655 -- packages/` is empty, asserted in PLAN.md); subject = branch `jgstern-agent/feat/django-related-managers` at 2fc749f655 plus the WI-gulaz runtime diff (`py.py`, the `python-web-and-orm` overlay, `python.yaml`'s `ambiguous_names`, one test file — `dirty=4`), ASSERTED in `armD.log` before and after the run (`DjangoRelationIndex` 1, `_DjangoReceiverOracle` 1, the four write rows 1); the PR's implementation commit differs from that working tree by one lint-driven hoist made after the run — the per-file half of the index builder moved into `_index_django_relations_in_file` with no logic change, the same 37 feature tests passing on both trees
- language_scope: python is the only language the change can affect and the only language in scope; kserve's go content ran and is reported unmoved, not measured-at-zero

## The question

WI-gulaz gives the Python analyzer a project-wide index of Django relation
declarations, so a manager reached through a reverse-relation accessor
(`order.payments`), a forward many-to-many field (`event.sponsors`), the
default `<model>_set`, or a class-level manager under another name
(`Checkin.all`) is typed the way `<Model>.objects` has been since WI-sozoj;
rows the RelatedManager's own writes (`add` / `remove` / `clear` / `set`);
and binds `x = <manager>.get(...)` to the model instance it returns. The
question 0003's method asks of a recall change: **of the flows this change
ADDS, how many are real, and how many of the real ones are useful?**

## Method, pre-registered before the run

Written in `PLAN.md` before any subject number existed, after a shape scan
of pretix sized the population (5,441 call sites on a related-name-like
accessor, 73 on a custom manager name; the table of which mechanism reaches
which share is in the plan), with four predictions:

- **P1** kserve: 0 moved, 0 lost; no verdict category moves in either
  repository.
- **P2** pretix gains situations only through a newly typed receiver
  reaching a `db_read` or `db_write` row; every new situation is read at
  source and classed TP / FP / VACUOUS / WRONG-TYPE.
- **P3** No situation is lost except by re-keying of the collapse
  representative; every gone key is read.
- **P4** The refutation check on the production survey reports 0 typed
  receivers whose class does not own the accessor.

Ship rule: a WRONG-TYPE situation blocks the merge until the index refuses
it; FP situations are the cost and are reported.

One arm, cold, on an isolated `XDG_CACHE_HOME` with a `cache-clear` per
repository (`arm.sh`): kserve 410 s, pretix 2,105 s, then a full
`hypergumbo survey` of pretix on the same tree for the refutation check.

## Results

### The control and the totals

| repo | rows | situations | new | gone | verdicts moved |
|---|---|---|---|---|---|
| kserve (control) | 63 → 63 | 58 → 58 | 0 | 0 | 0 of 7 |
| pretix (subject) | 99 → 132 | 95 → 122 | **27** | **0** | 0 of 7 |

P1 and P3 hold. Every affected claim was `violated` in both arms, so no
verdict could move. 24 kept pretix situations changed their source-primitive
or sink set without changing key: 14 gained a newly typed read (`exists` 5,
`__iter__` 4, `count` 4, `first` 3, `last` 3, `aggregate` 3, `iterator` 1),
12 swapped their representative sink from `create` to `delete` /
`bulk_create` — the collapse re-keying its representative, which is why the
unit is the situation and not the row — and 4 gained a newly reachable write
(`add` in `mail`, `update_or_create` in `send_webhook`, `create` in
`reactivate_order`, `bulk_create` in `_cancel_order`). No primitive was lost
from any kept situation.

### The 27 new situations, read at source

All 27 are on `untrusted-input-no-database`. By the mechanism that typed the
new receiver:

| mechanism | situations | examples |
|---|---|---|
| `x = <Model>.objects.get(...)` then `x.<accessor>.<write>` or `x.<method>()` → write | 12 | `gc = GiftCard.objects...get(pk=...)`; `gc.transactions.create(...)` (`GiftCardViewSet.transact`); `order = Order.objects.get(pk=order)`; `order.refunds.create(...)` (`_try_auto_refund`); `inv = TeamInvite.objects.get(token=token)`; `inv.team.members.add(request.user)` (`invite`) |
| annotated parameter or `self` in a model, then `<x>.<fk>.<accessor>` | 9 | `event.organizer.sales_channels.get(...)` (cart.py, 4 functions); `self.order.invoices.filter(...).last()` (`OrderPayment._mark_order_paid`); `invoice.refers.lines.all()` (`build_cancellation`); `self.event.subevents.annotate(...)` (`Rule.save`) |
| `for x in <accessor>.all()` — the evaluation call site on a related manager | 5 | `for e in self.events.all()` (`Organizer.delete_sub_objects`); `for t in other.tax_rules.all()` (`Event.copy_data_from`) |
| custom manager name | 1 | `qs = Checkin.all.filter(...)`; `for ci in qs` (`CheckInResetView.async_form_valid`) |

By class:

| class | n | what it is |
|---|---|---|
| **TP** | **18** | a fetched row written back through its own manager or method: `order.payments.create(...)`, `gc.transactions.create(...)`, `inv.team.members.add(user)`, `o.send_mail(...)` → the mail queue write, `order.create_transactions()` → `bulk_create`, `i.refered.exists()` gating a write on the same fetched order, `Checkin.all` rows copied into `LogEntry.objects.bulk_create(logentries)`, subevents iterated into `ScheduledMail` rows |
| **FP** | **9** | 4 × a fetched row used only as a CHECK argument two hops away (`sales_channel` → `CartManager` → `Seat.is_available(sales_channel=...)`; `CartPosition` has no such field, so `bulk_create` never writes it); 5 × a structural pairing (hops=0, no data-dependence walk) where the real carrier is an instance write the analyzer does not type — `e.delete()` / `t.save(force_insert=True)` / `line.save()` / `cp.save()` on a loop variable or typed local, while the typed sink in the same function (`self.teams.all().delete()`, `self.limit_sales_channels.set(...)`, `Checkin.objects.create(...)`, `invoice.lines.all().delete()` — a read AFTER the delete, `CartPosition.objects.bulk_create(...)`) carries nothing of the read |
| VACUOUS | 0 | the TP situations all carry the fetched row itself; `count` and `exists` appear only beside a carrying `get` / `last` |
| WRONG-TYPE | 0 | every typed receiver read back to the model the index names (`gc` a GiftCard, `inv.team` a Team, `self.order` an Order, `invoice.refers` an Invoice, `clist.event` an Event, …) |

**Correctness 18 / 27 = 66.7 %. Useful 18 / 27 = 66.7 %** (ADR-0046's two
numbers, same denominator; nothing vacuous to deduct). Against 0013's 40
situations (87.5 % correct, 17.5 % useful) the change is less often correct
and far more often useful, and the two facts have one cause: the new sources
are EXECUTING reads (`get`, `first`, `last`, `__iter__`) bound to a row that
the same function writes back, so a TP here is a real read→write pair; and
the FPs are the two shapes a structural pairing cannot tell apart — a row
used to decide, and a row written through an instance method the analyzer
does not yet type. Both FP shapes name filed work, not a defect in the
index: the loop-variable and `instance.save()` typing (WI-sozoj's deferral)
would turn the five structural FPs into flows on their real carrier, and a
data-dependence walk at two hops (`ddg_mixed/not_attempted` on all four
cart.py situations) would refuse the check-argument ones.

### The refutation check (P4)

`refute.py` joins every `django_orm` marker edge in the subject survey of
pretix (3,362 edges) back to its call site and resolves the receiver with
the INDEPENDENT bare-name index of `shape_scan.py`:

| verdict | edges |
|---|---|
| `.objects` marker (unchanged mechanism) | 2,739 |
| related accessor, receiver class owns it (directly or through a base) | 516 |
| custom manager name, declared on the class | 33 |
| two same-named calls on one line, one of them owned | 23 |
| receiver the bare-name scan cannot resolve | 49 |
| **receiver class does NOT own the accessor** | **0** |

The 49 unresolvable sites were read by hand (`unresolved_check.txt`): every
one is a binding the analyzer trusts and the scan's single-shape,
first-found rule does not — a rebinding from `Order.objects.select_for_update(...).get(pk=order)`
after an unannotated parameter, a related manager's `create` / `get` /
`first`, `gc = GiftCard.objects...get(pk=gc.pk)` inside the loop that first
bound `gc`, a two-hop relation chain (`self.team.organizer.events`), an FK
field declared on an abstract base (`op.item` for an `OrderPosition`), or
`t = Team.objects.create(...)` in the same branch as `t.members.add(...)`.
The pre-registered non-owned population — serializer fields named
`meta_properties`, the `OrderPosition.checkins` property — carries **no**
Django edge. P4 holds.

One gap the check surfaced on the way: Django names the default accessor of
an FK declared on an ABSTRACT base after each concrete subclass
(`SubEvent.orderposition_set`, `cartposition_set`), and the index names it
after the base, so neither is typed — 27 pretix sites, precision-safe,
filed with the index-registration residual.

## What it means

- **The relation index reaches what the census said it would.** 0013's five
  FPs included two whose real write lived on a reverse-relation manager;
  0014's read-back named `generate_seats`' `event.seats.select_related(...)`
  and `sendmail`'s `Exists()` over `o.positions` as untyped. All of those
  shapes are typed now, and the change added 27 situations while losing none
  (kserve: nothing moved).
- **Precision is priced in two numbers and the FP anatomy is named.** Two
  thirds of the added findings are real read→write pairs a reviewer can act
  on; the remaining third splits into check-arguments (needs the DDG at
  depth) and untyped instance writes (needs the filed loop-variable and
  typed-local `.save()` work). Neither is a wrong type.
- **What it did not do.** Fields assigned in a test's `setUp` (1,155 pretix
  sites, all under `src/tests`, excluded from verdicts by WI-bifob anyway),
  the loop variable of a `for` over a QuerySet (150 sites), a `@property`
  that returns a QuerySet (pretix's `OrderPosition.checkins`), and the
  one-to-one reverse accessor are each filed with their count.

## Limits

- A structural pairing (hops=0, `walk_verdict: unavailable`) is the
  instrument's weakest evidence and 5 of the 9 FPs sit there; the record
  classes them by reading the source, not by trusting the pair.
- The four cart.py situations are two hops deep with the DDG not attempted;
  the FP class rests on reading `CartManager` and `CartPosition`, which a
  future pretix could change.
- One subject repository; the Phase 6 cohort has one Django repository.
  awaits_bakeoff_validation on merge.
