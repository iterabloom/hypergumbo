<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0023: What stamping C's `#include` set on ambiguous bare calls recovers, and what it costs in verdict labels

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/lajus_include_09092026/` — `arm.sh` (arms before/after with the `c.py` blob asserted at BEGIN and END of each arm), `before/` and `after/` (survey + io-boundaries + verify-claims per repo)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-lajus` (the gap), `INV-linub` (the class), `INV-zimud` (the ALL-disjunct coverage gate this change activates for C), `INV-funuf` (the ANY-disjunct classification path it reuses)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: TWO units, reported separately because they move in opposite
  directions. (a) the catalogue PRIMITIVE reached, from io-boundaries'
  `primitive_counts`; (b) the CLAIM VERDICT, from `verify-claims`.
- allocation: CENSUS. Every primitive in every boundary of all three repos,
  both arms. No sample.
- seed: none. The delta is deterministic; there is no draw to reproduce.
- language_scope: c only. The stamp is in `c.py` and is gated on names in
  `c.yaml`'s `ambiguous_names`; nothing outside C can reach it.
- cohort: passt (2.7 MB), torsocks (1.2 MB), libvfio-user (1.5 MB) — three of
  the seven repos WI-lajus's arm-A census named. haproxy (30 MB) carries the
  largest filed population (28 of 48 sites) and was NOT run; see "external
  validity".
- claim_set: the seven generic taint claims, verbatim
- rubric: measurement 0001's. CORRECT = a real value flow exists from the
  source's value to the named sink.
- analyzer_sha: two arms, one file apart. AFTER = `c.py` blob
  `ad5c020ad6b3431faeb38fda30b02f40abe6f226` (commit 8e22832b31); BEFORE =
  blob `14db4446bccf3642a5012e96550ca6eedf5044f7` (`origin/dev`). The blob is
  asserted at BEGIN and END of each arm and the arm exits 3 on a mismatch, so
  a killed or interleaved run cannot report a mixed tree as a result.

## Result — recall

    repo            io_edges          new primitives                    lost
    passt           75 -> 119   sys/socket.{send,recv,connect,accept},     0
                                unistd.{read,write,fork,stat}
    libvfio-user    51 ->  58   sys/socket.{recv,connect,accept},          0
                                sys/stat.stat
    torsocks        15 ->  16   sys/stat.stat                              0

13 new primitives, ZERO lost, across three repos. `send` and `recv` — the
population WI-lajus filed — reach for the first time: 7 and 7 on passt, 4 on
libvfio-user.

## Result — verdict labels, and this is the cost

    repo            claims moved   direction
    passt                4         confirmed_with_caveats -> inconclusive
    libvfio-user         5         confirmed_with_caveats -> inconclusive
    torsocks             6         confirmed_with_caveats -> inconclusive

15 of 21 claim verdicts moved. **ZERO moved toward `confirmed`.** Findings are
unchanged in every repo (passt 50, libvfio-user 6, torsocks 11), so this is not
a precision result — there are no new findings to adjudicate, and none is
claimed.

## Why the verdicts moved, read in the code rather than inferred

The first reading — "the change added sources, so claims that were vacuously
confirmed became live" — is WRONG, and torsocks refutes it: torsocks has no
`net_recv` or `ipc_recv` chains in EITHER arm and still moved six claims.
`command_launch` is also unchanged in all three (77 / 9 / 2), so the change did
not add the opaque launches the new verdict text names.

The actual mechanism is **INV-zimud**, already documented in
`verify_claims.py`. The coverage gate expands a disjunctive module slot with
**ALL**, not ANY — "a non-match is informative ONLY if every possible home was
enumerated" — the opposite direction from the classification path's ANY
(INV-funuf). Its own comment records the population it was measured on: *"the
three C repos 0.0%"*. C dsts carried no commas, so the ALL-expansion never bit
C. Stamping the include set makes every ambiguous call carry one.

And `c.yaml` declares **no `module_completeness` section at all** — only a
comment referring to one. So no C module is an examined negative, every
stamped call is genuinely unexamined by the gate's own test, and the
qualification test `not unknown` flips. The same two opaque sites torsocks
discloses AFTER were already disclosed BEFORE, as an `opaque_boundary` caveat
on a confirmed verdict; what changed is whether they qualify as a caveat or a
downgrade.

## The direction is the safe one, and the previous confirmations were thin

The movement is entirely toward WITHHOLDING. Loosening a withholding gate is
the false-all-clear direction; this is its opposite. And the verdicts it
retracts were `confirmed_with_caveats` on repos whose socket I/O the analysis
could not see at all — passt is a user-mode TCP/IP stack that was confirming
"no untrusted input reaches a subprocess" with zero `send`/`recv` classified.
That is the shape INV-buzab and INV-zubuh were filed for: *"Each permitted a
real exfiltration into a confirmed verdict."*

It is still a LOSS OF INFORMATION at the label, and that is not argued away:
`confirmed_with_caveats` carried the sentence *"The claim holds everywhere the
analysis could see"*, and `inconclusive` with zero caveats does not. The
follow-up is `c.yaml`'s missing `module_completeness`, filed separately — with
it, these calls become examined negatives and the labels return.

## A regression this measurement caught before it shipped

The first implementation stamped EVERY unresolved call, not only ambiguous
ones. A module hint suppresses the permissive short-name fallback, and C
reaches most of libc through QUOTED project headers, so a slot naming only the
system includes made the module filter refuse rows that used to match. On
qemu-dtc: `stdio.fclose` 5 -> 2, `stdio.fopen` 1 -> 0, `stdio.fprintf`
35 -> 34, `stdio.fputc` 6 -> 5. PARTIAL EVIDENCE IS WORSE THAN NONE. WI-lajus
had specified restricting the stamp to `ambiguous_names`; the item was right
and the first implementation over-generalised. Both halves are now pinned by
tests.

## External validity — the honest limit

Three repos, one language, and the two largest filed populations were not run:
haproxy (28 of 48 sites) at 30 MB and dbus-broker / guacamole-server /
firejail, which are not in this corpus. The recall direction is unlikely to
reverse — the mechanism is structural — but the VERDICT-LABEL cost is a
function of how much of a repo's include surface `c.yaml` enumerates, which is
currently none of it, so the 15-of-21 movement rate should be read as a
property of today's catalogue rather than of this change.
