<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0006 — Taint-flow precision under the ratified frame

**Frame: 0006. Series position: N=1.** This measurement does **not** continue
the 0001/0004/0005 series and must not be plotted against it. INV-duvup
established that the earlier series cannot show whether the tool is improving:
pooling alone swung 0005's row rate between 5.9% and 16.7%, the estimator
changed silently between measurements (0001 population-weighted, 0005 a raw
census), and 0001/0004/0005 each disclaim comparability with the one before.
The owner ruled "fix the measurement frame first"; this is the first
measurement under the fixed frame, and the series restarts here.

Frame definition: `frame_08252026/FRAME-PROPOSAL.md`. Unit = **situation**
(canonical), row printed beside it. M=7 repositories x R=16 situations = 112
adjudicated. 0005's five repositories excluded. Rubric = measurement 0001's,
**cited verbatim, not rewritten** (F6). Seeded draw, seed 20260825. Language
cap one third.

## Headline

| | situation (canonical) | row (beside) |
|---|---|---|
| n | 112 | 336 |
| TP / FP | 38 / 74 | 106 / 230 |
| **precision** | **33.9%** | **31.5%** |
| UNADJUDICABLE | 0 | 0 |

Including the one contested refutation (see below): 37/112 = 33.0%.

Cohort: 16 repositories, 10 languages — cert-manager, cilium, jaeger, beads,
gocryptfs (go); shellcheck (haskell); mobx, session-desktop (typescript);
spacedrive (rust); rabbitmq (erlang); ArkLib (python); plausible (elixir);
tmate, tmux (c); guacamole-client (java); kamaraflow (javascript).

## The frame did what it was built to do

Row concentration by single largest repository:

| cohort | largest contributor | share |
|---|---|---|
| 0001 | pretix | 77% |
| 0005 | caddy | 75% |
| (earlier census) | rabbitmq | 50% |
| **0006** | **gocryptfs** | **14.6%** |

No repository dominates. Per-repository precision ranges from 0% (beads, mobx)
to 85.7% (ArkLib) — a spread that was previously invisible because one
repository drowned it.

## Protocol and what each pass moved

F7's four passes were run. **Both labelling passes were independent agent
panels** (16 agents each, one repository per agent); the orchestrator held no
labelling pass. That is a deviation from 0005, where pass 1 was the
orchestrator's own ledger, and the reason is recorded in full in
`PANEL-DESIGN-NOTE.md`: panel A was launched before the orchestrator's pass was
written, and every agent returns its labels into the orchestrator's context, so
any orchestrator pass afterwards would not have been independent.

| pass | what it did | headline after |
|---|---|---|
| 1 + 2 | two independent blind panels, 112/112 each | A 37.5%, B 39.3% |
| 3 | adjudication of the 6 disagreements (94.6% agreement) | 39.3% |
| 4 | adversarial refutation of all 44 TPs | **33.9%** |

Pass 4 refuted **7 of 44 (15.9%)**. 0005's refutation pass moved 1 of 9. The
protocol continues to earn its cost: a cheaper protocol would have shipped
39.3%.

**Panel B's standalone total (44) coincides with pass 3's total (44) by a
different assignment** — B called 4 of the 6 disagreements the other way. Equal
totals here are a coincidence, not corroboration, and the tables say so.

**Neither panel used UNADJUDICABLE once in 112 situations.** The rubric offers
it as a third label. Whether the sample is genuinely that readable or the label
is systematically under-used is not established by this measurement.

## Pre-registered sensitivities, and how they resolved

Before pass 4 ran, and withheld from the refuters, two levers were declared
with their arithmetic (`adjudication.json`):

- **open-handle-as-receiver** (ArkLib#0, ArkLib#1, jaeger#0) — predicted
  39.3% -> 36.6% if rejected. **All three were independently refuted.**
- **sink-designation** (cert-manager#5, gocryptfs#3) — 39.3% -> 41.1% if
  flipped. Not raised by any refuter; both remain FP.

Pass 4 also refuted three situations **outside** both declared levers
(guacamole-client#4, rabbitmq#5, tmux#3), which is the pass doing work the
adjudicator did not anticipate.

### The contested refutation

**tmux#6 is reported separately and NOT folded into the headline.** It was
refuted on the ground that the chain holds in a different function than the
anchor. That criterion is **not in 0001's rubric** — the adjudicator introduced
it in `refute/REFUTE-BRIEF.md`. Both panels had independently seen the wrong
anchor and chosen to score the real chain. Folding it in would be the
adjudicator rewriting the rubric mid-measurement, which F6 forbids.

## Producer defects found in passing

The refuters were asked to report tool defects they noticed. Forty raw reports
cluster into these families. **Attribution matters**: family F is the
measurement's own packet builder, not hypergumbo.

**A. Primitive classified by NAME, not by what it does** (5 languages,
7+ instances). The single most consequential family.

| language | primitive | classified | actually |
|---|---|---|---|
| haskell | `IORef`/`STRef` | `db_read` | in-process mutable cell; `newIORef` *writes* and reads nothing |
| c | `socket` | `net_recv` | returns a descriptor; receives nothing |
| elixir | `Application.get_env` | `env_read` | application config, not process environment |
| python | `parse_args` | `env_read` | argv, not environ |
| bash | `>` | `fs_write` | no redirect-target check: `>&2` and `>/dev/null` count |
| bash | `>>` | reported as `>` | loses append/truncate distinction |
| bash | `BASH_SOURCE` | `env_read` | bash-internal, never exported |

Consequence: findings can be **true value-flow and vacuous as claims**. All
five shellcheck TPs are real dataflow filed under
`untrusted-input-no-database` against Haskell in-process refs. This makes
33.9% an **upper bound on useful precision**, not a measure of it.

**B. Taint credited across an external-resource boundary** (engine). Produced
5 of the 6 uncontested refutations: an `open()` handle treated as a taint
propagator (and inconsistently — the same shape is modelled correctly as
`open @ builtins` elsewhere in the same repository); fetched bytes not
discriminated from computed values (`curl "$URL" > file`); a socket fd whose
`recvmsg` bytes reach the sink; an out-of-repo callee (`ct:pal`) summarized as
the in-repo sink; reachability fused with dataflow where no value crosses the
argument boundary.

**C. `collapsed_flow_count` not reconcilable** — three independent reports
(ArkLib, kamaraflow, shellcheck), including totals padded with cross-module
pairs that carry no value. **This is the row-level denominator**, so the 31.5%
row figure is less trustworthy than the situation figure. The frame already
makes situation canonical; this is a second reason.

**D. `hops` not realizable** — four reports. One advertised 4 hops where the
shortest genuine chain is ~10 and leaves the process.

**E. Span defects** — file-anchored spans are `count(newlines) + 1`, correct
only for files with no trailing newline (diagnosed precisely on ArkLib, where
a 471-line file with no final newline spans correctly). Symbol spans are exact,
except one C file uniformly shifted by 4 at both ends.

**F. The measurement's own packet builder, NOT hypergumbo** — ~12 reports:
candidate-line listings match sink names as bare substrings (`inFORMATion`
matched `format`; `-> ` inside a quoted pattern matched `>`), match any `$VAR`
including shell comments and locals, miss literal call sites, and search for
sink sites only inside the *source* symbol's span — which makes the sink
listing structurally empty for every multi-hop situation. Fix before reuse.

**G. Scope** — vendored third-party code counted as first-party (kamaraflow's
two largest blocks are an upstream HuggingFace training script the application
never invokes).

## Disclosed limitations

- 4 Go repositories were skipped by the language cap (restic, buildah,
  prometheus, notation) and 5 repositories timed out during screening and were
  never adjudicated (trino, unleash, cohort2_yjs, AFFiNE, gitlab).
- The claim set is asymmetric and was disclosed, not fixed: `host_secret`
  carries 3 claims against `host_description`'s 1, and `env_read` is the source
  in 88 of 112 situations.
- Panel A and panel B are not perfectly symmetric: panel B was additionally
  told not to read panel A's output directory. Panel A had only the rubric's
  general instruction. Residual, unmeasured.
- Two panel B agents were initially refused by the concurrency cap and started
  later than the rest. Same rubric, same packets, no knowledge of other
  verdicts.
