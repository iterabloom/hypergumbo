<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0013: What typing the Django QuerySet chain did to taint precision

**Status:** Complete
**Date:** 2026-09-06 / 07
**Instrument:** [`scripts/measure-taint-precision.py collect`](../../scripts/measure-taint-precision.py) (held constant across both arms), per-arm ledgers and the delta scripts in `~/hypergumbo_lab_notebook/mumov_qschain_09062026/` (`arm.sh`, `armA.sh`, `diff.py`, `rekey.py`, `situations.py`, `new_situations.txt`)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `INV-mumov` (the change under test, Phase 6 PR 1 of the INV-linub class), the 2026-09-06 derivability census on that item, `WI-lunav` (the lazy-combinator rows this measurement makes fixable)

## Frame

Machine-readable per ADR-0048 §A3. **This record is a marginal delta, not a
population measurement, and does not enter the 0006 series** (0003's shape,
under the gate that 0003 predates): the population is every situation one
change ADDED or REMOVED on one repository, adjudicated in full, so F2's
allocation and F3's seed have nothing to govern and the keys say so.

- unit: the SITUATION (claim, source function — INV-karud's collapse record), with the row count beside it (74 added / 29 removed rows = 40 new / 0 gone situations, 31 with a moved representative)
- allocation: CENSUS of the delta, NOT an M x R draw — all 40 new situations on the subject repository read at source; one subject (pretix, the Phase 6 cohort's only Django repository) and one control (kserve, expected and observed 0 moved)
- seed: no draw was made, so none was seeded — the population is every situation the change touched
- cohort: pretix (subject) and kserve (control), the two python repositories the 2026-09-06 derivability census on INV-mumov ran on; qualification rule = the census that chose the slice, not a random draw
- claim_set: `docs/example-claims/generic-taint-claims.yaml`, the seven generic claims, verbatim; every one of the 40 new situations is on `untrusted-input-no-database`
- rubric: measurement 0001's, verbatim (F6), including its tie-break ("taint flows through in-program computation, not through an external resource selected by the tainted value"), plus ADR-0046's two VACUOUS classes for the usefulness label (CONFIGURED-ACTION, KIND-MISDECLARED)
- analyzer_sha: subject ddb9a02404 (the implementation commit; arm B ran on the working tree whose runtime files that commit captured unchanged); baseline = the three runtime files checked out at the merge base 4f555a6fa9, ASSERTED in `armA.log` before the run (root rule 0, Django signature rows 0) and after the restore (1, 21)
- language_scope: python is the only language the change can affect and the only language in scope for the claim; the two repositories' other languages ran and are reported unmoved, not measured-at-zero — kserve's 63 situations span its go/python content and none moved

## The question

INV-mumov's Phase 6 PR 1 lets the RESULT of `<Model>.objects.<queryset-method>()`
carry `django.db.models`, so `Order.objects.filter(...).exists()` and
`qs = Order.objects.filter(...); qs.delete()` reach the catalogue's Django rows
at the second hop. The INV-linub class rule (2026-09-04) says an L3 fix is
measured through the taint path, not through io-boundaries chain counts, and
every Phase 6 recall PR ships its marginal precision by the
[0003](0003-instantiates-taint-delta.md) method: **of the flows this change
ADDS, how many are real, and how many are worth anything?**

## Method, pre-registered on INV-mumov before the run

Two arms on the editable install, sequential, each with its own
`XDG_CACHE_HOME` and a `cache-clear` per repository. Arm B (subject) ran on
the branch; arm A (baseline) ran with the three runtime files
(`py.py`, `library_signatures/python.yaml`, `library_signatures.py`) checked
out at the merge base `4f555a6fa9` and the baseline **asserted** in the log
before the run (root rule absent, Django signature rows 0) and the restore
asserted after (1, 21). Subject repository: pretix, the only Django repository
in the Phase 6 cohort. Control: kserve, where nothing should move.

The unit is production's situation (INV-karud's collapse): a record is "S
reads {P...} and reaches zone Z via {Q...}", keyed here on (claim, source
function). Every NEW situation was adjudicated by reading the source at the
cited lines under the 0001 rubric (the source value, or a value derived from
it by data flow, is an argument to the sink call or its receiver; 0001's
tie-break excludes an external resource merely SELECTED by the value) and
then ADR-0046's two-axis rubric (CONFIGURED-ACTION and KIND-MISDECLARED
deducted from the useful numerator only).

## Population

| repo | arm | rows | situations | new | gone | representative moved |
|---|---|---:|---:|---:|---:|---:|
| kserve (control) | A → B | 63 → 63 | — | **0** | **0** | 0 |
| pretix | A → B | 102 → 147 | 99 → 139 | **40** | **0** | 31 |

No verdict category moved in either repository (every affected claim was
already `violated` in both arms). The row-level diff read 74 added / 29
removed; all 29 "removed" rows belong to situations that survived with a
different representative (the collapse now names a chained hop —
`select_related`, `order_by`, `values_list`, `exists` — where it named
`filter`), which is [LIVE.md rule 11] in action: a collapse representative
is not an identity. **Nothing was lost.** Runtime: pretix 1,846 s → 2,491 s,
kserve 170 s → 307 s; the subject arm overlapped a full test slice for its
first seventeen minutes, so the difference is not attributed to the change.

## Verdict table — the 40 new pretix situations

| class | n | example | 0001 verdict | ADR-0046 |
|---|---:|---|---|---|
| **L — lazy-combinator source → write** | **28** | `WebHookCall.objects.filter(datetime__lte=...).delete()` (api/signals.py:73); `Voucher.objects.filter(pk=...).update(redeemed=F('redeemed')+1)` (models/orders.py:2764); `mails = ScheduledMail.objects.all(); mails.filter(...).update(...)` (sendmail/signals.py:154-180, DDG-confirmed) | **TP** — the QuerySet IS the receiver of the sink call | **VACUOUS: KIND-MISDECLARED.** `filter` / `all` / `prefetch_related` / `annotate` read nothing at that call: they are ADR-0049's "Lazy / unexecuted: Django's QuerySet combinators" row, kept under `db_read` by WI-lunav's ruling-3 reasoning because no executing read was represented on a chained receiver. This PR represents those reads (`exists` / `get` / `first` / `count` on the chain), so the removal is now licensed. A defect, deducted while it exists. |
| **R-TP — executing read → write, the value travels** | **7** | `order = Order.objects.select_for_update(of=OF_SELF).get(pk=...)` → `generate_invoice(order)` → `build_invoice` → `Invoice.objects.create(order=order, ...)` (api/views/order.py:600-616, :2066-2069; control/views/orders.py:1599-1609, :1737-1750); `locked_wle = WaitingListEntry.objects.select_for_update(...).get(pk=self.pk)` → `Voucher.objects.create(event=locked_wle.event, item=locked_wle.item, ...)` (models/waitinglist.py:194-226); `sq = OrderSyncQueue.objects.select_for_update(...).select_related("order").get(...)` → `self.sync_order(sq.order)` → `update_or_create` (datasync.py:200-217); `payment = OrderPayment.objects.select_for_update(...).get(pk=...)` → `ReferencedPayPalObject.objects.get_or_create(order=payment.order, payment=payment, ...)` (paypal2/payment.py:644, :680) | **TP** — the fetched row, or its fields, is an argument to the write | **USEFUL** — not configuration, not misdeclared |
| **R-FP — executing read, value does not travel** | **5** | `locked_instance = OrderPayment.objects.select_for_update(...).get(pk=self.pk)` used for its own state and copied into `self`; the invoice is built from `self.order` (models/orders.py:1908-1924 → `_mark_order_paid`): the payment's `order_id` SELECTS the order whose rows are written — 0001's tie-break; `gc = GiftCard.objects.select_for_update(...).get(pk=gcpk)` → reported route `payment.confirm()` carries no `gc` (payment.py:1637, :1655 is the real write, a reverse-relation manager the analyzer does not type); `locked_instance` compared once and dropped (services/orders.py:3061-3062); `cancel_order(self.order.pk, ...)` passes a primary key (control/views/orders.py:1546); `mail_send(**kwargs)` runs BEFORE the read at mail.py:412 | **FP** — reachability or resource selection, no value flow | — |

Read-back of the L class: 28 of 28 sites are `<Model>.objects.<lazy>(...)
.<write>()` or a bound QuerySet written back; none is a non-Django receiver
typed by name (the refutation condition written on INV-mumov did not fire,
because rule (c), the untyped-root rule, was not shipped).

## Headline

| unit | adjudicated | TP | FP | vacuous (KIND-MISDECLARED) | correctness | useful |
|---|---:|---:|---:|---:|---:|---:|
| situation, pretix delta | 40 | 35 | 5 | 28 | **87.5%** | **17.5%** (7 of 40) |

Both numbers are the DELTA's, not the repository's, and they say two
different things. The change is correct: nine in ten added situations carry a
genuine value flow, and the seven useful ones are a shape WI-sozoj could not
see at all — a row re-read under `select_for_update` and written into a new
row. The change is not yet useful on its own: seven in ten added situations
sit on a source that reads nothing, and that class is a **defect the same
change makes fixable**, not a permanent limit. With the lazy rows moved out of
the minting boundary the same delta reads 7 TP / 5 FP of 12, 58.3% useful.

## What this does not support

- No claim about the repository's precision (0012 remains the last
  population-level number); this is a marginal figure on one Django repository.
- No claim that the seven useful flows are security findings: they are the
  WI-vazal shape (database read reaching database write), reported with
  `source_boundary` so a consumer can filter them; the 2026-09-06 taint-label
  audit records what `untrusted_input` asserts here.
- The FP for `OrderPayment.confirm` rests on reading `order_id` as resource
  selection; a reader who counts a foreign-key traversal as data flow would
  move it to TP (8 of 40, 20.0% useful). Stated so the number is not quoted
  with false precision.
- kserve's zero is the control holding, not evidence of anything else.

## Consequences worth acting on

1. **The lazy QuerySet combinators must leave `db_read`** (`filter`, `exclude`,
   `all`, `order_by`, `select_related`, `prefetch_related`, `values`,
   `values_list`, `distinct`, `annotate`): ADR-0049's own table calls them
   deferred crossings, and ruling 3's licence condition — an executing read
   represented on the receiver they build — is what this PR delivers. Filed as
   the next Phase 6 item; the 28 vacuous situations are its positive control.
2. **The reverse-relation manager** (`gc.transactions.create(...)`,
   `self.order.payments.create(...)`) is where two of the five false positives'
   REAL writes live, unreported: INV-mumov's second candidate, sized at 1,901
   pretix sites by the derivability census.
3. INV-karud's own repro re-ran on the subject arm: zero pretix flows touch a
   d3 file or symbol (the 2026-08-01 bare-name attributions), and three
   JavaScript-rooted flows remain, none of them d3.
