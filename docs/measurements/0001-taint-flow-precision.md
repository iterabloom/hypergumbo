<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0001: Taint-flow precision on real repositories

**Status:** Complete
**Date:** 2026-08-11
**Instrument:** [`scripts/measure-taint-precision.py`](../../scripts/measure-taint-precision.py)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `WI-sivuz` (pre-registration), `WI-ruhol` (the prior datapoint)

## The question

Of the taint flows `hypergumbo verify-claims` reports as violations, how many
are real? "Real" meaning: a reader who opens the file can follow the value from
the source primitive to the sink call.

It matters because a violated verdict is what a user acts on, and because every
honesty gate added to this tool makes more claims `inconclusive` — so the
pressure to improve **recall** is constant, and recall work lands into whatever
precision the reporting layer already has.

## The prior datapoint, and why it could not be reused

`WI-ruhol` closed 2026-03-25 at **50% precision (3 of 6)** against a synthetic
reference implementation, before any of ADR-0017's nine phases shipped. It
landed exactly on its own decision boundary, on code written to be analysed. It
cannot be compared to a post-phase number, and re-running it would reproduce its
weakness rather than answer the question.

## Headline

| population | flows | adjudicated | TP | FP | precision | unadjudicable |
|---|---:|---:|---:|---:|---:|---:|
| **Census** — caddy, mitmproxy, poetry, express, apollo-server | 60 (all) | 42 | 11 | 31 | **26.2%** | 18 |
| **Sample** — pretix, stratified 44 of 205 | 205 | 43 | 17 | 26 | **39.5%** | 1 |
| combined | 265 | 85 | 28 | 57 | 32.9% | 19 |

Population-weighted across both (pretix's strata re-weighted to their share of
its 205 flows): **≈41%**.

Per `WI-sivuz`'s pre-registered decision bands — the band was named before any
interval was computed, so the interval could not be used to move it — this lands
in the **`<50%`** band: *"precision is the headline problem and recall work
should stop until it is understood."* The mechanisms below are that
understanding.

**Where the uncertainty actually is.** The census has *no sampling error*: it is
every flow those five repositories produce. The pretix figure is a sample, and
its 95% interval is **25.1%–54.0%** — it alone cannot exclude 50%. What no
reading of the data supports is a number near 80%, the threshold at which the
pre-registration said the removal-authority question (`WI-kabif`) would become
arguable on its merits.

## Population

**A census where it was feasible and a stratified sample where it was not.**
Every distinct violating flow that `verify-claims` reports for the six generic
claims. `_MAX_EVIDENCE_ROWS` (100) head-truncates the displayed evidence list;
the instrument raises that ceiling for its own in-process run so the frame is
the whole population rather than its first hundred rows in propagation order. No
repository approached the raised ceiling, and on every claim the raw flow count
equalled the distinct count, so nothing was lost to deduplication either.

| repo | language(s) | flows | violated claims |
|---|---|---:|---:|
| caddy | Go | 18 | 5 of 6 |
| mitmproxy | Python, JavaScript, C | 34 | 3 of 6 |
| poetry | Python | 4 | 1 of 6 |
| express | JavaScript | 3 | 1 of 6 |
| apollo-server | JavaScript/TypeScript | 1 | 1 of 6 |
| pretix | Python, JavaScript | 205 | **6 of 6** |

pretix is 77% of the population by itself and is the only repository in which
every claim fires, which is why it gets its own row in every table below rather
than being pooled away.

### The pre-registered population no longer existed

`WI-sivuz` fixed its frame in advance as "the 1,890 flows on hypergumbo's two
violated self-claims", parked behind a trigger on the `WI-vanun` thread. Both
claims were subsequently repaired — `runtime-cli-no-host-fs` by wrapping eight
unsanitized writes, `runtime-cli-no-subprocess` by declaring the
`repo_inspection` zone — and a repaired claim reports **zero** violating flows,
because `verify_claims` never downgrades a `violated` verdict, only a would-be
`confirmed` one. Verified live rather than assumed: a `verify-claims` run
against hypergumbo on this commit returns 18 verdicts, all `inconclusive`, none
violated. The frame is empty by construction and the park is moot.

**Recorded revisions**, per the item's own rule that revisions be recorded with
their reason:

1. **Population** moved from hypergumbo's self-claims to six external
   repositories, because the pre-registered one no longer exists.
2. **Design** moved from a single n=60 stratified sample to census-plus-sample,
   because five of the six repositories produce 60 flows in total and a census
   has no sampling error to argue about.
3. **Sample size** for pretix was set at target n=30 with a per-cell floor of 3,
   drawing 44. The pre-registered floor of 10 across pretix's 10 strata would
   have drawn 120+, which is not adjudicable at the care this rubric demands.
   The reduction was fixed before any pretix flow was labelled; the per-stratum
   counts were already visible from the collection run.
4. **Who adjudicates.** The census used the pre-registered method: primary pass
   by me, blind second pass by a fresh agent, third agent on disagreement. For
   pretix the primary pass was replaced by a **second independent blind agent**,
   with me as tie-breaker only. That reduces the one bias the pre-registration
   named and could not control — *"I wrote much of the code being measured"* —
   and it is the only change here that makes the method stronger rather than
   just smaller.

## Rubric

Fixed before labelling, given to every verifier in the same words.

> **TRUE POSITIVE** — The value returned by the source primitive, or a value
> derived from it by DATA FLOW (assignment, concatenation, attribute/index
> access, passed as an argument, comprehension binding, loop variable), is
> itself an ARGUMENT to the sink call, or the RECEIVER of the sink call. You
> must be able to cite the lines that carry it.
>
> **FALSE POSITIVE** — Any link in that chain does not exist in the source. This
> includes source and sink merely co-located in the same function or file, or
> reachable through the call graph, with no value passed. It also includes
> *control dependence only*: the source value reaches a branch condition
> deciding whether the sink runs, but is not among the sink's arguments.
>
> **UNADJUDICABLE** — The path cannot be followed from source alone: the tool
> records no location to read, or the hop is real dynamic dispatch or
> reflection. A third label, not a tie-breaker. Its count is printed beside
> precision and never folded into it.

**Exploitability is not the question.** `exec.Command` receiving a
locally-bound listener address is a TRUE POSITIVE if the value genuinely reaches
the argument.

### The one line the rubric did not draw in advance, drawn by the tie-breaks

Both disagreements in this measurement were the same question — *does taint flow
through a mutating call?* — and answering them consistently required a principle
the rubric did not state:

> **Taint flows through in-program computation, not through an external
> resource selected by the tainted value.**

- `mitmproxy` — an argv-derived *filename* is opened and the file's bytes are
  zipped into a request body. **FALSE POSITIVE**: the bytes come from the
  filesystem. Treating this as taint would make every `open(argv_path).read()`
  argv-derived, which is unbounded.
- `pretix` — an ORM-read `OrderPosition` is rendered onto a reportlab canvas
  whose backing store is `buffer`, and `buffer.read()` is the argument to
  `f.write()`. **TRUE POSITIVE**: the position's own field values are computed
  into the written bytes.

**One rubric extension, made before labelling:** the *control dependence only*
clause. It is a choice, not a discovery — a taint model that tracked control
dependence would call those flows real. Seven of the 57 false positives turn on
it, so the combined headline moves from 32.9% to 41.2% under the opposite
convention. That sensitivity is disclosed rather than argued away.

## By analysis method — and why it does NOT give you a filter

This was the pre-registered stratification axis, on the expectation that it
separates behaviour. It does — in *opposite directions* on the two populations.

| `analysis_method` | census n | census precision | pretix n | pretix precision |
|---|---:|---:|---:|---:|
| `ddg` (walk confirmed a data dependence) | 4 | **100.0%** | 3 | **100.0%** |
| `ddg_mixed` (walk ran, did not confirm) | 17 | **11.8%** | 19 | **57.9%** |
| `structural` (no reaching-def data) | 39 | 23.8% | 22 | **14.3%** |

`ddg_mixed` is five times more trustworthy on pretix than on the census cohort.
Whatever the label means, it does not mean the same thing across repositories,
and a consumer who calibrated a threshold on one would be badly wrong on the
other.

**And the obvious remedy is refuted outright.** Reporting only `precise`/`ddg`
flows yields 100% precision on both populations — and keeps **7 of 85**
adjudicated flows while losing **21 of the 28 true positives (75%)**:

- `caddy` — `for _, v := range os.Environ() { fmt.Println(v) }`
  (`cmd/main.go:470-471`), a genuine whole-environment dump, and one of the
  three real leaks the earlier removal-authority trial found;
- `mitmproxy` — `filename = os.getenv("MITMPROXY_OUTFILE", "out.mitm");
  self.f = open(filename, "wb")`, adjacent lines, a direct argument;
- `pretix` — eleven Django ORM read → invoice/mail/log write chains, several
  running four to six hops through `generate_invoice` and `OutgoingMail`.

Every one is a data flow a reader can follow. **Do not filter on `confidence`.**

## By claim — the security questions score very differently

| claim | n | TP | FP | precision |
|---|---:|---:|---:|---:|
| `untrusted-input-no-database` | 23 | 14 | 9 | **60.9%** |
| `host-secret-no-network` | 10 | 4 | 6 | 40.0% |
| `untrusted-input-no-host-fs` | 7 | 2 | 5 | 28.6% |
| `host-secret-no-host-fs` | 20 | 4 | 16 | 20.0% |
| `host-secret-no-logging` | 37 | 3 | 15 | 16.7% |
| `untrusted-input-no-subprocess` | 7 | 1 | 6 | 14.3% |

The best-performing claim is the ORM round trip, where Django's read→write
chains are long, explicit and genuinely data-carrying. The worst is **command
injection**, and its failure is systematic rather than random: all six pretix
false positives are `subprocess.run(pdftk_cmd)` where `pdftk_cmd` is
`[settings.PDFTK, *tempdir_paths, 'cat', 'output']`. The tainted rows reach the
PDF that pdftk *reads*; they never reach its argv. The tool is correct to model
the argument rather than the effect, and correct to report zero real
command-injection flows here — but a user asking "can user input reach a shell?"
gets six answers, none of them yes.

## Claim-level correctness is roughly twice flow-level

A verdict is a disjunction, so a claim is correctly `violated` if **any** one of
its flows is real. **9 of 17 violated verdicts (52.9%)** rest on at least one
true flow: 6 of 11 on the census, 3 of 6 on pretix. The headline a user reads
first is about twice as trustworthy as any individual evidence row they drill
into.

Caddy's `printEnvironment` is the clean illustration: correctly `violated`, with
the flow row naming the wrong `os.Getenv` read.

## False-positive mechanisms

| n | mechanism | what it is |
|---:|---|---|
| 21 | `co-location-function` | source and sink in the same function, no value passed. Frequently the sink call *precedes* the source read in the body — flow-insensitivity, not just imprecision. |
| 8 | `content-not-argument` | the tainted value reaches the file's *contents* or the rendered bytes, while the sink's argument is a generated temp path. All six pretix `no-subprocess` false positives are this. |
| 8 | `reachability-only` | the sink is reached across the call graph while the first hop takes **no arguments at all** (`caddy.TrapSignals()`, `get_all_payment_providers()`, `_get_preview_position()`), so nothing could have crossed. |
| 7 | `co-location-file` | the source symbol is a FILE node (span `1-1`, kind `file`); the analysis established nothing finer than "both appear in this file". Vendored bundles (`vue.js`, `cropper.js`) dominate. |
| 7 | `control-dependence-only` | the source value reaches a branch condition guarding the sink but is not among its arguments. FP by this rubric's stated convention. |
| 2 | `misattributed-source-read` | a real source→sink pair exists in the callee, but the reported source is a *different* read in the caller. |
| 2 | `unrelated-files` | source and sink are in two files with no call relationship at all — a vendored `cropper.js` and a Vue component that never references it. |
| 1 | `short-name-collision` | `Checkin.objects.create(...)` resolved to `QuestionSerializer.create`, producing a call edge that does not exist. |
| 1 | `opened-path-not-content` | an argv-derived *filename* was opened and the file's bytes reached the sink. |

And the 19 unadjudicable flows have a single mechanism:

**`unanchored-source-placeholder`** — the source symbol is
`javascript:<external>:0-0:file:external_symbol`. No file, no span, nothing to
read. On the census, 18 such rows are the cartesian product of six catalogued
browser-global sources (`userAgent`, `document`, `screen`, `cookie`, `location`,
`platform`) against three `console` sinks (`warn`, `error`, `log`), all
collapsed into one placeholder bucket. **One unanchored bucket generates
*sources × sinks* distinct "violations"**, and the count scales with the
catalogue rather than with the code. On the census the split is exact: every
unadjudicable flow is an unanchored one, every anchored flow was adjudicated.

## Independent verification

- **Census (60 flows):** primary pass, then a blind second pass by a fresh agent
  given the flow and the source but not the first label; the one disagreement
  went to a third agent that saw both arguments unattributed and ruled against
  the first pass.
- **pretix (44 flows):** two independent blind passes, neither seeing the
  other's labels; the one disagreement was adjudicated by reading the source.

**Disagreement rate: 2 of 104 (1.9%).** Both disagreements were the
mutating-call question resolved above, and in the census case the first pass had
already flagged that flow in its own rationale as the least obvious call in the
ledger.

**What a 1.9% disagreement rate does and does not establish.** It shows the
rubric is unambiguous enough that independent readers apply it identically —
including its reasoning, not just its verdicts: on pretix the two passes
converged on the same mechanism, the same line numbers and often the same
six-hop chain. It does **not** show the labels are correct, because every pass
shares the rubric, and the rubric's control-dependence convention moves the
headline by 8.3 percentage points on its own.

## What this does not support

- **This is precision, not accuracy.** It says nothing about recall. A
  repository with one reported flow and forty real ones scores 100% here.
- **Six repositories are not a random sample of software.** The census is exact
  within its five; the pretix estimate has a ±14pp interval; generalising either
  to other repositories is an extrapolation, not an inference.
- **One framework dominates.** 77% of the population and 14 of the 28 true
  positives are pretix, and most of those are one shape — a Django ORM read
  reaching `InvoiceLine.objects.create` or `OutgoingMail.objects.create`. A
  cohort without a Django application would score lower.
- **The census author wrote much of the code being measured.** The pretix half
  removes that bias; the census half mitigates it with a blind pass and a
  tie-breaker, which is not the same thing.

## Consequences worth acting on

1. **Recall work lands into a ~41%-precision reporting layer.** Fixes that make
   more sinks matchable (`INV-suril`, `INV-gijis`) will add flows at roughly
   this precision unless the flows they add are DDG-confirmed. An argument for
   sequencing, not for abandoning them.
2. **Anchoring is the cheapest large win.** 19 flows cannot be read at all, and
   the defect is one collapsed placeholder rather than a deep analysis limit.
3. **Flow-insensitivity is visible and unexploited.** Several false positives
   have the sink call *textually before* the source read in the same function.
   A statement-order check inside one function body would remove them without
   touching the DDG — but it is a heuristic, not a proof (loops, closures and
   repeated invocation all break it), so it needs measuring in both directions.
4. **`env_read → host_secret` is doing more work than the name admits.** The Go
   catalogue's `env_read` includes `os.Getwd`, `os.Executable`, `os.Hostname`
   and `runtime.GOOS`; the JavaScript one includes `navigator.platform` and
   `window.screen`. Calling all of that a *secret* is why `host-secret-*` claims
   carry 48 of the 85 adjudicated flows at 22.9% precision between them. A
   vocabulary question (`INV-vaduk` shape 4 asks the sibling question about
   `logging`), not a per-row edit.
5. **`analysis_method` is not a calibrated confidence.** It reverses between
   populations. Publishing it is right; treating it as a filter threshold is
   not, and nothing in the tool currently says so.

## Related

- `WI-sivuz` — the pre-registration this measurement executes and revises.
- `WI-ruhol` — the 50% (3/6) synthetic prior.
- `INV-sadah` — the `analysis_method` labelling invariant whose axis this
  stratifies on.
- `INV-vaduk` — dual-classified catalogue rows, including the
  `fs_write`/`logging` overlap that puts C `fprintf` into `host_fs`.
- [ADR-0017](../adr/0017-taint-zone-dataflow.md) — the taint-flow design.
