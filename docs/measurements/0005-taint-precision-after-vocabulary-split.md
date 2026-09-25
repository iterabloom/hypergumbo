<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0005: Taint precision after the fail-open fixes and the `env_read` split

**Status:** Complete
**Date:** 2026-08-25
**Instrument:** [`scripts/measure-taint-precision.py`](../../scripts/measure-taint-precision.py)
(extended for this run — see *Instrument changes*)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tree:** dev `ba01153095`
**Tracker:** `WI-sivuz` (the pre-registered method and decision bands), `WI-nazos`
(the carrier this campaign is trying to release), `INV-tutar` (the vocabulary
split this prices), `INV-karud` (the situation collapse)

## The question

Phase 1 of the taint-root campaign fixed six fail-open defects; phase 2 split
the `env_read` boundary into `env_read` (→ `host_secret`) and `host_info_read`
(→ `host_description`). What is taint precision now, at both units, and what
did those two phases actually do to the reported population?

Measurement `0001` put precision at ≈41% per row population-weighted, landing
in `WI-sivuz`'s pre-registered **`<50%`** band: *"precision is the headline
problem and recall work should stop until it is understood."* Recall work
(`INV-linub` and its members) is stopped until the owner re-ranks on a fresh
number. **This is that number. It does not move the band by itself** — the band
is a ranking rule the owner applies, and it was named before any interval was
computed precisely so a later interval could not argue it down.

## Headline

Census of every violating record the six generic claims produce on `0004`'s
cohort. Not a sample.

| unit | TP | FP | UNADJ | population | precision |
|---|---:|---:|---:|---:|---:|
| **situation** (post-collapse record) | 8 | 29 | 0 | 37 | **21.6%** |
| **row** (source→sink pair) | 17 | 153 | 0 | 170 | **10.0%** |

Per-situation precision is **2.16×** per-row precision, the third population in
a row where the two units differ by roughly that factor (`0004`: 2.9×). The two
units are not comparable to each other, and neither is comparable to `0001`'s
census rate.

Both figures are the ones that survived a blind second pass and an adversarial
third pass; **both passes changed them**, in opposite directions, and the
sequence is recorded under *Method*.

## The positive control, read before any number

Not one claim on any of the five repositories is reported **confirmed**.

| verdict | count |
|---|---:|
| `violated` | 13 |
| `inconclusive` | 22 |
| `confirmed` | **0** |

A precision measurement says nothing about the 22 inconclusive verdicts, and
this project's recurring failure mode is a zero that cannot be told apart from
"the analysis never ran". Every repository also reports its blind languages
(`ansible`, `yaml`, `markdown`, … per repo) via `INV-javam`'s disclosure.

## What changed the population — the finding that reframes the headline

`0004` reported **297 rows / 59 situations** on this cohort. This run reports
**170 rows / 37 situations**. The obvious reading — "phases 1 and 2 removed 43%
of the reported flows" — is wrong, and the instrument can say so exactly.

`host_secret` has three claims (`network`, `logging`, `host_fs`).
`host_description`, created by phase 2, deliberately has **one** (`network`) —
the claims file records the reason: *"Reading the OS name or the machine's
hostname is not a credential leak; SENDING it somewhere is a fingerprinting and
telemetry question."* So a host-description read that reaches the filesystem or
a log is no longer covered by any generic claim, where before the split the
same read fired all three `host-secret-*` claims.

Re-running the identical census with two **scope-probe** claims added
(`host-description-no-host-fs`, `host-description-no-logging` — probes, not
proposals) restores the population exactly:

| repo | `0004` rows | this run | this run + the two probe claims |
|---|---:|---:|---:|
| caddy | 219 | 128 | **219** |
| mitmproxy | 60 | 32 | **60** |
| poetry | 13 | 5 | **13** |
| express | 3 | 3 | **3** |
| apollo-server | 2 | 2 | **2** |
| **total** | **297** | **170** | **297** |

The 170 rows are a strict subset of the 297 by flow identity (0 rows present
before and absent now), and all 127 extras carry one of the two probe claim
ids. **On this cohort, at the row unit, phase 1's fail-open fixes removed
nothing and phase 2's re-partition removed nothing; the whole reduction is the
one-claim scope decision.** That is consistent with what phase 1 did — it
turned false *cleans* into unknowns and repaired vouching, generated-file and
disclosure predicates, none of which were firing here — but it is worth stating
plainly rather than letting a 43% population drop read as a precision fix.

## Instrument changes, and why each was needed

`0001` and `0003` predate `INV-karud`'s situation collapse, so the instrument
was still reporting one unit while calling it the other. Three changes:

1. **The collapse fields reach the flow record** (`collapsed_flow_count`,
   `source_primitives`, `sink_primitives`, `sink_symbols`). Without
   `collapsed_flow_count` the row denominator is not recoverable at all.
   Absent → 1, so `0001`/`0003` flow files still score correctly: there, one
   record *was* one pair.
2. **`score` reports both units, always, each labelled with its unit**, and
   **refuses** to print a row rate for a multi-pair `TP` that carries no
   `tp_pairs`. A situation is TRUE POSITIVE iff *at least one* of its pairs is,
   so a situation label cannot say how many are — assuming "all" inflates and
   assuming "one" deflates. The refusal is the same discipline `collect`
   already applies to a zero it cannot distinguish from a run that never
   happened.
3. **`collect --no-collapse`** disables the collapse for that process, so the
   row unit is **adjudicated on its own records** instead of apportioned from
   situation labels. `0004` had to hand-block that.

**Instrument control.** The two arms were run separately and reconcile
exactly: 37 situations whose `collapsed_flow_count` sums to 170, against 170
pair records; every situation's member set was recovered (37/37) and both
ledgers agree on TP=17, FP=153, 10.0%.

## Method — the controls, and what they changed

`0001`'s rubric verbatim, including its *control dependence only = FP*
convention and its tie-break *taint flows through in-program computation, not
through an external resource selected by the tainted value*. The rubric text as
given to every adjudicator is in the run directory (`blind/RUBRIC.md`).

`0004`'s weakest point was that it was a **single non-blind pass by the author
of the change being measured**, mitigated with a *ties go to FP* convention
that made both of its rates floors. `WI-sivuz` pre-registered the stronger
method: *"every label gets a second pass by an agent that did NOT produce it,
given the flow and the source but NOT my label… a third agent adjudicates
disagreements… disagreement RATE is published as a headline number alongside
precision."*

**That control was run here**, on the owner's explicit authorisation
(2026-08-25, recorded on `WI-sivuz`).

### 1. Blind second pass — 0/37 disagreements

Four independent adjudicators each took a disjoint chunk of the 37 situations,
given the packet and the repository but not the primary label, and forbidden
from reading either ledger, any prior measurement, or the ledger-building
scripts. **They reached the identical verdict on all 37**, including both
contested cases: one reconstructed caddy's stdin-config-to-autosave chain hop
for hop, including a hop the primary pass had inferred rather than read;
another independently found the shadowed `var false bool` that disables
`cmdRespond`'s autosave guard.

**The blindness itself is checked rather than asserted.** The packets are
generated mechanically from the flow records and the repository source, and
both passes consumed the same packets. Grepping all 37 for verdict vocabulary
(`TRUE POSITIVE` / `FALSE POSITIVE` / `UNADJUDICABLE` / `"label"` /
`tp_pairs`) returns nothing, no packet names a ledger or a prior measurement,
the rubric names no specific flow, and the chunk manifests were asserted to be
a partition of the 37 before the pass ran.

**A zero here is not evidence of correctness**, and `0001` said so in advance:
*"a low disagreement rate shows the RUBRIC is applied consistently. It is not
evidence the rubric is right — both passes share it."* The next two passes
exist because of that sentence, and each of them moved the number.

### 2. The contested label — the primary pass was wrong, and the row rate rose

Five of the ten pairs under `caddy:untrusted-input-no-host-fs:2` end in
`FileWriter.OpenWriter` / `mkdirAllInherit` / `mkdirAllFromFile` /
`acmeserver.openDatabase`, where the sink's path argument is a **config field**
reached from stdin through `json.Unmarshal` into a module caddy selects at run
time. The primary pass called them **UNADJUDICABLE** on `0001`'s clause *"the
hop is real dynamic dispatch or reflection"*.

A separately commissioned adjudication, asked to argue both sides, **corrected
that to TRUE POSITIVE**: the clause reads *"the path cannot be followed from
source alone: … or the hop is real dynamic dispatch or reflection"*, which
makes reflection an *instance* of unfollowability rather than an independent
trigger — and this path is followable from in-repo declarations
(`filewriter.go:35` `RegisterModule(FileWriter{})`, `:155` the module ID
`caddy.logging.writers.file`, `:83` the `json:"filename"` tag, `logging.go:301`
`WriterRaw`'s `inline_key=output` tag, `logging.go:344`
`ctx.LoadModule(cl, "WriterRaw")`, `filewriter.go:167` `fw.Filename = filename`,
and `filewriter.go:239` `os.Chmod(fw.Filename, configuredMode)` taking the
field itself). Every citation was re-read and confirmed before the label was
changed. **Row rate 7.9% → 10.6%; the situation label was unaffected.**

It accepted the consequence in terms, and so does this measurement: *under a
rubric that defines TP purely as data flow into a sink argument and rules
exploitability out of scope, every filesystem, network and subprocess operation
whose argument comes from a configured value is a true positive from the config
source, for any config-driven server.* For caddy that is not absurd — its
config **is** a program for the filesystem. The finding is **uninformative, not
false**, and uninformativeness is an actionability axis this rubric refuses to
admit. Filed on its own terms rather than patched here, because inventing a
fourth category at adjudication time would be using UNADJUDICABLE as the
tie-breaker the rubric says it is not.

### 3. Adversarial pass — 1 of 9 true positives refuted, and the row rate fell

An agent charged with **refuting** the nine surviving true positives broke one.

`mitmproxy:host-secret-no-host-fs:4` — `f = Fernet(os.environ["CI_BUILD_KEY"].encode())`
at `release/build.py:305`, then `outfile.write(f.decrypt(infile.read()))` at
`:310`. The primary pass called it TP on `0001`'s pretix precedent (a value
passed into an object whose output reaches the sink). **The refutation is
better and is accepted:** the written bytes originate at `infile.read()` — an
external resource — and `Fernet.decrypt` *authenticates before decrypting*, so
a wrong key raises `InvalidToken` rather than producing different output. The
key does not vary the written bytes at all; it gates whether the write happens,
which is control dependence. The pretix analogy fails because there the field
values were computed *into* the bytes. The refuter drew the line precisely by
contrast with a flow it did **not** refute: `url.Values.Encode` is also a
library hop, but its output literally contains the source strings.

**Situation rate 24.3% → 21.6%; row rate 10.6% → 10.0%.**

The other eight survived attacks on reachability (`printEnvironment` is reached
from `caddy run --environ` and `caddy environ`; `upgradeBuild` from
`cmdRemovePackage`), on wrong-variable substitution, on guard conditions, and
on the tie-break.

**Contamination disclosed, because the refuter disclosed it.** While checking
what the packets' `collapsed pairs` field counts, it grepped the run directory
and landed in `make_pair_ledger.py`, whose rule table embeds primary rationales
for six of the nine. It stopped immediately and states all nine verdicts
predate that read. **The exclusion list given to the four blind chunks named
that file; the one given to the refuter did not — that is an error in the
instructions, not in the agent.** It matters less than it would for a blind
pass, since the refuter was told the nine TP labels in its own prompt, so what
leaked is rationale rather than verdict. Recorded rather than quietly fixed.

## Population

`0004`'s cohort, so the comparison is against a measured number rather than a
remembered one: **caddy, mitmproxy, poetry, express, apollo-server**. Every
distinct violating record the six generic claims produce. A census.

| repo | situations | rows | ratio |
|---|---:|---:|---:|
| caddy | 16 | 128 | 8.00× |
| mitmproxy | 14 | 32 | 2.29× |
| express | 3 | 3 | 1.00× |
| poetry | 2 | 5 | 2.50× |
| apollo-server | 2 | 2 | 1.00× |
| **total** | **37** | **170** | **4.59×** |

`0001`'s pretix half is deliberately not re-run, for `0004`'s reason: it was
77% of that measurement's population and one framework dominated its true
positives.

## By repository

| repo | TP sit | sit | prec/sit | TP rows | rows | prec/row |
|---|---:|---:|---:|---:|---:|---:|
| mitmproxy | 4 | 14 | 28.6% | 7 | 32 | 21.9% |
| caddy | 4 | 16 | 25.0% | 10 | 128 | 7.8% |
| apollo-server | 0 | 2 | 0.0% | 0 | 2 | 0.0% |
| express | 0 | 3 | 0.0% | 0 | 3 | 0.0% |
| poetry | 0 | 2 | 0.0% | 0 | 5 | 0.0% |

caddy is 43% of the situations and **75% of the rows**. Any pooled row number
on this cohort is mostly a statement about caddy.

## By claim

| claim | TP sit | sit | prec/sit | TP rows | rows | prec/row |
|---|---:|---:|---:|---:|---:|---:|
| `untrusted-input-no-subprocess` | 1 | 1 | 100.0% | 1 | 1 | 100.0% |
| `host-secret-no-network` | 2 | 4 | 50.0% | 5 | 18 | 27.8% |
| `host-secret-no-logging` | 2 | 9 | 22.2% | 2 | 28 | 7.1% |
| `untrusted-input-no-host-fs` | 1 | 7 | 14.3% | 6 | 53 | 11.3% |
| `host-description-no-network` | 1 | 7 | 14.3% | 2 | 38 | 5.3% |
| `host-secret-no-host-fs` | 1 | 9 | 11.1% | 1 | 32 | 3.1% |
| `untrusted-input-no-database` | — | 0 | — | — | 0 | — |

## By source family — the question phase 2 was supposed to answer

Compared **on its new membership**, which is the only honest comparison: the
`host_secret` family no longer contains host-description reads.

| family | TP sit | sit | prec/sit | TP rows | rows | prec/row |
|---|---:|---:|---:|---:|---:|---:|
| `untrusted_input` (`net_recv`/`ipc_recv`) | 2 | 8 | 25.0% | 7 | 54 | 13.0% |
| `host_secret` (`env_read`) | 5 | 22 | 22.7% | 8 | 78 | 10.3% |
| `host_description` (`host_info_read`) | 1 | 7 | 14.3% | 2 | 38 | 5.3% |

**Phase 2 did not raise `host_secret` precision, and the reason is worth
stating.** `0004` measured the then-undivided `host-secret-*` family at 17 TP
of 51 situations = 33.3%; the same family, on its post-split membership, is now
5 of 22 = 22.7%. The split moved out 29 situations — but it moved out a *true
positive* with them: caddy's `upgradeBuild`, which sends `runtime.GOOS` and
`runtime.GOARCH` to a remote build service, was a `host-secret-*` true positive
in `0004` and is now the single true positive of `host-description-no-network`.
Removing a family's false positives raises its rate; removing its true
positives lowers it; this did both.

What phase 2 *did* buy is not on this table: `host-secret-no-network` fires
only on credentials now, and the fingerprinting question has a claim of its own
with an answer of its own. That is a change in what the claims **mean**, which
`INV-tutar` argued for on its own terms, and it should not be defended with a
precision number it did not produce.

## By analysis method

| `analysis_method` | TP sit | sit | prec/sit | TP rows | rows | prec/row |
|---|---:|---:|---:|---:|---:|---:|
| `ddg` (walk confirmed a dependence) | 1 | 3 | 33.3% | 1 | 3 | 33.3% |
| `structural` (no reaching-def data) | 4 | 17 | 23.5% | 7 | 38 | 18.4% |
| `ddg_mixed` (walk ran, did not confirm) | 3 | 17 | 17.6% | 9 | 129 | 7.0% |

* **`ddg` is no longer clearly the best label on this population.** It was
  80.0% in `0004` and 66.7% here until the adversarial pass refuted one of its
  three situations; at n=3 the difference from `structural` is not a difference
  anyone should act on. What survives from `0004` is the *shape*: `ddg` is
  far too small to filter on. Reporting only `ddg` keeps 3 flows and
  **discards 7 of the 8 true positives** — `0001`'s *do not filter on
  confidence* conclusion, reproduced at both units on a third population.
* **`ddg_mixed` scores below `structural` again**, now in a third population
  and at both units, and at the row unit it is worse by a factor of 2.6. A walk
  that ran and did not confirm is evidence of nothing; the label is not a
  calibrated confidence. It also carries 129 of the 170 rows.

## False-positive mechanisms, at the situation unit

| n | mechanism | what it is |
|---:|---|---|
| 12 | `reachability-only` | the sink is reached across the call graph while nothing on the route carries the value. **The largest class, and new to the top of this table** — `0004` had 11 of these behind 17 control-dependence cases. Caddy supplies 9 of the 12. |
| 8 | `co-location-function` | source and sink in one function or file, no value passed; frequently the sink call *precedes* the source read. |
| 4 | `control-dependence-only` | the source value reaches a branch condition guarding the sink but is not among its arguments. A stated rubric convention, not a tool defect. |
| 2 | `mutually-exclusive-branch` | the source read and the sink call are on branches that cannot both run (`cmdFmt`'s stdin arm returns before the write; `listen`'s ip-network arm cannot reach the unix-socket `chmod`). |
| 1 | `route-disables-sink` | **new mechanism, not in `0004`'s table.** `cmdRespond` really does carry stdin into `cfgJSON` — and the same config sets `Admin.Config.Persist` false, which is the guard on the autosave write. The route that carries the value is the route that turns the sink off. |
| 1 | `content-not-argument` | the tainted value reaches a sibling call while the reported sink's argument is something else (`ZipFile(tempfile)` where the path went to `f.write`). |
| 1 | `opened-path-not-content` | a path is opened and the *file's* bytes reach the sink. `0001`'s tie-break. |

**`reachability-only` overtaking control-dependence is the structural finding
of this measurement.** Caddy's call graph is fully connected through
`caddy.Load → changeConfig → unsyncedDecodeAndRun`, so the *same* ten or eleven
terminal I/O calls — `FileWriter.OpenWriter`, `InstanceID`, `exitProcess`,
`acmeserver.openDatabase`, `httploader`, `reverseproxy` — appear under four
different sources in four different packages. That is `INV-karud`'s route
multiplier again, but at the *sink-set* level rather than the route level: one
source read buys a fixed tax of a dozen rows regardless of what it is.

## Producer defects found in passing

Not adjudication questions — things the adjudicators had to work around, each
reproducible from the record.

* **A prefix primitive and its extension are counted as two source
  primitives for one read.** `os.environ` and `os.environ.get` both appear in
  the `source_primitives` of seven situations. In five of them the bare
  `os.environ[...]` form **does not occur anywhere in the file** (verified by
  grep on `release/release.py`, `mitmproxy/log.py`, `docs/scripts/api-render.py`,
  `tools/console/master.py`, poetry's `env_manager.py`) — one read counted
  twice by substring matching. **9 of the 170 rows are that artefact**, and one
  of them is inside a true positive, so it inflates the denominator by 9 and
  the numerator by 1. In the remaining two situations
  (`release/deploy-microsoft-store.py`) both forms genuinely occur.
* **A sink primitive can name something that is not a call.**
  `mitmproxy:host-secret-no-logging:1` declares `sys.stderr` as its sink, but
  at `tools/main.py:107` `sys.stderr` is the value of a `file=` keyword; the
  call is `print`, which is not among the declared sinks. The rubric's test
  ("argument to the sink CALL, or the RECEIVER of the sink call") is literally
  unsatisfiable against the named primitive. Every reader treats
  `print(file=sys.stderr)` as the stderr write, so this did not change a label
  — but the record cannot be checked against its own claim.
* **A route can be fictitious in a diagnosable way.** The tool's claimed hop
  `StoragePool.Provision → caddy.Load` is a **name collision**:
  `modules/caddytls/capools.go:358` calls `ca.storage.Load(ctx, caID)`, an
  interface method on `certmagic.Storage`, not the package function
  `caddy.Load(cfgJSON []byte, bool)`. Under the rubric that hop is real dynamic
  dispatch, so the tool's *own* route would be UNADJUDICABLE; the true-positive
  label is rescued only by the rubric's "fictitious route, real chain" clause,
  and the real chain runs *up* out of the source symbol through `cmdRun` — a
  direction the tool never claimed. The same fictitious `FileSystemMap.Get →
  caddy.Load` hop appears under four separate situations.

## The judgement calls, named rather than smoothed

**`caddy` `cmdRespond` — the route that disables its own sink.** The stdin body
genuinely reaches `cfgJSON` (`io.ReadAll(os.Stdin)` → `body` → the
static-response handler → `cfg.AppsRaw` → `caddy.Run(cfg)` → `json.Marshal` →
`Load` → `changeConfig` → `unsyncedDecodeAndRun`), and the *same* `cfg` sets
`Admin.Config.Persist` to a pointer at the shadowed `var false bool`, which is
the guard on the autosave write at `caddy.go:379-384`. **FALSE POSITIVE**, and
a mechanism `0004` had no name for. It is the strongest kind of false positive
to have found: the data flow is real and the sink is unreachable anyway.

**`mitmproxy` `io-write-flow-file.py` — a true positive whose claim is a poor
fit.** `os.getenv("MITMPROXY_OUTFILE")` → `open(filename, "wb")` is as clean as
a flow gets, and the adversarial pass could not dent it. Its own complaint is
worth recording: `MITMPROXY_OUTFILE` is an output-path knob, not a credential,
so `host-secret-no-host-fs` is the wrong question to have asked about it — the
same vocabulary problem `INV-tutar` fixed one level up, one level lower down.

## What this does not support

- **It is precision, not accuracy.** It says nothing about recall. A repo with
  one reported flow and forty real ones scores 100% here.
- **It is not `0001` re-run**, and the row rate is not comparable to `0001`'s
  26.2% census rate, `0001`'s ≈41% population-weighted figure, or `0004`'s
  11.1% — different populations, different claim sets, different adjudications.
- **Five repositories are not a random sample of software**, and caddy alone
  supplies 128 of the 170 rows. The per-row headline is largely a statement
  about one Go server whose call graph is unusually connected.
- **It does not move the band.** `WI-sivuz`'s bands were named before any
  interval was computed, and this measurement is input to the owner's re-rank,
  not a substitute for it. Recall work (`INV-linub` and its members) stays
  stopped until that re-rank.
- **Zero disagreement is not zero error, and this run is the proof.** The blind
  pass agreed on all 37 — and the two passes that followed it changed the
  headline twice, in opposite directions. `0001`'s caveat is not a formality.

## Related

- `0001` — the row-level prior and the rubric.
- `0003` — the recall-widening delta, 2.9% marginal precision.
- `0004` — the per-situation prior and this run's population baseline.
- `INV-tutar` — the vocabulary split this prices.
- `INV-karud` — the collapse whose two units this reports separately.
- `WI-sivuz` — the pre-registered method and the decision bands.
- `WI-nazos` — the carrier holding `INV-zogud`; the campaign this belongs to.
