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

| shape | example | returns to caller | verdict |
|---|---|---|---|
| Setup | `socket`, `bind`, `listen` | a descriptor | deferred crossing |
| Blocking serve loop | `ListenAndServe`, `serve_forever`, `Warp.run`, `uvicorn.run` | `error` / `None` / `()` | deferred crossing |
| Per-connection accept | `accept`, `net.Listener.Accept`, `TcpListener.accept` | **a peer-chosen address / connection** | **transfer — stays `net_recv`** |
| Route-registration DSL | `Phoenix.Router.{get,post,…}` | nothing; declarative | deferred crossing |

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

**Ruling 2's mechanism has shipped (WI-nosah); no catalogue row uses it yet.**
`net_listen` is a catalog-declarable boundary carrying all three clauses: absent
from `AUTO_SOURCE_LABEL_MAP`, disclosed in its own `net_listen_edges` count and
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

**Still true: no catalogue row moves and no shipped output changes.**
`TestServerLaunchStaysAReceive` remains the pin — until the row work lands with
its own evidence the family stays `net_recv` and cannot be split silently. Its
docstring cites this ADR rather than serving as the ruling.

## Open work — sequencing and the evidence bar

Filed as tracker items, in order. **No row moves until 1–3 are done.**

1. **A corrected census.** The prompting item claimed 37 rows across 9
   languages; its own enumeration sums to 52; three independent counts under
   three membership rules produced 48+15, 75/13, and ~60 of 138 `net_recv`
   names. Four numbers, no agreement — because there is no written membership
   rule to count against. Ruling 1 supplies one. The census is re-run against
   Ruling 1, with the production loader, all catalogues, parents and shipped
   overlays, case-insensitively.
2. **Reachability before adjudication.** Every candidate row gets a fixture
   proving it emits a classifiable edge. This is not optional: `flask.Flask.run`
   — the prompting item's headline example — is in Python's `ambiguous_names`,
   so `app.run()` yields **0 edges** and mints nothing. A row can be argued
   about for an entire panel while being inert. If the family is largely
   inert, this is a correctness cleanup rather than a precision fix, which
   changes its priority but not its verdict.
3. **Adjudicated findings per shape.** Live findings from the measurement-0006
   corpus (which already contains caddy, cilium, jaeger, cert-manager — the
   archetypal `ListenAndServe` population), labelled under ADR-0046. Note
   that **zero** launch-family findings appear among 0006's 112 adjudicated
   situations; the only adjudicated datapoint anywhere is C's `socket`
   ("returns a descriptor; receives nothing").
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
