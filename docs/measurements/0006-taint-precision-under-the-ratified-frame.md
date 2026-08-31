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
(canonical), row printed beside it. M=7 situations x R=16 repositories = 112
adjudicated. 0005's five repositories excluded. Rubric = measurement 0001's,
**cited verbatim, not rewritten** (F6). Seeded draw, seed 20260825. Language
cap one third.

## Frame

Machine-readable per ADR-0048 §A3, and consistent with the prose above.

- unit: situation (canonical); row rate reported beside it every time
- allocation: M=7 situations x R=16 repositories = 112 adjudications
- seed: 20260825
- cohort: `frame_08252026/COHORT.json`; 0005's five repositories excluded; language cap one third
- claim_set: the seven generic claims (frame F5)
- rubric: measurement 0001's, cited verbatim, not rewritten (frame F6)
- analyzer_sha: unrecorded
- language_scope: drawn from the catalogued languages present in the cohort; NO language excluded by declaration in this measurement

**Two of those values carry a caveat, stated here rather than left to be
noticed.**

`analyzer_sha: unrecorded` is a real gap, not a formality. No artifact of this
run records the tree it ran against, and eighteen commits changed taint
behaviour between the 2026-08-25 screen and figures published two days later —
so attribution across that boundary is not recoverable for this record. A SHA
reconstructed from dates would be a guess wearing a fact's clothing. ADR-0048
§A1 makes the SHA mandatory from measurement 0009; this record is exempted for
that one key, by name, in `scripts/check-measurement-frame`.

`language_scope` records that this measurement excluded no language *by
declaration*. That is not the same as every language having been able to
contribute: INV-linub's exposure table predicts that languages at >=87%
method-kind sinks produce zero taint flows structurally, so any such language
in the draw could only ever have supplied zero. ADR-0048 §A2 requires future
measurements to name that exclusion rather than pool a structural zero into the
headline.

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
| bash | `>` | `fs_write` | no redirect-target check: `>/dev/null` counts |
| bash | `BASH_SOURCE` | `env_read` | bash-internal, never exported |

**Two rows printed here originally did not reproduce, and are struck rather
than carried.** INV-nular re-ran both against the shipped analyzer: `>&2` does
*not* count (`_redirect_edge` returns `None` for fd duplication, so `echo hi
>&2` emits no edge at all — a recall gap, the opposite of the over-report
filed), and `>>` is *not* reported as `>` (the operators carry distinct `dst`
ids and distinct `io_mode`, pinned by `test_bash_redirection.py` since
INV-vavup). A refuter's report is a lead, not a finding; these two were
published without being reproduced.

Consequence: findings can be **true value-flow and vacuous as claims**. All
five shellcheck TPs are real dataflow filed under
`untrusted-input-no-database` against Haskell in-process refs. This makes
33.9% an **upper bound on useful precision**, not a measure of it. The bound
is now derived rather than asserted — see "Useful precision, re-derived"
below.

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

## Useful precision, re-derived under ADR-0046 (WI-gibom)

**This record originally published no useful-precision figure.** The
measurements index carried one — *"33.9% is an upper bound on useful
precision (≤25.0%)"* — added a day later, in the commit that published 0008,
and **≤25.0% was not derivable from anything written here**: the body names
exactly five vacuous TPs (shellcheck's), and five removals from 38 of 112
gives 29.5%, not 25.0%. WI-gibom was filed to derive it or restate it. This
section derives it.

| | |
|---|---|
| correctness precision | **33.9%** (38/112) — unchanged, and not relitigated |
| VACUOUS: KIND-MISDECLARED | **11** |
| VACUOUS: CONFIGURED-ACTION | **0** |
| **useful precision** | **24.1%** (27/112) |
| band `≤25% useful ⇒ recall stays stopped` | **tripped** |

Reproduce with `derive.py` in `gibom_08272026/`: it reads this measurement's
own `ledger-final.json` and `pass4-summary.json`, asserts the population it
was handed matches the headline set, and computes every figure above. No
count in this section is hand-copied.

**The published ≤25.0% survives as an upper bound and fails as a derivation.**
24.1% ≤ 25.0%, so nothing that was decided on the strength of that bound needs
revisiting. What was missing was never the answer — it was the working. The
basis this record states supports 29.5%, and the deduction that actually pays
for the difference, a bash redirect defect, is named only in part in section A
above. Whatever reasoning produced 25.0% was not written down, so a reader had
no way to check it and no way to disbelieve it.

### The eleven deductions

Every one is KIND-MISDECLARED (ADR-0046's INV-nular class: a *defect*, which
disappears when fixed). Each is citable in the repository's own source.

| situation | why the claim is vacuous |
|---|---|
| shellcheck#0, #1, #2 | `readSTRef`/`writeSTRef` under `db_read`/`db_write`. `haskell.yaml`'s own `boundary_ruling: unruled` note says these "are in-process mutable references, not a database". The claim is `untrusted-input-no-database`. |
| shellcheck#3, #5 | `newIORef` — a *constructor* — under `db_read`, into `modifyIORef`. |
| cert-manager#0 | `make/cluster.sh`: every `>` targets `/dev/null` (L103, L147, L168). |
| cert-manager#1 | `make/config/lib.sh`: both `>` target `/dev/null` (L50, L69). |
| cert-manager#2 | `make/_shared/tools/util/checkhash.sh`: the only `>` is L21 `>/dev/null`. L53 is `>>` to `$LEARN_FILE` — a distinct `dst` id, so not this finding's sink. |
| cert-manager#3 | `make/e2e.sh`: the only `>` is L159 `>/dev/null`. |
| spacedrive#6 | `apps/mobile/android/gradlew`: L89, L136, L220 all `/dev/null`; L96/L103 are `} >&2` fd duplications, which emit no edge. |
| guacamole-client#3 | `700-configure-features.sh`: the only `>` is L63 `> /dev/null`. |

The six bash deductions were live when this measurement ran and were fixed the
next day by PR #541, which refuses the boundary when *every* collapsed call
site discards. What that does to a *future* measurement is a re-measurement
question, not a restatement of this one.

**One reversal, recorded because only the second check caught it.** gocryptfs#2
was first labelled discard-only on the same evidence shape — four `>` sites,
all `/dev/null`. A second pass over the raw file found a fifth: L42
`exec 200> "$LOCKFILE"`, a numbered-fd redirect to a real path, and the path is
env-derived (`TMPDIR` → `TESTDIR` → `LOCKFILE`). It is a genuine environment →
host-filesystem write. The first cut would have published 23.2%.

### CONFIGURED-ACTION contributes zero, and that refutes the expectation

ADR-0046 predicted this number would fall *because* the CONFIGURED-ACTION
deduction had never been applied to this population. **It does not apply at
all here.** No TP in the set passes ADR-0046's clause 2 — a deserialization
call into a type whose fields are *declared* as a configuration schema. The
reason is structural rather than lucky: this cohort's claim set admits only
`env_read` and `host_info_read` sources (`env_read` is the source in 88 of 112
situations), and an environment variable reaches its sink through ordinary
string handling, not through a schema. ADR-0046's motivating case — caddy's
JSON-into-tag-declared-struct — is a shape this cohort does not contain, caddy
having been 0005's repository.

The number *did* get worse, 33.9% → 24.1%. It got worse for the other reason.

**Nearest miss, disclosed because the test is strict:** cilium#6 reads
`os.Getenv(defaults.SockPathEnv)` and dials the socket it names. Clauses 1 and
3 are citable; clause 2 is not, so it counts useful. ADR-0046 says the test
"can only *understate* the damage this class does", and here that is visible
rather than theoretical.

### One declared sensitivity — and two this record withdrew

Section A named five languages of kind-misdeclaration. Two are settled defects
and are deducted above. Of the remaining three, **this record first published
two as open vocabulary questions and then withdrew them**, because the ruling
they were said to await already existed. That is recorded here rather than
quietly corrected, because a sensitivity is a claim about what a reader should
still doubt, and publishing a doubt that the project had already resolved
overstates the uncertainty of the very figure a decision band reads.

**WITHDRAWN — `argv` under `env_read` (10 TPs) and application config under
`env_read` (5 TPs).** `docs/hypergumbo-spec.md` defines the boundary, in the
sentence INV-tutar's resolution added: *"`env_read` is an ambient
CONFIGURATION read (environment variables, system properties, **argv** —
values that **may carry a credential**)"*. The membership rule is **capability
to carry a credential, not being one** — which is exactly why `runtime.GOOS`
was split out into `host_info_read` and argv was not. Both classes sit where
that definition puts them, so neither is a misdeclaration and neither is
deducted. `taint.py` carries the same rule in force: *"THE SPLIT IS OF THE
BOUNDARY, NOT THE LABEL, and not a per-row override."*

The evidence agrees with the definition rather than merely deferring to it.
argv is world-readable in `ps`, which is why a `--password` flag is a known
hazard rather than a benign one; and one of the five application-config
situations is plausible reading `Application.get_env(:plausible, :paddle)` —
a payments provider's configuration, which is credential-bearing on its face.
INV-nular's own wording had already reached this conclusion without noticing:
it calls `Application.get_env` *"an ambient configuration read"*, the spec's
exact phrase, and then files the question as open.

**What was actually unresolved is not a vocabulary question at all.** The
boundary means *"may carry a credential"* while the label it derives says
`host_secret`. That gap is real, and it is the one **ADR-0046 already
answered** — by publishing two numbers instead of narrowing the rubric.
Re-deducting these rows would be doing by catalogue membership what that ADR
decided to do by measurement, and it would push in the false-all-clear
direction: a change that makes a security tool report *less* needs more
evidence than one that makes it report more, not less.

**STANDING — S1: `io_lib:format` as a `logging` sink (3 TPs, rabbitmq#0/#4/#6).**
Unaffected by the above, because it is a kind-vs-semantics question about a
sink primitive rather than about what `env_read` means. If it resolves against
the row, useful precision is **21.4%** (24/112).

| | question | members | useful precision if resolved against |
|---|---|---|---|
| **S1** | `io_lib:format` declared `logging` while its own catalogue note says it *"Returns iolist, not direct I/O"* | 3 | **21.4%** |

**So the band is tripped at 24.1%, and the one live sensitivity can only trip
it harder.** No reading of the evidence puts useful precision above 25%.

#### CORRECTION 2026-08-30 — S1 STANDS. Useful precision is 21.4%, and the first version of this section was wrong

**This section was published earlier the same day claiming the opposite, and a
blind panel refuted it. Both versions are recorded, because the error is
instructive and excising it would hide the mechanism.**

S1 asked whether the three rabbitmq true positives are vacuous, `io_lib:format`
being a call that returns an iolist and writes nothing. The owner ruled against
the row, and before the removal shipped it was tested at the finding level per
ADR-0049 ruling 3. That A/B (rabbitmq, both arms cold on separate
`XDG_CACHE_HOME` with the cold line asserted) showed:

| | arm A (row present) | arm B (row removed) |
|---|---|---|
| `host-secret-no-logging` | violated, 100 flows | violated, **68 flows** |
| sink modules | `io_lib` 94, `io` 5 | `io` **64**, other 4 |
| rabbitmq#0 / #4 / #6 | `io_lib:format`, 8/13/8 hops | **`io:format`, 17/15/17 hops** |

**The wrong inference drawn from it:** that the findings "re-root onto the
genuine sink", so the crossing is represented, the claims are not vacuous, and
S1 deducts nothing. On that reading useful precision stayed at 24.1%.

**What a blind panel found.** Two independent adjudicators per repository, given
the flow and the source but not the prior labels, walled off from every ledger
and prior measurement. **Disagreement 0 of 7.** All four rabbitmq situations are
FALSE POSITIVES, and both passes derived the same structural refutation without
conferring: the claimed route terminates at `rabbit_misc:module_attributes/1`,
whose `io:format` (`rabbit_misc.erl:731`) has exactly one argument list —
`[Module]` — bound at `rabbit_misc.erl:755-762` from
`{ok, Modules} <- [application:get_key(App, modules)]`, an independent read of
the application controller's module table. Neither parameter of
`module_attributes_from_apps/2` reaches it: `Name` is used only in an equality
test (control dependence), `Apps` only selects which external table to read
(resource selection, excluded by the tie-break). The route is structurally
incapable of carrying any of the four source values.

Each panellist then searched independently for a real chain and found none: in
`deps/rabbit/src` and `deps/rabbit_common/src` the actual `io:format` call sites
are enumerable, and no source value reaches any of them. The real formatting in
these files runs through `rabbit_misc:format/2` — `lists:flatten(io_lib:format(
Fmt, Args))` at `rabbit_misc.erl:558` — and the result is handed to `?LOG_*`
macros, which expand to `logger:` calls.

**So what the removal did is the reverse of what was claimed.** It replaced a
*vacuous true positive* — the value genuinely reaches `io_lib:format`, which
writes nothing — with an *outright false positive*, in which the value reaches
nothing at all. **S1's deduction stands. Useful precision under it is 21.4%
(24/112), the figure this record published before the correction attempt.**

**THE METHODOLOGICAL LESSON, which is the part worth carrying.** The A/B was
run correctly and measured what it measured. What it cannot do is tell you
whether a finding is TRUE — **the presence of a finding after a change is not
evidence that the finding is correct**, and re-rooting is not relocation unless
the destination chain is real. An A/B over tool output prices a change; only
adjudication prices a claim. This record's own headline exists because pass 4
refuted 7 of 44 panel-agreed true positives; the same discipline applied one
level down would have caught this before it was published.

**What is NOT retracted.** `io_lib:format` returns an iolist and writes nothing;
Erlang splits `io_lib` from `io` for that reason and the row's own note conceded
it. Removing the row was correct and remains correct — it deleted findings that
were vacuous. Only the *licence argument* offered for it, that a real crossing
was represented at `io:format`, is withdrawn. For these three situations there
is no real crossing to represent.

**A defect this exposed, filed rather than absorbed.** Two independent panels
demonstrated a 14-to-17-hop chain that provably cannot carry a value, on
`analysis_method: structural`. That is family D ("`hops` not realizable") with a
reproducible instance and a checkable rule: the terminal function's sink call
takes no argument derived from that function's parameters.

### Two sink-side defects found by this pass, neither deducted

Both are new — section A looked only at sources — and both are filed onto
INV-nular rather than acted on here.

- **`io_lib:format` declared `logging`.** `erlang.yaml`'s own note concedes it:
  *"Returns iolist, not direct I/O, but typically written immediately."* The
  formatted value is returned in-process; whether it reaches output is a hop
  this tool did not establish. All three rabbitmq TPs land on it, at 7, 12 and
  7 hops. Not deducted: the landing site could not be traced from this
  measurement's packets (family F below), so the conservative default holds.
  Declared as S1, the one standing sensitivity above.
- **`urllib.request.Request` declared `net_send`,** rowed beside `urlopen`.
  `Request` is a constructor and sends nothing — the same shape as `newIORef`.
  Not deducted: in jaeger's `scripts/release/notes.py` every `Request(...)` is
  consumed by `urlopen(req)` on the next line, so the network boundary really
  is crossed. An attribution defect, not a vacuous claim.

### What this section is not

**One adjudicator, not a panel.** The headline 33.9% carries two independent
16-agent panels, an adjudication pass and an adversarial refutation pass. This
vacuity labelling has none of that; it is the orchestrator's own pass, and its
protection is that every verdict is a citation a reader can check, not a
judgement a reader must trust. The three-citation test was built to make that
possible. Treat 24.1% as derived-and-checkable, not as panel-validated.

**A snapshot, not a trend.** This is the tool as of 2026-08-25. Six of the
eleven deductions were fixed the following day. A baseline series may anchor to
this number, but the first datum after it will not be comparable on the
KIND-MISDECLARED axis until INV-nular closes.

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
