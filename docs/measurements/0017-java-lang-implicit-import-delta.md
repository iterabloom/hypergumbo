<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0017: What resolving java.lang statics did to taint precision

**Status:** Complete
**Date:** 2026-09-07
**Instrument:** the scripts in `~/hypergumbo_lab_notebook/suril_javalang_09072026/` — `PLAN.md` (the pre-registration, written before any arm ran, with its two dated amendments and its record of a process failure), `arm.sh` (both arms, each asserting its own `java.py` blob at BEGIN and END), `analyze.py` (the situation diff), `readback.py` / `rb_*.out` (every moved row against source), `ADJUDICATION.md` (the verdicts) — plus the pre-fix sizing in `~/hypergumbo_lab_notebook/suril_09072026/` (`probe.py`, `census.py`, `residual2.py`, `langcensus.py`)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `INV-suril` (the invariant under test), `WI-gotun` (the open def/use gap this measurement prices), `INV-hahak` (the wildcard half, satisfied), `INV-januj` / `INV-fofoj` (the qualifier strip that made the second mechanism dead), `WI-nakut` (the java half of the untyped-receiver caveat)

## Frame

Machine-readable per ADR-0048 §A3. **A marginal delta, not a population
measurement; it does not enter the 0006 series** (0003's shape, as 0013–0016).

- unit: the SITUATION (claim, source symbol), with the run's `findings_total` beside it
- allocation: CENSUS of the delta — every situation the change added or removed on three repositories, read at source in full; no draw
- seed: none, no draw was made
- cohort: cassandra (6,090 .java, the repository the pre-fix sizing was taken on), jenkins (1,495+ .java, a second large java application in an unrelated domain), sherpa-onnx (169 .java, the repository INV-suril was FILED from — required by the closure-evidence discipline whatever the other two say)
- claim_set: `docs/example-claims/generic-taint-claims.yaml`, the seven generic claims, verbatim
- rubric: measurement 0001's rubric with ADR-0046's two numbers — TP when the value read at the java.lang source actually reaches the reported sink; FP when the two are only call-reachable; VACUOUS = a read that is discarded at the source (compared to null, never bound); WRONG-TYPE = the source is not a java.lang class at all (the pre-registered refutation cell RC1/RC2)
- analyzer_sha: baseline = `java.py` blob `2bec4543f84c5f273fc7a60e3a49d92e8d4575c9` (dev `c2657786a2`); subject = blob `4bb97c9b060ec532defba5dfbe320e707e25affa`. The BLOB, not the commit, is the identity here, and deliberately: the subject tree was dirty in both arms' logs (`dirty_non_ops=3` / `2`), so a commit sha would not describe the bytes analysed. Both arms assert the blob AND a marker count (subject 3, baseline 0) at BEGIN and END.
- language_scope: java only. The python content of all three repositories ran in both arms and is reported unmoved rather than measured-at-zero.

## THE DECLARED BLINDNESS THIS RECORD SHIPS WITH

**java has no def/use extractor** (`WI-gotun`, open; the run's own
`dataflow_coverage.languages` reports java `dataflow_capable: false`, blockers
`atomic_statement`, `def_use_extractor`, `ddg_spec`). Every java flow is
therefore `analysis_method=structural`, `walk=unavailable`; the section-3a walk
cannot refute any of them, and the sanitizer barrier arm can never credit a java
barrier. **Every precision figure below is a FLOOR** — it reads low by however
many false positives a walk would have removed — **and is not comparable with the
python figures in 0013–0016.** The arc's own rule for a java recall change is
"ship the extractor or a dated declared blindness; silence stays banned"; this
section is that declaration.

## The question

INV-suril's mechanism had moved twice and both statements were dead at tip
(§"What the premise check found first"). Its surviving population is `java.lang`
— the one package a correct java file never imports. This change makes a static
call on such a class carry its module slot. **Of the flows it ADDS, how many are
real, and how many of the real ones are useful?**

## What the premise check found first

Production `analyze_java` + production `classify_call` at dev `c2657786a2`,
before any code was written:

| shape | emitted dst | cc | classifies |
| --- | --- | --- | --- |
| `import java.nio.file.Files;` + `Files.write(p,b)` | `java:java.nio.file.Files:0-0:Files.write` | method | **fs_write** |
| `import static java.nio.file.Files.write;` + `write(p,b)` | `java:java.nio.file.Files:0-0:write` | **None** | **fs_write** |
| `import java.nio.file.*;` + `Files.write(p,b)` | `java:java.nio.file.Files,java.io.Files,java.lang.Files:...` | method | **fs_write** |
| fully qualified `java.nio.file.Files.write(p,b)` | `java:java.nio.file.Files:0-0:Files.write` | method | **fs_write** |
| `System.getProperty(...)`, no import | `java:external:0-0:System.getProperty` | method | **NONE** |
| `import java.lang.System;` + `System.getProperty(...)` | `java:java.lang.System:0-0:System.getProperty` | method | **env_read** |

Two of INV-suril's own statements die here. The construct clause dies on row 2:
a method-kind entry with `cc=None` classifies anyway, because the module-hint
path never consults the construct. The name-prefix clause dies on rows 1/3/4:
`strip_redundant_module_qualifier` strips a head the module slot already
carries. **The item's filed repro also no longer reproduces** — sherpa-onnx's
`java.nio.file.Files:copy` and `createTempDirectory`, the two dsts the item
names, are the repo's only two `Files.*` edges and BOTH classify.

Rows 5 and 6 are the live defect, and they are each other's control.

## Sizing, before the fix

| repo | edges lost (java.lang primitive, placeholder module) | in-run control (java.* modules that DID resolve and classify) |
| --- | --- | --- |
| cassandra | **459** / 5 names — `currentTimeMillis` 285, `nanoTime` 123, `getProperty` 29, `getenv` 16, `getProperties` 6 | 446, incl. `java.lang.ProcessBuilder` ×7 and `java.lang.Runtime` ×6 via the typed-local path |
| sherpa-onnx | 6 / 1 name (`getProperty`) | 14 |

459 lost against 446 that classify in cassandra's ENTIRE repository. 15 of 142
java catalogue rows (10.6%) live in `java.lang`; three of the five lost names
are `env_read`, so the loss reaches the taint SOURCE side, not only the io map.

## The delta

| repo | situations B→S | NEW | GONE | `findings_total` B→S |
| --- | --- | --- | --- | --- |
| sherpa-onnx | 26 → 27 | 1 | **0** | 145 → 147 |
| jenkins | 6 → 26 | 20 | **0** | 49 → 207 |
| cassandra | 23 → 23 | **0** | **0** | 84 → **357** |

**P2 (zero findings lost) HOLDS on all three.** RC1 (an added stamp on a class
the repo defines itself) and RC2 (a matched row outside `java.lang.*`) are both
EMPTY: no repository defines `System`, `Runtime` or `ProcessBuilder`, and every
moved row carries `source_module = java.lang.System`. **WRONG-TYPE = 0 of 21.**

## Adjudication — 21 moved rows, all read against source

**2 certain TP (9.5%); 3 counting one plausible (14.3%).**

- `SystemProperties.getString` (jenkins): `String value = System.getProperty(key)`
  at 236, `LOGGER.log(logLevel, "…{1}", new Object[]{key, value})` at 239. Same
  method, direct. **TP and useful.**
- `WinswSlaveRestarter` (jenkins): `exe = System.getenv("WINSW_EXECUTABLE")` is
  a field; `LOGGER.info(exe + " cmd: output:…")` logs it. **TP.**
- `CLI._main` (jenkins): `System.getenv("JENKINS_URL")` → `SSHCLI.openConnection`
  → `url.openConnection()`. Plausible; the intermediate hop was NOT read, so it
  is counted only in the generous tally.

The other 18 fall into three shapes, all of them the shape a def/use walk
removes: **clock-to-sink** (5 — `currentTimeMillis` paired with a network sink
that in one case executes EARLIER in the same method), **discarded-at-source /
vacuous** (3 — the read appears only inside a `!= null` test and is never
bound), and **cross-method no-flow** (10 — a real read and a real sink in
call-reachable methods with no dependence; one logs `e.getMessage()` rather than
the properties it collected).

## Why this ships rather than being withheld

**The baseline's own java findings are already in this regime.** Of jenkins'
6 baseline situations, 3 are java and ALL THREE are `structural` /
`walk=unavailable` (`System.in`→`Files.delete`, `System.in`→`File.delete`,
`LocalDate.now`→`HttpClient.send`); the other 3 are python. So this change
SCALES java's existing precision; it does not introduce a worse one. That is the
difference from the rule withheld under `WI-sihoh`, whose false positives came
from a DEFECT the fix itself amplified (`INV-putug`) rather than from a
declared, filed, untouched ABSENCE.

**And one gain does not depend on a walk at all.** Eight blindness caveats
RETIRE on jenkins — `untyped_receiver`, `unknown_receiver_scope`,
`analyzer_construct_blind`, `analyzer_method_call_blind`, on both
`host-secret-no-host-fs` and `host-secret-no-network`. They existed because the
analyzer could not see the receiver. Now it can.

## The repository where nothing moved, and why

**cassandra moved 0 situations while gaining 273 structural findings**
(`structural` 64 → 337; python's `ddg` 6 and `ddg_mixed` 14 are byte-identical
across arms, as are all 25 evidence rows). The added findings surface as ZERO
claim violations because **the claim set asks seven questions and none of them
is the question those flows answer**: the additions are dominated by
`currentTimeMillis` / `nanoTime`, which mint `host_description`, and the only
host_description claim is `host-description-no-network` — which stays
`inconclusive` because no clock read reaches a network sink in cassandra. There
is no `host-description-no-logging` and no `host-description-no-host-fs`. The one
visible movement is `host-secret-no-host-fs` gaining `excluded_flows:
{"test_sourced": 2}` — two new flows added and correctly excluded as
test-sourced.

This is not "the fix did nothing on cassandra". It is 273 findings landing
outside the questions asked.

## What this does not establish

Nothing about java flows a def/use walk would refute — that is `WI-gotun`, and
this record prices it rather than answering it. Nothing about `Runtime` or
`ProcessBuilder`, whose catalogued subprocess sinks already reach through the
typed-local path and are not in the moved set. Nothing about whether a CLOCK
READ should mint a taint source at all: `currentTimeMillis` / `nanoTime` are
catalogued `host_info_read` and account for 5 of jenkins' 20 new rows and the
bulk of cassandra's 273, and that is an `INV-nular`-family catalogue question,
filed rather than changed inside a recall PR. And nothing about kotlin or scala,
whose identical shape was enumerated (22 of 186 and 57 of 184 catalogue rows on
auto-imported modules, both confirmed on emission) and filed.
