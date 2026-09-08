<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0018: What taking the receiver out of java's callee name slot did to the disclosures — and what it exposed

**Status:** Complete
**Date:** 2026-09-07
**Instrument:** the scripts in `~/hypergumbo_lab_notebook/nakut_java_09072026/` — `PLAN.md` (pre-registration, written before any arm ran, with its dated Amendment 1 for the third arm), `census.py` / `census2.py` (pre-change sizing on three repositories), `meas/arm.sh` and `meas/arm2.sh` (the three arms, each asserting its own blobs at BEGIN and END), `meas/analyze.py` / `analyze2.py` (verdicts and caveats), `survdiff.py` / `lossprobe.py` / `collapse.py` / `collapse2.py` / `whopass.py` / `threeway.py` (locating an unpredicted edge-count delta), `readback.py` / `preexisting.py` (every adjudicated row read against source), `meas/fixarm.sh` (the two-arm CLI fixture)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml) and `meas/boundary-claims.yaml` (ten boundary claims, measurement-only, not shipped)
**Tracker:** `WI-nakut` (the item under test, java half), `INV-divuf` (its objc half, satisfied), `INV-fibis` / `INV-nuhun` (the two disclosure arms), `WI-gigoz` (the method-call-recovery linker this exposed), `INV-suril` (the java.lang fix whose measurement 0017 this supersedes on one row)

## Frame

Machine-readable per ADR-0048 §A3. **A disclosure-quality measurement, not a
precision measurement, and it does not enter the 0006 series.** No 0003-method
marginal-precision figure is computed, and that is a claim rather than an
omission: H3 below is the claim that the change produces **no new findings at
all**, so a marginal-precision figure over an empty delta would be a category
error. If H3 had been refuted the change would not have shipped and the record
replacing this one would be a precision measurement.

- unit: the CAVEAT (its kind, its scope, and its entry list), with the survey's
  EDGE population beside it; and the CLAIM VERDICT as the control
- allocation: CENSUS — every claim in both claim files on both repositories, and
  every moved edge in the delta, with a seeded sample where the moved population
  is too large to read in full (`readback.py`, `preexisting.py`; seed 20260907)
- seed: 20260907, used only for the read-back samples
- cohort: sherpa-onnx (169 .java — the repository `WI-nakut`'s sibling half was
  filed from, and the SMALL end of the sizing), jenkins (1,884 .java — a large
  java application), plus a 1-file synthetic fixture for the one cell the two
  real repositories cannot reach. cassandra (6,090 .java) supplies pre-change
  sizing only and no delta: a full `verify-claims` arm on it is unbudgeted, and
  a number it did not produce is not quoted as one
- claim_set: the seven generic taint claims verbatim, plus ten boundary claims
  written for this measurement (one per boundary the java catalogue declares a
  method-kind row for)
- rubric: a removed or added first-party call edge is adjudicated by reading the
  CALL SITE and comparing the receiver's type in source against the class the
  edge names. CORRECT when they agree; WRONG when the source's receiver is of
  another type that itself declares the method
- analyzer_sha: three arms by BLOB. **B** (baseline, dev `6dffed9add`) `java.py`
  `4bb97c9b06…`, `method_call_recovery.py` `91de62876d…`. **S** (name-slot fix)
  `java.py` `d475dcbb5d…`, linker unchanged. **S2** (fix + the linker guard)
  `java.py` `d475dcbb5d…`, linker `e6e3a15b80…`. Every arm asserts its blobs and
  a marker count at BEGIN and END and ran with `dirty_non_ops=0`
- language_scope: java. Every other language in the polyglot cohort is reported
  UNMOVED from a measurement that could have moved it, not assumed inert:
  sherpa-onnx carries cpp, python, go, kotlin, javascript, swift, csharp, rust
  and dart, and all nine are byte-identical across the arms

## The question

java glued the receiver identifier into the name slot of an unresolved call
edge — `java:external:0-0:u.mkdirs:unresolved` rather than `mkdirs`. `WI-nakut`
filed the consequence and named what it had not measured: *"whether fixing
java's prefix would let the SCOPED caveat fire and thereby ATTRIBUTE the site to
a boundary (the more useful disclosure) rather than only counting it."*

**Does it — and does anything else move with it?**

## Parity: java was the only outlier, and was inconsistent with itself

One fixture per language, same construct (an untypable local receiver calling a
catalogued method), through the shipped `hypergumbo survey`:

| language | dst emitted |
| --- | --- |
| go | `go:external:0-0:Write:external_symbol` |
| kotlin | `kotlin:external:0-0:write:external_symbol` |
| python | `python:external:0-0:sendall:external_symbol` |
| rust | `rust:external:0-0:write_all:external_symbol` |
| scala | `scala:external:0-0:write:external_symbol` |
| **java** | **`java:external:0-0:u.write:external_symbol`** |

java also disagreed with itself. Of its five emit branches, the static-import
and typed-receiver branches already shortened the name; the explicit-FQ and
`java.lang` branches kept the prefix and left `strip_redundant_module_qualifier`
to remove it downstream; and the placeholder branch kept it with nothing
anywhere able to remove it. **The slot's content was a function of which
resolution branch fired** — a resolution fact filed under a naming slot.

## H1 — the scoped caveat can now fire and attribute

Sizing first, on the analyzer alone (`census.py`), counting edges whose module
slot is the placeholder, whose `call_construct` is `method`, and whose SHORT
name is a catalogued method-kind row:

| repository | prefixed edges / names | become attributable | boundaries | already matching |
| --- | --- | --- | --- | --- |
| sherpa-onnx | 870 / 110 | **10 edges / 4 names** | 3 | 10 |
| jenkins | 14,268 / 4,752 | **974 / 43** | 9 | 650 |
| cassandra | 61,923 / 19,306 | **4,708 / 53** | 10 | 4,953 |

**MATCH-AS-WRITTEN IS 0 ON ALL THREE**, which is the defect stated as a number:
not one prefixed name matched a catalogue row in its shipped spelling.

On the two repositories that ran arms, the taint arm's scoped caveat grew its
entry list: sherpa-onnx `untrusted-input-no-database` **63 → 71** sites and both
network claims 69 → 70; jenkins `untrusted-input-no-database` **25 → 85**.

### The boundary arm, which the real repositories could not reach

Every one of the ten boundary claims comes back `violated` or `inconclusive` on
both sherpa-onnx and jenkins — these are large applications that really do write
files, log, read the environment and launch processes — and a caveat attaches
only on a CLEAN path. **So the boundary half of the disclosure is not measurable
on this cohort, and saying "0 → 0" about it would be reporting the cohort as a
result.** A 1-file fixture reaches the cell, through the shipped CLI, no flags:

```
BEFORE  ! [no-fs-write] Verdict: confirmed_with_caveats
          CAVEAT (unknown_receiver_scope): ... distinct method(s): u.mkdirs.

AFTER   ! [no-fs-write] Verdict: confirmed_with_caveats
          CAVEAT (untyped_receiver): The claim holds everywhere the analysis
            could see. At 1 call site(s) — src/App.java:10 mkdirs() — a method
            the fs_write catalogue declares is called on a receiver whose type
            could not be determined, so whether those calls perform this I/O
            was never decided.
          CAVEAT (unknown_receiver_scope): ... distinct method(s): mkdirs.
```

Both halves of the item's complaint are visible in those five lines: the scoped
caveat appears and names `fs_write`, and the unscoped one stops printing
`u.mkdirs`, which is "readable but is not the catalogue's spelling".

## H2 — the unscoped caveat was printing an inflated count

`unknown_receiver_scope` reports **"N distinct method(s)"**, built from the
callee names. With the receiver glued on, one method arrived under many
spellings:

| repository | printed | true | inflation | worst single name |
| --- | --- | --- | --- | --- |
| sherpa-onnx | 229 | 178 | **+29%** | `release` as 14 spellings |
| jenkins | 5,875 | 2,903 | **+102%** | `getName` as 69 |
| cassandra | 22,584 | 8,191 | **+176%** | `get` as **427** |

Measured through the shipped pipeline the counts are larger, because the caveat
is repo-wide rather than java-only, and the drop is slightly LARGER than the
java-only census predicts (sherpa-onnx 1,081 → 1,009 against a predicted −51;
jenkins 6,083 → 3,091 against −2,972) — collapsing java's prefixed spellings
onto short names that other languages already contribute removes more distinct
entries than a java-only count can see. **Names still carrying a receiver
prefix: 110 → 0 and 4,752 → 0.**

## H3 — the control, and the reason this could ship at all

**No verdict moved.** 34 claim evaluations across two repositories and two claim
files, B against S and B against S2: zero differences, in either direction.

The guard is one the tree already argued rather than a new claim.
`receiver_name` is assigned only inside `if object_node is not None`, so every
edge this shortens carries `call_construct="method"`; `gate_named_entry` opens
with `if call_construct == "method": return None` for **every** kind, so a
shorter name cannot acquire a classification; and `_register_sanitizer_callers`
refuses an unresolved bare-name sanitizer match on the same stamp, so it cannot
acquire a phantom barrier. `make_unresolved_edge`'s own docstring names that
stamp as the guard against exactly "a name-shortening improvement".

## What the A/B found that the plan did not predict

The java method-construct edge count FELL — 1,642 → 1,571 (sherpa-onnx), 45,860
→ 45,390 (jenkins) — while `analyze_java` emits an **identical** 1,711-edge java
population in both arms. An A/B prices a change; only reading the moved rows
prices the claim, and here it found two mechanisms and a defect.

**1. A same-line receiver collapse — 1 edge on sherpa-onnx, 101 on jenkins.**
The survey holds one edge per `(src, dst, line)` in BOTH arms and for every
language, so two calls to one method through two different receivers at one line
in one function were two dsts and are now one. No walk can lose a path to it:
the merged edges share src, dst and line.

**2. The `method-call-recovery` linker started firing on java** — and got it
wrong. `parse_unresolved_name` reads the id's name slot **verbatim**, so
`audio.getSampleRate` matched no class member and `getSampleRate` matches six.
That linker's premise is that the class hint IS the receiver
(`CliRunner().run(args)`, one expression arriving as two edges); java's
variable-receiver calls do not satisfy it, and nothing checked. Arm S added 9
newly-recovered rows on sherpa-onnx and 241 on jenkins. Read back against source:

* **sherpa-onnx: 9 of 9 WRONG**, all one shape —
  `float d = audio.getSamples().length / (float) audio.getSampleRate();`
  resolved to `OfflineTts.getSampleRate`. `audio` is a `GeneratedAudio`, which
  declares `getSampleRate` itself; six classes in that repository declare it;
  the line-proximity tiebreaker chose the class the enclosing `main` happened to
  instantiate.
* **jenkins: a sample of 14 is ~10 right, 3 wrong, 1 ambiguous**
  (`user2.hasPermission` → `HudsonPrivateSecurityRealm`, `virtualRoot.list` →
  `FilePath.list`, `l.annotate` → `HyperlinkNote.annotate`).

**The evidence to refuse was already stamped on every one of those edges.**
`receiver_type_hint` reads `GeneratedAudio` on the wrong rows and
`OfflineStream` / `AudioTagging` on the right ones — agreeing, in each case,
with the class the linker chose or failed to. So arm S2 drops a class hint that
CONTRADICTS the stamp, rather than re-ranking it.

## H4 — what the guard did, and the refutation that fired

Pre-registered in Amendment 1 before arm S2 ran: sherpa-onnx 9 → 0; jenkins
fewer but **not zero** (zero would mean the guard refuses on a spelling mismatch
and has disabled the linker); no verdict moves; and *"the guard may only remove
recoveries arm S introduced"*.

| | sherpa-onnx | jenkins |
| --- | --- | --- |
| first-party java edges removed by the guard | **9** | **347** |
| …of which arm S had introduced | 9 | 85 |
| …of which existed in the BASELINE | **0** | **262** |
| first-party java edges the guard ADDED | 0 | 9 |
| verdicts moved (B vs S2) | 0 | 0 |
| any other language moved | none | none |

**THE LAST CLAUSE WAS REFUTED, AND THE CLAUSE WAS THE WRONG TEST.** The guard
removes 262 recoveries jenkins already had before this PR. A seeded sample of 12
of them, read against source with the receiver type the edge itself declared:

| call site | recovered to | receiver declared | verdict |
| --- | --- | --- | --- |
| `d.child(sb.toString())` | `FilePath.toString` | `StringBuilder` | WRONG |
| `List<FilePath> children = f.list()` | `VirtualFile.FilePathVF.list` | `FilePath` | WRONG |
| `property.getViews().contains(…)` | `ListView.contains` | `java.util.Collection` | WRONG |
| `map.put("A", "a")` | `EnvVars.put` | `TreeMap` | WRONG |
| `configUrl.getFile()` | `XmlFile.getFile` | `URL` | WRONG |
| `strategy.add(Jenkins.READ, "alice")` | `ListView.add` | `GlobalMatrixAuthorizationStrategy` | WRONG |
| `ls.createTrendChart(…).createChart()` | `LoadStatistics.createChart` | `TrendChart` | WRONG |
| `content = text.toString()` | `Run.toString` | `ByteArrayOutputStream` | WRONG |
| `channel.call(new SlaveLogFetcher())` | `SlaveComputer.SlaveLogFetcher.call` | `Channel` | WRONG |
| `while (!tmp.exists())` | `FilePath.exists` | `File` | WRONG |
| `w.flush()` | `AtomicFileWriter.flush` | `PrintWriter` | WRONG |
| `VirtualFile.forFilePath(…).child(s)` | `FilePath.child` | `VirtualFile` | WRONG |

**12 of 12 are false resolutions.** The pre-registration asked whether the guard
touches pre-existing edges; the question that decides anything is whether it
removes a CORRECT one, and on this sample it removes none. The guard is
therefore a net precision gain on the call graph beyond neutralising this PR:
262 pre-existing false edges on jenkins, plus the 85 arm S would have added.

## What this does not establish

* **No precision number.** No finding moved, so nothing was adjudicated for
  precision, and this record does not belong in the 0006 series.
* **The boundary arm is measured on a synthetic fixture, not in the wild.** The
  fixture reaches the shipped CLI end to end, which is what the closure-evidence
  discipline requires; it says nothing about how often that cell is reached in
  real repositories, and the two real repositories in this cohort never reach it.
* **cassandra contributes sizing only.** Its 4,708-edge / 53-name figure is a
  pre-change census, not a delta; no arm ran on it.
* **The 262 pre-existing removals are sampled, not censused.** 12 of 262 were
  read; all 12 were false resolutions. The other 250 are unread, and the claim
  made here is about the sample.
* **The guard's effect on the seven non-java languages that stamp
  `receiver_type_hint`** (python, js/ts, kotlin, scala, objc, swift, d) is
  measured only as "unmoved on sherpa-onnx's nine other languages" and by the
  10,605-test suite. A repository where one of those languages carries a
  contradicting hint would move, and none is in this cohort.
* **Measurement 0017's premise table is superseded on one row.** It records
  `System.getProperty(...)` with no import emitting
  `java:external:0-0:System.getProperty`; that spelling no longer exists. The
  table stands as a dated observation at the sha it names, and 0017's own
  conclusion is unaffected — the `java.lang` module slot is what fixed it, and
  since this change that slot is matched on the FIRST catalogue pass rather than
  through `strip_redundant_module_qualifier`'s retry.
