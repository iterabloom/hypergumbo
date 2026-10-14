<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0030: What the JavaScript handler-assignment construct (`ws.onmessage = h`) moves on a corpus

**Status:** Complete
**Date:** 2026-10-01
**Instrument:** `~/hypergumbo_lab_notebook/decontend/run_09302026/zohuk/instrument/` — `run_arm.sh` / `run_arm2.sh` (one arm: `survey`, then `verify-claims` and `io-boundaries` on that survey, the imported `js_ts.py` blob and the subject repo's HEAD + porcelain digest asserted at BEGIN and END), `armA_js_ts.py` (arm A's only source difference), `compare.py` (two-way diff: an order-independent multiset digest over every edge, the node-id sets, io-boundary chains, verdicts and taint situations), `census.py` (an analyzer-only, arm-B census over 38 repositories), `vc_np.sh` (the `--include-non-production-sources` configuration), `fixture1/`, `fixture2/`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-zohuk` (this measurement), `WI-dosuh` (the construct, PR #903), `WI-ponid` (receiver typing), `WI-dapap` and `WI-bobuf` (the starvation-gate finding below)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: THREE units, never pooled. (a) EMITTED: `calls` edges stamped
  `meta.call_construct == "assignment"` in the arm-B survey. (b) REACHING: of
  those, the ones an arm-B `io-boundaries` chain sits on, meaning the catalogue
  row classified them; reported for the default production-only run and for
  `--include-tests`. (c) FINDINGS: `verify-claims` verdicts, and taint
  situations and rows present in arm B and absent in arm A. A row here is a
  call-graph reachability pair, not a value flow (see
  [What a row counts](README.md#what-a-row-counts)).
- allocation: CENSUS. Every file of every surveyed repository, both arms. The
  analyzer census reads every JS/TS file of 38 repositories, arm B only.
- seed: none. Both arms are deterministic and nothing is sampled.
- cohort: the item's three named repositories (vscode, nextjs,
  podman-desktop), plus every corpus repository the analyzer census found
  emitting the construct (argo-cd, nestjs, nest), plus lila, chosen BEFORE
  measuring as a browser-facing repository whose source spells
  `ws.onmessage =` on a `new WebSocket(...)` receiver. Surveyed in both arms:
  vscode, lila, argo-cd, nestjs, nest. NOT surveyed: podman-desktop (zero
  emissions, so the arms cannot differ) and nextjs (stopped at 3 h 05 min of
  arm B, NOT MEASURED); see "Cohort decisions".
- claim_set: the seven generic taint claims, verbatim, in two configurations:
  the default (production sources only) and `--include-non-production-sources`.
- rubric: measurement 0001's, for any new finding (CORRECT = a real value flow
  exists from the source's value to the named sink). An EMITTED edge is correct
  iff, read at source, the receiver at that line is constructed as the row's
  module and the line assigns a handler to that row's property.
- analyzer_sha: dev `8cfcab7b07`. Arm B imports the worktree's `js_ts.py`, blob
  `11d17da8a1c202290d39dad523a3c94c7e926fc7`. Arm A imports an overlay copy of
  the `hypergumbo-lang-mainstream` sources whose ONLY difference is line 1046,
  `JS_ASSIGNABLE_ROWS = {}`, blob `d8ca812439c60d52bb5a3661ddae8dde7f8c0c14`.
  That name is read at exactly one site, the emission branch, so emptying it
  withholds the construct and nothing else. Each arm imports `js_ts`, hashes
  the file it actually loaded and checks the row count (0 / 7) at BEGIN and at
  END; one `XDG_CACHE_HOME` per arm.
- language_scope: javascript and typescript (one analyzer, `js_ts.py`). Every
  other analyzer runs in both arms from identical sources; that they produced
  identical output is ASSERTED by the edge-multiset digest, not assumed.

## The construct, and why the arms can be this narrow

WI-dosuh made a property assignment `recv.<prop> = h` emit an unresolved `calls`
edge when `recv` types to a catalogue module and `<prop>` is one of that
module's method-kind rows (`JS_ASSIGNABLE_ROWS`, derived from
`javascript.yaml`). At `8cfcab7b07` that set is:

    WebSocket         addEventListener, onclose, onmessage, send
    EventSource       addEventListener, onmessage
    BroadcastChannel  addEventListener, postMessage
    XMLHttpRequest    open, send, setRequestHeader
    net.Socket        connect, end, write
    dgram.Socket      send
    dns.Resolver      resolve, resolve4, ... reverse (14 names)

Three of those are handler registrations (`WebSocket.onmessage`,
`WebSocket.onclose`, `EventSource.onmessage`); the rest are method names that an
assignment would monkey-patch. Every emitted edge in this measurement is on one
of the first three.

## Result — per repository

| repo | edges A → B | calls A → B | EMITTED | REACHING (prod / +tests) | verdicts changed | situations new / lost / changed |
|---|---:|---:|---:|---:|---:|---|
| vscode | 1,216,567 → 1,216,567 | 342,838 → 342,838 | 0 | 0 / 0 | 0 of 7 | 0 / 0 / 0 |
| lila | 151,822 → 151,822 | 82,681 → 82,681 | 0 | 0 / 0 | 0 of 7 | 0 / 0 / 0 |
| argo-cd | 89,351 → 89,352 | 49,104 → 49,105 | 1 | 1 / 1 | 0 of 7 | 0 / 0 / 0 |
| nestjs | 64,361 → 64,363 | 20,658 → 20,660 | 2 | 0 / 2 | 0 of 7 | 0 / 0 / 0 |
| nest | 64,979 → 64,981 | 20,938 → 20,940 | 2 | 0 / 2 | 0 of 7 | 0 / 0 / 0 |
| nextjs | NOT MEASURED | NOT MEASURED | 1 (census) | NOT MEASURED | NOT MEASURED | NOT MEASURED |

**NON-DESTRUCTION HELD IN EVERY REPOSITORY.** Arm A's edge multiset equals arm
B's minus exactly the emitted edges (an order-independent digest over
`(type, src, dst, line, meta)` of every edge), and the only node arm B adds is
the external symbol each emitted edge points at. `calls_total` therefore
differs by exactly the emitted count and by nothing else. The digest control
was mutated (one edge's line shifted in a copy of arm A) and went red, then
restored.

**Every emitted edge is correct, and every surveyed one reaches its row.** Read at source:

- argo-cd `ui/src/app/shared/services/requests.ts:92`,
  `eventSource.onmessage = msg => observer.next(msg.data)` after
  `let eventSource = new EventSource(fullUrl)` at :91. PRODUCTION. The edge
  classifies `net_recv` / `EventSource.onmessage` in both io-boundaries runs.
  The file opens with `declare class EventSource { ... }` (:9-16), an ambient
  TypeScript declaration of the browser global with no body; the survey points
  the `instantiates` edge at it, and the object at runtime is the browser's
  EventSource, so the row is the right one.
- nestjs `packages/core/test/router/sse-stream.spec.ts:202`,
  `es.onmessage = e => {...}` after `const es = new EventSource(...)`. TEST.
- nestjs `integration/websockets/e2e/gateway.spec.ts:164`,
  `eventSource.onmessage = resolve` after `const eventSource = new
  EventSource(...)` at :156. TEST. A named handler (`resolve`), the spelling
  WI-vubal is about; the registration edge is emitted all the same.
- nest is a second clone of the nestjs repository at a different commit
  (`782e0715` vs `c93c2474`); its two edges are the same two source lines and
  are not counted twice in the totals below.
- nextjs `test/development/app-dir/hmr-iframe/app/page1/subscribeToHMR.ts:39`,
  `ws.onmessage = (event: any) => {...}` after `const ws = new WebSocket(...)`
  at :35. TEST. From the analyzer census only: nextjs was not surveyed (see
  "Cohort decisions"), so whether its row is reached through io-boundaries was
  not measured.

TOTAL: 4 distinct emitted sites in 38 repositories (6 edges counting the nest
clone). 1 is production code (argo-cd), 3 are tests. All 4 are correct. The 3
surveyed ones reach their row; the default, production-only io-boundaries run
sees 1 of them (argo-cd), because the other two are test files.

## Result — findings

**Zero new taint situations, zero lost, zero changed, and zero verdict changes,
in every repository, in the default configuration.** With nothing new, there is
nothing to adjudicate under the rubric, and the precision question the item
asks ("for EVERY new finding, adjudicate it") has an empty population on this
cohort. That is reported as an empty population, not as a precision of 100%.

Why argo-cd's production edge produces no finding, read from the survey: its
source is anchored on the enclosing `Observable.create` callback
(`_cb_create@33`, lines 70-111), whose outgoing calls in the arm-B survey are
`apiRoot`, `fetch` (:76), `then`/`catch`, `next`, `error`, `EventSource.close`
and `abort`. No generic claim pairs `untrusted_input` with the network zone that
`fetch` belongs to, and arm B's verify-claims output names no situation in
`requests.ts` at all. So the co-location shape
the item warns about (source anchored on the enclosing function, any sink in it
pairs) is present at this site, and the claim set has no sink it could pair
with.

`--include-non-production-sources` (LIVE rule 8: the opt-in flag is the
configuration that puts the TEST-file sources into play), run on nestjs, nest,
argo-cd and lila from the same per-arm surveys: 0 verdict changes, 0 changed
`details` or `caveats`, 0 new / lost / changed situations, row totals identical
(nestjs 3 → 3, nest 3 → 3, argo-cd 648 → 648, lila 163 → 163). The two nestjs
test sources (`_cb_listen@18`, `_cb_Promise@24`) enter no situation in arm B's
output under any of the seven claims.

## A verdict regression the construct causes, reproduced on a fixture and absent from the corpus

The construct turns `confirmed_with_caveats` into `inconclusive` on EVERY claim
when a catalogued module is reached only through a handler assignment.

    // fixture2/app.js
    function start(u, sink) {
      const ws = new WebSocket(u);
      ws.onmessage = function (ev) { console.log(ev.data); };
      sink.write("x");      // an untyped method call: JS has construct evidence in BOTH arms
    }

    arm A: 7 of 7 claims confirmed_with_caveats
    arm B: 7 of 7 claims inconclusive -- "NOT CONFIRMED: the analysis calls into
           1 module(s) whose catalogued I/O is method-shaped (WebSocket) but
           produced no method call edge for any of them"

MECHANISM (`verify_claims.method_starved_modules`). The assignment edge's dst
names the module `WebSocket`, so the module enters `called`. Route 1 asks
whether the edge's `call_construct` is a member of the module's declared
primitive KINDS (`{"method"}`); `"assignment"` is not. Route 2 asks whether the
name is a FUNCTION-kind row; `onmessage` is method-kind. So `WebSocket` is
reported structurally invisible in the same run in which `io-boundaries`
classified a call into it as `net_recv`. This is WI-dapap remedy (2)'s
cross-axis membership test, whose filing predicted the js `assignment` member
would be inert because its dst is "normally a local handler"; the dst is the
catalogue module. It is also a sixth instance of WI-bobuf's classified-yet-
starved contradiction. Without the `sink.write` line the flip happens for a
second reason as well: the assignment edge is then the first JS edge carrying
any `call_construct`, which switches off the gate's abstention for languages
that never stamp one (fixture1).

ON THE FIVE SURVEYED REPOSITORIES IT FIRED ZERO TIMES (0 changed `details`
strings, 0 verdict changes). In each surveyed emitting repository the same
module is also reached by a method-stamped call (`es.close()`,
`eventSource.close()`), which satisfies route 1. The harm is real and its corpus volume here is zero; both facts are on
WI-dapap and WI-bobuf. It is not fixed here.

## Why the emitted count is this small: receiver typing, not the construct

The analyzer census (arm B, `analyze_javascript` only, 38 repositories chosen
because their source constructs `new WebSocket(` / `new EventSource(` or spells
`.on(message|close|error|open) =`) finds the construct in four: nextjs 1,
nestjs 2, nest 2, argo-cd 1. The other 34 emit 0, including v8 (340,104 call
edges), gitlab (482,896), vscode (512,717), grafana, metabase and
podman-desktop.

A spelling census (rg, not the instrument) of `.onmessage =` / `.onclose =` in
the 36 corpus files outside v8 that construct a WebSocket or EventSource
separates the handler sites by how the receiver is bound:

| binding | sites | emitted? |
|---|---:|---|
| `const` / `let x = new X(...)` in the same scope | 4 | yes, all 4 |
| assignment to an outer untyped `let` (`x = new X(...)`) | 4 | no (WI-ponid) |
| outer `let x: WebSocket;` / `let x: EventSource;` annotation, then assignment | 5 | no (WI-ponid; a parameter annotation types the receiver, a `let` annotation does not: argo-cd's `webSocket.send` at :68 lands on `external:send` too) |
| field (`this.socket = new WebSocket(...)`), or a local alias of one (`const ws = this.ws`) | 6 | no (WI-ponid) |
| parenthesized chained declarator (`const ws = (this.ws = new WebSocket(u))`) | 2 | no (noted on WI-ponid; `js_ts.py` records a constructed type only when the `new_expression`'s parent IS the `variable_declarator`) |
| factory return (`WS.getSocket(id)`) or an uncatalogued wrapper class (`new WebSocketClient(...)`) | 6 | no, and not a typing defect |

27 distinct sites (nest's three repeats of nestjs lines not counted). Outside
those files, v8 carries 19 emscripten-generated `peer.socket.onmessage =` sites
whose socket is stored in an object literal `{ socket: ws }`, and phoenix and
workadventure assign handlers on `this.conn = new this.transport(...)` and
`new WorkAdventureWebSocket(...)`. So the construct reaches 4 of the 21 typable
handler sites this census sees; the 17 it misses are receiver-binding gaps that
predate it. The census does not see a receiver constructed in one file and
handled in another.

## Cohort decisions

- **podman-desktop was not surveyed.** The analyzer census found 0 emitted edges
  (87,459 JS/TS call edges). Arm A differs from arm B only inside the emission
  branch, so with zero emissions the two surveys are identical by construction;
  vscode and lila, the two other zero-emission repositories, were surveyed in
  both arms and their edge-multiset digests, verdicts and situation sets were
  identical, which is that argument checked twice rather than assumed.
  The board was saturated, and a pair of surveys that cannot differ was not
  run.
- **nextjs: NOT MEASURED by survey A/B.** The vscode trial (2.2 h and 2.5 h per
  survey) and the analyzer census (nextjs's JS/TS pass 1.23x vscode's)
  predicted ~2.7-3.1 h per arm. Arm B was stopped by its pid at 3 h 05 min with
  no survey written, under the run coordinator's rule that a pair estimated
  over 6 h of wall clock is not run on the shared box; arm A was never started.
  Two earlier sessions (`t0c_09112026`) also never saw a nextjs survey finish.
  What is known without it: the analyzer census finds exactly one emitted edge,
  in a test file; the default configuration excludes test sources, and the two
  test-only repositories that were surveyed (nestjs, nest) moved nothing in
  either configuration. The starvation flip described above is excluded for
  nextjs by a census, not by a survey: an
  analyzer-only census of every JS/TS call edge into `WebSocket` / `EventSource`
  (`census_ws.py`) finds nextjs's `WebSocket` reached by 7 method-stamped calls
  (`on` 6, `addEventListener` 1) beside the 1 assignment, and route 1 is
  satisfied by any one of them. The same census on nestjs and argo-cd finds
  `EventSource.close` method calls (2 and 1), matching their surveys, in which
  the gate did not fire. This bounds nextjs's verdict delta by argument; it is
  not a measurement of it.
- **The item's cohort is a Node cohort and the construct is a browser
  surface**, as the item predicted. lila was added for that reason and emits 0,
  for the chained-declarator reason above; argo-cd's UI is the one production
  browser site found.

## What this does not establish

- No precision figure. The new-finding population is empty in the default
  and the non-production configuration alike, on the five surveyed repositories.
- Not the construct's recall in principle. The ceiling is set by receiver
  typing (WI-ponid); after a WI-ponid fix, the 17 missed typable sites above become
  emissions and this measurement must be re-run, since its "zero findings" is a
  statement about 4 emitted edges.
- Not WI-ritog's arrival-scope seeding, WI-vubal's named-handler edge or
  WI-vogos's `net_listen` retag. Each changes what an A/B would measure; none
  had landed at `8cfcab7b07`.
- n = 5 surveyed repositories (4 distinct projects); 38 in the analyzer census.
  Neither is a sample of browser code in general.
