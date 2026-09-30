<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0021: the `logging` family — is a standard-stream write `logging` or `ipc_send`?

| | |
|---|---|
| **Axis** | io-boundary ([ADR-0050](../adr/0050-io-boundary-axis.md)), `data_crossing` section |
| **Date** | 2026-09-30 |
| **Closes** | ADR-0050 §"Open question"; unblocks WI-runos |
| **Methodology** | [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md), the four leakage tests of the Fundamental Concept Audit playbook §3 |
| **Artifact** | every shipped `io_primitives/*.yaml` and `io_primitives_overlays/*.yaml` at dev `544ac087ee`; `io-boundaries` on a 12-repo cohort at the same commit |
| **Outcome** | `logging` CANONICAL, redefined by its medium; `ipc_send` CANONICAL; 7 rows move `ipc_send` → `logging`; 5 adjacent membership defects filed, none moved here |

```yaml
kind: io_boundary_membership
axis: io-boundary
commit: 544ac087ee
values:
  - value: logging
    verdict: CANONICAL
    fired_test: mechanism_vs_category
    definition: >-
      Data written to the process's OWN standard output or standard error --
      the channel whose far side the launcher chose, not the program -- directly,
      or through a print or logging facility whose destination by default is one
      of those streams.
  - value: ipc_send
    verdict: CANONICAL
    fired_test: property_derivability
    definition: >-
      Data sent to another process over a channel the program itself set up or
      addressed -- a pipe, a child's stdin, a local socket, an IPC message
      channel, a signal.
moves:
  - {language: go,   module: os,               names: [Stdout, Stderr],    from: ipc_send, to: logging}
  - {language: java, module: java.lang.System, names: [out, err],          from: ipc_send, to: logging}
  - {language: cpp,  module: std,              names: [cout, cerr],        from: ipc_send, to: logging}
  - {language: cpp,  module: std,              names: [clog],              from: null,     to: logging}
kept_asymmetry:
  - {value: ipc_recv, rows: "stdin in every catalogue", reason: inbound standard input is launcher-chosen data, the untrusted_input source; there is no inbound logging}
filed_not_moved:
  - in-process pub/sub rowed ipc_send (objc NSNotificationCenter, swift NotificationCenter.post)
  - elixir BEAM message sends rowed ipc_send while erlang's are process_send
  - elixir Task.* spawns rowed ipc_send
  - community Oban.insert* rowed ipc_send (a database write)
  - logging-facility constructors / configuration rowed logging (python basicConfig / StreamHandler, go slog.New*Handler, elixir Logger.metadata / configure)
```

## Context

ADR-0050 recorded `logging` as the one `data_crossing` value naming a
**purpose** rather than a medium, overlapping `ipc_send` on stdout, and moved no
row: "answering it here would be exactly the undisciplined move the axis exists
to prevent". WI-runos (go, java and cpp still row their standard output streams
`ipc_send` while every other catalogue rows them `logging`) was a row move on
exactly that seam, so the owner ruled the audit comes first (2026-09-30).

The seam was not idle. The same write lands in two zones depending on the
language, and in two languages the zone it belongs to reads clean — measured on
dev with one-file repositories:

| file | `host_secret -> ipc` | `host_secret -> logging` |
|---|---|---|
| go `io.WriteString(os.Stderr, os.Getenv("API_KEY"))` | violated (sink `os.Stderr`) | violated (sink `io.WriteString`) |
| java `System.out.println(System.getenv("API_KEY"))` | violated (sink `System.out`) | **confirmed_with_caveats** |
| cpp `std::cerr << getenv("API_KEY")` | violated (sink `std.cerr`) | **confirmed** |
| python `print(k, file=sys.stderr)` | confirmed | violated |

## Methodology

ADR-0024's family-audit method, applied to a membership question rather than to
a value's existence. The `audit_verdicts` format does not fit, for two reasons:
its lifecycle predicates are bound to the three registries
`hypergumbo_core.audit_findings` knows (the io-boundary axis is not one), and the
question is which ROWS belong to a value, not whether a value stays. Hence this
sibling kind, `io_boundary_membership`.

## Step 1 — the suspicion

*`logging` and `ipc_send` are split by purpose on one side and medium on the
other, so a standard-stream write has no principled home and each catalogue
picked one.*

Falsifiable: it would be rejected if the rows under each value already sorted
by a single property, or if the split did not change any consumer's answer.

## Step 2 — inventory

Shipped catalogues, rows declared in each file (inherited rows not counted
twice): **202 `logging`, 63 `ipc_send`**; community overlays 9 and 3.

`logging`, by what the call does:

| population | rows (examples) |
|---|---|
| the standard stream itself, or a write on it | `sys.stdout`/`stderr` + their `write`/`writelines`/`flush`, `stdio.stdout`/`stderr`, `process.stdout`/`stderr`, rust `std::io::{stdout,stderr}` and the `write*` methods of `Stdout`/`Stderr`/`*Lock` |
| a print whose destination IS a standard stream | `print`, `fmt.Print*`, `putStrLn`, `IO.puts`, `io:format`, `console.*`, kotlin `println`, swift `print`, `traceback.print_*`, `argparse`/`optparse` `print_*`, `pprint`, `warnings.warn`, `System.Exit.die`, `Debug.Trace.*`, `typing.reveal_type`, the `sys.*hook` writers |
| a writer twin SELECTED by a `std_stream` stamp | go `os.File`/`bufio.Writer`/`io.WriteString`/`fmt.Fprint*`, python `typing.TextIO.write*`, haskell `hPutStr*` |
| a logging facility whose default destination is stderr | python `logging.*`, go `log.*` / `log/slog`, `java.util.logging.Logger`, elixir `Logger`, erlang `logger` / `error_logger`, `NSLog`, `os_log`, swift-log (overlay) |
| configuration of a facility, no write | python `basicConfig` / `StreamHandler`, go `slog.NewTextHandler` / `NewJSONHandler`, elixir `Logger.metadata` / `configure` |

`ipc_send`: pipe and child-stdin writes (c `unistd.write` pipe twin, go writer
pipe twins, rust `ChildStdin` / `PipeWriter`, python `multiprocessing` `Pipe` /
`Queue`), local sockets (c AF_UNIX twins, python `send_fds`), IPC channels (node
`process.send`, `BroadcastChannel.postMessage`, `NSDistributedNotificationCenter`),
signals (`os.kill`, `killpg`, `Child.kill`, `NSTask` / `Process` signalling) —
and the defects below: **go `os.Stdout`/`Stderr`, java `System.out`/`err`,
cpp `std::cout`/`cerr`**, in-process pub/sub, elixir BEAM sends and `Task.*`,
community `Oban.insert*`.

**How much of `ipc_send` is the seam, measured.** `io-boundaries` at
`544ac087ee` on 12 repositories chosen because they USE the go / java / cpp
stream rows (notation, docker-compose, alertmanager, runc, clover, jenkins,
okhttp, sbt, modsecurity, falco, apt, binaryen): **552 of 552 `ipc_send`
crossings are those stream attributes.** Every IPC finding in those
repositories is a stdout / stderr mention. The cohort is selected for the rows,
so this is not a corpus rate; it is what an operator of such a repository sees.
WI-tolif measured the python version of it on this repository in April: 70 of
77 `ipc_send` chains were `sys.stderr` writes.

## Step 3 — the four leakage tests on (`logging`, `ipc_send`)

1. **Property derivability — fires, and decides it.** Whether a write is a
   standard-stream write or a pipe write IS a property of the call site's
   target, and the code already derives it: `io_target_kind` stamps
   `std_stream` / `pipe` / `host_path` / `net_stream`, and
   `io_boundary._WRITE_TARGET_KIND_BOUNDARY` maps `std_stream -> logging`,
   `pipe -> ipc_send` (WI-suhug, 2026-09-03 — two days after ADR-0050). The
   property does not make either value redundant, any more than `host_path`
   makes `fs_write` redundant: it is the evidence that SELECTS the value. What
   it shows is that the two values are already separated by a medium property
   in the one place the code decides it from evidence, and the three outlier
   catalogues contradict that place.
2. **Apex/peer overloading — does not fire on the pair.** Neither value is used
   as the top of the other. Within `logging` a milder form exists: a logging
   facility's call rows the facility's *default* destination (stderr) although
   a configured handler may send it to a file or syslog. That is the call-site
   undecidability every writer row has (it is why the `call_site_undecidable`
   twins exist), not a second role for the value. Kept, with the trigger below.
3. **Construct vs relationship — does not fire.** Both name a crossing.
4. **Mechanism vs category — fires on the DESCRIPTION, not the membership.**
   The registry described `logging` by purpose ("emit data to a log sink").
   Read against its 202 rows, the membership is a medium: every row writes the
   process's own standard output / error, or is a facility whose default
   destination is one. So the fix is to the definition, not to the rows: the
   axis names media, and `logging`'s medium is **the channel whose far side the
   launcher chose**. `ipc_send`'s is a channel the program chose. That is the
   line ADR-0050 could not find, and it is the line the rows already follow in
   every catalogue but three.

## Verdicts

- **`logging` — CANONICAL**, redefined by its medium (definition in the block
  above). Not FOLD into `ipc_send`: that would reverse WI-tolif / WI-dutah over
  ~200 rows, re-create the 70-of-77 false-positive shape, and merge two claims
  users write separately — "secrets never reach logs" and "secrets never reach
  another process" — into one zone. `logging` is also one of the ten built-in
  trust zones users write claims against.
- **`ipc_send` — CANONICAL**, with the standard streams excluded by definition.
- **Moves (the membership consequence):** go `os.Stdout` / `os.Stderr`, java
  `java.lang.System.out` / `.err` (kotlin and scala inherit), cpp `std::cout` /
  `std::cerr` to `logging`; cpp `std::clog`, the third standard output stream
  and unrowed, gets a `logging` row.
- **Kept asymmetry:** standard INPUT stays `ipc_recv` everywhere. The inbound
  stream is launcher-chosen data, which is exactly why it mints
  `untrusted_input`; there is no inbound `logging`, and
  `_READ_TARGET_KIND_BOUNDARY` already maps `std_stream -> ipc_recv`.

## Step 4 — silent bugs

- The three outlier catalogues (`go.yaml`, `java.yaml`, `cpp.yaml`): a stdio
  write is a false `ipc` sink, and in java and cpp — where no writer row carries
  the write — the `logging` claim reads clean (table above). Measured.
- The registry and the ADRs described the question with an example that no
  longer exists: `io_boundary_types`' docstring gap 2 and ADR-0050 said
  "`stdout.write` is catalogued `ipc_send`", and ADR-0016's vocabulary table
  gives `stdout.write()` as `ipc_send`'s example. At `544ac087ee` no catalogue
  rows a stdout write CALL `ipc_send` (python's `sys.stdout.write` is `logging`;
  c's `unistd.write` `ipc_send` row is the pipe twin and its note excludes the
  standard streams); the only standard-stream rows under `ipc_send` are the
  stream ATTRIBUTES of the three outlier catalogues. The open question was being
  carried on an example that no longer existed.
- kotlin and scala FILES never reach `java.lang.System.out` / `.err`: their
  analyzers emit no attribute reference for the field (java emits
  `module_attr_ref`). The row is right for them and unreachable; a stdio write
  in a kotlin or scala file is seen by neither zone. Not a membership question;
  filed with WI-dorus's receiver typing, which would reach it from the call.

## Step 5 — adjacent sweep (filed, not moved)

Each is a membership defect under the same axiom, on the `ipc_send` side, found
while inventorying it. None changes the stream verdict, and moving any of them
is its own row question:

- **In-process pub/sub rowed `ipc_send`**: objc `NSNotificationCenter`
  `postNotification*`, swift `NotificationCenter.post`. No process boundary is
  crossed (their cross-process siblings `NSDistributedNotificationCenter` /
  `DistributedNotificationCenter` are rightly `ipc_send`).
- **Elixir BEAM message sends rowed `ipc_send`**: `GenServer.call` / `cast` /
  `abcast` / `multi_call`, `Process.send` / `send_after`. Erlang's `!` is
  `process_send`, whose registry description names exactly this case.
  Zero taint effect today (both map to the `ipc` zone), a headline effect.
- **Elixir `Task.*` spawns rowed `ipc_send`**: an execution spawn inside the
  runtime, not a data crossing.
- **Community `Oban.insert*` rowed `ipc_send`**: a job enqueue is a database
  insert.
- **Configuration rowed `logging`**: python `basicConfig` / `StreamHandler`,
  go `slog.NewTextHandler` / `NewJSONHandler`, elixir `Logger.metadata` /
  `configure` write nothing at the call; ADR-0049's clause ("not what the
  program is thereby arranged to do later") is the test. python's are
  deliberate for its `logging` completeness entry and say so.

## Keep, with its re-evaluation trigger

A logging facility's row names its **default** destination. A configured
`FileHandler` / `SysLogHandler` / file-backed zap core sends the same call to a
file or a socket, which this definition calls `logging`. Kept because the call
site cannot see the configuration, and the `logging` zone is what a user
reading "secrets never reach logs" means. **Re-open** if a consumer needs the
configured destination (a claim zone that separates "log file on disk" from
"terminal"), or if a catalogue starts stamping a handler's target kind the way
`io_target_kind` stamps a writer's.

## Related

- [ADR-0050](../adr/0050-io-boundary-axis.md) — the axis; its open question is answered here.
- [ADR-0016](../adr/0016-io-boundary-analysis.md), [ADR-0017](../adr/0017-taint-zone-dataflow.md) §2b — the vocabulary and the zone map.
- WI-tolif, WI-dutah — the python and c / js / rust stream moves this completes; WI-suhug, WI-nunab — the target-kind seam.
- WI-runos (the move), WI-dorus (java receiver typing), INV-hopib (a stream-object sink row matching a non-call use).
