<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0022: INV-nular remainder sweep, tranche 3 — catalogue rows checked against source

| | |
|---|---|
| **Axis** | io-boundary ([ADR-0050](../adr/0050-io-boundary-axis.md)): row membership, checked against each primitive's source |
| **Date** | 2026-10-03 |
| **Addresses** | WI-vafad (the INV-nular remainder sweep), tranche 3 of 3 |
| **Methodology** | WI-vafad's five layers: a mechanical probe first, a candidate pool, two blind source-citing workers per chunk (one neutral, one primed to refute), blind plants, a verifier |
| **Artifact** | `io_primitives/*.yaml` + `io_primitives_overlays/*.yaml` at dev `fe3c32259d`, loaded through `io_boundary.load_catalog(lang, include_defaults=True)` |
| **Outcome** | Tranche 3: 196 units reviewed by 10 workers, all 30 MISLABEL plants recovered; 6 candidates ACCEPTED by the verifier, 3 HELD, the rest REJECTED or already owned. Prior backlog: 31 still-shipped, never-filed flags re-verified, giving 13 ACCEPT, 7 HOLD, 11 REJECT. 11 fix items filed and 7 notes posted on existing items. **No catalogue row is edited here.** |

```yaml
kind: io_boundary_row_candidates
axis: io-boundary
commit: fe3c32259d
scope:
  census_rows_at_tip: 3624
  census_rows_without_rationale: 1339
  stratum_R_remainder_never_reached: {rows: 76, units: 41, note: "73 bash/c/cpp rows tranches 1-2 declared unreachable + 3 go x/sys/unix rows new since 2026-09-11"}
  stratum_N_added_or_repointed_since_2026_09_11: {rows: 259, keys: 207}
  units_total: 217
  units_in_pool: 196
  units_signal_consistent_not_pooled: 21
plants: {mislabel: 15, correct: 15, workers: 10, mislabel_recovered: "30 of 30", correct_flagged: "2 of 30 (both sys/socket.connect)"}
candidates:
  - id: T3-A1
    verdict: ACCEPT
    rows: {language: [c, cpp], module: stdio, names: [fseek, ftell, rewind, ungetc], current: fs_read, proposed: NONE}
    shape: removal
    agreement: "2/2 workers on all four"
    citation: "man/fseek.3.txt DESCRIPTION; man/ungetc.3.txt; c.yaml:39-40"
    filed_as: WI-paful-rudar-lijof-sopog-dujuf-bokik-lajob-dihaj
  - id: T3-A2
    verdict: ACCEPT
    rows: {language: [scala], module: scala.sys.process.Process, names: [apply, Process], current: "fs_write (simultaneous)", proposed: NONE}
    shape: removal of the fs_write half only
    agreement: "2/2"
    citation: "scala/src/library/scala/sys/process/Process.scala:57-102 (every apply overload returns a ProcessBuilder)"
    filed_as: WI-novap-tugaz-bovag-lobid-radok-gibov-sibog-zujut
  - id: T3-A3
    verdict: ACCEPT
    rows: {language: [go], module: google.golang.org/grpc, names: [NewClient], current: net_send, proposed: "relocate to the RPC send (ClientConn.Invoke / NewStream), then demote"}
    shape: re-point, community overlay
    agreement: "2/2"
    citation: "grpc-go/clientconn.go:183 (NewClient builds an IDLE ClientConn); :263-300 (Dial/DialContext = NewClient + exit idle)"
    filed_as: WI-pusah-dirit-tavof-mudiz-pufan-somug-pafom-zipov
  - id: T3-A4
    verdict: ACCEPT
    rows: {language: [python], module: subprocess.Popen, names: [terminate, kill, send_signal], current: subprocess, proposed: ipc_send}
    shape: re-point; found by the R6 parity check, NOT by a worker
    agreement: "0/2 -- both workers AGREED with subprocess, whose definition says 'launch or communicate with a child'"
    citation: "python.yaml:930-931 against python.yaml multiprocessing.Process kill/terminate (ipc_send, dated WI-dupok ruling) and INV-babiz (objc/swift terminate moved subprocess -> ipc_send)"
    filed_as: WI-jipip-kufif-linan-nitol-povit-gihun-monuv-ralih
  - id: T3-A5
    verdict: ACCEPT
    rows: {language: [go], module: "syscall + golang.org/x/sys/unix", names: [Read, Write, Pread, Pwrite, Fstat, Open, Openat, Sendto], current: "one boundary each", proposed: "c's shape: call_site_undecidable twins (INV-vaduk) / the mode seam for Open (WI-bulub)"}
    shape: re-shape, not remove
    agreement: "2/2 UNDECIDABLE_BY_ARGUMENT on Read/Write/Fstat/Open/Openat/Sendto"
    citation: "go-sys/unix syscall wrappers on an arbitrary fd; go.yaml:165-166, 252-253, 313-314; overlay go-x-sys-and-grpc.yaml:55-92; c.yaml unistd.read/write rows"
    filed_as: WI-pilis-risom-pugoj-pasas-nafin-vadad-jutak-vahuj
  - id: T3-A6
    verdict: ACCEPT
    rows: {registry: io_boundary_types.IO_BOUNDARY_TYPES, value: net_listen, current: "description begins 'Bind or accept: ...'", proposed: "drop 'or accept' -- ADR-0049 keeps accept under net_recv"}
    shape: vocabulary definition text (also rendered in docs/concept-axes.md)
    agreement: "both GO workers were MISLED by it (proposed unix.Accept -> net_listen, citing the definition)"
    citation: "io_boundary_types.py:366 against docs/adr/0049 table row 'Per-connection accept ... stays net_recv' and 'accept is not a member of the family and never was'"
    filed_as: WI-pipoj-vopit-rotav-piluj-buluk-sanul-zoros-zumoj
  - id: T3-H1
    verdict: HOLD
    rows: {language: [c, cpp, python], names: ["c wait/waitpid (subprocess)", "python os.wait/waitpid (ipc_recv)", "python Popen.wait/poll (subprocess)", "python multiprocessing join/is_alive (unrowed, disclosed)"]}
    question: "Is reaping or polling a child a launch, a receive, or nothing? The same act is rowed three ways."
    filed_as: WI-jipip-kufif-linan-nitol-povit-gihun-monuv-ralih (part B)
  - id: T3-H2
    verdict: HOLD
    rows: {language: [scala], module: scala.sys.process.Process, names: [apply, Process], current: subprocess}
    question: "A builder rowed subprocess: INV-dukam's Command half exactly. Blocked on taint through the builder receiver (go.yaml:396-403)."
    filed_as: note on INV-dukam-fadof-rivun-kupan-pahoz-vumuv-havos-zigim
  - id: T3-H3
    verdict: HOLD
    rows: {language: [elixir], module: Task, names: [async_stream], current: process_send}
    question: "A LAZY builder (ADR-0049 ruling 4's shape): the spawns happen on enumeration, which no row represents."
    filed_as: note on WI-tunog-gazot-zobot-sukuz-habuv-lazos-kufor-nigun
prior_backlog:
  flags_raised_by_tranches_1_2: 60
  still_shipped_at_tip: 41
  out_of_pass: {r6_refuted: 3, contested_owned_by_WI_lipis: 2, owned_by_INV_dukam: 1, builders_WI_lunav_or_WI_ziviv: 3, partly_fixed_platform_architecture: 1}
  verified: 31
  accept: {units: 13, filed_as: [WI-lasih-mijun-fomop-dijog-suvas-jotoz-gufab-nujan, WI-lotaz-zupuv-karol-jahih-horim-funid-bikik-jaduv, WI-foloh-guvig-lapat-pubok-humud-bigis-rulal-mafov, WI-tavir-nifij-bivuk-balal-pinog-rukol-vuhiv-bilip, WI-bavat-kabap-barun-gubop-vupij-jajiv-mahaj-diman, "note on WI-paful (objc/swift seek)"]}
  hold: 7
  reject: 11
```

## Context

INV-nular's 2026-09-06 re-scope declared the catalogue's name-bound remainder
UNVERIFIED and disclosed its size on every `verify-claims` run; WI-vafad drains
it. Tranche 1 (2026-09-11, python + rust, 335 rows) and tranche 2 (2026-09-13,
eight git-hosted languages + objc/swift, 1,079 rows) covered 1,414 of the
1,487 rows that carried no written rationale on 2026-09-11. Neither filed most
of its flags: tranche 1 filed 4 of 25, tranche 2 checked the tracker and filed
its row families, but its apple flags "still need this same pass before
filing". Since then the catalogue moved: the stage-2 catalogue PRs (#1408,
#1415, #1417) re-pointed and added rows, and ADR-0059 re-keyed many rows
method↔function. The owner ran this item last in stage 2 so it would sweep
the corrected rows.

## Scope, re-taken at run time

The census reproduces tranche 1's row shape through the production loader, so
the two diff. Matching on `(lang, module, name, boundary)` and IGNORING the
kind slot matters: a kind-keyed diff reported 67 "never reviewed" rows that
ADR-0059 had only re-keyed.

| | rows | units |
|---|---|---|
| census at `fe3c32259d` | 3,624 (1,339 without rationale) | |
| without rationale AND already in the 09-11 remainder | 1,336 | |
| **R** — bash/c/cpp remainder tranches 1-2 could not reach, + 3 new go x/sys/unix rows | 76 | 41 |
| **N** — keys present now and absent on 09-11 (added or re-pointed since) | 259 | 207 keys |
| reviewed here (units = (family, module, name, kind); the worker sees every declared boundary) | 335 | 217 |

**Not in scope:** the ~2,000 rows that carried a rationale on 09-11 and have not
moved since. Tranches 1-2 excluded them too. `notes` is a proxy, so that
population is unverified, not verified.

## Methodology

Pre-registered before any worker ran (PLAN-T3.md, lab notebook
`vafad_t3_10032026/`, sha256 `70d4da58…`).

1. **Probe, no model.** Tranche 2's resolvers and signal vocabulary reused
   verbatim, plus tranche 1's python (inspect) and rust (module path first)
   resolvers, plus roots for the sources vendored for this tranche (glibc,
   libstdc++, django, defusedcsv, grpc-go, oban and mdn/content, from
   github.com only; Linux man-pages from disk) and a fallback for python
   3.12's frozen `os`/`posixpath`. The probe never grants: a unit counts as
   SIGNAL_CONSISTENT only when every boundary under review shows its medium.
   Buckets: CONSISTENT 21, NO_SIGNAL 85, MISMATCH 19, UNRESOLVED 19,
   ABSTRACT_DECL 2, NO_MECHANICAL_RESOLVER 71 (c/cpp/bash syscall wrappers, the
   browser DOM, apple). **Pool = everything except CONSISTENT = 196 units.**
   Unlike tranches 1-2, UNRESOLVED / ABSTRACT / NATIVE units went to the
   workers instead of being refused, because the remaining UNRESOLVED units
   were gaps in the orchestrator's resolvers. That over-collects.
2. **Workers.** Five chunks (C, PY, GO, MIX1 = beam+haskell+rust, MIX2 =
   jvm+apple+javascript). Each chunk got TWO general-purpose sonnet workers,
   one NEUTRAL and one REFUTER, at most 3 at a time. They were read-only, with
   no network and no tracker, and never saw the catalogue notes, the ADRs or
   the plant key. Labels: the 19 catalogue-declarable io-boundary values with
   their registry definitions, plus NONE and UNDECIDABLE_BY_ARGUMENT. Every
   verdict needed a `path:line` citation.
3. **Blind plants.** 30 in total, 6 per chunk. The item's named historical
   wrong rows were planted at their pre-fix values: c `sys/socket.bind` /
   `listen` net_recv, python `urllib.request.Request` net_send and
   `os.path.abspath` fs_read, go `net.Listen` net_recv, erlang
   `io_lib:format` logging, haskell `Data.IORef.readIORef` db_read, elixir
   `Phoenix.Controller.json` net_recv, and javascript `path.join` fs_read.
   Alongside them went boundary-flip plants and 3 CORRECT plants per chunk.
   Every plant's implementation was checked present in the vendored trees
   first, which was the uncatchable-plant fault of tranches 1 and 2.
4. **Scoring.** R1 is the item's literal rule (any missed catchable plant
   discards the worker). R2 counts false flags. R3 checks the citation on
   EVERY verdict, not only on disagreements. R6 checks each flag against the
   row's own notes, its module's sibling rows, the ADRs and the tracker. R7
   adds an anchored-citation diagnostic: does the cited span name the
   primitive?
5. **Verifier.** The orchestrator read every T3 flag against its citation, the
   row's notes and its cross-language analogues, and gave each one ACCEPT,
   REJECT or HOLD. A separate read-only opus verifier did the same for the 31
   prior-tranche flags that still ship unowned.

## Worker scores

| chunk | worker | MISLABEL caught | CORRECT flagged | citations ok (R3) | wall time |
|---|---|---|---|---|---|
| MIX1 (mini-trial) | neutral / refuter | 3/3, 3/3 | 0/3, 0/3 | 33/33, 33/33 | 101 s / 92 s |
| C | neutral / refuter | 3/3, 3/3 | 1/3, 1/3 | 56/56, 56/56 | 93 s / 102 s |
| PY | neutral / refuter | 3/3, 3/3 | 0/3, 0/3 | 52/52, 52/52 | 124 s / 136 s |
| GO | neutral / refuter | 3/3, 3/3 | 0/3, 0/3 | 52/52*, 52/52 | 93 s / 88 s |
| MIX2 | neutral / refuter | 3/3, 3/3 | 0/3, 0/3 | 53/53, 53/53 | 130 s / 171 s |

**Plant recall: 30 of 30 MISLABEL plants recovered by the 10 surviving
workers.** All 10 workers pass R1 and R2, so no chunk was re-run. The two
CORRECT-plant flags are both `sys/socket.connect` net_send, which both C
workers called NONE because connect sends no payload. That plant choice is
arguable (WI-dosov's connection-lifecycle family) and is reported here rather
than excused. \*The GO neutral worker said it wrote three citations without
opening the exact line (bufio.go ~682 / ~762, a walk reference). Those 9
verdicts were discarded row by row under R3. None of them was a disagreement.

Pool verdicts, both workers: AGREE 248, UNDECIDABLE_BY_ARGUMENT 140, DISAGREE
44, UNREADABLE 14. The UNREADABLE verdicts are NSFetchRequest (CoreData) and
NWListener (Network.framework), which have no open implementation; Swift
`CommandLine.arguments`, because the Swift stdlib was not vendored; `_csv` and
`sqlite3.Connection.backup`, which are C-implemented; and `Req.Request.run_request`,
which **does not exist** in the vendored Req: both workers found it only in
`mix.exs` / `CHANGELOG.md`. That last one is a row naming a function this
version of the library does not define. It is a community-overlay row and is
reported here, not filed.

## Candidates (tranche 3)

Each ACCEPT is a candidate for its OWN fix item. It pays ADR-0049 ruling 3
there: a represented-crossing proof at the finding level, per language, both
arms cold, every moved verdict read back, the direction guard, and no pinned
removal.

**T3-A1 — c/cpp `stdio.{fseek, ftell, rewind, ungetc}` under fs_read.** None of
the four reads data. They move or report a stream position, or push a character
back into the buffer. Parity: rust.yaml:70 and :186 leave seek/rewind out ("a
syscall, but nothing crosses"), and python.yaml:2062 leaves seek/tell unrowed.
The reads that follow stay represented by c.yaml:53 [fread, fgets, fgetc,
fscanf, getc, getline]. fs_read mints nothing, so no taint verdict moves. The
effect is on io-boundaries counts and on what `examined` covers. Sibling
outside scope: haskell.yaml:133 rows `hSeek / hTell / hIsEOF / hReady /
hFileSize` under fs_read.

**T3-A2 — scala `Process.{apply, Process}` under fs_write (`simultaneous`).**
Every `apply` overload returns a `ProcessBuilder`, and nothing launches until
`run` / `!` / `!!`. Even once launched, the child's file writes belong to the
subprocess boundary's OPACITY, which withholds a clean verdict on every
boundary; they are not an fs_write crossing at this call. **Wider effect:** the
`IoPrimitive` docstring in io_boundary.py uses exactly this row as its
canonical example of SIMULTANEOUSLY TRUE ("`scala.sys.process.Process.apply`
both launches a program and (through it) writes files"). That example's premise
is false. The subprocess half is T3-H2.

**T3-A3 — go grpc `NewClient` under net_send (community overlay).** It builds
a `ClientConn` in idle mode and connects to nothing. This is a member of
INV-gujoh's constructor-under-net_send family that its 2026-09-08 scan did not
reach, because the row lives in an overlay rather than the base catalogue. It
**cannot simply be removed.** The overlay rows nothing but
`Dial/DialContext/NewClient`, so a gRPC client's actual sends (`Invoke`,
`NewStream`) would be represented nowhere. The fix is to row the send first and
then demote the constructor.

**T3-A4 — python `subprocess.Popen.{terminate, kill, send_signal}` under
subprocess.** Signalling an already-running child is ipc_send under the dated
WI-dupok ruling. Python's own `multiprocessing.Process.kill / terminate` rows
follow it, so does rust `Child.kill`, and INV-babiz moved objc/swift
`terminate / interrupt` out of subprocess for the same reason. The Popen rows
are out of parity with their own catalogue. **Provenance:** no worker flagged
this. Both labels' definitions claim signals ("launch or COMMUNICATE with a
child" against "... a signal"), so a reader blind to the ruling cannot find it.
The R6 parity read found it. Sibling: WI-gurud (elixir `Port.command`).

**T3-A5 — go raw-fd syscalls rowed one boundary each.** `syscall.*` and its
x/sys/unix overlay copy row `Read / Pread / Fstat / Open / Openat` fs_read,
`Write / Pwrite` fs_write and `Sendto` net_send, all unconditionally. c rows the
same syscalls `call_site_undecidable` (unistd.read/write, INV-vaduk) or under
the mode seam (fcntl.open, WI-bulub). A `Read` on a socket fd is a net_recv
these rows cannot express, so this is a RECALL gap on untrusted_input claims
(the false-all-clear direction), not only a label nit.

**T3-A6 — the net_listen DEFINITION.** `io_boundary_types.py:366` (rendered into
`docs/concept-axes.md`) defines net_listen as "Bind or accept: ...". ADR-0049
says "`accept` is not a member of the family and never was", and its table keeps
per-connection accept under net_recv. Both GO workers read the registry
definition and proposed `unix.Accept → net_listen`. The definition misleads
anyone who adjudicates from it, and this sweep's workers are exactly that
reader.

**Held (design questions, not row fixes).**

- **T3-H1:** reaping or polling a child is rowed three ways. c `wait/waitpid`
  is subprocess. python `os.wait/waitpid` is ipc_recv, with the note "exit
  status - data received from another process" (python.yaml:908). python
  `Popen.wait/poll` is subprocess. python `multiprocessing` `join/is_alive` is
  deliberately unrowed (python.yaml:2290).
- **T3-H2:** scala `Process.apply` under subprocess is INV-dukam's Command half
  in another language. It is blocked on the same receiver-taint prerequisite
  (go.yaml:396-403).
- **T3-H3:** elixir `Task.async_stream` builds a lazy stream (task.ex:732-749).
  The spawns happen on enumeration, which no row represents. This is ADR-0049
  ruling 4's lazy shape without a vocabulary value.

**Rejected after verification:**

| flag | agreement | reason |
|---|---|---|
| go `unix.Accept/Accept4` net_recv → net_listen | 2/2 | ADR-0049: accept is a transfer and stays net_recv (go.yaml:332, :363). Both workers were misled by the definition (T3-A6). |
| swift `ServerBootstrap`, `NIOWebSocketServerUpgrader` net_listen → NONE | 2/2 | dated ruling (swift-nio-and-log.yaml:69-113, ADR-0049 SETUP / REGISTER); its premise holds |
| elixir `Task.start` process_send → NONE | 2/2 | the label names Control.Concurrent (a spawn); erlang spawn/spawn_link rowed alike; a spawn copies the closure's data into the new process |
| c `exec*` subprocess → NONE | refuter only | the subprocess opacity rationale is exactly exec; python os.exec* rowed alike (python.yaml:1009) |
| c `open/openat` fs_read → NONE | refuter only | the mode-seam row (WI-bulub, c.yaml:43) |
| c `opendir` fs_read → NONE | refuter only | the handle convention: fopen sits in the same unconditional row; python os.scandir is fs_read |
| c `getpid` host_info_read → NONE | neutral only | host_info_read includes identity; python.yaml:1080 says getpid would be "an ordinary row" there |
| go `grpc.Dial/DialContext` net_send → NONE | refuter only | these start connecting (exit idle); go net.Dial and c connect are rowed net_send |
| go `os.Stdout/Stderr` logging → NONE | refuter only | audit 0021 moved exactly these rows; an attribute row is how a stream reference is rowed (ADR-0059) |
| java `PrintStream.checkError` logging → NONE | refuter only | the WI-dorus note: rows apply only under a std_stream stamp, and "checkError flushes" |

**Already owned (corroboration, not new):** python `csv.DictWriter` /
defusedcsv / `_csv.Writer` fs_write. 8 rows drew 2/2 UNDECIDABLE_BY_ARGUMENT,
and WI-munab owns them ("a write into a caller-supplied object ... is fs_write
even wh...").

## Prior-tranche flags still shipped (re-verified)

Tranches 1-2 raised 60 flags. At tip, 41 rows still ship as they were flagged
and 19 have moved since. 10 of the 41 are out of this pass: 3 were refuted
under R6 in tranche 2 (`application.set_env`, `Properties.propOrNone`,
`System.getProperties`); 2 were contested and are owned by WI-lipis
(`io.ReadAll`, `io.Copy`); 1 is owned by INV-dukam (`os/exec.CommandContext`);
3 are builder rows that WI-lunav kept or WI-ziviv owns (`NSMutableURLRequest`
`requestWithURL:` / `initWithURL:` / `setHTTPMethod:`); and
`platform.architecture` gained its `subprocess` row (both rows are now
declared). **The remaining 31 had no tracker owner.** A read-only opus
verifier re-checked each against its citation, the row's notes, the ADRs and
cross-language parity. The orchestrator spot-checked its ACCEPTs and
downgraded one (B030) under R6.

| result | flags | filed as |
|---|---|---|
| **ACCEPT, NONE**: python `tempfile.gettempprefix` (returns the literal "tmp"), python `inspect.getfile` (reads `__file__`), java `Path.toFile` (`new File(toString())`) | 3 units / 5 rows | WI-lasih |
| **ACCEPT, NONE**: python `asyncio.StreamWriter.close`, go `net/smtp.Client.Close` (close declared as a send: INV-nular F4 again) | 2 / 2 | WI-lotaz |
| **ACCEPT, call_site_undecidable**: python `asyncio.StreamReader.read` / `readuntil` (the same reader is fed by subprocess pipes and AF_UNIX sockets) | 2 / 2 | WI-foloh |
| **ACCEPT, NONE**: objc `NSNotificationCenter` `addObserver:selector:name:object:` / `addObserverForName:object:queue:usingBlock:` (in-process registrar; the receive half of WI-povom) | 2 / 2 | WI-tavir |
| **ACCEPT, NONE**: erlang/elixir `proc_lib.hibernate`, `global.whereis_name` (no message sent) | 2 / 4 | WI-bavat |
| **ACCEPT, NONE**: objc `NSFileHandle.seekToFileOffset:`, swift `FileHandle.seekToEnd` (lseek only; the fs_write rows are wrong) | 2 / 2 | note on WI-paful (same family as T3-A1) |
| **HOLD**: python `fileinput.input`, `importlib.resources.files` (lazy: the reads happen on unrowed later calls); rust `io::stdin()` (handle shape; ruling 3 needs a run proof); swift `FileHandle.fileHandleFor{Reading,Writing}` (MIS-KEYED: in the reimplementation these names exist only as `Pipe`'s stored properties); haskell `Network.HTTP.Req.runReq` (removal would leave `reqBr` / `reqCb` / `req'` egress unrepresented); java `HttpURLConnection.setRequestMethod` (verifier ACCEPT, downgraded: a sink-side builder setter, which is WI-lunav's argument-position question, the java twin of objc `setHTTPMethod:`) | 7 / 9 | not filed |
| **REJECT**: `filecmp.dircmp.report*` x3 (they lazily run listdir / stat / compare; worker B008's mechanism was false); `importlib.import_module` (fs_read vs no-I/O is not a two-boundary split; dated note); `tempfile.gettempdir` (host_info_read under INV-zuhat; its first-call write probe could justify an ADDED fs_write, not a swap); `ElementTree.parse` (the File-object rule); `platform.mac_ver` / `libc_ver` / `python_version`, `locale.getdefaultlocale` (host_info_read is defined by WHAT is read); objc `NSURLSession.downloadTaskWithResumeData:` (the cited code is a reimplementation's unsupported-path stub; ADR-0049 holds the task rows) | 11 / 11 | not filed |

The opus verifier also noticed four rows it did not adjudicate: the swift
`NotificationCenter.addObserver` twin (named on WI-tavir); objc
`NSMutableURLRequest setHTTPMethod:` (the B030 twin, WI-ziviv);
`platform.python_implementation` / `python_version_tuple`, which are unrowed
although they are the same kind of call as `python_version`; and a stale
tempfile completeness note that still says "rowed under env_read" (named on
WI-lasih).

## Pre-registered predictions

- **P1:** stratum N's rate would be lower than tranche 2's 7.8% per unit.
  **HELD.** Worker-flag ACCEPTs from N: T3-A2 (1 unit), T3-A3 (1), T3-A5 (6
  overlay units), so 8 of 158 = 5.1%. Counting T3-A4, which the R6 parity read
  found and no worker did, gives 11 of 158 = 7.0%. Still lower.
- **P2:** stratum R (libc/POSIX) would have the lowest rate. **REFUTED.**
  T3-A1 is 4 of its 38 units (10.5%), the highest of any stratum. As in
  tranche 2, the defects sit in what the row actually contains (stream
  POSITION calls swept into an fs_read row) and not in how ambiguous the I/O
  is.
- **P3:** multi-boundary units would draw UNDECIDABLE, i.e. agreement with a
  `call_site_undecidable` declaration. **HELD.** Of 59 multi-boundary
  assertions, 42 were UNDECIDABLE from both workers, 13 AGREE from both, and 4
  drew a DISAGREE (`open/openat` refuter-only, rejected; scala `Process` 2/2,
  T3-A2 / T3-H2).

## What this does not show

- **Recall outside the pool.** The 21 SIGNAL_CONSISTENT units were not read by
  any worker. A regex-consistent row can still be wrong (`urllib.request.localhost`
  is net_recv "consistent" because its body calls `gethostbyname`).
- **Worker independence, fully.** All workers wrote into one shared directory.
  The PY refuter ran while the PY neutral worker's file existed there. 0 of 55
  mechanism sentences match between the pair, and the simultaneous MIX1 pair
  agrees 100% on verdicts as well. That argues against copying. It does not
  rule copying out. Per-worker directories would close it.
- **That a cited span shows the mechanism.** R3 checks that the file and line
  exist. The anchored diagnostic (does the span ±6 lines name the primitive?)
  reads 33/33 and 31/33 (MIX1) and 48/56 and 50/56 (C). The C misses are
  generic exec(3)/wait(2) DESCRIPTION paragraphs, not fabrications. Flagged
  rows were re-read by the verifier. AGREE rows were not.
- **Apple contract.** swift-corelibs-foundation is a reimplementation. Apple
  verdicts evidence the mechanism, not Apple's shipping behaviour.
- **INV-nular's disclosure count.** It still counts rows with `notes`. Nothing
  records adjudication per row (the 2026-09-08 caveat on this item), so this
  sweep cannot move "N of M adjudicated" without a per-row adjudication record.
  That gap is unchanged.

## Related

- [ADR-0049](../adr/0049-deferred-crossings-are-disclosed-not-minted.md): crossings, the represented-crossing rule (ruling 3), the accept ruling
- [ADR-0050](../adr/0050-io-boundary-axis.md): the io-boundary axis
- [Audit 0021](0021-logging-family.md): logging vs ipc_send, the moved stream rows
- INV-nular (the re-scope and its disclosure), WI-vafad (this sweep), INV-gujoh (constructors under transfer boundaries), INV-dukam (go builders under subprocess), INV-babiz / WI-dupok (signals are ipc_send), WI-gurud (elixir Port.command), WI-munab (writes into caller-supplied objects)
- Lab record: `~/hypergumbo_lab_notebook/vafad_t3_10032026/` (PLAN-T3.md, OBSERVATIONS-T3.md, instruments/, verdict files)
