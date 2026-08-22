<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0004: Per-situation taint precision, and what the row denominator was hiding

**Status:** Complete
**Date:** 2026-08-22
**Instrument:** `hypergumbo verify-claims --format json` on the post-collapse
tree (dev `c14cbc2d7f`), adjudicated by hand against source
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `INV-karud` (the collapse whose effect this prices), `WI-sivuz`
(the pre-registered decision bands), `0001` (the row-level prior)

## The question

Measurement `0001` computed precision **per reported row** and got ≈41%
population-weighted, landing in `WI-sivuz`'s pre-registered `<50%` band.
`INV-karud` then established that a row is not a situation: on a six-repo
cohort, 359 reported flows described 78 `(source_symbol, claim)` situations —
**4.60×**, with 78% of rows restating a situation already reported.

Per-**situation** precision is a different quantity, it had never been
computed, and it is the one a reader acts on: *"is this function a problem?"*
rather than *"is row 7 of 32 a problem?"*. `INV-karud`'s own entry recorded
that **which way it moves is not predictable** — it depends on whether the
large groups adjudicate TP or FP, and `0001`'s 21 `co-location-function` false
positives are exactly the rows most likely to be grouped.

This answers that, on one population, at both units.

## Method limit, stated first because it is the weakest part

`0001`'s census used a primary pass plus a **blind second pass by a fresh
agent**, with a third agent on disagreement, and its pretix half replaced the
primary pass entirely with two independent blind agents — specifically to
control the bias it named: *"the census author wrote much of the code being
measured."*

**That control is not reproduced here.** This is a SINGLE-PASS, NON-BLIND
adjudication by the author of the change being measured. It is strictly weaker
than `0001`'s method, and the only mitigation available was fixed in advance:

> **Ties go to FALSE POSITIVE.** A single non-blind adjudicator who resolves
> doubt favourably produces an unfalsifiable number, so every genuinely
> arguable case is labelled FP. Both headline rates below are therefore
> **floors**.

Consequence for use: this is admissible as evidence about the **direction and
size of the row-versus-situation difference**, measured on one population at
two units where everything except the unit is held constant. It is **not** a
replacement for `0001`'s headline, and nothing here moves the `<50%` band.

## Population

`0001`'s own **census cohort**, so the comparison is against a measured number
rather than a remembered one: **caddy, mitmproxy, poetry, express,
apollo-server**. Every distinct violating record `verify-claims` reports for
the six generic claims. A census, not a sample.

`0001`'s pretix half is deliberately **not** re-run: it was 77% of that
measurement's population and one framework (Django ORM read → write) dominated
its true positives, so pooling it back in would re-import that concentration.

**The population has drifted since `0001`**, which that measurement itself
recorded (60 → 298 flows in nine days on its own cohort). So the row counts
here are NOT comparable to `0001`'s 60, and the row-level rate below is a fresh
measurement of the same question rather than a re-reading of `0001`'s.

| repo | rows (pre-collapse) | situations (post-collapse) | ratio |
|---|---:|---:|---:|
| caddy | 219 | 23 | 9.52× |
| mitmproxy | 60 | 26 | 2.31× |
| poetry | 13 | 5 | 2.60× |
| express | 3 | 3 | 1.00× |
| apollo-server | 2 | 2 | 1.00× |
| **total** | **297** | **59** | **5.03×** |

## Rubric

`0001`'s, verbatim — TRUE POSITIVE / FALSE POSITIVE / UNADJUDICABLE, including
its *control dependence only = FP* convention and its tie-break principle
*taint flows through in-program computation, not through an external resource
selected by the tainted value*.

**The situation reading, fixed before labelling.** A SITUATION is a TRUE
POSITIVE iff **at least one** `(source primitive, sink primitive)` pair inside
it satisfies the pair rubric. That is exactly what the collapsed record claims
— *"S reads {P…} and reaches zone Z via {Q…}"* is an existential over its pairs
— so it is the same rubric applied to the new claim, not a relaxation chosen
because it scores better.

## Headline

| unit | TP | population | precision |
|---|---:|---:|---:|
| **row** (source→sink pair, pre-collapse) | 33 | 297 | **11.1%** |
| **situation** (post-collapse) | 19 | 59 | **32.2%** |

**Per-situation precision is 2.9× per-row precision on this population.** The
two rates are computed from the same adjudication over the same repositories at
the same commit; only the unit differs.

**The direction the item could not predict is now measured: it moves UP**, and
the mechanism is that false positives concentrate in the largest groups. caddy's
`cmd/commandfuncs.go cmdRun` emits **76 rows across three claims, every one of
them false** (its `os.Getenv` reads are all compared to `""` and its
`runtime.GOOS` is a `switch` subject — control dependence, which this rubric
calls FP), and collapses to **3** situations. Meanwhile the true positives are
mostly small groups: 12 of the 19 TP situations stand for a single pair.

**Zero UNADJUDICABLE, against 19 of 104 in `0001`.** Every situation named a
file a reader could open, including the module-level ones
(`release/deploy-microsoft-store.py:1-1:file:file`,
`examples/cookies/index.js:1-1:file:file`). `0001`'s 19 unadjudicable rows were
all the collapsed `javascript:<external>:0-0:file:external_symbol` anchor.
**This is not offered as INV-rozob closure evidence** — these particular file
nodes are in-repo anchors that the collapse never touched, and a discriminating
before/after needs a repo whose flows anchor on a *tier-dropped* file. It is
recorded because the absence of that category is what made a census possible at
all.

## By repository

| repo | TP | situations | precision |
|---|---:|---:|---:|
| mitmproxy | 12 | 26 | 46.2% |
| caddy | 7 | 23 | 30.4% |
| poetry | 0 | 5 | 0.0% |
| express | 0 | 3 | 0.0% |
| apollo-server | 0 | 2 | 0.0% |

The three zero rows are one shape between them: `process.env.NODE_ENV !== 'test'`
guarding a `console.log` of a string literal (express ×3, apollo-server ×2), and
poetry's `os.environ.get("VIRTUAL_ENV") is not None`. Every one is
control-dependence-only.

## By analysis method

| `analysis_method` | TP | situations | precision |
|---|---:|---:|---:|
| `ddg` (walk confirmed a dependence) | 4 | 5 | **80.0%** |
| `structural` (no reaching-def data) | 10 | 30 | 33.3% |
| `ddg_mixed` (walk ran, did not confirm) | 5 | 24 | 20.8% |

`ddg` remains the most trustworthy label and remains **too small to filter on** —
5 of 59 situations, holding 4 of 19 true positives. Reporting only `ddg` would
discard 15 of 19 real findings, which is `0001`'s **"do not filter on
`confidence`"** conclusion reproduced at the situation unit.

`ddg_mixed` scoring *below* `structural` is the reversal `0001` warned about,
now visible in a second population: the label is not a calibrated confidence.

## By claim

| claim | TP | situations | precision |
|---|---:|---:|---:|
| `untrusted-input-no-subprocess` | 1 | 1 | 100.0% |
| `host-secret-no-host-fs` | 9 | 25 | 36.0% |
| `host-secret-no-network` | 3 | 9 | 33.3% |
| `host-secret-no-logging` | 5 | 17 | 29.4% |
| `untrusted-input-no-host-fs` | 1 | 7 | 14.3% |
| `untrusted-input-no-database` | — | 0 | — (no violated verdict on this cohort) |

## False-positive mechanisms, at the situation unit

| n | mechanism | what it is |
|---:|---|---|
| 17 | `control-dependence-only` | the source value reaches a branch condition guarding the sink but is not among its arguments. `runtime.GOOS == "windows"`, `os.Getenv("HOME") == ""`, `process.env.NODE_ENV !== 'test'`. **The single largest class**, and it is a stated rubric convention rather than a tool defect. |
| 11 | `reachability-only` | the sink is reached across the call graph while nothing on the route carries the value. caddy's `cmdRun` stdin bytes go to `conn.Write`, never to a file. |
| 6 | `co-location-function` | source and sink in one function, no value passed; frequently the sink call *precedes* the source read. |
| 3 | `content-not-argument` | the tainted value reaches the written *content* or a sibling call while the reported sink's argument is something else (`ZipFile(tempfile)` where the tainted path went to `f.write`). |
| 2 | `opened-path-not-content` | a path is opened and the *file's* bytes reach the sink. `0001`'s tie-break, and one of the two instances is literally the flow `0001` tie-broke, in the same script. |
| 1 | `mutually-exclusive-branch` | caddy's `cmdFmt` reads stdin and returns inside that branch; the `os.WriteFile` below it is unreachable from it. |

## The judgement calls, named rather than smoothed

Two cases turn on the same question — *does a path reach a log through an
exception object?* — and they were split, for a reason worth stating:

- **TP**, mitmproxy `keymap.py`: `raise KeyBindingError(f"Error reading {p}: {e}")`
  then `logging.error(e)`. The path is formatted into the message **in the
  repository's own source**, and both hops are citable lines.
- **FP**, mitmproxy `readfile.py`: `open(path)` raises `OSError`, and
  `logging.error(f"Cannot load flows: {e}")` logs it. The path enters `e`
  through the *runtime's* `OSError` construction, not through a source-visible
  step. Under *ties go to FP* this is FP — and a taint model that understood
  exception construction would call it TP.

The second is the one place where the floor is visibly a floor.

## What this does not support

- **It is not `0001` re-run.** Different population size, one repository fewer,
  and a weaker adjudication method. The 11.1% row rate is not comparable to
  `0001`'s 26.2% census rate.
- **It is precision, not accuracy.** It says nothing about recall.
- **Five repositories are not a random sample of software**, and one of them
  (caddy) supplies 219 of the 297 rows.
- **It does not move the band.** `WI-sivuz`'s bands were named before any
  interval was computed. This changes what the number *measures*, not the
  ranking rule — which is exactly what `INV-karud`'s entry said in advance.
- **`host_secret` is doing more work than its name admits.** 51 of the 59
  situations are `host-secret-*`, and the sources include `runtime.GOOS`,
  `platform.system`, `sys.argv` and `shutil.get_terminal_size`. Calling that a
  *secret* is `INV-tutar`'s open question, and it is the single biggest lever on
  this table that is not a precision fix at all.

## Related

- `0001` — the row-level prior this is measured against.
- `0003` — the recall-widening delta, 2.9% marginal precision.
- `INV-karud` — the collapse; this prices it.
- `INV-tutar` — the `env_read → host_secret` vocabulary question.
- `WI-sivuz` — the pre-registered decision bands.
