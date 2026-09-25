<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0024: What routing Scala's already-inferred receiver type into the module slot recovers, and what it discloses

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/sigog_scala_slot_09092026/` — `PLAN.md` (pre-registration, written before any A-arm number existed, including the §2 covariate it was predicted against), `bderive.sh` / `arm.sh` (the two arms, subject blob asserted at BEGIN and END, exit 3 on mismatch), `diffarms.py` (three-level delta), `readback.py` → `ADJUDICATION.txt` (per-row census), `RESULT.md`, `RESULT_RAW.txt`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-sigog` (the gap), `INV-linub` (the class), `INV-tapat` / `INV-maluk` (the F3 kind gate this makes reachable), `INV-fazim` (the bare-name category error the fix refuses to commit)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: THREE units, reported separately because only the third moved in the
  way a reader expects a taint change to move. (a) the TYPED SHARE of external
  method-call edges, from the survey; (b) the catalogue PRIMITIVE reached
  through this language's own edges, from io-boundaries; (c) the CLAIM VERDICT
  and its CAVEATS, from verify-claims.
- allocation: CENSUS. Every primitive newly reached through a scala edge, in
  both repos, read back against source in full. No sample.
- seed: none. The delta is deterministic; there is no draw to reproduce.
- language_scope: scala only. The change is one expression in `scala.py`'s
  external method-call branch; nothing outside Scala can reach it.
- cohort: sbt (36 MB, a build tool) + lila (185 MB, a web application) as
  SUBJECTS; killbill (228 MB, java, 0 `.scala` files) + modsecurity (13 MB,
  cpp, 0 `.scala` files) as CONTROLS. killbill is the load-bearing control
  because Scala's catalogue MERGES java's, so it detects leakage into the
  borrowed rows rather than merely into an unrelated language.
- claim_set: the seven generic taint claims, verbatim
- rubric: measurement 0001's. CORRECT = the source really calls that method on
  a receiver of the type the module slot names, with the import present.
- analyzer_sha: two arms, one expression apart. AFTER = `scala.py` blob
  `f5854f219cdc89e44fc9b415ef9ed731b5b022da` (commit `c9d2aad2fd`); BEFORE =
  blob `91b17322d8370f8279365aeb95ece5adf771f40b` (dev `82f6dd89c5`). The
  blob is asserted at BEGIN and END of the A arm and the arm exits 3 on a
  mismatch. The B arm is DERIVED — its surveys already encode the pre-change
  analyzer, and io-boundaries / verify-claims are interpreters of a survey, so
  both arms were interpreted at ONE interpreter SHA and the only inter-arm
  difference is the subject blob.

## Result — recall

    repo   scala external method-call edges typed
    sbt      0 / 10,985  (0.00%)  ->  595 / 11,032  (5.39%)
    lila     0 / 29,985  (0.00%)  ->  317 / 30,019  (1.06%)

    method-kind catalogue rows reached VIA SCALA'S OWN EDGES     chains
    sbt      0  ->  13                                           0 -> 38
    lila     1  ->   2                                           1 ->  2

Zero of 40,970 became 912 of 41,051. The 14 gained rows are **14/14 CORRECT**
on a census read-back — every one names a real Java I/O method on a receiver
whose declared type matches the slot, with the import in the same file.
**Zero false positives.**

sbt: `BufferedReader.readLine`, `BufferedWriter.newLine`/`.write`,
`File.createNewFile`/`.delete`/`.exists`/`.length`/`.list`/`.listFiles`/
`.mkdirs`/`.renameTo`, `ServerSocket.accept`, `Path.toFile`. lila:
`File.mkdirs`.

## Result — verdicts unchanged, disclosure materially better

No claim verdict moved on any repo and `sanitized_flows` is 0 → 0 everywhere:
the pre-registered sanitizer hazard did not materialise. What moved is the
closed-world caveat.

    sbt    receivers the analysis could not type
             11,177 / 11,645  (95.98%)  ->  10,629 / 11,692  (90.91%)
           per-boundary untyped call sites
             host_fs   450 -> 416     database  103 -> 90     logging 70 -> 68
             network    11 ->  11     subprocess 96 -> 96     (both flat)

    lila   receivers the analysis could not type
             40,157 / 40,739 (98.57%)  ->  39,874 / 40,773 (97.80%)
             database   30 -> 29

548 sbt and 283 lila call sites moved from "the catalogue could not be asked
about this call" to examinable, and none became a violation. The verdicts held
while the ground under them got firmer, which for a tool whose verdicts are
only as good as their disclosed blind spots is the result rather than a
consolation prize.

## The controls, and why they are the strong half

killbill and modsecurity produce **byte-identical verify-claims JSON** across
the arms, and identical L3/L4a on every observable (killbill 65.00% typed both
arms; modsecurity 95.53% both arms). The identity holds under the WIDENED
comparison that includes the caveat digest, so it is not an artefact of
comparing too few fields.

## The instrument was wrong once, in the direction that exits 0

`diffarms.py`'s first cut compared `verdict` / `evidence_count` /
`sanitized_flows` / `excluded_flows` and reported **"no claim-level movement"**.
Every one of those four is genuinely flat, so it exited 0 with a plausible and
wrong answer; a whole-file JSON compare showed `verdicts` differing in five of
seven sbt claims. The caveat counts — the only place this change's effect lands
at L4 — were outside what it looked at. The instrument now digests them. A
one-of-N instrument hides N−1, and it does not error while doing so.

## The +47 / +34 edges are dedup splits, not invented call sites

`method_edges` rose, which a slot rewrite must not do. `deduplicate_edges` keys
on `(src, dst, edge_type)` IGNORING LINE, keeping the first site and absorbing
the rest into `meta["call_lines"]`; while every dst read `scala:external:…`,
two calls to the same method name from one function — one on a typed receiver,
one not — shared a key. Giving the typed one a real module splits it.

Tested, not asserted: of the 33 sbt and 18 lila `(src, line)` pairs appearing
as an `edge.line` only in the A arm, **33/33 and 18/18 were already present in
B inside some edge's `call_lines`**, and 0 call sites were lost. Previously
conflated sites are now individually addressable — what ADR-0017 §4's "which
callee is called at line U" needs.

## Two pre-registered predictions were refuted, both toward more effect

| observable | predicted | actual | |
|---|---|---|---|
| sbt typed share | 1–4% | **5.39%** | REFUTED, above band |
| lila typed share | 0.3–2% | 1.06% | held |
| sbt chains via scala | 1–15 | **38** | REFUTED, above band |
| lila chains via scala | 1–10 | 2 | held |
| new sanitizer barriers | 0–2 | 0 | held |
| controls | exactly 0 | 0 | held |

The prediction discounted hard for Scala's implicit imports (`String`,
`Boolean`, `Thread`) and project types (`Coll`, `Me`, `UserId`), none of which
get a path. Too aggressive for sbt: a build tool imports `java.io` types
explicitly and by name far more than a web application does. The error is in
the flattering direction, which is the one to distrust first.

## External validity — the ceiling, and it is not this change

`PLAN.md` §2 measured the covariate BEFORE predicting: only **10.1%** (sbt) and
**4.8%** (lila) of these edges carry `receiver_type_hint` at all. So 5.39% is
53% of sbt's achievable ceiling and 1.06% is 22% of lila's, and the remaining
~90–95% carry no inferred receiver type for any downstream change to route.
That is a distinct and larger defect (`var_types` coverage) filed as its own
item, with one cheap confirmed mechanism — `val b: java.io.File = …` yields no
hint at all, because a qualified type is not a `type_identifier` node — and one
hard one, typing `val x = mk()`, which needs the return-type registry objc and
swift already have (the WI-dizag-A / WI-garar shape).

Two repositories, one build tool and one web application, is a narrow base for
the 5.39% / 1.06% spread, and the spread between them is large enough that
neither number should be quoted as "Scala's typed share". What the two agree on
is the direction and the zero they both started from.
