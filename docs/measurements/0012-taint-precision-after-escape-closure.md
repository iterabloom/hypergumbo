<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0012 — Taint precision after the escape-closure fixes

- **Instrument:** `verify-claims --format json` via
  `scripts/measure-taint-precision.py collect|packet`, two independent
  12-agent adjudication panels, an adversarial refutation pass, and an
  ADR-0046 vacuity pass.
- **Tool state:** dev `f247b52587`.
- **Series position:** continues 0006 under the same ratified frame. **It is
  not independent of 0006** — see "What this does not support".

## The question

The standing decision band is *"≤25% useful precision ⇒ recall work stays
stopped"*. 0006 measured **24.1%** and tripped it, which is why INV-linub —
the largest known recall defect — has been blocked. Between 2026-08-25 and
2026-09-02 a run of fixes landed against the escape and vacuity classes 0006
identified. **Does the band still trip?**

A different answer changes what gets built next, which is why this is a
measurement rather than a note.

## The population

Sample, not census. The frame is 0006's, ratified 2026-08-25 and reproduced
in `~/hypergumbo_lab_notebook/measurement_0012_09022026/FRAME-NOTE.md`: the
same 16 repositories, the same seven generic claims, situation as the
canonical unit with the row rate beside it, cohort seed `20260825`.

**n = 94, of which 68 carry 0006's labels and 26 were judged fresh** (seed
`20260902`). The current population across the cohort is **669** situations.

### Why n=94 and not 112, and why that is not a shrunken sample

F2 samples *exactly* M=7 from each repo, which is what makes the pooled rate
and the unweighted per-repo mean the same number by construction. That is no
longer reachable: five repositories cannot supply seven situations, because
**the defects they exhibited were fixed** — shellcheck fell 23 → 3 and
cert-manager 9 → 3 as vacuous families closed.

| repo | population | drawn |
|---|---|---|
| guacamole-client | 2 | 2 |
| cert-manager | 3 | 3 |
| shellcheck | 3 | 3 |
| spacedrive | 3 | 3 |
| ArkLib | 6 | 6 |
| the other eleven | ≥ 7 | 7 each |

`sum(min(population, 7))` over the cohort is **94**, and no draw respecting
both the cohort and the cap can exceed it. The alternatives were priced and
rejected: dropping the short repos (R=12, n=84) changes the cohort and loses
comparability; F9's "hold R, reduce M" gives M=2 and n=32; raising M where the
population allows (rabbitmq holds 318) restores the repo concentration the
frame exists to control.

**Consequence, which is the cost of that choice: pooled ≠ unweighted mean.**
Both are reported everywhere below. Five repos carry 6 or fewer situations and
their per-repo rates are noise; they are printed with denominators and never
quoted alone.

## The rubric

Measurement 0001's, verbatim, carried through 0005 and 0006 unchanged, and
byte-identical for both panels. It is reproduced in full in the frame note and
in `blind/RUBRIC.md`. **No revision was made after labelling started.**

The ADR-0046 vacuity vocabulary (USEFUL / VACUOUS:KIND-MISDECLARED /
VACUOUS:CONFIGURED-ACTION) is applied to surviving true positives only, by a
single adjudicator, as in WI-gibom's pass for 0006.

## The headline

|  | situations | rows |
|---|---|---|
| **correctness precision** | **32/94 = 34.0%** | 97/323 = 30.0% |
| **useful precision** | **29/94 = 30.9%** | 93/323 = 28.8% |
| VACUOUS:KIND-MISDECLARED | 3 | |
| VACUOUS:CONFIGURED-ACTION | 0 | |

Unweighted per-repo mean useful: **28.0%**.

**The band does not trip on either estimator** (30.9% pooled, 28.0%
unweighted, against ≤25%). 0006 read 24.1%.

## Disagreement rate, reported beside the headline and not folded into it

The two blind panels agreed on **25 of 26 = 96.2%** (0006: 94.6%).

The single disagreement, `tmux:host-description-no-network:6`, was resolved at
pass 3 by reading source. Panel B's specific assertion — *"no line composes it
into an ibuf"* — is contradicted by `file.c:486` and `proc.c:171`. It was
resolved **to TP**, and then the vacuity pass deducted it anyway on a
different ground, so the disagreement did not move the useful figure.

**Agreement is not correctness, and 0006 is the evidence.** Its refutation
pass killed 7 of 44 panel-agreed true positives, three of them unanimous.
This measurement's refutation pass killed **0 of 11** — see below.

## The verdict table (the 26 freshly judged)

**†** = resolved at pass 3 by the adjudicator reading source.

| id | panel A | panel B | pass 3 | pass 4 | ADR-0046 |
|---|---|---|---|---|---|
| `ArkLib:host-secret-no-host-fs:2` | TP | TP | TP | survived | USEFUL |
| `ArkLib:host-secret-no-host-fs:3` | TP | TP | TP | survived | USEFUL |
| `ArkLib:host-secret-no-host-fs:5` | TP | TP | TP | survived | USEFUL |
| `beads:host-secret-no-logging:24` | FP | FP | FP | — | — |
| `beads:untrusted-input-no-host-fs:1` | FP | FP | FP | — | — |
| `cert-manager:host-secret-no-host-fs:0` | TP | TP | TP | survived | USEFUL |
| `cilium:host-description-no-network:12` | FP | FP | FP | — | — |
| `gocryptfs:host-secret-no-network:0` | FP | FP | FP | — | — |
| `gocryptfs:untrusted-input-no-host-fs:0` | TP | TP | TP | survived | **VACUOUS:** KIND-MISDECLARED |
| `gocryptfs:untrusted-input-no-subprocess:1` | FP | FP | FP | — | — |
| `gocryptfs:untrusted-input-no-subprocess:3` | FP | FP | FP | — | — |
| `guacamole-client:host-secret-no-host-fs:0` | FP | FP | FP | — | — |
| `jaeger:host-secret-no-logging:1` | FP | FP | FP | — | — |
| `rabbitmq:host-secret-no-logging:17` | TP | TP | TP | survived | USEFUL |
| `rabbitmq:host-secret-no-network:11` | FP | FP | FP | — | — |
| `rabbitmq:host-secret-no-network:37` | FP | FP | FP | — | — |
| `rabbitmq:host-secret-no-network:45` | TP | TP | TP | survived | USEFUL |
| `rabbitmq:untrusted-input-no-database:55` | TP | TP | TP | survived | USEFUL |
| `shellcheck:host-secret-no-host-fs:0` | FP | FP | FP | — | — |
| `shellcheck:host-secret-no-host-fs:1` | FP | FP | FP | — | — |
| `shellcheck:host-secret-no-host-fs:2` | TP | TP | TP | survived | **VACUOUS:** KIND-MISDECLARED |
| `spacedrive:host-secret-no-host-fs:0` | FP | FP | FP | — | — |
| `tmate:host-secret-no-host-fs:2` | TP | TP | TP | survived | USEFUL |
| `tmux:host-description-no-network:13` | FP | FP | FP | — | — |
| `tmux:host-description-no-network:6` | TP | FP | TP **†** | survived | **VACUOUS:** KIND-MISDECLARED |
| `tmux:host-secret-no-host-fs:0` | FP | FP | FP | — | — |

The 68 carried situations keep 0006's verdicts unchanged, including its pass-4
refutations and its vacuity labels; they contribute **21 useful TPs**. Their
identities are in `sample/sample-94.json` under `_origin: carried_0006`.

## Pass 4 killed nothing, and that is a result

Eleven true positives were attacked by seven independent refuters against the
same rubric. **0 refuted, 11 survived, 0 contested.** Against 0006's 7-of-44,
this is the sharpest single difference between the two measurements.

The brief carried 0006's own lesson forward: a refutation resting on a
criterion not in the rubric must be marked `contested` and named. None was —
two refuters explicitly declined attacks (a "this only fires when a user
configures it" argument, and an indirect-dispatch argument) on exactly that
ground, which is the rule working rather than going unused.

## The three vacuous true positives, each citable

All three are KIND-MISDECLARED — a claim asserting a boundary crossing the
call does not make. **CONFIGURED-ACTION remains at zero**, structurally: the
claim set admits only `env_read` and `host_info_read` sources, so ADR-0046's
clause 2 (schema deserialization) has no candidate to match. A cohort meant to
exercise that class still needs a config-file-driven server, and this one is
not it.

1. **`shellcheck:host-secret-no-host-fs:2`** — both sink sites are
   `hPutStrLn stderr` (`shellcheck.hs:484`, `:536`). `haskell.yaml` lists
   `System.IO.hPutStrLn` under **both** `fs_write` and `logging` with
   `boundary_ruling: call_site_undecidable` and the note *"stderr reads as
   logging, a file handle as fs_write"*. The handle is stderr; the asserted
   host-filesystem crossing does not occur. **The catalogue already says so.**

2. **`gocryptfs:untrusted-input-no-host-fs:0`** — the sink is
   `io.WriteString(in, s+"\n")` at `internal/fido2/fido2.go:61`, where
   `in = cmd.StdinPipe()` (`:56`) — a pipe to a child process.
   `go.yaml:101` declares `io.WriteString` under `fs_write` **with no
   `boundary_ruling`**, although its first argument is an `io.Writer` and the
   boundary is decided by the writer. Same shape as the haskell row; the
   declaration is missing.

3. **`tmux:host-description-no-network:6`** — the sink is
   `sendmsg(fd, &msg, 0)` at `compat/imsg-buffer.c:803`, and that fd is
   always AF_UNIX: `client.c:122` and `server.c:123` both
   `socket(AF_UNIX, SOCK_STREAM, 0)`, `proc.c:364` `socketpair(AF_UNIX, ...)`.
   tmux opens no AF_INET socket anywhere. This is process-local IPC — the
   catalogue's own `ipc_send` zone — not a network crossing.
   `c.yaml:90` declares `sys/socket.sendmsg` under `net_send` **with no
   `boundary_ruling`**, while the very next row gives `unistd.write` one for
   precisely this reason.

Items 2 and 3 are new INV-nular members and are filed as such.

## One standing sensitivity

**S1 — `ets` as a database boundary.** `rabbitmq:untrusted-input-no-database:55`
is an `ets:lookup` → `ets:insert` round trip. It is counted **useful** and not
deducted: `erlang.yaml:206/214` declare both under `db_read`/`db_write` with no
in-process disclaimer, unlike `haskell.yaml`'s `Data.STRef` row whose own note
(*"in-process mutable references, not a database"*) is what justified 0006's
shellcheck deduction — and the claim text contemplates the shape explicitly
(*"a read-then-write round trip within one store also fires here"*).
Resolving the vocabulary question against ETS would take useful precision to
**29.8%** (28/94), which still clears the band.

## Nine producer defects, found while adjudicating

Recorded because they are label-neutral evidence about the tool, not about the
findings. Filed to the tracker separately.

- **Source spans mis-anchored to a same-named different-arity function.**
  `rabbitmq:...:17`'s source span `2743-2744` points at
  `get_consumer_timeout/2`, an argument-forwarding wrapper with no env read;
  the real `application:get_env` is at `rabbit_channel.erl:819`.
- **A sink site matched inside a block comment.**
  `compat/imsg-buffer.c:813` is comment text (*"assumption: fd got sent if
  sendmsg sent anything"*), listed as a `sendmsg` call site.
- **Fictitious intermediates on the path.** `rabbit_writer:internal_flush/1`
  is not on the STOMP value's route; `hops=11` is measured along a route the
  value does not take.
- **`hops` counts that match no real route** (`tmate` `hops=4` against real
  routes of 2 and 5; `gocryptfs` `hops=0` for a five-hop interprocedural loop
  whose sink at `:62` runs strictly *before* its source at `:65`).
- **A whole-file pseudo-symbol double-attributing sites** already attributed
  to the enclosing function (`ArkLib` `:3`, `:5`).
- **`${BASH_SOURCE[0]}` listed as an `environ` source site** — a bash-internal
  special array, not a process-environment variable.
- **Sink-family mis-attribution**: `tmate`'s packet header declares
  `SINK fprintf @ stdio` while the enumerated set is
  `['fflush','fprintf','mkdir']` and the only site shown is `mkdir`.

## An instrument defect was fixed mid-staging, before any packet was judged

`Path(".github_deploy").suffix` is `""` — a leading dot is not an extension —
so shellcheck's two extensionless bash scripts were read as an unknown
language, the bash ambient-expansion listing was skipped, and the packet
printed `(none found; searched .github_deploy 1-28)` for a file whose line 11
is `for tag in $TAGS`. **Two of the 26 packets carried that manufactured
absence, pointing toward FALSE POSITIVE.** Fixed at `f247b52587`.

Two other empty listings were left alone because they are **honest**:
spacedrive's `combine_files` genuinely reads no environment variable in its
span, and rabbitmq's `rabbit_channel.erl` span really holds no `get_env`.
Telling the two kinds apart is only possible because an empty listing names
what was searched — and one refuter used exactly that disclosure to find the
real call site 1,900 lines away.

## What this number does NOT support

- **It is not independent of 0006.** 68 of the 94 labels *are* 0006's labels.
  Any comparison between 30.9% and 24.1% shares that majority.
- **It is not evidence that a fix caused the movement.** No A/B was run. The
  cohort's population fell 730 → 669 and its composition changed; both figures
  are consistent with fixes removing vacuous findings, and neither is
  attribution.
- **The per-repo rates for the five short repositories are noise.**
  guacamole-client's "0.0%" is 0 of 2.
- **Precision is not accuracy.** Nothing here measures recall, and the recall
  defect the band gates (INV-linub) is untouched and still open.
- **Vacuity was labelled by one adjudicator**, not a panel. The two labelling
  passes were panels; this pass was not, matching 0006's own vacuity
  derivation but inheriting its weaker provenance.
- **Cause G from 0006 is not addressed.** Vendored third-party code counted as
  first-party remains live, and it hit kamaraflow hardest — two of its largest
  blocks are an upstream training script the application never invokes.

## The stratum comparison, which is the load-bearing check

The obvious objection to a carry-forward is survivorship: the 68 are 0006's
sample filtered by *survival*, so a fix that removed only bad findings would
raise their rate without the tool improving. The 26 fresh situations exist to
close that hole, and they are drawn from a population that has changed by
~600 situations since 0006.

| stratum | correctness | useful |
|---|---|---|
| 68 carried | 30.9% | **30.9%** |
| 26 fresh | 42.3% | **30.8%** |

**The two agree to a tenth of a point on the figure the band reads.** The
fresh draw does not differ from the survivors, so the survivorship objection
does not bite here. It is one draw of 26 and the interval is wide; it is
evidence against the objection, not proof of its absence.
