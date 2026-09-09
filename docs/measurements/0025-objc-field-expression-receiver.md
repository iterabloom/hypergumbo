<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0025: What fixing Objective-C's message-receiver parse corrects, and why it cannot move recall

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/garar_objc_09092026/` — `PLAN.md` (pre-registration, written before either arm ran), `arm.sh` (both arms, `objc.py` blob asserted at BEGIN and END, tree restored, exit 3 on mismatch), `diffarms.py` (four-level delta incl. caveat digest), `RESULT.md`, `RESULT_RAW.txt`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-garar` (the gap, stage 1 of 2), `INV-linub` (the class), `INV-fazim` (the bare-name category error the old slot committed), `INV-tapat` / `INV-maluk` (the F3 gate that makes stage 1's recall effect exactly zero), `INV-divuf` / `WI-nakut` (the lossless name home this restores meaning to)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: THREE, reported separately. (a) the CALL SITE and the selector its edge
  names; (b) the MODULE SLOT and what it claims; (c) the catalogue PRIMITIVE
  reached through objc's own edges, plus claim verdicts and caveats.
- allocation: CENSUS for (b) — all 20 edges whose module slot disappeared were
  read back to source. SAMPLE for (a): 787 renames, of which a handful per repo
  were read back; the population is characterised, not adjudicated one by one.
- seed: none. The delta is deterministic.
- language_scope: objc only. The change is confined to `objc.py`.
- cohort: AFNetworking, CocoaLumberjack, fmdb, Mantle. **No control repository
  exists** — every repo in the objc cohort is objc, so leakage outside the
  language cannot be shown by a within-cohort control. This is weaker than
  measurement 0024's byte-identical java/cpp controls; see "limits".
- claim_set: the seven generic taint claims, verbatim
- rubric: measurement 0001's, applied to the SLOT: CORRECT = the name slot holds
  the selector the source sends, and the module slot names a type that exists.
- analyzer_sha: two arms one file apart. AFTER = `objc.py` blob
  `e3278f70150256d2a6cc5be6b6ff11896a9e23eb`; BEFORE = `bc0f1e044bb3e9d949ba4a3084564eb532f2db5f`.

## The defect

`_extract_message_selector`, `_extract_message_receiver` and the emit site's
nested-receiver typing each decided independently which child of a
`message_expression` is the receiver, and all three assumed it is either a bare
`identifier` or a nested `message_expression`. `self.<prop>` parses to a
`field_expression`, which none handled, so the parse desynchronised — differently
at each site. Counted from source by a scan sharing no code with the analyzer,
AFNetworking's 2,729 sends break down as `identifier` 70.0%, `field_expression`
15.7% (379 of 429 being `self.<x>`), nested `message_expression` 11.8%, and
subscript / call / string-literal / cast 2.4%. **Only the 1st and 3rd were
handled.**

## Result — correctness

    repo             method edges      typed        sentinel    prims via objc
    AFNetworking     1672 -> 1801   880 -> 860    792 ->  941      12 -> 12
    CocoaLumberjack  1808 -> 1870   973 -> 973    835 ->  897      19 -> 19
    fmdb              891 -> 1090   317 -> 317    574 ->  773       1 ->  1
    Mantle             227 ->  243  138 -> 138     89 ->  105       0 ->  0

**787 sites gained a correct selector; 380 of them emitted no edge at all
before** — a no-argument selector was consumed as the receiver, leaving nothing
for the selector. The old names show the mechanism: `dictionaryValueerror:` and
`resultforKey:` are MANGLED CONCATENATIONS of a truncated selector and a
following identifier; `YES` is a literal argument in the name slot.

**The −20 typed on AFNetworking is 20 false positives removed**, and it is
exactly six module slots: `GET` 12, `POST` 4, `HEAD` 1, `PATCH` 1, `PUT` 1,
`DELETE` 1. At `AFHTTPSessionManagerTests.m:195`,
`[self.sessionManager GET:@"/get" parameters:…]` had `GET` taken as the receiver;
uppercase, so it was treated as an ObjC class and written into the module slot as
a module that does not exist (INV-fazim), while the selector lost its first
keyword. The other three repos lost no module slots.

## Result — recall is ZERO, structurally

`primitives via objc edges` and `io-boundary chains` are FLAT in all four repos.
787 corrected selectors matched not one additional catalogue row, and this is the
F3 gate working as specified rather than the fix failing: `gate_named_entry`
refuses a method-kind row for an UNTYPED method call however correct its name is
(INV-tapat / INV-maluk), and these sites still carry the `external` sentinel
because typing `self.<prop>` is WI-garar stage 2. A correct name is NECESSARY
and NOT SUFFICIENT — precisely INV-linub's L3→L4 structure.

Two pre-registered predictions HELD and both were falsifiable: primitives "UP or
flat" (flat), and "AFNetworking `net_recv` stays ZERO — if it moves here my model
of the fix is wrong" (it stayed zero).

## The honest negative: the disclosure ratio got worse

    AFNetworking   untypable 798/1679 (47.5%)  ->  947/1808 (52.4%)
    fmdb                     574/891  (64.4%)  ->  773/1090 (70.9%)
    CocoaLumberjack         1276/2284 (55.9%)  -> 1338/2346 (57.0%)

Emitting 380 previously-invisible sends adds them to the denominator while they
stay untyped until stage 2, so the closed-world caveat reads WORSE after a change
that made the analysis strictly more truthful. The correct reading is that the
tool's estimate of its own blind spot was previously too flattering by ~380 sites
on this cohort. No claim verdict moved; `sanitized_flows` 0 everywhere.

## Limits

No control repository (above). And the 787 renames are characterised rather than
adjudicated one by one — the census is complete only for the 20 module-slot
removals, which are the correctness claim that could have gone the other way.

## An instrument defect caught mid-analysis

The first cut keyed the per-site name map on `(src, line)`. ObjC nests sends
freely and one line routinely carries several, so it kept only the last and
reported the siblings as renames, reading "251 renamed" on AFNetworking against a
true 399 new / 270 gone. Counting `(src, line, name)` triples compares multisets.
Same family as 0024's caveat miss: the instrument answered a narrower question
than the one asked, and exited 0 doing it.
