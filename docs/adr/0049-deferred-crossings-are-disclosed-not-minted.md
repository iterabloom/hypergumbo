<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0049: A Deferred Crossing Is Disclosed, Not Minted (extends ADR-0016)

Status: Accepted
Date: 2026-08-29
Extends: ADR-0016 §"Implementation note" (INV-vavup), which ruled the outbound
direction of this same question.

## Context

`io_primitives` catalogue rows tag a library primitive with an I/O boundary.
`AUTO_SOURCE_LABEL_MAP` (`taint.py`) collapses `net_recv` / `ipc_recv` /
`db_read` into the taint label `untrusted_input`, so **a boundary tag doubles
as a taint-source declaration**: tag a row `net_recv` and a taint source is
minted at that call, whether or not that was intended.

Across the shipped catalogues, the call that *starts a server* is tagged
`net_recv` — `net/http.ListenAndServe`, `HTTPServer.serve_forever`,
`Warp.run`, `uvicorn.run`, `http.createServer`, `grpc.Server.Serve`,
`gin.Engine.Run`, `asyncio.start_server`, `Deno.listen`, `ServerBootstrap`,
`syscall.{Socket,Bind,Listen}`, and the `Phoenix.Router` route-registration
DSL, among others.

A server does receive. But the bytes reach the handler the runtime invokes,
not the caller of `run` / `serve_forever` / `createServer`, so the source is
attributed to a scope that never sees a request — and taint then flows forward
from there through everything reachable, which for a launch call is
approximately all of `main`.

**This question had been answered four times and ruled zero times.** INV-nular
F3 kept Django's lazy `QuerySet` because the execution has no call site; the
JavaScript rows were kept because arrival is a callback; INV-nular F5 kept
`Phoenix.Router` and called it a deliberate control; WI-joruz kept Warp's
launch rows while moving the Wai response constructors. Four derivations, same
argument, same verdict. A search of `docs/adr/` and `docs/audits/` for the
family returns nothing: the only governing text was a test docstring
(`TestServerLaunchStaysAReceive`) and a tracker discussion. Each
re-derivation was a fresh chance to derive it differently, and the cost was
real — INV-kanuk (P1) sat blocked behind a question that had no forum.

**The outbound twin of this question is already ruled.** ADR-0016's
implementation note (INV-vavup) forbids attributing a launched program's I/O
to the script that launched it, and installs `command_launch` as the
disclose-don't-resolve alternative. The measured consequence of getting it
wrong is in that ADR's own table: cataloguing `curl` as `net_send` moved a
cron-dropper claim from `inconclusive` rc 2 to **`confirmed` rc 0** — six
correct lines bought a green tick over a write into a root cron directory,
because since INV-buzab a classified call is what `examined` means.

Server launch is the same mis-attribution pointed inward. This ADR rules it.

## Decision

### Ruling 1 — the test

> **A boundary tag on a call asserts that the crossing happens at that call,
> in that call's scope.** The discriminator is one question:
>
> **Does this call return — or write into a caller-visible location — a value
> whose content is chosen by the party on the far side of the boundary?**
>
> If yes, it is a **transfer**. If no, and the call instead *opens, registers,
> subscribes to, schedules, or defers* a crossing whose data becomes available
> only in a scope the runtime enters later, it is a **deferred crossing**.

The test is not new law. It is what the tree already applied by hand:
Haskell's `gracefulClose` was removed from `net_recv` because "it really does
read … but returns unit, so no byte reaches the program"; WI-dosov removed
`socket` / `bind` / `listen` while deliberately keeping `accept`, "which
returns the peer address, network-controlled". Ruling 1 states the rule those
decisions were instances of, so the fifth encounter cites it instead of
re-deriving it.

### Ruling 2 — a deferred crossing is disclosed, never minted

A deferred crossing carries a **disclosure boundary**. A disclosure boundary:

1. is **absent** from `AUTO_SOURCE_LABEL_MAP` and `AUTO_SINK_ZONE_MAP` — it
   mints no taint;
2. is **disclosed in its own count**, not folded into the `total_io_edges`
   headline (the `_DISCLOSED_ONLY_BOUNDARIES` treatment); and
3. is **opacity-gating** — it records that the analysis cannot see past the
   call, so it withholds `confirmed` on a `must_not_exist` claim over the
   corresponding data boundary rather than licensing one.

Clause 3 is load-bearing and is the half that is easy to omit. Without it,
retagging a launch call still *classifies* it, so it still counts as examined
under INV-buzab while minting nothing — which is ADR-0016's `curl` row
reproduced exactly: a green tick over live ingress. **A disclosure boundary
that is not opacity-gating is a false-all-clear generator, and shipping one
would be worse than the status quo it replaces.**

`OPAQUE_BOUNDARIES` anticipates this membership. Its governing sentence is
"boundaries whose classification records that the analysis CANNOT SEE PAST the
call, rather than a known and complete I/O surface (INV-gahuz)", and its
docstring states that if a future boundary is added to
`CATALOG_BOUNDARY_TYPES` with that meaning, "it belongs here too, and the
axis-conformance tests are what will ask". A deferred-crossing boundary meets
the governing criterion. Note honestly that the docstring's *example* phrasing
is "control left this process", which is the outbound instance; the inbound
case ("data will arrive at a scope this call does not name") is covered by the
general sentence and not by the example. That extension is made here
deliberately, not by silent reading.

### Ruling 3 — removal is licensed only against a represented crossing

Removing or retagging a deferred-crossing row is licensed only once **either**
the arrival scope is represented as a source, **or** the disclosure boundary
is in place. Both halves land in the same change; neither ships alone. A
retag that leaves a webserver able to return `confirmed` for "never receives
data from the network" is a deletion of the crossing, not a relocation of it,
and is forbidden.

This is the F2/F3 licence rule, promoted from a test docstring to an ADR. It
is enforced by measurement, not by reasoning: WI-lunav established that a row
which "transmits nothing", whose every real counterpart is already catalogued,
can still be load-bearing — dropping `newManager` collapsed a real
`violated(3)` to `inconclusive(0)` because the true egress ran through an
uncatalogued combinator. **Test removal at the finding level, not the row
level.**

### Ruling 4 — the family is cut by mechanism, not by name

"Server launch" is not one shape, and ruling the shapes together would repeat
the `env_read` error INV-tutar closed (one boundary value carrying two
readings). Under Ruling 1 they separate cleanly:

| shape | example | returns to caller | verdict | rows |
|---|---|---|---|---:|
| Setup | `socket`, `bind`, `listen`, `net.Listen` | a descriptor | deferred crossing | 22 |
| Blocking serve loop | `ListenAndServe`, `serve_forever`, `Warp.run`, `uvicorn.run` | `error` / `None` / `()` | deferred crossing | 25 |
| Per-connection accept | `accept`, `net.Listener.Accept`, `TcpListener.accept` | **a peer-chosen address / connection** | **transfer — stays `net_recv`** | — |
| Route-registration DSL | `Phoenix.Router.{get,post,…}`, `addEventListener`, `process.on` | nothing; declarative | deferred crossing | 23 |
| **Handle constructor** | `sqlite3.connect`, `Connection.prepareStatement`, `bufio.NewReader`, `Socket.getInputStream`, `sys.stdin` | **a handle, not data** | deferred crossing | 28 |
| **Lazy / unexecuted** | Django's QuerySet combinators, `Ecto.Repo.stream`, `TypedQuery.getResultStream`, `EntityManager.getReference`, `TcpListener.incoming` | **a query or iterator that has run nothing** | deferred crossing | 29 |
| **Callback-delivered** | `ftplib.FTP.retrbinary`, `dets.traverse`, `ets.foldl` | **nothing; the data goes to a function you passed** | deferred crossing | 8 |

**The last three rows were added after the census** (`WI-hazop`) and the
reachability measurement ([0009](../measurements/0009-deferred-crossing-reachability.md))
enumerated the family per row.

**What the shapes are worth was then measured, and the table above does not
predict it** ([0010](../measurements/0010-deferred-crossing-findings-per-shape.md)).
Shape decides how a crossing is *arranged*; whether the mint lands in the right
scope is decided by whether the returned handle is **consumed in the scope that
built it**. `Handle` splits on exactly that line — `cmd.Stdin = os.Stdin` mints
where nothing is read (0 of 8 true), while `bufio.NewReader(os.Stdin)` followed
four lines later by `ReadString` mints where the read is (7 of 22 true *and*
useful; a real read is necessary and not sufficient, since the value still has
to reach the sink's argument rather than a y/N branch condition). `Setup` and `Blocking serve loop` do not split: their arrival scope is
always a function the runtime enters later, and every adjudicated finding
rooted in them is false. Two corrections the same measurement forced: the counts in this
table are **membership, not the number still minting** — eleven `Setup` rows
moved to `net_listen` with INV-kanuk and twenty-eight `Blocking serve loop`
rows moved with the launch retag, leaving javascript's `createServer`/`Deno`
rows and elixir's `Phoenix.Router` rows as the only members still declared
`net_recv`; and **`ets:foldl` is a transfer, not `Callback-delivered`** — it
returns the accumulator to its caller, unlike `ftplib.retrbinary`, which
returns a status. They are not new law — each fails Ruling 1
identically to the first two — but the original four-shape table implied
"server launch" was the centre of gravity and it is not: **Lazy and Handle are
the two largest shapes, and Setup + Blocking serve loop together are under
half the family.** The row counts are the register's, over the three
boundaries adjudicated to completion.

`accept` is not a member of the family and never was. WI-dosov had this right
in Haskell; the shipped Go rows carry `Accept` alongside `Socket`/`Bind`/
`Listen` as if they were the same thing, and they are not.

## Consequences

**The class is larger than the family that prompted this.** Ruling 1 applies
wherever a crossing is arranged at one call and arrives at another: framework
callbacks, lazy and deferred execution (Django's surviving lazy combinators,
`Ecto.Repo.stream`), queue consumers, event-listener registration, signal
handlers, scheduler entry points, completion-handler tasks, callback-driven
walks (`filepath.Walk`, `filelib.fold_files`), and non-querying handle
constructors (`sqlite3.connect`, `NSFetchRequest`, `Socket.getInputStream`).
A panel estimate put the full class at roughly 130 inbound rows against the
37 the prompting item named. **That number is an estimate and is not the
census.** See "Open work".

**Where a deferred crossing's arrival scope can be named, it should be
minted there.** The substrate already exists and is unwired: `start_at:
"callee"` seeds a source at a symbol rather than at its caller;
`dispatches_to` is already in `TAINT_CALL_EDGE_TYPES` (added by INV-zuhig for
exactly this reason); `route_handler.py` resolves routes to handler symbols;
`entrypoints.py` carries `HTTP_ROUTE` / `GO_HANDLER` / `WEBSOCKET_HANDLER` /
`EVENT_HANDLER` / `SCHEDULED_TASK` on a declared axis. What is missing is the
join: `taint.py` and `verify_claims.py` contain **zero** references to
entrypoints, `framework_role`, or route concepts, and every auto-derived
source is `start_at: "caller"`. Arrival-scope seeding is therefore a code
change, not a catalogue change, and it is **not** authorised by this ADR.

**Decisiveness is traded for not inventing flows.** Where no arrival scope
resolves, `verify-claims` will report `confirmed_with_caveats` where it
previously reported `violated`. That is a real loss and must be stated as one.
Under ADR-0046 the gain accrues to *useful* precision (the KIND-MISDECLARED
bucket), not to correctness precision, so the two headline numbers move
differently and any record of the change must say which moved.

## Implementation state

**Ruling 2's mechanism has shipped (WI-nosah) and thirty-nine catalogue rows
use it** — eleven Go connection-SETUP rows (INV-kanuk) plus the twenty-eight
server-LAUNCH rows measurement 0010 licensed, across go, python, haskell and
erlang. `net_listen` is a catalog-declarable boundary carrying all three
clauses: absent from `AUTO_SOURCE_LABEL_MAP`, disclosed in its own `net_listen_edges` count and
held out of the `total_io_edges` headline, and shadowing `net_recv` through
`DEFERRED_CROSSING_SHADOWS` so a listen site qualifies a clean `net_recv`
verdict and nothing else. `IO_BOUNDARIES_SCHEMA_VERSION` is `2.2`.

**The shadow is scoped, not routed through `OPAQUE_BOUNDARIES`,** and the
distinction is load-bearing. That set is TOTAL opacity — control left the
process for a program that could do anything — so membership would send every
server in every language to `inconclusive` on `fs_write` and `env_read`. A
deferred crossing is the opposite shape: we know exactly what we cannot see.
Clause 3's "over the corresponding data boundary" is that scoping, and it is
asserted as an absence (`net_listen not in OPAQUE_BOUNDARIES`) so a later tidy
cannot quietly widen it — every existing opacity test would stay green through
such a change.

**The middle row of ADR-0016's table is reproduced as a control** and it is what
makes clause 3 non-optional: with the shadow map emptied, the same edge and the
same claim yield a bare `confirmed` over a live listener, because the call is
still classified and therefore still counts as examined (INV-buzab).

**Eleven rows have moved and shipped output has changed.** INV-kanuk retagged
Go's connection-SETUP family — `net.Listen{,TCP,UDP,Unix,Packet}` and
`{syscall,unix}.{Socket,Bind,Listen}` — against a run-level represented-crossing
proof: eight `net_recv` chains on an idiomatic accept loop became seven
`net_listen` plus one, with nothing dropped. Measurement 0010 observes the
consequence on a real repository: gocryptfs's `untrusted-input-no-host-fs`
verdict moved from `violated` (measurement 0006, rooted at `net.Listen`) to
`inconclusive`, which is clause 3's shadow doing what it was built to do.

**The LAUNCH rows have now moved too, in four languages of six.**
`TestServerLaunchStaysAReceive` is `TestServerLaunchIsADeferredCrossing`; it
pins the new state and, separately, the two holdouts — because they are held
for *different* reasons and one assertion covering both is how the next retag
takes the wrong one with it. **JavaScript** is `F2_EXEMPT`: its whole reachable
`net_recv` surface is `{http,https,net}.createServer` and `Deno.listen`, and
the rows that would survive are INV-misup-unreachable, so the move would
relocate the receive to nothing. **`Phoenix.Router`** is held because step 3
produced *no adjudicated findings* for the route-registration shape, which is
absence of evidence rather than evidence of harmlessness.

**Ruling 3's proof was run per language, not argued.** An idiomatic server
probe in each affected language, before and after the move: go keeps
`net.Listener.Accept`, python keeps `socket.socket.accept`, haskell keeps
`Network.Socket.{accept,recv}`, erlang keeps `gen_tcp.{accept,recv}` — every
one still reporting a `net_recv` chain afterwards. The finding-level half is in
the repository's own suite: four `test_taint_recall_corpus.py` fixtures used
launch rows as their untrusted-input source and were re-pointed at genuine
transfers, with every assertion holding unchanged.

**The database family has its twin, and ruling 3 was applied with both halves
in one change (WI-fasap, 2026-09-07).** `db_compose` is the second
`deferred_crossing` value: catalogue-declarable, absent from
`AUTO_SOURCE_LABEL_MAP`, disclosed in its own `db_compose_edges` count outside
the headline (`IO_BOUNDARIES_SCHEMA_VERSION` 2.3), and shadowing `db_read`
through `DEFERRED_CROSSING_SHADOWS`. It is deliberately not named `db_query`:
that string is already the database-query linker's `call_kind` and means a
call that *executes* a query. The twenty-one Django QuerySet combinators —
ruling 4's Lazy row, kept under `db_read` by INV-nular F3 because "the
execution is an implicit `__iter__` with no call site to catalogue" — moved
to it, and the licence was measurement [0013](../measurements/0013-django-queryset-chain-delta.md):
28 of the 40 situations chain typing added were `Model.objects.filter(...).delete()`,
a source that observes nothing. **The retag alone would have deleted real
reads**, which is 0010's HANDLE finding again: a pre-estimate over the same
ledger found 62 pretix situations resting only on lazy sources, and a text scan
classed at least 12 of them as the QuerySet *evaluated in the scope that
composed it* (`for o in qs:`, `list(Model.objects.filter(...))`,
`for i in qs[:n]`). So the represented-crossing half is a producer change:
`py.py` now emits a `calls` edge to `django.db.models.__iter__` (`__aiter__`
for the async forms) at a `for`, a comprehension and the materialising builtins
(`list`/`tuple`/`set`/`frozenset`/`sorted`/`dict`/`enumerate`, refused when the
name is rebound), and `__getitem__` at an index subscript, with a slice
propagating the type instead; the overlay rows those three under `db_read`.
What the shadow discloses is the remaining case — a QuerySet returned, handed
to a form or a paginator — whose evaluation no call site in the composing
scope represents. The INV-buzab three-row table (open work 5) is in
`test_deferred_crossing_boundary.py::TestTheDatabaseThreeRowTable`, with one
honest wrinkle: the Django rows ship in the community overlay, whose rows are
`unvouched` under ADR-0047, so on the shipped tree a Django program's
boundary claims are `inconclusive` before clause 3 is consulted; the table is
run on a vouched copy, the population (a user's own overlay) for which the
shadow is the first line rather than the second. The other Lazy members of the
family (`TypedQuery.getResultStream`, `EntityManager.getReference`,
`sqlite3.Connection.iterdump`, the `NSURLSession` task rows) did NOT move: the
Django licence does not transfer, and each needs its own ruling-3 proof.

## Open work — sequencing and the evidence bar

Filed as tracker items, in order. **No row moves until 1–3 are done.**
**1, 2 and 3 are now done** (`WI-hazop`, measurement 0009, measurement 0010). 4 and 5 are per-removal obligations and are discharged by the change that moves a row, not once for the family — INV-kanuk discharged both for the eleven Go SETUP rows.

1. **A corrected census.** The prompting item claimed 37 rows across 9
   languages; its own enumeration sums to 52; three independent counts under
   three membership rules produced 48+15, 75/13, and ~60 of 138 `net_recv`
   names. Four numbers, no agreement — because there is no written membership
   rule to count against. Ruling 1 supplies one. The census is re-run against
   Ruling 1, with the production loader, all catalogues, parents and shipped
   overlays, case-insensitively.
2. **Reachability before adjudication.** ~~Every candidate row gets a fixture
   proving it emits a classifiable edge.~~ **DONE — `WI-vapud`, measurement
   [0009](../measurements/0009-deferred-crossing-reachability.md).** The family
   is **not** largely inert: **45 of 62 rows measured in real idiom fire
   (72.6%)**, and Python's slice is 22 of 22. The worry recorded here rested on
   `flask.Flask.run` yielding 0 edges — which is true only of an untyped
   receiver (`def f(app): app.run()`) and **false of both forms a Flask program
   is written in**. Where the family *is* inert the cause is not this ruling:
   go's seven third-party framework launch rows and java's `prepareStatement`
   are `WI-lalot` (a receiver typed from a library return value is not typed at
   all), and six javascript rows are `INV-misup`. Those rows should be retagged
   last, because moving them changes no output.
3. ~~**Adjudicated findings per shape.**~~ **DONE — measurement
   [0010](../measurements/0010-deferred-crossing-findings-per-shape.md).**
   **The ruling holds for LAUNCH and fails for HANDLE, and shape is not the
   discriminator — mechanism is.** Two arms over 0006's cohort plus caddy.

   | mechanism | situations | TP | useful |
   |---|---:|---:|---:|
   | `DEFERRED` — arrival really is in another scope | 3 | 0 | **0** |
   | `WIRING` — `cmd.Stdin = os.Stdin`, never read here | 8 | 0 | **0** |
   | `WRONG-CHANNEL` — wraps a file or a buffer, not the declared boundary | 17 | 0 | **0** |
   | `READ-IN-SCOPE` — the handle is read in the scope that built it | 22 | **7** | **7** |

   Three of the four mechanisms produce nothing true at all.

   **The refutation condition, pre-registered before labelling, fired seven
   times.** `bufio.NewReader(os.Stdin)` returns a handle and so fails ruling 1
   exactly as `ListenAndServe` does — but it is read four lines later in the
   same function, so "the scope the call does not name" *is* the caller's, and
   the mint is not a mis-attribution. Retagging it would delete a true, useful
   finding; and **Go's entire stdin surface is three deferred-crossing rows**
   (`os.Stdin`, `bufio.NewScanner`, `bufio.NewReader`) with no catalogued
   `ReadString`/`Scan`/`Text` to relocate the crossing to, so ruling 3 forbids
   the HANDLE retag on its own terms. **Catalogue the reads first.**

   **LAUNCH is unanimous and thin.** Every adjudicated launch-family finding in
   both arms is a false positive — 2 on this tree, 4 in 0006 (three C `socket`,
   one Go `net.Listen`) — and that is *all* the evidence seventeen repositories
   contain: an over-counting grep finds **49 files** with a launch-shaped call
   against **2 findings**, because jaeger returned `inconclusive` on all three
   `untrusted-input-*` claims and cilium's framework rows are `WI-lalot`-inert.
   So the retag is a **correctness fix worth about two findings**, not a
   precision fix, which is the priority calculus 0009 flagged.

   **`REGISTER`, `LAZY` and `CALLBACK` produced no findings at all** in this
   cohort. That is absence of evidence, not evidence of harmlessness, and this
   ADR's open work is not cleared for them.

   **Two claims previously in this step were wrong and are corrected.** 0006's
   corpus does **not** contain caddy — caddy was 0005's repository and 0006
   excluded 0005's five by construction, which is exactly why 0006 could report
   zero CONFIGURED-ACTION findings "structurally rather than by luck". And it is
   not true that zero launch-family findings appear among 0006's 112: **seven**
   of the 112 are family findings, three of them the C `socket` datapoint the
   same sentence went on to name.
4. **A represented-crossing proof per removal**, per Ruling 3 — run, not
   reasoned. If the fixture reports `inconclusive` afterward, that is a
   WI-lunav repeat and not a licence.
5. **An INV-buzab check on any disclosure boundary**, per Ruling 2 clause 3:
   reproduce ADR-0016's three-row table (absent / tagged / tagged+opaque). If
   the middle row confirms a claim the first row withheld, the design ships a
   false all-clear and must not merge.

**`io_boundary` is not a declared axis, and that is the root cause of the
forum problem.** It is absent from `_known_axes()` — no registry module, no
axiom, no drift linter — while ADR-0016 and `taint.py` both call it
"hypergumbo's canonical I/O-boundary risk taxonomy". A canonical taxonomy with
no axiom is the condition under which `env_read` split (INV-tutar), and it is
why this question had nowhere to be recorded. Declaring the axis is filed
separately; this ADR is a value-level ruling that does not wait on it.

## Alternatives rejected

- **Rule the launch call a receive and keep all rows.** Rejected: it ratifies
  a value that fails the Ruling 1 test, and measurement 0006 already carries
  an adjudicated verdict against `c` `socket`, so a family-wide keep would
  ratify a misdeclaration the project has already measured.
- **Delete the rows.** Rejected under Ruling 3 — unrepresented ingress.
- **Mint at the handler only, keeping the launch row where no handler
  resolves.** Rejected *as a ruling*, not as engineering: it makes the
  classification of a program depend on framework-detection coverage, so the
  same Go source classifies differently under two analyzer versions. Handler
  seeding is the right mechanism (see Consequences) but it is the
  re-representation half of Ruling 3, not a substitute for the rule.
- **Reuse `command_launch` directly.** Rejected: it is producer-stamped and
  deliberately disjoint from `CATALOG_BOUNDARY_TYPES`, while every affected
  row is catalogue-declared. Its *policy* is the precedent; its *vehicle* is
  not available.
