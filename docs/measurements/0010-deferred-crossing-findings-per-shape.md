<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0010: Adjudicated deferred-crossing findings, per shape

**Status:** Complete
**Date:** 2026-08-30
**Instruments:** [`scripts/measure-taint-precision.py`](../../scripts/measure-taint-precision.py)
`collect`, plus per-arm join/ledger scripts under
`~/hypergumbo_lab_notebook/step3_adjudication_08302026/`
**Tracker:** `WI-johuk` (the ruling), `WI-hazop` (the census), `WI-vapud` /
measurement [0009](0009-deferred-crossing-reachability.md) (reachability),
`INV-kanuk` (the retag already shipped), `WI-lalot` / `INV-misup` (the two
reachability defects), `WI-rivur` (the misdeclared siblings)

## The question

[ADR-0049](../adr/0049-deferred-crossings-are-disclosed-not-minted.md) §"Open
work" step 3 is the last gate before the deferred-crossing family may be
retagged:

> **Adjudicated findings per shape.** Live findings from the measurement-0006
> corpus, labelled under ADR-0046.

Steps 1 and 2 are done — the census (`WI-hazop`) and the reachability pass
(0009). Step 3 is the one that asks what the family's findings are actually
*worth*, and `TestServerLaunchStaysAReceive` pins the launch rows across nine
languages until it clears.

**Two claims in that step are wrong and are corrected here rather than
inherited.** ADR-0049 says 0006's corpus "already contains caddy" — it does
not; caddy was 0005's repository and 0006 excluded 0005's five repositories by
construction, which is why 0006 could report zero CONFIGURED-ACTION findings
"structurally rather than by luck". And it says "**zero** launch-family
findings appear among 0006's 112 adjudicated situations", then names C's
`socket` as the one adjudicated datapoint — a sentence that contradicts itself,
because those `socket` findings **are** three of the 112. Seven of the 112 are
family findings. They are re-derived below.

## Frame

Machine-readable per ADR-0048 §A3. **This record is not a precision measurement
and does not enter the 0006 series.** It is a census of one filtered
subpopulation — the findings a named set of catalogue rows roots — so F2's equal
allocation and F3's seed have nothing to govern, and the keys say so rather than
naming a value they do not have.

- unit: the SITUATION (canonical, per F1), with the row count it stands for printed beside it every time; `collapsed_flow_count` is the row multiplier
- allocation: CENSUS of a filtered subpopulation, NOT an M x R draw — every violating situation in the cohort whose source primitive is a live deferred-crossing row. A repository contributing zero family findings stays in the cohort as a zero, which equal allocation exists to prevent and a census requires
- seed: no draw was made, so none was seeded — the population is every qualifying situation, not a sample of them
- cohort: measurement 0006's sixteen repositories PLUS caddy (17). caddy is added deliberately and named: ADR-0049 asserts it is in 0006's cohort and it is not, and ADR-0046 says "a cohort meant to exercise [CONFIGURED-ACTION] must contain one". Both the 16-repo and 17-repo figures are reported so the addition is visible
- claim_set: the seven generic claims (`docs/example-claims/generic-taint-claims.yaml`), unchanged. Only the three `untrusted-input-*` claims can admit a family source, since `AUTO_SOURCE_LABEL_MAP` maps this family's boundaries to `untrusted_input`
- rubric: measurement 0001's, cited verbatim below (F6), plus ADR-0046's two VACUOUS classes for the usefulness label
- analyzer_sha: 4575c5a7d4248e329cc0522413d9977188e3cad9. The working tree was dirty during the run, in `docs/`, `CHANGELOG.md`, `scripts/smart-test` and `tests/` only — no path under `packages/*/src` differed, so the analyzer content is this commit's
- language_scope: go, python, erlang, elixir, haskell, c, rust, javascript, typescript, java and bash are all present in the cohort and all in scope. FINDINGS were produced by go, erlang and python only. EXCLUDED BY NAME — none. The absence of java/scala/kotlin/objc/swift family findings is INV-linub's structural zero (>=87% method-kind sinks), and is reported as an absence of findings, never as a measured zero

## Rubric

**Correctness label — measurement 0001's, verbatim (F6):**

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

**Usefulness label — ADR-0046**, on TRUE POSITIVES only: `CONFIGURED-ACTION`
(all three clauses citable) or `KIND-MISDECLARED` (the INV-nular class),
otherwise **useful**. The two are counted separately because they have opposite
lifetimes.

**The refutation condition, fixed before any label was written.** ADR-0049
ruling 1 says a deferred-crossing row cannot supply far-side data *at its own
call site*. So every family-rooted situation should be an FP, or a TP that is
vacuous. **A family-rooted situation adjudicated TP *and useful* refutes the
blanket application of the ruling to that row.**

**It fired.** Eleven times.

## Headline

| | arm 1 — 0006's own labels | arm 2 — this tree, fresh |
|---|---|---|
| population | 112 situations / 336 rows | 783 violating flows |
| family-rooted | **7 situations / 11 rows** (6.2%) | **50 situations / 411 rows** |
| TRUE POSITIVE | **0** | 24 (**48.0%**) |
| FALSE POSITIVE | 7 | 26 |
| VACUOUS: KIND-MISDECLARED | 0 | 10 |
| VACUOUS: CONFIGURED-ACTION | 0 | **3** |
| **USEFUL** | **0 (0.0%)** | **11 (22.0%)** |

The two arms disagree, and the disagreement is the result. Arm 1 saw only
`SETUP` and `HANDLE` rows that are wired or file-backed; arm 2's population is
14× larger and contains the shape arm 1 did not: a stdin handle **read in the
scope that constructed it**.

## The ruling holds for LAUNCH and fails for HANDLE

| shape | situations | rows | TP | useful |
|---|---:|---:|---:|---:|
| **LAUNCH** (`net/http.ListenAndServe`) | 2 | 5 | **0** | **0** |
| **HANDLE** (`os.Stdin`, `bufio.New*`, `sys.stdin`, `dets.open_file`) | 48 | 406 | 24 | **11** |

Shape is not the discriminator. **What the source line does is**, and it is
visible in the source rather than inferred:

| mechanism | situations | rows | TP | useful |
|---|---:|---:|---:|---:|
| `DEFERRED` — the crossing really does arrive in a scope this call does not name | 3 | 6 | **0** | **0** |
| `WIRING` — the handle is assigned somewhere and never read here (`cmd.Stdin = os.Stdin`) | 8 | 19 | **0** | **0** |
| `WRONG-CHANNEL` — the constructor wraps something other than the declared boundary | 17 | 65 | 10 | **0** |
| `READ-IN-SCOPE` — the handle is consumed by a read in the same statement or function | 22 | 321 | 14 | **11** |

**`READ-IN-SCOPE` is the refutation.** `bufio.NewReader(os.Stdin)` returns a
handle, so it fails ADR-0049 ruling 1 exactly as `ListenAndServe` does — but the
handle is read four lines later *in the same function*, so "the scope the call
does not name" is the caller's own scope, and minting there is not a
mis-attribution at all. The cleanest instance is citable end to end:

```
beads cmd/bd/init_contributor.go
   26  reader := bufio.NewReader(os.Stdin)
  129  planningPath, err := readLineWithContext(ctx, reader, os.Stdin)
  136  planningPath = strings.TrimSpace(planningPath)
  144  planningPath = filepath.Join(homeDir, planningPath[2:])
  151  os.MkdirAll(planningPath, 0750)
  157  cmd.Dir = planningPath          // exec.Command("git", "init")
```

That is a true, useful, unhedged finding rooted at a deferred-crossing row.
Retagging `bufio.NewReader` would delete it.

**And Go has nowhere to relocate it to.** The catalogue's entire Go stdin
surface is three rows — `os.Stdin`, `bufio.NewScanner`, `bufio.NewReader` — and
**all three are deferred-crossing rows**. There is no `bufio.Reader.ReadString`,
no `Scanner.Scan`, no `Scanner.Text`, no `os.File.Read`. So a retag of the
HANDLE shape in Go is forbidden by ADR-0049 ruling 3 on its own terms: it would
take Go's stdin ingress to **zero** rather than relocating it, which is WI-lunav
at three rows instead of one.

## LAUNCH: two findings against forty-nine files

The launch arm is unanimous — 0 TP, 0 useful — and **thin**, and the second fact
has to be published as loudly as the first.

```
beads examples/monitor-webui/main.go
   80  if foundDB := beads.FindDatabasePath(); foundDB != "" {     <- the sink path starts here
  125  if err := http.ListenAndServe(addr, nil); err != nil {      <- the "source", after it
```

`ListenAndServe` is the last statement of `main`; its only value is `err`,
consumed by `Fprintf` and `os.Exit`. The sink is
`internal/git/gitdir.go:32 exec.Command("git", "rev-parse", ...)` — every
argument a string literal — reached from a call that runs **before** the source.
The second finding is the same source reaching
`internal/configfile/configfile.go:81 os.Remove(legacyPath)`, where `legacyPath`
is built from `Load`'s parameter. Both are call-graph reachability with no value
passed, which 0001's rubric names a false positive in terms.

**The site control, because a finding count is not a site count.** A deliberately
over-counting grep finds **49 files** in the cohort containing a launch-shaped
call:

| repo | files | dominant spelling |
|---|---:|---|
| cilium | 27 | `grpc.NewServer(` 10, `.Serve(lis` 9, `http.ListenAndServe(` 7 |
| jaeger | 17 | `grpc.NewServer(` 9, `.Serve(lis` 7 |
| beads · caddy · kamaraflow · plausible · rabbitmq | 1 each | — |

Forty-nine files, two findings. Two causes, both already filed and both visible
in the per-claim verdicts printed by every run:

- **jaeger returned `inconclusive` on all three `untrusted-input-*` claims** —
  the analysis was blind there, not clean. A blind repository contributes no
  findings and is not evidence of none.
- **cilium resolved those claims and still produced no launch-rooted finding**,
  with seven `http.ListenAndServe` files. `grpc.NewServer()` / `.Serve(lis)` are
  method rows on a receiver typed from a library return value — `WI-lalot`,
  which 0009 measured as making all seven Go third-party framework launch rows
  inert. The stdlib row is reachable and still roots nothing, because `main`
  usually has no sink carrying its value.

**So the honest priority statement is: retagging the launch family removes two
findings across seventeen repositories.** It is a correctness fix, not a
precision fix, and 0009 already warned that this is the calculus that decides
its priority.

## Arm 1 — what the family did to measurement 0006

Re-derived from 0006's own `sample-112.json`, `ledger-final.json` and
`pass4-summary.json`. Every source primitive in the population is classified
against ADR-0049 ruling 1 directly, rather than joined to 0009's register: that
register describes the catalogue as of 2026-08-29, by which point WI-dosov had
**removed** C's `socket`/`bind`/`listen` and WI-rigut had fixed Haskell's
`IORef`/`STRef` rows, so a join would have silently dropped the very rows this
arm exists to count.

| situation | label | shape | primitive | sink | rows |
|---|---|---|---|---|---:|
| tmate#1 | FP | SETUP | c `sys/socket.socket` | `execl` | 2 |
| tmux#3 | FP | SETUP | c `sys/socket.socket` | `execl` | 3 |
| tmux#5 | FP | SETUP | c `sys/socket.socket` | `unlink` | 1 |
| gocryptfs#4 | FP | SETUP | go `net.Listen` | `os.Remove` | 1 |
| beads#6 | FP | HANDLE | go `bufio.NewScanner` | `os.Remove` | 2 |
| cert-manager#5 | FP | HANDLE | go `os.Stdin` | `exec.CommandContext` | 1 |
| gocryptfs#3 | FP | HANDLE | go `os.Stdin` | `exec.Command` | 1 |

**Seven situations, eleven rows, zero true positives** — against a population
precision of 33.0%. Both rows that produced them have since been dealt with
independently: C's `socket` was removed by WI-dosov, and go's `net.Listen` was
retagged to `net_listen` by INV-kanuk. Re-running gocryptfs on this tree
confirms it: `untrusted-input-no-host-fs` is now `inconclusive` where 0006 had
it `violated`, which is the disclosure boundary's shadow doing exactly what
ADR-0049 ruling 2 clause 3 specifies.

**`ledger-final.json` is not final, and that nearly published a wrong table.**
It carries the blind pass's **44** TPs; 0006's headline **38** is what survives
pass 4's adversarial refutations, which live in a separate file. The first cut
of this arm asserted 38 against the file whose name says "final", failed, and
only then found `pass4-summary.json`. `tmux#3` is one of the seven refuted —
so it is an FP here and would have been a TP had the assertion not been written.
Filed as `WI-tuhop`, together with the fact that **ADR-0048 publishes 44/112 =
39.3% for 0006 while 0006 publishes 38/112 = 33.9%**.

## A register correction this arm found

**`ets:foldl/3` is a TRANSFER, not a callback-delivered deferred crossing.**
0009's register put it under `CALLBACK` with "the data goes to a function you
passed". It does — *and it returns the accumulator to its caller*, whose content
is computed by that function from the table's rows, which is ADR-0049 ruling 1's
YES branch. rabbitmq's own use makes the return load-bearing:

```
deps/rabbitmq_prometheus/.../prometheus_rabbitmq_core_metrics_collector.erl:640
  {Table, A1, A2, A3, A4} = ets:foldl(fun({_, Props}, {T, A1, A2, A3, A4}) -> ...
```

It is unlike `ftplib.FTP.retrbinary`, which returns the server's response
*status* and no data. Fifteen situations moved out of the family on this
correction — 23% of the raw join — and they are held out and reported here
rather than folded in, the way 0009 held its 14 misdeclared rows out.
`dets:traverse` is the same shape and is **not** re-checked here; it produced no
findings in this cohort. Filed on `WI-rivur`.

## The first CONFIGURED-ACTION findings this project has adjudicated

0006 reported **zero**, structurally: its claim set's TPs were all `env_read`,
and an environment variable reaches its sink through string handling, not
through a schema. ADR-0046 said "a cohort meant to exercise this class must
contain one". This cohort contains caddy, and it produced three — including
ADR-0046's own motivating example, now citable end to end:

| clause | citation |
|---|---|
| 1. declared-configuration source | `cmd/main.go:163` `if configFile == "-" { config, err = io.ReadAll(os.Stdin) }` |
| 2. schema deserialization | `caddy.go:344` `StrictUnmarshalJSON(strippedCfgJSON, &newCfg)` |
| 3. field-parameterized sink | `modules/logging/filewriter.go:87` `Filename string \`json:"filename,omitempty"\`` → `:223` `os.OpenFile(fw.Filename, …)`, `:239` `os.Chmod(fw.Filename, …)` |

All three are rooted at `os.Stdin` — a deferred-crossing HANDLE row. The class
ADR-0046 was written for is reached **through** the family this ADR proposes to
retag.

## What this licenses, and what it does not

**Licensed.** Retagging the `LAUNCH` shape, and the network-scoped `SETUP` shape
that goes with it, to `net_listen`. Every adjudicated launch-family finding in
both arms is a false positive (2 in arm 2, 4 in arm 1), the mechanism is the
same in all six, and no useful finding is at risk. ADR-0049 ruling 3's
represented-crossing proof must still be run per removal — this record does not
substitute for it.

**Not licensed.** Retagging the `HANDLE` shape. Eleven useful findings and all
three CONFIGURED-ACTION findings are rooted there, and in Go there is no
catalogued transfer row to relocate the crossing to. The correct sequence is the
reverse of the one the ADR implies: **catalogue the reads first**
(`bufio.Reader.ReadString`, `Scanner.Scan`/`Text`, `os.File.Read`, and their
peers in the other languages), measure that the crossing is still represented,
and only then consider the constructors.

**Neither, on this evidence.** `REGISTER`, `LAZY` and `CALLBACK`. They produced
**no findings at all** in this cohort — `Phoenix.Router` appears in one
repository, Django is absent, and `ets:foldl` turned out not to be a member.
Absence of findings is not evidence of harmlessness; it is absence of evidence,
and this record supplies none for those three shapes.

## Limitations, stated rather than left to be noticed

- **One adjudicator, single pass.** 0006's frame rule F7 — a blind pass plus an
  adversarial pass — is **not** reproduced. These labels are weaker than 0006's
  by construction. What partly replaces the second pass is that the refutation
  condition was written down before any label and then fired, and that every
  label carries the file and line a reader needs to disagree
  (`arm2_ledger.json`).
- **The launch evidence is six situations.** Unanimous, and thin. It is all the
  evidence 17 repositories contain, which is itself the finding.
- **Arm 2's 48.0% is not comparable to 0006's 33.9%.** Different denominator (a
  filtered census, not an equal-allocation draw), different adjudicator, and no
  refutation pass. It is reported to characterise the family, not to track the
  tool.
- **One sensitivity, disclosed.** cilium's two `bufio.NewScanner` TPs are
  labelled `KIND-MISDECLARED` because the scanner wraps `strings.NewReader` over
  a child process's stdout. A reader who holds that a subprocess pipe *is* an
  IPC channel would call them useful, moving useful precision from 22.0% to
  26.0%. Neither reading changes any conclusion above.
- **`WRONG-CHANNEL` is a second defect this record surfaces and does not
  measure.** `bufio.NewScanner` is declared `ipc_recv` unconditionally, but in
  this corpus it wraps a file or an in-memory buffer far more often than stdin.
  The boundary is a property of the ARGUMENT, which one row cannot express —
  INV-vaduk's dual-classification problem in a new place. Ten of arm 2's
  24 TPs are vacuous for that reason and not for ADR-0049's.

## Reproduction

```bash
cd ~/hypergumbo_lab_notebook/step3_adjudication_08302026
bash collect_all.sh            # 17 repos, ~20 min, writes flows/ and logs/
python3 family.py              # live family membership on the pinned tree
python3 join.py                # flows x family -> family_hits.json
python3 retro.py               # arm 1, from measurement 0006's own artifacts
python3 -c 'import ledger; ledger.score()'   # arm 2
python3 sites.py               # the launch site control
```

`ledger.py` asserts that the set of labels and the set of situations are
**equal** before it computes anything: a label for a situation that no longer
exists, or a situation with no label, is a hard error rather than a silently
smaller denominator.
