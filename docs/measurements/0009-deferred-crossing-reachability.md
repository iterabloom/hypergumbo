<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0009: Reachability of the deferred-crossing family

**Status:** Complete
**Date:** 2026-08-29
**Instruments:** [`scripts/measure-catalogue-reach.py`](../../scripts/measure-catalogue-reach.py)
(L2) plus hand-written per-idiom fixtures (L3); working files under
`~/hypergumbo_lab_notebook/vapud_reach_08292026/`
**Tracker:** `WI-vapud` (this measurement), `INV-kanuk` (the retag it gates),
`WI-hazop` (the census it consumes), `WI-lalot` / `INV-misup` (the two defects
it re-derives independently)

## The question

[ADR-0049](../adr/0049-deferred-crossings-are-disclosed-not-minted.md) ruled
that a call which merely *arranges* a crossing is disclosed, never minted, and
`WI-hazop` then counted the family: **131 rows**. `WI-vapud` asked the question
that decides what that count is worth:

> A row can be argued about for a whole panel while minting nothing.
> `flask.Flask.run` is the headline Python example in three separate write-ups
> — and it is in python's `ambiguous_names`, so a bare `app.run()` yields zero
> edges. How much of the family is equally inert?

Two decisions turn on the answer. **Priority:** if most of the family is inert,
the retag removes near-zero false-positive volume and is a correctness cleanup
competing with six P1s. **Correctness of any later A/B:** moving an inert row
measures nothing and reports a clean zero — the exact failure mode this family
keeps producing.

## Population

The **register**: every own row (inherited counted once, via `_CATALOG_PARENTS`)
under the three boundaries `WI-hazop` adjudicated to completion, re-adjudicated
per row against ADR-0049 ruling 1 and reconciled against the census's published
counts.

| boundary | own rows | transfer | **deferred** | misdeclared | census said | delta |
|---|---:|---:|---:|---:|---:|---:|
| `net_recv` | 138 | 54 | **83** | 1 | 80 | +3 |
| `db_read` | 130 | 91 | **36** | 3 | 38 | −2 |
| `ipc_recv` | 67 | 41 | **16** | 10 | 13 | +3 |
| **total** | **335** | **186** | **135** | **14** | **131** | **+4** |

Two reconciliation notes, both reported rather than absorbed:

- **`ipc_recv` has 67 own rows, not 64, and the corpus is 335, not 332.**
  `WI-hazop`'s summary table says 64/332; its own enumeration file says 67, and
  the production loader agrees with the enumeration. The summary was
  transcribed wrong. Every rate in that document with 332 as its denominator is
  off by that much.
- **The 14 `misdeclared` rows are held OUT of the family.** They fail for an
  unrelated reason — `ets.info` returns table metadata, `os.wait*` returns an
  exit status, `removeObserver` *un*-registers, `sqlite3.Connection.backup`
  writes. They are `WI-rivur`, not ADR-0049, and folding them in would inflate
  this family with a different bug.

**A verdict key that matches no shipped row is a hard error in the register
build**, not a warning: a typo would silently shrink the deferred set, which is
precisely the wrong-instrument failure this line of work exists to avoid. All
149 keys matched.

## Rubric

Fixed before labelling, from ADR-0049 ruling 1 verbatim:

> Does this call return — or write into a caller-visible location — a value
> whose content is chosen by the party on the far side of the boundary?

YES ⇒ **transfer**. NO — it opens, registers, subscribes, schedules or defers ⇒
**deferred crossing**. Per ruling 4, `accept` is a transfer and always was.

Reachability is then measured in **three layers**, reported separately because
they answer different questions and **they disagree**.

| layer | question | scope |
|---|---|---|
| **L1 gate** | can the row match *at all*, given a perfect module hint? | all 12 languages, 335 rows |
| **L2 probe** | does `measure-catalogue-reach.py`'s synthetic idiom attribute it? | python, go, javascript, java |
| **L3 idiom** | does the shape **real programs are written in** attribute it? | method/attribute rows in those 4 |

## Headline

**The family is live, and the reason it is live is not the reason L2 says.**

| layer | measured | fires | inert |
|---|---:|---:|---:|
| L1 gate (perfect module hint) | 135 | **134** (99.3%) | 1 |
| L1 gate (no module hint) | 135 | 37 (27.4%) | 98 |
| L2 synthetic probe | 78 | **72** (92.3%) | 6 |
| **L3 real idiom** | **62** | **45** (72.6%) | **16 + 1 partial** |

L1's transfer arm is the control — rows nobody proposes to move — and it scores
95.7% / 25.8%, statistically alongside the deferred arm. The gate does not treat
this family differently, which is what makes the L1 numbers believable and also
what makes them uninformative on their own.

**L2 and L3 disagree on 15 of 62 measured rows (24%), in both directions.** L3
wins every one of them, because L2's spelling for a method row is
`require('WebSocket')` and `Connection.prepareStatement(a, b)` — lines no
program contains.

| language | L3 measured | fires | inert | not measured |
|---|---:|---:|---:|---:|
| python | 22 | **22** | 0 | 9 |
| go | 24 | 17 | **7** | 0 |
| java | 8 | 5 | **3** | 0 |
| javascript | 8 | 1 | **6** (+1 partial) | 7 |
| elixir · erlang · objc · swift · rust · c · cpp | 0 | — | — | **57** |

## The filed premise was measured on the one form nobody writes

`WI-vapud` was filed on `flask.Flask.run` minting nothing. That is **true of the
untyped-receiver form and false of every form a Flask program is written in**:

| form | source | fires |
|---|---|---|
| A constructed | `flask.Flask(a).run(b)` | **yes** |
| B assigned | `app = flask.Flask(__name__)` … `app.run(...)` | **yes** |
| C untyped | `def f(app): app.run(...)` | no |

Across all 22 python deferred method rows: **A 22/22, B 22/22, C 0/22.** And
the twelve Django lazy combinators — whose catalogue module slot is
`django.db.models`, a receiver spelling no Django program uses — fire **12/12**
through the real `Thing.objects.filter(...)` idiom.

So the premise that motivated the "maybe it's all inert" worry does not hold for
Python at all. **Python's slice of the family is fully live.**

## Where it *is* inert, one mechanism explains three languages

A receiver typed from a **constructor** or a **declared parameter** resolves. A
receiver typed from an **external function's return value** does not resolve at
all — there is no declaration in the repo to register.

| language | fires | inert | the difference |
|---|---|---|---|
| go | `var r gin.Engine; r.Run()` | `r := gin.Default(); r.Run()` | return value of a library function |
| java | `Socket s = new Socket(h,p); s.getInputStream()` | `Connection c = DriverManager.getConnection(u); c.prepareStatement(s)` | constructor vs method return |

Both are **`WI-lalot`** — "receivers from library return values cannot be typed
from analysed source at all" — re-derived here from the opposite direction. The
consequence for this family is specific and large: **all seven Go third-party
framework launch rows (gin, fiber, echo, gRPC) are inert in real code**, because
every one of those frameworks is entered through a constructor call
(`gin.Default()`, `fiber.New()`, `echo.New()`, `grpc.NewServer()`).

JavaScript is a **separate** mechanism and already filed: six of its eight
deferred method rows are **`INV-misup`** — a constructor-bound receiver
(`ws = new WebSocket(url)`) never resolves, so the call becomes a `uses` edge
with a null `dst_ref` and the classifier never sees it. This measurement
re-derived the identical six rows from the deferred-crossing register, having
started from a completely different place than the investigation that filed it.

## Findings that did not already have a home

1. **`process` is absent from `JS_KNOWN_GLOBALS`.** Controlled, one file, no
   `require`: `localStorage.getItem` fires, `process.stdin` fires (through the
   separate `module_attr_ref` emitter), and **`process.on('message', h)` does
   not**. Adding an explicit `const process = require('process')` makes it fire.
   `process` is a true Node global, so the dominant spelling is the one that
   misses. This is *not* `INV-misup`'s constructor mechanism — it is a named
   global missing from a list.
2. **One row is dead at the catalogue gate itself**, with a perfect module hint
   and across every form: objc
   `NSURLConnection.sendAsynchronousRequest:queue:completionHandler:`. It is the
   only row of 335 in that state.
3. **ADR-0049 ruling 4's shape table is short by two.** The census found six
   shapes; the ruling names four. Measured across the register the distribution
   is far flatter than the ruling implies — LAZY 29, HANDLE 28, LAUNCH 25,
   REGISTER 23, SETUP 22, CALLBACK 8 — so "server launch" is not even the
   largest shape.

## What this does not support

- **It is not a real-code volume estimate.** Every number here comes from
  fixtures. "This row *can* fire" is not "this row *does* fire, often, in the
  corpus". The priority question is answered only in the direction that a retag
  is not moving dead rows.
- **57 of 135 deferred rows (42%) are unmeasured at L3**, and all of elixir,
  erlang, objc, swift, rust, c and cpp are unmeasured at both L2 and L3 —
  `measure-catalogue-reach.py` has no emitter for them. Their L1 gate results
  are reported and are a ceiling, not a reachability claim.
- **L3 covers method and attribute rows only.** Function-kind rows carry their
  module in the import, which the L2 fixture emits faithfully, so L2 and L3
  coincide for them — but that is an argument, not a measurement.
- **Eight L3 verdicts are marked `†` (same mechanism)**: TLS/variant siblings of
  a row that was run individually, resolved through the identical receiver path.
  That is an inference and is labelled as one.
- **Single-pass, non-blind adjudication** by one agent, reconciled against a
  prior independent census that disagrees by +4 rows. Both passes are the same
  kind of judgement and neither is ground truth.

## What it means for `INV-kanuk`

The retag is worth doing and is not moving dead rows — **but the tranche order
changes.** The rows that fire are not the rows the ruling's shape table
foregrounds:

- **Go stdlib SETUP + LAUNCH fires completely.** `net.Listen`, `syscall.{Socket,
  Bind,Listen}`, `unix.{Socket,Bind,Listen}` and `net/http.ListenAndServe` all
  produce chains from an ordinary accept loop, and in that same fixture
  `ln.Accept()` and `conn.Read()` produce **nothing** — so Go's entire
  `net_recv` surface on a real server is **eight false setup sources and zero
  true receives**. That is INV-kanuk's case, measured on a run rather than read
  off the catalogue.
- **Go's seven third-party framework rows should be retagged last or not yet.**
  They are inert until `WI-lalot` lands, so moving them changes no output.
- **Python is the largest live slice** — 22 of 22 method rows plus 9 function
  rows — and it is where a retag will actually move findings.

## Reproducing

```
~/hypergumbo_lab_notebook/vapud_reach_08292026/
  enumerate.py    -> rows_332.json   the 335-row population
  register.py     -> register.json   per-row verdict + shape (hard-fails on a dead key)
  gate.py         -> gate.json       L1, all 12 languages
  join.py         -> final.json      L2 joined on, join failures reported
  consolidate.py  -> verdict.json    L3 + the disagreement table
  idiom/          the L3 fixtures, one directory per form
```

## Per-row verdict table

`L1 hint` = matches with a perfect module hint · `L2 probe` = attributed by the
synthetic probe · `L3 idiom` = attributed in the real spelling (`†` = inferred
from an individually-run sibling, `—` = not measured).

### c — 1 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `stdio.stdin` | attribute | HANDLE | yes | — | — |

### cpp — 1 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `std.cin` | attribute | HANDLE | yes | — | — |

### elixir — 13 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `MyXQL.prepare` | function | HANDLE | yes | — | — |
| `Postgrex.prepare` | function | HANDLE | yes | — | — |
| `Ecto.Repo.stream` | function | LAZY | yes | — | — |
| `Phoenix.Router.delete` | function | REGISTER | yes | — | — |
| `Phoenix.Router.get` | function | REGISTER | yes | — | — |
| `Phoenix.Router.head` | function | REGISTER | yes | — | — |
| `Phoenix.Router.match` | function | REGISTER | yes | — | — |
| `Phoenix.Router.options` | function | REGISTER | yes | — | — |
| `Phoenix.Router.patch` | function | REGISTER | yes | — | — |
| `Phoenix.Router.post` | function | REGISTER | yes | — | — |
| `Phoenix.Router.put` | function | REGISTER | yes | — | — |
| `Phoenix.Router.resources` | function | REGISTER | yes | — | — |
| `Phoenix.Router.scope` | function | REGISTER | yes | — | — |

### erlang — 9 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `dets.foldl` | function | CALLBACK | yes | — | — |
| `dets.foldr` | function | CALLBACK | yes | — | — |
| `dets.traverse` | function | CALLBACK | yes | — | — |
| `ets.foldl` | function | CALLBACK | yes | — | — |
| `ets.foldr` | function | CALLBACK | yes | — | — |
| `dets.open_file` | function | HANDLE | yes | — | — |
| `httpd.start` | function | LAUNCH | yes | — | — |
| `httpd.start_service` | function | LAUNCH | yes | — | — |
| `ssl.handshake` | function | SETUP | yes | — | — |

### go — 24 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `bufio.NewReader` | function | HANDLE | yes | yes | fires |
| `bufio.NewScanner` | function | HANDLE | yes | yes | fires |
| `os.Stdin` | attribute | HANDLE | yes | yes | fires |
| `github.com/gin-gonic/gin.Engine.Run` | method | LAUNCH | yes | yes | **inert** |
| `github.com/gin-gonic/gin.Engine.RunTLS` | method | LAUNCH | yes | yes | **inert**† |
| `github.com/gofiber/fiber/v2.App.Listen` | method | LAUNCH | yes | no | **inert** |
| `github.com/gofiber/fiber/v2.App.ListenTLS` | method | LAUNCH | yes | no | **inert**† |
| `github.com/labstack/echo/v4.Echo.Start` | method | LAUNCH | yes | no | **inert** |
| `github.com/labstack/echo/v4.Echo.StartTLS` | method | LAUNCH | yes | no | **inert**† |
| `google.golang.org/grpc.Server.Serve` | method | LAUNCH | yes | yes | **inert** |
| `net/http.ListenAndServe` | function | LAUNCH | yes | yes | fires |
| `net/http.ListenAndServeTLS` | function | LAUNCH | yes | yes | fires† |
| `net/http.Serve` | function | LAUNCH | yes | yes | fires† |
| `net.Listen` | function | SETUP | yes | yes | fires |
| `net.ListenPacket` | function | SETUP | yes | yes | fires† |
| `net.ListenTCP` | function | SETUP | yes | yes | fires† |
| `net.ListenUDP` | function | SETUP | yes | yes | fires† |
| `net.ListenUnix` | function | SETUP | yes | yes | fires† |
| `syscall.Bind` | function | SETUP | yes | yes | fires |
| `syscall.Listen` | function | SETUP | yes | yes | fires |
| `syscall.Socket` | function | SETUP | yes | yes | fires |
| `unix.Bind` | function | SETUP | yes | yes | fires |
| `unix.Listen` | function | SETUP | yes | yes | fires |
| `unix.Socket` | function | SETUP | yes | yes | fires |

### haskell — 4 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `Network.Wai.Handler.Warp.run` | function | LAUNCH | yes | — | — |
| `Network.Wai.Handler.Warp.runEnv` | function | LAUNCH | yes | — | — |
| `Network.Wai.Handler.Warp.runSettings` | function | LAUNCH | yes | — | — |
| `Network.Wai.Handler.Warp.runTLS` | function | LAUNCH | yes | — | — |

### java — 8 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `java.lang.System.in` | attribute | HANDLE | yes | yes | **inert** |
| `java.net.Socket.getInputStream` | method | HANDLE | yes | yes | fires |
| `java.sql.Connection.prepareCall` | method | HANDLE | yes | yes | **inert** |
| `java.sql.Connection.prepareStatement` | method | HANDLE | yes | yes | **inert** |
| `jakarta.persistence.EntityManager.getReference` | method | LAZY | yes | no | fires |
| `jakarta.persistence.TypedQuery.getResultStream` | method | LAZY | yes | no | fires |
| `javax.persistence.EntityManager.getReference` | method | LAZY | yes | yes | fires† |
| `javax.persistence.TypedQuery.getResultStream` | method | LAZY | yes | yes | fires† |

### javascript — 15 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `process.stdin` | attribute | HANDLE | yes | yes | fires |
| `BroadcastChannel.addEventListener` | method | REGISTER | yes | yes | **inert** |
| `EventSource.addEventListener` | method | REGISTER | yes | yes | **inert** |
| `EventSource.onmessage` | method | REGISTER | yes | yes | **inert** |
| `WebSocket.addEventListener` | method | REGISTER | yes | yes | **inert** |
| `WebSocket.onclose` | method | REGISTER | yes | yes | **inert** |
| `WebSocket.onmessage` | method | REGISTER | yes | yes | **inert** |
| `process.on` | method | REGISTER | yes | yes | partial |
| `Deno.listen` | function | SETUP | yes | yes | — |
| `Deno.listenDatagram` | function | SETUP | yes | yes | — |
| `Deno.listenTls` | function | SETUP | yes | yes | — |
| `dgram.createSocket` | function | SETUP | yes | yes | — |
| `http.createServer` | function | SETUP | yes | yes | — |
| `https.createServer` | function | SETUP | yes | yes | — |
| `net.createServer` | function | SETUP | yes | yes | — |

### objc — 13 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `NSURLConnection.sendAsynchronousRequest:queue:completionHandler:` | method | CALLBACK | **NO** | — | — |
| `NSFetchRequest.fetchRequestWithEntityName:` | method | HANDLE | yes | — | — |
| `NSManagedObjectContext.objectWithID:` | method | LAZY | yes | — | — |
| `NSURLSession.dataTaskWithURL:` | method | LAZY | yes | — | — |
| `NSURLSession.dataTaskWithURL:completionHandler:` | method | LAZY | yes | — | — |
| `NSURLSession.downloadTaskWithRequest:` | method | LAZY | yes | — | — |
| `NSURLSession.downloadTaskWithRequest:completionHandler:` | method | LAZY | yes | — | — |
| `NSURLSession.downloadTaskWithResumeData:completionHandler:` | method | LAZY | yes | — | — |
| `NSURLSession.downloadTaskWithURL:` | method | LAZY | yes | — | — |
| `NSURLSession.downloadTaskWithURL:completionHandler:` | method | LAZY | yes | — | — |
| `NSNotificationCenter.addObserver:selector:name:object:` | method | REGISTER | yes | — | — |
| `NSNotificationCenter.addObserverForName:object:queue:usingBlock:` | method | REGISTER | yes | — | — |
| `NSPersistentStoreCoordinator.addPersistentStoreWithType:configuration:URL:options:error:` | method | SETUP | yes | — | — |

### python — 31 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `ftplib.FTP.retrbinary` | method | CALLBACK | yes | yes | fires |
| `ftplib.FTP.retrlines` | method | CALLBACK | yes | yes | fires |
| `dbm.open` | function | HANDLE | yes | yes | — |
| `shelve.open` | function | HANDLE | yes | yes | — |
| `shlex.shlex` | function | HANDLE | yes | yes | — |
| `sqlite3.connect` | function | HANDLE | yes | yes | — |
| `sys.stdin` | attribute | HANDLE | yes | yes | — |
| `aiohttp.web.run_app` | function | LAUNCH | yes | yes | — |
| `flask.Flask.run` | method | LAUNCH | yes | yes | fires |
| `http.server.HTTPServer.handle_request` | method | LAUNCH | yes | yes | fires |
| `http.server.HTTPServer.serve_forever` | method | LAUNCH | yes | yes | fires |
| `socketserver.TCPServer.handle_request` | method | LAUNCH | yes | yes | fires |
| `socketserver.TCPServer.serve_forever` | method | LAUNCH | yes | yes | fires |
| `uvicorn.run` | function | LAUNCH | yes | yes | — |
| `xmlrpc.server.SimpleXMLRPCServer.handle_request` | method | LAUNCH | yes | yes | fires |
| `xmlrpc.server.SimpleXMLRPCServer.serve_forever` | method | LAUNCH | yes | yes | fires |
| `django.db.models.all` | method | LAZY | yes | yes | fires |
| `django.db.models.annotate` | method | LAZY | yes | yes | fires |
| `django.db.models.distinct` | method | LAZY | yes | yes | fires |
| `django.db.models.exclude` | method | LAZY | yes | yes | fires |
| `django.db.models.filter` | method | LAZY | yes | yes | fires |
| `django.db.models.iterator` | method | LAZY | yes | yes | fires |
| `django.db.models.none` | method | LAZY | yes | yes | fires |
| `django.db.models.order_by` | method | LAZY | yes | yes | fires |
| `django.db.models.prefetch_related` | method | LAZY | yes | yes | fires |
| `django.db.models.select_related` | method | LAZY | yes | yes | fires |
| `django.db.models.values` | method | LAZY | yes | yes | fires |
| `django.db.models.values_list` | method | LAZY | yes | yes | fires |
| `sqlite3.Connection.iterdump` | method | LAZY | yes | yes | fires |
| `asyncio.start_server` | function | SETUP | yes | yes | — |
| `asyncio.start_unix_server` | function | SETUP | yes | yes | — |

### rust — 3 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `std::io.stdin` | function | HANDLE | yes | — | — |
| `std::io::Stdin.lines` | method | LAZY | yes | — | — |
| `std::net::TcpListener.incoming` | method | LAZY | yes | — | — |

### swift — 13 deferred rows

| row | kind | shape | L1 hint | L2 probe | L3 idiom |
|---|---|---|---|---|---|
| `ClientBootstrap.ClientBootstrap` | method | HANDLE | yes | — | — |
| `EventLoopGroup.MultiThreadedEventLoopGroup` | method | HANDLE | yes | — | — |
| `ModelContext.ModelContext` | method | HANDLE | yes | — | — |
| `NIOAsyncChannel.NIOAsyncChannel` | method | HANDLE | yes | — | — |
| `NIOWebSocketServerUpgrader.NIOWebSocketServerUpgrader` | method | HANDLE | yes | — | — |
| `NSFetchRequest.NSFetchRequest` | method | HANDLE | yes | — | — |
| `NWListener.NWListener` | method | HANDLE | yes | — | — |
| `ServerBootstrap.ServerBootstrap` | method | HANDLE | yes | — | — |
| `URLSession.downloadTask` | method | LAZY | yes | — | — |
| `NotificationCenter.addObserver` | method | REGISTER | yes | — | — |
| `WebSocket.onBinary` | method | REGISTER | yes | — | — |
| `WebSocket.onClose` | method | REGISTER | yes | — | — |
| `WebSocket.onText` | method | REGISTER | yes | — | — |
