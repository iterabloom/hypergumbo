<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0028 — The 28 deleted `route` rows: which constructs are detected elsewhere

```yaml
kind: deleted_row_triage
date: 2026-11-22
pinned_tree: a5b3cca93
trigger: WI-movur (first tranche - the 28 rows whose concept is `route`)
outcome: measured
rows: 28
row_forms: 61
verdicts: {DETECTED: 20, PARTLY: 10, NOT_DETECTED: 21, NOT_A_ROUTE: 10}
deleted_by: 07066b91c (WI-jilik)
referenced: [WI-movur, WI-jilik, INV-nabas]
```

## Context

Commit `07066b91c` deleted 164 framework rows that keyed a CALL name on `decorator:`. The matcher reads
`decorator:` against `meta["decorators"]`, which only decorator and annotation syntax fills, so none of the 164
could ever fire. The rows were still the only written record that these constructs are meant to be detected.
What nobody checked per row is whether another row, an analyzer or a linker already detects the same construct.
This document is that check for the 28 rows whose concept is `route` (aiohttp 1, akka-http 3, express 2,
fastify 2, go-web 2, grape 3, graphql 1, hapi 1, koa 2, ktor 1, play 4, plug 1, slim 2, solid 1, vapor 2).

This is not an axis-conformance audit, so it does not use the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy.
It declares the sibling kind `deleted_row_triage`: one verdict per row FORM (one deleted row often names several
call shapes), from four values defined below.

## Method

1. Each row (file and line at `07066b91c^`, from `sweep/deleted_rows.txt` in the WI-jilik lab notes) was read
   for the source construct it meant, and split into its distinct forms. Where real code commonly writes the
   same construct in a shape the row's regex did not name (an inline handler, another receiver name, a chained
   call), that shape was added as its own form and is marked "outside the row's regex" where it is.
2. One fixture project per framework under
   `~/hypergumbo_lab_notebook/three_pr_plan_11222026/pr2/movur_fixtures/<framework>/`: the manifest that makes
   hypergumbo detect the framework (every fixture's `profile.frameworks` names it; go-web is detected as its
   alias `gorilla`), source that uses each form at least once with a named handler where the language has one,
   and one similar-looking line that is not a route (a control). No control produced a route marker.
3. `run_all.sh` runs `hypergumbo run <fixture> --no-sketch-fan-out --no-handler-slices` with the pinned tree's
   code, and `route_table.py` prints, per source line: (a) route markers (`meta.framework_role == "route"`, with
   `http_method` / `route_path`), (b) nodes carrying a `route` concept and the YAML file that stamped it,
   (c) `http_route` entries, (d) `dispatches_to` edges from a marker, and the usage contexts the analyzer
   recorded. Output: `route_table_output.txt` next to the scripts.
4. Verdict per form:
   - **DETECTED** — a marker with the right method and path, an `http_route` entry, and a `dispatches_to` edge
     to the handler where a named handler exists (an inline block or closure has no named handler to link).
   - **PARTLY** — some of that is missing or wrong; the row says which.
   - **NOT DETECTED** — nothing in the map marks the registration as a route.
   - **NOT A ROUTE** — the row named something that registers no route (a combinator, a prefix group, a hook).
5. Each DETECTED verdict was checked on one real repository where one exists locally (`run_real.sh`, one
   analysis at a time). Where none was found the row says "fixture only".

## Verdicts

One row per form. Names used in the table: `js_ts` = `hypergumbo_lang_mainstream/js_ts.py`; `materializer` =
`framework_patterns.materialize_route_symbols` (pass `route-materializer`, which turns a `route` concept on a
symbol into a marker placed on that symbol); `linker` = `linkers/route_handler.py` (pass `route-handler-linker`,
which writes `dispatches_to`).

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| A1 | aiohttp.yaml:35 | `app.router.add_get/add_post/add_put/add_delete/add_patch(path, handler)` | NOT DETECTED | `py.py` has route extractors for Django, Flask/FastAPI `add_url_rule` / `add_api_route` and Starlette, none for aiohttp's router; 0 usage contexts. The surviving usage row aiohttp.yaml:20 therefore has no producer. Needs: a `py.py` extractor that emits a call usage context (method, path, handler `symbol_ref`) for `<router>.add_<verb>` / `add_route`. | fixture `aiohttp` |
| A2 | aiohttp.yaml:35 | `app.router.add_route(method, path, handler)` | NOT DETECTED | as A1 | fixture `aiohttp` |
| A3 | aiohttp.yaml:35 | `router = app.router; router.add_get(...)` | NOT DETECTED | as A1 | fixture `aiohttp` |
| A4 | aiohttp.yaml:35 | `app.router.add_view(path, ViewClass)` | NOT DETECTED | as A1; the view's methods get only a class-to-method `dispatches_to`, no route. Needs the A1 extractor to read `add_view` and point at the class. | fixture `aiohttp` |
| A5 | aiohttp.yaml:35 | `app.router.add_static(prefix, directory)` | NOT DETECTED | as A1. It registers a static-file route with no handler function; whether such a route should become an entry is not decided here. | fixture `aiohttp` |
| K1 | akka-http.yaml:32 | method directives `get { ... }`, `post`, `put`, `delete`, `patch`, `head`, `options` | NOT DETECTED | The Scala analyzer emits no usage contexts and no route markers; the surviving usage row akka-http.yaml:24 has no producer. Needs: a Scala route-DSL walk that composes the enclosing path directives with each method directive leaf and emits one marker per leaf. | fixture `akka-http` |
| K2 | akka-http.yaml:37 | path directives `path`, `pathPrefix`, `pathEnd`, `pathSuffix`, `pathSuffixTest` | NOT DETECTED | These are the path half of an akka route (the method directive inside is the other half). Same detector as K1. | fixture `akka-http` |
| K3 | akka-http.yaml:62 | `concat(...)` | NOT A ROUTE | Combines alternative routes (as `~` does); registers nothing. No marker on the fixture. | fixture `akka-http` |
| E1 | express.yaml:34 | `app.get/post/put/delete/patch(path, namedHandler)` | DETECTED | Marker by `js_ts._detect_route_call` (receiver in `ROUTER_RECEIVER_NAMES`); concept by the express.yaml usage row (express.yaml:25 today); entry; `dispatches_to` by the linker. `head` / `options` were measured through the same function in the koa fixture. | fixture `express`; real: `curriculum_repos/express` examples/route-separation, 7 of 7 `app.get/put(path, named)` lines have markers (express not detected there, it is Express's own repo); the `app.all` line next to them has none (E4) |
| E2 | express.yaml:34 | `router.get(...)` on `const router = express.Router()` | DETECTED | as E1 | fixture `express`; real: snapshot-hub (E3) |
| E3 | express.yaml:34 | any verb with an inline handler `(req, res) => ...` | DETECTED | `js_ts` records the call with a synthetic handler symbol (`_GET_<path>_handler`); the express.yaml (or fastify.yaml) usage row stamps the concept; the materializer makes the marker. This path works only when express or fastify is detected in a manifest: on Express's own repo (not detected) its examples' inline-handler routes give usage contexts and no markers. | fixture `express`; real: `ALL_REPOS/plazaflow_deep_repos/cohort5_governance/snapshot-hub`, 12 registrations (11 `router.get/post`, 1 `app.get`), 12 markers, 12 concepts, 12 `http_route`, 12 `dispatches_to` |
| E4 | express.yaml:34 | `app.all(path, handler)` / `router.all` | NOT DETECTED | `js_ts.HTTP_METHODS` has no `all`, so no usage context and no marker. Needs: `all` in that set, recorded as method `ANY`. | fixture `express`; real: route-separation `app.all` line, no marker |
| E5 | (outside the row's regex) | a Router under another name: `const userRouter = express.Router(); userRouter.get(...)` | NOT DETECTED | `_detect_route_call` accepts only the receivers `app`, `router`, `express`, `server`, `fastify`, `koa`. Needs: receivers bound to `express.Router()` / `new Router()` / `Fastify()` treated as routers. Prevalence on real code not measured. | fixture `express` |
| E6 | express.yaml:34 | the `express.<verb>` alternative of the regex | NOT A ROUTE | The `express` export has no HTTP verb methods (routes are registered on `express()` or `express.Router()`), so this alternative names no construct. Not measured. | none |
| E7 | express.yaml:40 | `app.route(path).get(h).post(h)` | DETECTED | `js_ts._find_route_path_in_chain` gives each chained verb the path; concept, entry and `dispatches_to` as E1. | fixture `express` only |
| E8 | express.yaml:40 | `router.route(path).get(h)` | DETECTED | as E7 | fixture `express` only |
| F1 | fastify.yaml:39 | `fastify.get/post/put/delete/patch(path, handler)` | DETECTED | as E1 (`fastify`, `app`, `server` are in the receiver set); concept by the fastify.yaml usage row. | fixture `fastify`; real: F3 |
| F2 | fastify.yaml:39 | `fastify.get(path, { schema }, handler)` | DETECTED | as F1 (the handler is the last argument). | fixture `fastify`; real: F3 |
| F3 | fastify.yaml:39 | any verb with an inline handler | DETECTED | as E3 (fastify.yaml usage row plus materializer). | fixture `fastify`; real: `ALL_REPOS/optattest_repos/zksync-era/private-rpc`, 4 registrations `app.<verb>(path, schema, async (req, reply) => ...)`, 4 markers, 4 concepts, 4 `http_route`, 4 `dispatches_to` |
| F4 | fastify.yaml:39 | `fastify.all(path, handler)` | NOT DETECTED | as E4. | fixture `fastify` |
| F5 | fastify.yaml:45 | `fastify.route({ method, url, handler })` | PARTLY | Method and handler right, path WRONG: `js_ts._extract_hapi_usage_contexts` (which also reads `.route({...})` objects) reads only the `path` key, so Fastify's documented `url` key gives path `/`, a route `GET /` the code does not have. The marker sits on the handler's definition line (materializer), not the call. Needs: read `url` as well as `path`. | fixture `fastify` only |
| F6 | fastify.yaml:45 | `fastify.route({ method, path, handler })` (the `path` alias) | DETECTED | as F5, with the right path; marker on the handler's definition line. | fixture `fastify` only |
| G1 | go-web.yaml:55 | `http.HandleFunc(path, handler)` | DETECTED | Marker by `go.py._extract_go_routes` (method `ANY`, which is what the call registers); entry; `dispatches_to`. No `route` concept: Go markers come from the analyzer, not from a usage row. | fixture `go-web`; real: `whole_bunch_of_repos/ingress-nginx/images`, 4 of 4 `http.HandleFunc` calls have markers |
| G2 | go-web.yaml:55 | `http.HandleFunc("GET /items/{id}", h)` (Go 1.22 pattern) | DETECTED | as G1, method `GET` split from the pattern. | fixture `go-web` only |
| G3 | go-web.yaml:58 | `mux := http.NewServeMux(); mux.HandleFunc(path, h)` | DETECTED | as G1 (receiver-agnostic). | fixture `go-web` only |
| G4 | go-web.yaml:58 | `mux.Handle(path, http.FileServer(...))` / `promhttp.Handler()`: handler given as a call or a name | DETECTED | as G1; no `dispatches_to` when the handler is a library constructor call (nothing local to link). | fixture `go-web`; real: ingress-nginx/images `http.Handle("/metrics", promhttp.Handler())` has a marker |
| G5 | go-web.yaml:58 | `mux.Handle(path, staticHandler{})` / `router.Handle(path, &staticHandler{})` | NOT DETECTED | `go.py._extract_handler_name` returns nothing for a composite literal or `&T{}`, so no marker. Needs: take the literal's type name as the handler (its `ServeHTTP` method is what runs). | fixture `go-web` |
| G6 | go-web.yaml:58 | gorilla `router.HandleFunc(path, h).Methods("GET")` | PARTLY | Path and handler right; method recorded `ANY`: `.Methods(...)` is read only in the `Path().Methods().Handler()` builder chain, not when chained after `HandleFunc`. Needs: read a `.Methods(...)` call whose receiver is the `HandleFunc` / `Handle` call. | fixture `go-web`; real: `whole_bunch_of_repos/microservices-demo/src/frontend`, 14 registrations, 14 markers, 12 of them carry `.Methods(...)`, all 14 recorded `ANY` |
| G7 | (outside the row's regex) | gorilla subrouter `r := router.PathPrefix("/v1").Subrouter(); r.HandleFunc(...)` | PARTLY | Marker, entry and `dispatches_to` present; the `/v1` prefix is missing from the path. Needs: `PathPrefix(...).Subrouter()` bindings added to the group-prefix map. | fixture `go-web` only |
| R1 | grape.yaml:37 | verb with a path argument, `get ':id' do ... end` | PARTLY | Marker by `ruby.py._extract_rails_routes` (the Rails/Sinatra extractor), method right. The path keeps a `namespace` prefix but not the segments of enclosing Grape `resource`, `resources`, `group`, `segment` or `route_param` blocks (`/:report_id` for `/admin/reports/:report_id`; `/admin/reset` for `/admin/stats/reset`). Inline block, no named handler. Needs: a Grape-aware prefix walk in `ruby.py` over those five block calls. | fixture `grape`; real: `whole_bunch_of_repos/gitlab/lib/api` (Grape API tree, analysed alone), 882 markers against 883 verb-with-path lines found by grep; `badges.rb` paths lack the enclosing `resource` |
| R2 | grape.yaml:37 | verb without a path, `get do ... end`, `head { }` | NOT DETECTED | `_extract_rails_routes` requires a path argument. Needs: R1's prefix walk to emit a route for a bare verb at the enclosing prefix. | fixture `grape`; real: gitlab/lib/api has 96 bare-verb lines, no markers for them |
| R3 | grape.yaml:43 | `resource`, `resources`, `namespace`, `group`, `segment` | NOT A ROUTE | Path-prefix groupings. Only `namespace` is composed into inner paths (the Rails namespace logic); `resource :x do` / `resources :x do` are recorded as Rails `RESOURCES` usage contexts and give no markers (no false CRUD routes were made). | fixture `grape` |
| R4 | grape.yaml:47 | `route_param :id do` | NOT A ROUTE | A path-parameter grouping; the routes inside it are R2 forms and are not detected. | fixture `grape` |
| Q1 | graphql.yaml:81 | `app.graphql(query)` / `fastify.graphql(query)` (mercurius) | NOT A ROUTE | Mercurius's decorator that executes a query in process. No marker on the fixture. The route mercurius does register, `/graphql` via `app.register(mercurius, ...)`, is not detected either; that gap is not this row's. | fixture `graphql` |
| H1 | hapi.yaml:37 | `server.route({ method, path, handler: namedFn })` | DETECTED | Usage context by `js_ts._extract_hapi_usage_contexts`; concept by the hapi.yaml usage row; marker by the materializer, placed on the handler's definition line, not on the `server.route` call; entry; `dispatches_to`. | fixture `hapi` only |
| H2 | hapi.yaml:37 | `server.route([ {...}, {...} ])` | DETECTED | as H1, one marker per array element. | fixture `hapi` only |
| H3 | hapi.yaml:37 | `server.route({ ..., handler: (request, h) => ... })` | NOT DETECTED | The usage context is recorded with method and path but no `symbol_ref` (no synthetic handler symbol, unlike E3), so no concept and no marker. Needs: the synthetic inline-handler symbol E3 uses. | fixture `hapi` only |
| O1 | koa.yaml:20 | `router.get/post/put/delete/patch/head/options(path, namedHandler)` | DETECTED | Marker directly by `js_ts._detect_route_call`; entry; `dispatches_to`. No `route` concept: koa.yaml has no route row left, and express.yaml loads only when express is detected. | fixture `koa` only (no local repo uses `@koa/router`) |
| O2 | koa.yaml:20 | any verb with an inline handler `async (ctx) => ...` | NOT DETECTED | The usage context is recorded (synthetic handler symbol), but no loaded row matches it: the deleted koa row never fired, and the express.yaml usage row that would match `router.get` is not loaded for a koa project. Needs: a koa.yaml `usage:` route row (the shape of express.yaml:25). | fixture `koa` only |
| O3 | koa.yaml:20 | `router.all(path, handler)` | NOT DETECTED | as E4. | fixture `koa` only |
| O4 | koa.yaml:26 | `router.prefix('/api')` | NOT A ROUTE | Sets a prefix for the router's routes. The prefix is not composed: the fixture's routes read `/items`, not `/api/items`. | fixture `koa` |
| T1 | ktor.yaml:29 | `get("/path") { ... }`, `post`, `put`, `delete`, `patch`, `head`, `options` inside `routing { }` | NOT DETECTED | The Kotlin analyzer emits no usage contexts and no route markers; the surviving usage row ktor.yaml:20 has no producer. Needs: a Kotlin route-DSL walk (`routing` / `route(path)` nesting composed, one marker per verb call). | fixture `ktor` |
| T2 | ktor.yaml:29 | `route("/orders/{id}") { get { ... } }` (verb without a path) | NOT DETECTED | as T1. | fixture `ktor` |
| P1 | play.yaml:36 | `def index = Action { ... }` | PARTLY | `Action` defines the handler, not the route. The route is detected from `conf/routes` by `play_routes.parse_play_routes` (marker with method, path, `meta.controller_action`; entry). Missing: the link from that marker to the controller method (no `dispatches_to`, filed as INV-nabas) and any concept on the action (the Scala analyzer emits no usage contexts, so the surviving play.yaml:24 usage row has no producer). | fixture `play` only |
| P2 | play.yaml:40 | `Action.async { ... }` | PARTLY | as P1 | fixture `play` only |
| P3 | play.yaml:44 | `UserAction { ... }` (custom ActionBuilder) | PARTLY | as P1 | fixture `play` only |
| P4 | play.yaml:48 | `Action(parse.json) { request => ... }` | PARTLY | as P1 | fixture `play` only |
| L1 | plug.yaml:29 | `get/post/put/patch/delete/options/head "/path" do ... end` in a `use Plug.Router` module | DETECTED | Marker by `elixir.py._extract_phoenix_routes` (`_ROUTER_MACROS["plug"]`); entry. Inline block, no named handler. No `route` concept (the usage context has no `symbol_ref`). | fixture `plug`; real: `ALL_REPOS/special_repos/thc_dev/deps/swoosh` `lib/plug/mailbox_preview.ex`, 6 of 6 routes |
| L2 | plug.yaml:29 | keyword form `get "/users", do: MyApp.Users.list(conn)` | DETECTED | as L1 | fixture `plug` only |
| L3 | plug.yaml:29 | `match _ do` / `match "/legacy", via: [:get, :post] do` | NOT DETECTED | `match` is not in `_ROUTER_MACROS["plug"]`. Needs: `match` with its `via:` methods (none = any method) added there. | fixture `plug` |
| S1 | slim.yaml:29 | `$app->get/post/put/delete/patch/options/any(path, handler)`, `$app->map([...], path, handler)` | NOT DETECTED | `php.py` extracts only Laravel `Route::` facade calls; 0 usage contexts on the fixture, so the surviving usage row slim.yaml:20 has no producer. Needs: a `php.py` extractor for `$app->`/`$group->` verb calls with `callable` / `[Class::class, 'method']` / `'Class:method'` handlers. | fixture `slim` |
| S2 | slim.yaml:35 | `$app->group('/api', function (RouteCollectorProxy $group) { $group->get(...) })` | NOT A ROUTE | A prefix group. The routes inside it are S1 forms on `$group` and are not detected. | fixture `slim` |
| D1 | solid.yaml:74 | `<Route path="/users" component={Users} />` | DETECTED | Marker by `js_ts._detect_jsx_route` (the React Router detector; the tag name is the same); entry; `dispatches_to` to the component. Note: a client-side route is recorded with method `GET` and entry kind `http_route` ("HTTP GET /users"); whether a client-side route should be an HTTP route entry is not decided here. | fixture `solid` only |
| D2 | solid.yaml:74 | `<Router>`, `<Routes>` | NOT A ROUTE | Container components; the `<Route>` elements inside are D1. | fixture `solid` |
| D3 | solid.yaml:74 | `useNavigate`, `useParams`, `useSearchParams`, `useLocation` | NOT A ROUTE | Navigation and location hooks. | fixture `solid` |
| V1 | vapor.yaml:29 | `app.get("hello") { req in ... }` and the other verbs, closure handler | DETECTED | Marker by `swift.py._extract_vapor_usage_contexts` (receiver-agnostic for builders rooted at `app` / `routes` / `router` / a RoutesBuilder parameter); entry. Inline closure, no named handler. | fixture `vapor`; real: `whole_bunch_of_repos/vapor` `Sources/Development/routes.swift`, 32 of 33 registrations have markers (the miss is V4) |
| V2 | vapor.yaml:29 | `app.get("hello", use: hello)`, `todos.get(use: index)` | PARTLY | Method and path right; no `dispatches_to` to the named handler (`handler_ref` empty, the usage context has no `symbol_ref`). Needs: resolve the `use:` argument to the function or method symbol. | fixture `vapor` only |
| V3 | vapor.yaml:29 | `app.on(.OPTIONS, "echo", use: hello)` | PARTLY | Method from the enum and path right; handler not linked, as V2. | fixture `vapor`; real: vapor repo `app.on(.POST, ...)` closures have markers |
| V4 | vapor.yaml:29 | a route call with a call chained after it, `app.get("json") { ... }.description("...")` | NOT DETECTED | No marker. Needs: the extractor to accept a route call that is the receiver of a further call. | fixture `vapor`; real: vapor repo `Sources/Development/routes.swift:69` |
| V5 | vapor.yaml:29 | routes on a local `let app = Application(.testing)` | NOT DETECTED | The local binding to a non-builder expression invalidates the `app` root, so every route on it is dropped. Needs: `Application(...)` / `Application.make(...)` treated as a builder root. | fixture `vapor`; real: vapor repo `Tests/`, 165 `app.<verb>(` calls, 7 markers |
| V6 | vapor.yaml:35 | `app.grouped("api")`, `routes.grouped("todos")`, `app.group("users") { ... }` | NOT A ROUTE | A prefixed RoutesBuilder. The routes on it are detected WITH the prefix composed (`/api/status`, `/todos`, `/users`). | fixture `vapor` |

Counts: 61 forms over the 28 rows. DETECTED 20, PARTLY 10, NOT DETECTED 21, NOT A ROUTE 10. Of the 28 rows, the
construct is wholly undetected for aiohttp:35, akka-http:32, akka-http:37, ktor:29 and slim:29, i.e. for every
form of aiohttp, akka-http, ktor and slim.

## Diagnostic findings

- **WI-movur's description is wrong about koa.** It says "Express and koa routes do come from usage rows". Koa
  routes with a named handler come from `js_ts` markers directly (O1, 0 route concepts on the fixture); koa
  routes with an inline handler are not detected (O2), because koa.yaml has no route row and the express.yaml
  row is not loaded for a koa project. Express routes with a named handler also get their marker from `js_ts`;
  only inline-handler routes depend on the express.yaml / fastify.yaml usage rows.
- **Surviving usage rows with no producer.** For aiohttp, akka-http, ktor, play and slim the fixture gave 0 usage
  contexts, so the route `usage:` rows that survived the deletion (aiohttp.yaml:20, akka-http.yaml:24,
  ktor.yaml:20, play.yaml:24, slim.yaml:20, line numbers at `a5b3cca93`) cannot fire either. Grape, plug and vapor
  record usage contexts without a `symbol_ref`, so their route rows stamp no concept; their routes still become
  entries through the analyzers' markers.
- **Wrong output, not only misses.** F5 invents a route `GET /`; G6 records `ANY` for a method-restricted route;
  G7, R1 and O4 drop a prefix from the path; D1 labels a client-side route "HTTP GET".
- **Real-repository entry counts are capped.** On gitlab/lib/api the 882 markers give 75 `http_route` entries:
  `entrypoints.py` caps the entry list by repository size (`MAX_ENTRYPOINT_COUNT`). The (c) column of the
  fixtures is not affected (they are below the cap); on the real repositories it understates.
- **Test directories are skipped for Ruby routes by design.** `ruby.py` does not extract routes under `spec/` or
  `test/` (there, `get` / `post` are request helpers). The one local Grape-using gem
  (`gitlab/gems/gitlab-grape-openapi`) keeps all its Grape APIs under `spec/fixtures/`, gives 0 markers, and its
  `grape` dependency, declared in the gemspec, is not detected as a framework; R1 and R2 were measured on
  gitlab/lib/api instead.

## What this tranche does not cover

- The other 136 deleted rows, by concept: middleware 61, command 28, error_handler 10, websocket_handler 8,
  task 6, entrypoint 6, controller 5, graphql_schema 5, ipc_handler 2, liveview 2, scheduled_task 1,
  event_handler 1, graphql_resolver 1. Not checked.
- Forms not in a fixture: express and fastify routes with several handlers (middleware before the handler),
  routes mounted with `app.use('/prefix', router)` (whether the mount prefix reaches the path), Go `chi` / `echo`
  / `gin` (other go-web rows, not deleted ones), Ktor typed resources (`get<Resource>`), Slim's
  `$app->redirect`, Grape `mount`, Plug `forward`. Not checked.
- No real repository was found locally for hapi, koa with `@koa/router`, Solid, akka-http routes, Ktor, Slim or
  aiohttp; those verdicts rest on the fixture alone. Real-repository counts were read once each; the express
  and fastify real checks cover inline-handler forms only.
- Whether static-file routes (A5) and client-side routes (D1) should be `http_route` entries is a question this
  document raises and does not answer.

## Related

- `07066b91c` (WI-jilik) — the commit that deleted the rows; its message carries the per-row proof that none
  could fire.
- [0027](0027-entry-point-kinds.md) — the entry-kind audit whose sweep found the call-keyed rows.
- INV-nabas — Play `conf/routes` markers get no `dispatches_to` (P1-P4).
- Scripts, fixtures and raw output: `~/hypergumbo_lab_notebook/three_pr_plan_11222026/pr2/movur_fixtures/`
  (`run_all.sh`, `run_real.sh`, `route_table.py`, `route_table_output.txt`, `real_*.txt`).
