<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0019: What rowing DNS lookups as `net_recv` added — and why the number measures the WALK, not the rows

**Status:** Complete
**Date:** 2026-09-08
**Instrument:** `~/hypergumbo_lab_notebook/dozul_dns_09082026/` — `PLAN.md` (pre-registration, written before any arm ran, with a dated cohort amendment made while NO arm had produced a result), `dnsscan.sh` (pre-design sizing over 273 corpus repositories), `arm.sh` (one arm, asserting its catalogue BLOBS and a row canary at entry), `runboth.sh` (both arms, sequential), `diffarms.py` (situation-level delta, printing full evidence rows rather than counts), `RESULT-removal.md` (the erlang removal proof)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim, no project-local catalogue
**Tracker:** `WI-dozul` (the item under test), `WI-gotun` (the java twin of the cause this found), `WI-tuzaf` (the untyped-receiver family that makes two row groups inert)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: the SITUATION (an ADR-0046 source→sink situation as `verify-claims`
  reports it), with the CLAIM VERDICT beside it as the control
- allocation: CENSUS — every situation added or removed across both arms was
  read back against source. There is no sample and no seed: the delta is 6 rows
- seed: none. The delta is 6 situations and every one was read back, so there
  is no sample to seed and no draw to reproduce
- language_scope: c, go, python (measured); javascript and rust EXCLUDED from
  the measured cohort and demonstrated by fixture only — javascript because the
  corpus holds one call site and it is in a 277 MB repository, rust because its
  single row is inert on arrival. erlang carries the pre-existing rows this
  change achieved parity WITH and is measured only by the removal proof in
  `RESULT-removal.md`
- cohort: 11 repositories, 364 MB, 35 measured DNS call sites — torsocks,
  passt, webtunnel, robyn, redis, qemu-skiboot, nova, powerdns, tor, vault,
  django. Chosen by MEASURED call-site count over 273 corpus repositories
  (61 have at least one), not by familiarity
- claim_set: the seven generic taint claims, verbatim
- rubric: ADR-0046's two axes. CORRECT = a real value flow exists from the
  lookup's returned value to the sink. USEFUL = a reader would act on it
- analyzer_sha: two arms by BLOB. SUBJECT = `830290c401` catalogues
  (python `5051865cf0…`, go `373c55933f…`, c `c68dfe2a27…`, javascript
  `4f99788c54…`, erlang `7efdce5564…`, rust `a23791a6eb…`). BASELINE = the same
  six files at merge-base `ecacaefea3`. Both arms asserted their blobs in the
  log at entry, and the BASELINE arm restored the subject blobs at exit
- canary: pre-registered and checked before each arm ran — the loaded python
  catalogue must hold 6 module-level `socket` `net_recv` rows in SUBJECT and 0
  in BASELINE. Both fired correctly. An arm failing its canary was to be
  discarded, not interpreted

## Result

**6 situations added, 0 removed, 0 verdicts moved.** Every one of the 6 was read
back against source. **0 of 6 are correct.**

| repo | lang | source | sink | method | correct? |
|---|---|---|---|---|---|
| passt | c | `getaddrinfo` in `source.c:main` | `fprintf` | structural | NO |
| passt | c | `getaddrinfo` in `target.c:main` | `fprintf` | structural | NO |
| redis | c | `getaddrinfo` in `hiredis/test.c` | `printf` | structural | NO |
| redis | c | `getaddrinfo` in `anet.c:_anetTcpServer` | `chmod` | structural | NO |
| qemu-skiboot | c | `gethostbyname` in `TSS_Socket_Open` | `printf` | structural | NO |
| vault | go | `net.LookupNS` in `PushConfig` | `os/exec.Run` | ddg_mixed | NO |

Worked example, because "false positive" without a mechanism is an assertion:
redis `_anetTcpServer` calls `anetListen(err, s, p->ai_addr, p->ai_addrlen,
backlog, **0**)`, and `anetListen`'s only `chmod` sits behind
`if (sa->sa_family == AF_LOCAL && perm)`. The path is infeasible on TWO
independent conditions — `perm` is a literal `0`, and the address came from a
`getaddrinfo` whose `hints.ai_family` is AF_INET or AF_INET6, never AF_LOCAL.
In vault, the `os/exec` calls run BEFORE the `LookupNS` in program order and
consume `ts.domains`, not the resolver's answer.

## THE NUMBER MEASURES THE WALK, NOT THE ROWS

The finding that matters is not 0/6. It is WHERE the 6 came from:

| language | def/use extractor | DNS sites | situations added | correct |
|---|---|---|---|---|
| python | **yes** | 12 | **0** | — |
| go | **yes** | 4 | 1 | 0 |
| c | **NO** | 14 | 5 | 0 |

**Five of the six false positives are in the one language of the three that
cannot prove dependence.** `registered_def_use_languages()` returns exactly
`go, javascript, python, rust, typescript` — c, cpp, java and erlang have no
def/use extractor, so every c flow is STRUCTURAL, and a structural walk pairs
any source with any sink reachable in the same function. The DNS rows did not
create that; they exposed it by putting a taint source into c functions that
also call `printf`.

**The python rows are sound, and that is asserted rather than inferred from
their zero.** A fixture calling `socket.gethostbyname(host)` into
`subprocess.run` reports the flow at **`confidence: precise`** — DDG-reasoned,
because python has the extractor. Python's zero on the cohort is 12 call sites
whose results genuinely do not reach a prohibited sink: a TRUE NEGATIVE, not a
reach gap. An earlier read of these same JSON files as "the python rows never
classified" was a WRONG INSTRUMENT — the `verify-claims` envelope reports
flows, not every classified call, so absence from it is not evidence of
absence.

## The pre-registered re-open trigger: CONDITION MET, PREMISE NOT

`WI-dozul`'s trigger, written before the run: *if the adjudicated FORWARD-lookup
flows are majority not-useful, file the forward-vs-reverse cut as an ADR-0024
family audit and rule any new boundary on that number.*

All 6 are forward lookups and all 6 are not useful, so the condition fires. **It
should not be acted on as written, and the reason is the table above.** The
trigger anticipated findings that were CORRECT but not worth acting on — the
lookup-then-connect idiom. What happened instead is that they are not correct,
for a reason that has nothing to do with forward versus reverse: a reverse
lookup in c would produce identical spurious pairings, and a forward lookup in
python produced none. **Filing a boundary audit on this number would attribute
to the ROW a defect that belongs to the WALK.**

This cohort therefore cannot answer the trigger's question. That is a negative
result and it is the deliverable: the languages where the walk can prove
dependence produced zero findings to adjudicate, and every finding came from
the language where it cannot.

## What this does not establish

- That DNS rows never produce a useful finding. On this cohort they produce
  none, in 35 call sites. A cohort where a resolved address reaches a sink in
  python or go would answer a different and better question, and is not
  assembled.
- That the ruling is wrong. The rows are correct as catalogue rows — a resolver
  chooses the answer — and the erlang parity that decided the ruling is
  unaffected. Nothing here argues for removing them.
- Anything about javascript. The corpus holds ONE javascript DNS call site, in
  a 277 MB repository, so javascript is absent from the measured cohort. Its
  rows are demonstrated REACHABLE on a fixture instead, which is a weaker claim
  and is labelled as one: `dns.resolve4(h, cb)` produces a `net_recv` chain
  carrying `primitive: dns.resolve4`.
- Anything about `dns.Resolver` (14 rows) or rust `ToSocketAddrs` (1 row). Both
  are INERT ON ARRIVAL — a constructed or untyped receiver resolves to the
  `external` sentinel, which yields an empty catalogue key by design — and both
  declare their inertness on the row rather than being counted as coverage.

## Cohort amendment, and why it cannot have been results-driven

The first cohort (airflow, django, keda, redis, fluent-bit, nextjs) was
abandoned 60 minutes into its FIRST repository, which never completed. **No arm
had produced a result, so there was no delta to steer toward.** The cause was a
bad extrapolation, recorded because the failure is reusable: the mini trial gave
redis at 23 MB in 85 s, and I extrapolated LINEARLY IN SIZE to ~60 min for
976 MB per arm. airflow (183 MB, 8x redis) had not finished at 60 minutes —
over 40x the time for 8x the bytes. Honestly re-extrapolated, six repositories
across two arms was 9-18 hours against an 8-hour ceiling. The amended cohort
trades size for DNS density and cost 39 + 39 minutes for both arms.

## SAME-DAY CORRECTION: the remedy this measurement implies was priced, and it is not removal

Added 2026-09-08, before this record shipped, by pricing its own implied remedy
instead of resting on it. Instrument: `~/hypergumbo_lab_notebook/c_defuse_09082026/`
(`RESULT.md`, six fixtures — three flow shapes x c and python, each a separate
repo and a separate run so every verdict is attributable).

**Nothing in the Result or attribution sections above changes.** The 6
situations, the 0-of-6 adjudication, the worked `_anetTcpServer` example and the
trigger ruling all stand. What changes is what a reader should conclude the fix
IS.

**The natural reading of the table above — build c a def/use extractor and these
five false positives go away — is FALSE, and this record should not be cited for
it.** Measured: for the co-located-non-flow and discarded-at-source shapes,
python (full extractor) and c (none) both report the finding, both at
`approximate` confidence, and `flows_removed_by_walk` is **0 in both**. The
extractor changes the METHOD LABEL (`ddg` / `ddg_mixed` / `structural`) and not
the reported population.

The mechanism is documented in `dataflow_scope`'s module docstring rather than
discovered here: the walk only ever SUBTRACTS, and only on `unconfirmed` —
`escaped` is ignorance and removes nothing. That docstring records the removal
class as measured-EMPTY on **this measurement's own 11-repo cohort** (0
unconfirmed / 14 escaped / 139 not_attempted). It activates as escape sites
close, which the arc schedules last (INV-busis).

**This measurement's own table already carried the counter-evidence, and the
first read of it missed the inference: the vault false positive is in `go`,
which HAS an extractor, and it is labelled `ddg_mixed`.** A row that says
"extractor: yes" beside "correct: NO" is the whole refutation, and it was
printed here before it was understood.

So the attribution "five of six are in the one language that cannot prove
dependence" is a CORRELATION whose causal story this cohort does not establish.
The competing explanation is a corpus property, not an analyzer one — c code
co-locating resolver calls with `printf` more often than python co-locates them
with `subprocess` — and it is not measured.

**What the missing extractor demonstrably costs c is TRIAGE, not precision.**
In python a reader separates `ddg` (dependence proven) from `ddg_mixed`
(unproven) from `structural` (the walk had nothing to say). In c every finding
is `structural` because it is the only reachable branch, so the label carries
zero bits; a c reader filtering on `analysis_method == "ddg"` gets an empty set,
always. Filed as `WI-himob` with this evidence, ranked BELOW its java twin
`WI-gotun`: `dataflow_coverage` names FOUR blockers for c against java's three
(there is no `cfg_nodes/c.yaml`), and c's `catalog_sanitizers` is **0** against
java's 1, so c is more work for a benefit bounded strictly lower.
