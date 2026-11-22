<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0029 — The other 136 deleted rows: what registers user code, and what detects it

```yaml
kind: deleted_row_triage
date: 2026-11-22
pinned_tree: c8b4180d0
trigger: WI-movur (second tranche - the 136 rows whose concept is not `route`)
outcome: measured
rows: 136
row_forms: 348
verdicts: {DETECTED: 13, PARTLY: 66, NOT_DETECTED: 185, NO_USER_CODE: 83, not_measured: 1}
forms_served_by_the_same_change: 158
deleted_by: 07066b91c (WI-jilik)
referenced: [WI-movur, WI-jilik, WI-dopuh, WI-tavum, WI-babuv, WI-pahom]
```

## Context

Commit `07066b91c` deleted 164 framework rows that keyed a CALL name on `decorator:`. None could fire. Audit
[0028](0028-deleted-route-rows-triage.md) checked the 28 whose concept is `route`. This document is the same check
for the other 136: middleware 61, command 28, error_handler 10, websocket_handler 8, task 6, entrypoint 6,
controller 5, graphql_schema 5, ipc_handler 2, liveview 2, scheduled_task 1, event_handler 1, graphql_resolver 1.

It uses 0028's sibling format (`kind: deleted_row_triage`, one verdict per row FORM) with one verdict renamed: a
non-route row cannot be "not a route", so the fourth value is **NO USER CODE**.

Unlike 0028, the change that adds this document also builds detectors. The verdicts below describe the tree
BEFORE that change (`c8b4180d0`); the section "What the same change builds" says which forms it serves.

## Method

1. The rows were split into six groups by language and measured by six agents, one per group, each reading its
   rows at `07066b91c^` with their comments. A row was split into its forms; a shape real code writes that the
   row's regex did not name was added and marked "outside the row's regex".
2. The question asked of every form: does the construct hand the framework code WRITTEN IN THE REPOSITORY (a
   named function, a method, a class, a module, an inline block) that the framework will call? `app.use(auth)`
   does. `app.use(helmet())` does not.
3. One fixture project per framework (manifest that makes hypergumbo detect it, each form with a named local
   handler where the language has one, one similar-looking control line), analysed with the pinned tree's code.
   Read per form: is the handler an entry point and of which kind, does it carry a concept, does the analyzer
   record a usage context, which edges end on it.
4. Verdicts:
   - **DETECTED** — the user code the construct registers is an entry point of a fitting kind.
   - **PARTLY** — something is detected and part is missing or wrong; the row says which.
   - **NOT DETECTED** — the construct registers user code and nothing marks it.
   - **NO USER CODE** — the row named something that registers no code of the repository (a library
     constructor, a configuration call, a helper called inside a handler).
5. A DETECTED verdict was checked on one real repository where one exists locally; a NOT DETECTED form was
   counted on real code. "fixture only" where none exists.
6. A defect of the tool found on the way changed the method: `find_non_test_files` classified a file by its
   absolute path, so a fixture kept under a folder named `fixtures/` lost the output of every content-scanning
   linker. Three groups had measured from copies elsewhere; the other three re-ran every fixture from a copy
   and no verdict changed. The defect is fixed in the same change.

The mechanisms a cell names (D1, G2, A ...) are the group's own labels, defined in its triage notes
(`~/hypergumbo_lab_notebook/movur_dibin_11222026/movur/triage_<group>.md`), which also hold the fixtures, the
raw tables and each group's findings.

## Verdicts

Form ids are `<group>:<id>`. One id letter can mean different frameworks in different groups.

### JavaScript / TypeScript (express, fastify, hapi, koa, cli-js, electron)

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| js:E1 | express.yaml:45 | `app.use(namedMw)` | NOT DETECTED | refs-only; no concept, no entry. Needs D1. | fixture `express` app.js:48; real: `plazaflow_deep_repos/polis/server` has 7 `app.use(<local fn>)`; on its map all 7 functions have `references:1, module_exports:1` and no entry (5 plain, 2 are E7) |
| js:E2 | express.yaml:45 | `app.use((req,res,next) => ...)` | NOT DETECTED | `_cb_use` refs-only. D1. | fixture app.js:51; real by grep: snapshot-hub `src/helpers/metrics.ts:51` (1) |
| js:E3 | express.yaml:45 | `app.use('/api', namedMw)` | NOT DETECTED | refs-only. D1 (the function is `args[1]`). | fixture app.js:54 only |
| js:E4 | express.yaml:45 | `router.use(namedMw)` | NOT DETECTED | refs-only. D1. | fixture app.js:57 only |
| js:E5 | express.yaml:45 | `app.use(makeLimiter(100))`: a local factory returns the middleware | NOT DETECTED | The factory has a `calls` edge from the file. The closure it returns gets no symbol (return position, WI-piror), so nothing is reported dead and nothing is marked. A detector would have to mark the returned function, which needs WI-piror first. | fixture app.js:60; real not counted (grep cannot tell a local factory from a library one) |
| js:E6 | express.yaml:45 | `app.use('/v1', router)`: mounts a Router | NO USER CODE | The argument is a Router object. The code it reaches is the route forms of audit 0028. | fixture app.js:63; real: snapshot-hub `src/index.ts:29-31` (3 mounts) |
| js:E7 | express.yaml:45 + comment at :51 | 4-argument error middleware, named: `app.use(function (err, req, res, next))` | NOT DETECTED | refs-only. Should be `error_handler`. Needs D1 plus the handler's arity (D1a). | fixture app.js:66; real: polis/server `globalErrorHandler` (server-middleware.ts:132) and `middleware_log_middleware_errors` (:94), both `references` + `module_exports` only on the map |
| js:E8 | express.yaml:45 + comment at :51 | 4-argument error middleware, inline | NOT DETECTED | `_cb_use` refs-only. D1 + D1a. | fixture app.js:69; real by grep: `optattest_repos/private-proof-of-innocence/packages/node/src/api/api.ts:136` (1) |
| js:E9 | express.yaml:49 | `app.param('id', namedFn)` | NOT DETECTED | refs-only. D1. | fixture app.js:72; real: 0 in polis/server, snapshot-hub, ppoi node |
| js:E10 | express.yaml:49 | `router.param('id', namedFn)` | NOT DETECTED | as E9 | fixture app.js:75 only |
| js:E11 | express.yaml:49 | `app.param('slug', inline)` | NOT DETECTED | `_cb_param` refs-only. D1. | fixture app.js:78 only |
| js:E12 | express.yaml:61 | `app.use(session({...}))` | NO USER CODE | express-session's constructor; registers library middleware. | fixture app.js:81 |
| js:E13 | express.yaml:65 | `app.use(morgan('dev'))` | NO USER CODE | library logger. | fixture app.js:84; real: polis `app.ts:239`, workadventure/uploader `src/App.ts:22` |
| js:E14 | express.yaml:65 | `morgan(localFormatFn)`: a custom format function | NOT DETECTED | refs-only. morgan calls it per request. This is an option callback, and no entry kind fits it (D4). | fixture app.js:87 only |
| js:E15 | express.yaml:69 | `app.use(helmet())` | NO USER CODE | library. | fixture app.js:90 |
| js:E16 | express.yaml:73 | `app.use(cors())` | NO USER CODE | library. | fixture app.js:93; real: snapshot-hub `src/index.ts:26` |
| js:E17 | express.yaml:73 | `cors({ origin: localFn })` | NOT DETECTED | refs-only (`object_field_reference`). Option callback, D4. | fixture app.js:96 only |
| js:E18 | express.yaml:77 | `bodyParser.json()`, `express.json()`, `express.urlencoded()` | NO USER CODE | library. | fixture app.js:99-101; real: snapshot-hub `src/index.ts:24-25` |
| js:E19 | express.yaml:77 | `json({ verify: localFn })` | NOT DETECTED | refs-only. Option callback, D4. | fixture app.js:104 only |
| js:E20 | express.yaml:81 | `multer({ dest })`, then `upload.single('f')` as route middleware | NO USER CODE | library. | fixture app.js:107,119 |
| js:E21 | express.yaml:81 | `multer({ fileFilter: localFn })` | NOT DETECTED | refs-only. Option callback, D4. | fixture app.js:109 only |
| js:E22 | express.yaml:85 | `body('email').isEmail()` validator chain; `validationResult(req)` inside a handler | NO USER CODE | Library validators, and a helper called from a handler. | fixture app.js:112-113 |
| js:E23 | express.yaml:85 | `body('name').custom(localFn)` | NOT DETECTED | refs-only. Option callback, D4 (or D1 on `.custom`). | fixture app.js:118 only |
| js:E24 | (outside the row's regex) | route-level middleware `app.get(path, requireAdmin, handler)` | NOT DETECTED | `requireAdmin` has `references` from the file, and js_ts:7623-7672 emits the `middleware_chain` edge `requireAdmin -> handler` (it ENDS on the handler). The handler is the route entry; the middleware has no caller. D1 (positions before the last argument), or reverse the chain so it starts at the route marker. | fixture app.js:124; real by grep: polis/server 119 `app.<verb>(path, <mw>, ..., handler)` calls |
| js:E25 | express.yaml:89 | `io.on('connection', namedFn)` | PARTLY | `websocket-linker` mints `ws:connection` (`framework_role=websocket_endpoint`). That role makes no entry, and no edge from it reaches `onConnection` (refs-only). D1 or D5. | fixture app.js:130 |
| js:E26 | express.yaml:89 | `io.on('connection', inline)` | PARTLY | as E25; `_cb_on` refs-only. | fixture app.js:133; real: `whole_bunch_of_repos/socketio-chat-example` index.js:48, measured: `ws:connection` node, `_cb_on` `references:1`, no entry; networked-aframe `server/socketio-server.js:51` (grep) |
| js:E27 | express.yaml:89 | `socket.on('chat', namedFn)` inside the connection handler | NOT DETECTED | Only the generic `event_subscriber` node `chat`. The websocket linker records a receive site but mints no node. `onChat` refs-only. D1. | fixture app.js:34 |
| js:E28 | express.yaml:89 | `socket.on('typing', inline)` | NOT DETECTED | as E27 | fixture app.js:36; real: socketio-chat-example index.js:49, measured: generic subscriber `chat message`, `_cb_on` refs-only; networked-aframe 14 `socket.on(` (grep; includes client-side adapters) |
| js:E29 | (outside the row's regex) | another receiver name: `client.on('kick', fn)` | NOT DETECTED | generic subscriber only. The websocket linker regex `(?:socket\|io)\s*\.\s*on` (websocket.py:200) does not see it. | fixture app.js:43 only |
| js:E30 | (outside the row's regex) | namespace `io.of('/admin').on('connection', fn)` | NOT DETECTED | No `ws:` endpoint (the regex needs `io.on`). `onAdminConnection` refs-only. | fixture app.js:136 only |
| js:F1 | fastify.yaml:49 | `fastify.register(localPlugin, opts)` | NOT DETECTED | refs-only. The routes inside get no marker either when the plugin parameter is not named `fastify`/`app`/`server` (Finding 7). D1. | fixture `fastify` server.js:25; real: `optattest_repos/zksync-era/private-rpc` `src/build-app.ts:37-39`, measured: `usersRoutes`, `healthRoutes`, `rpcRoutes` each `references:1, module_exports:1`, no entry |
| js:F2 | fastify.yaml:49 | `fastify.register(require('./routes'))` | NOT DETECTED | `itemRoutes` (routes.js:4) has `module_exports:1, references:1`, no entry. D1 cannot resolve a `require(...)` argument; needs the module's export (D3-like). | fixture server.js:28, routes.js |
| js:F3 | fastify.yaml:49 | `fastify.register(async (instance) => {...})` | NOT DETECTED | `_cb_register` refs-only. D1. | fixture server.js:31 only |
| js:F4 | fastify.yaml:49 | `fastify.register(fp(localPlugin))` | NOT DETECTED | `dbConnector` refs-only (an argument of `fp`). D1 would need to look through the `fp(...)` wrapper. | fixture server.js:36 only |
| js:F5 | fastify.yaml:49 | `fastify.register(cors, {...})`: a library plugin | NO USER CODE | library. | fixture server.js:39; real: private-rpc `src/build-app.ts:22` |
| js:F6 | fastify.yaml:53 | `fastify.decorate('authenticate', fn)`, then used as a route hook `onRequest: [fastify.authenticate]` | NOT DETECTED | `authenticate` refs-only. Fastify calls it as a hook through the decorated name. Needs D7 (name-keyed binding). `middleware` was the wrong concept for `decorate`; the hook use is what makes it framework-called. | fixture server.js:42,63 only |
| js:F7 | fastify.yaml:53 | `fastify.decorate('formatDate', fn)`, called as `fastify.formatDate(d)` | NOT DETECTED | The call at server.js:93 binds to an external `formatDate`; the local function stays refs-only. User code calls it, not the framework, so an entry would be wrong. Needs D7. | fixture server.js:45,93 only |
| js:F8 | fastify.yaml:53 | `decorateRequest('user', null)`, `decorate('context', {...})`: a value | NO USER CODE | stores a value. | fixture server.js:48; real: private-rpc `build-app.ts:30` |
| js:F9 | fastify.yaml:57 | `fastify.addHook('onRequest', namedFn)` / `addHook('onSend', namedFn)` | NOT DETECTED | refs-only. D1. | fixture server.js:51,57 only |
| js:F10 | fastify.yaml:57 | `fastify.addHook('preHandler', inline)` | NOT DETECTED | `_cb_addHook` refs-only. D1. | fixture server.js:54 only |
| js:F11 | fastify.yaml:57 | the alternatives `fastify.onRequest(...)`, `.preHandler(...)`, `.onSend(...)` and the rest | NO USER CODE | Not Fastify instance methods. Hook names are `addHook`'s first argument (F9) or route options (F12). The alternatives name no construct. Not measured. | none |
| js:F12 | (outside the row's regex) | route-level hook option `fastify.get(p, { preHandler: [requireAdmin] }, h)` | NOT DETECTED | `requireAdmin` refs-only, and no `middleware_chain` edge (it is inside an object). D2. | fixture server.js:60 only |
| js:F13 | fastify.yaml:61 | `fastify.setErrorHandler(namedFn)` | NOT DETECTED | refs-only. D1 → `error_handler`. | fixture server.js:66 only |
| js:F14 | fastify.yaml:61 | `child.setErrorHandler(function (err, req, reply) {...})` inside a plugin | NOT DETECTED | `_cb_setErrorHandler` refs-only. D1 (receiver `child`, Finding 7). | fixture server.js:69-71 only |
| js:F15 | fastify.yaml:65 | `fastify.setNotFoundHandler(namedFn)` | NOT DETECTED | refs-only. D1. | fixture server.js:74 only |
| js:F16 | fastify.yaml:73 | `addContentTypeParser('text/csv', opts, namedFn)` | NOT DETECTED | refs-only. D1 (handler is the last argument). | fixture server.js:77 only |
| js:F17 | fastify.yaml:73 | `addContentTypeParser(type, inline)` | NOT DETECTED | `_cb_addContentTypeParser` refs-only. D1. | fixture server.js:80 only |
| js:F18 | fastify.yaml:77 | `fastify.websocket(...)` as written | NO USER CODE | Not an API of `@fastify/websocket`. The construct is F19. Not measured. | none |
| js:F19 | (outside the row's regex) | `fastify.get('/ws', { websocket: true }, handler)`, named and inline | PARTLY | Detected with the WRONG KIND: `http_route` "HTTP GET /ws" on `onSocket` and on `_GET_ws-inline_handler`. It should be `websocket_handler`. D6. | fixture server.js:86-87 only |
| js:H1 | hapi.yaml:55 | `server.ext('onRequest', namedFn)` | NOT DETECTED | refs-only. D1. | fixture `hapi` server.js:27 only (no hapi server repo local) |
| js:H2 | hapi.yaml:55 | `server.ext('onPostAuth', inline)` | NOT DETECTED | `_cb_ext` refs-only. D1. | fixture server.js:30 only |
| js:H3 | hapi.yaml:55 | `server.ext({ type, method: namedFn })` | NOT DETECTED | `translateErrors` refs-only (`object_field_reference`). D2. | fixture server.js:33 only |
| js:H4 | hapi.yaml:55 | `server.ext([{ type, method }, ...])` | NOT DETECTED | refs-only. D2 over array elements, the way `_extract_hapi_usage_contexts` already walks route arrays. | fixture server.js:36-39 only |
| js:H5 | hapi.yaml:75 | `server.events.on('response', namedFn)` | NOT DETECTED | generic subscriber `response`; `onResponseEvent` refs-only. D1 → `event_handler`. | fixture server.js:42 only |
| js:H6 | hapi.yaml:75 | `server.events.on({ name: 'log', channels }, namedFn)` | NOT DETECTED | not even a generic subscriber node; `onLogEvent` refs-only. D1. | fixture server.js:45 only |
| js:H7 | hapi.yaml:75 | `server.events.on('start', inline)` | NOT DETECTED | generic subscriber; `_cb_on` refs-only. D1. | fixture server.js:48 only |
| js:H8 | hapi.yaml:87 | `Boom.notFound(...)` thrown in a handler; `Boom.boomify(err)` in an extension | NO USER CODE | Boom builds error objects. The calls sit inside user handlers and register nothing. `error_handler` was the wrong concept. | fixture server.js:14,19 |
| js:O1 | koa.yaml:43 | `app.use(namedMw)` | NOT DETECTED | refs-only. D1. | fixture `koa` server.js:36 only |
| js:O2 | koa.yaml:43 | `app.use(errorBoundary)`: koa's error handling is a try/catch around `await next()` | NOT DETECTED | refs-only. Same shape as O1. Only the body (try/catch around `next()`) marks it as an error handler. | fixture server.js:39 only |
| js:O3 | koa.yaml:43 | `app.use(async (ctx, next) => ...)` | NOT DETECTED | `_cb_use` refs-only. Also on `subApp.use(...)` (server.js:98). D1. | fixture server.js:42; real by grep: `plazaflow_deep_repos/hocuspocus/playground/backend/src/koa.ts:13` (1) |
| js:O4 | koa.yaml:43 | `app.use(router.routes())` | NO USER CODE | The router's dispatcher. The routes are 0028's O-forms. | fixture server.js:65 |
| js:O5 | koa.yaml:31 | `router.use(namedMw)` | NOT DETECTED | refs-only. D1. | fixture server.js:45 only |
| js:O6 | koa.yaml:31 | `router.use('/api', namedMw)` | NOT DETECTED | refs-only. D1. | fixture server.js:48 only |
| js:O7 | koa.yaml:31 | `router.use(inline)` | NOT DETECTED | `_cb_use` refs-only. D1. | fixture server.js:51 only |
| js:O8 | koa.yaml:31 | `router.use('/admin', adminRouter.routes())` | NO USER CODE | nests a router. | fixture server.js:54 |
| js:O9 | koa.yaml:35 | `router.param('user', namedFn)` | NOT DETECTED | refs-only. D1. | fixture server.js:57 only |
| js:O10 | koa.yaml:35 | `router.param('post', inline)` | NOT DETECTED | `_cb_param` refs-only. D1. | fixture server.js:60 only |
| js:O11 | koa.yaml:39 | `app.use(router.allowedMethods())` | NO USER CODE | library middleware factory. | fixture server.js:66 |
| js:O12 | koa.yaml:50 | `koaBody()`, `bodyParser()` | NO USER CODE | library. | fixture server.js:69-70 |
| js:O13 | koa.yaml:54 | `session(config, app)` | NO USER CODE | library. | fixture server.js:73 |
| js:O14 | koa.yaml:66 | `helmet()` | NO USER CODE | library. | fixture server.js:76 |
| js:O15 | koa.yaml:70 | `cors()` | NO USER CODE | library. | fixture server.js:79; real: hocuspocus playground `tiptapcollab.ts:9` |
| js:O16 | koa.yaml:70 | `cors({ origin: localFn })` | NOT DETECTED | refs-only. Option callback, D4. | fixture server.js:80 only |
| js:O17 | koa.yaml:74 | `serve('public')`; `send(ctx, path)` inside a handler; the alternative `static` | NO USER CODE | A library factory and a helper. `static` is a reserved word in strict mode and in ES modules, so `const static = require('koa-static')` cannot be written there. | fixture server.js:83,29 |
| js:O18 | koa.yaml:82 | `logger()` | NO USER CODE | library. | fixture server.js:86 |
| js:O19 | koa.yaml:82 | `logger(localTransporter)` | NOT DETECTED | refs-only. Option callback, D4. | fixture server.js:87 only |
| js:O20 | koa.yaml:86 | `compress()` | NO USER CODE | library. | fixture server.js:90 |
| js:O21 | koa.yaml:86 | `compress({ filter: localFn })` | NOT DETECTED | refs-only. Option callback, D4. | fixture server.js:91 only |
| js:O22 | koa.yaml:90 | `mount('/hello', namedMw)` | NOT DETECTED | refs-only. D1, with a bare callee gated on the import from `koa-mount`. | fixture server.js:94 only |
| js:O23 | koa.yaml:90 | `mount('/sub', subApp)`: a Koa application | NO USER CODE | An application object. Its middleware are O1-O3 forms on `subApp`, measured NOT DETECTED at server.js:98. | fixture server.js:99 |
| js:C1 | cli-js.yaml:18 + :22 | `program.command('build <dir>')....action(namedFn)` | NOT DETECTED | refs-only. D1 (context `program.action`, chained receiver root). | fixture `cli-js` cli.js:17-20; real by grep: pm2 1 named action (`lib/binaries/CLI.js`) |
| js:C2 | cli-js.yaml:18 + :22 | `program.command('serve').action(inline)` | NOT DETECTED | `_cb_action` refs-only. D1. | fixture cli.js:23; real: `curriculum_repos/knex` (cli-js detected), measured: 11 `_cb_action` in bin/cli.js, all `references:1`, no entry; pm2 113 inline `.action(function ...)` (grep); zksync-era/infrastructure/protocol-upgrade 14 (grep) |
| js:C3 | cli-js.yaml:18 | `program.command('install [pkg]', 'desc')`: a stand-alone executable subcommand file `cli-install.js` | DETECTED | `cli-install.js` is a `script_module` entry (0.8), which is a fitting kind for a file commander spawns as a process. It comes from the file's top-level call, not from the registration: nothing links the registration to the file, and the kind is not `cli_command`. | fixture cli.js:26, cli-install.js only (pm2, knex: 0 executable subcommands) |
| js:C4 | cli-js.yaml:22 | `program.action(rootAction)` on the root command | NOT DETECTED | refs-only. D1. | fixture cli.js:29 only |
| js:C5 | (outside the row's regex) | another receiver: `const deploy = new Command('deploy'); deploy.argument(...).action(fn)` | NOT DETECTED | refs-only. D1 with receivers bound to `new Command()` / `program.command()`. | fixture cli.js:32-34 only |
| js:C6 | cli-js.yaml:18 + :22 | `commander.command('legacy').action(fn)` on the module object | NOT DETECTED | refs-only. D1. | fixture cli.js:37; real: pm2 `lib/binaries/CLI.js` uses `commander.command(...)` (86 `.command(` calls) |
| js:C7 | cli-js.yaml:34 | `program.version('1.0.0')` | NO USER CODE | sets the version string. | fixture cli.js:14; real: pm2 4, knex 1 |
| js:C8 | cli-js.yaml:39 | `yargs.command(cmd, desc, builderFn, handlerFn)` | NOT DETECTED | `serveBuilder` and `serveHandler` refs-only. D1 (`args[3]`). | fixture yargs-cli.js:13 only (no yargs command code found locally) |
| js:C9 | cli-js.yaml:39 | `yargs.command(cmd, desc, (y) => y, (argv) => ...)` | NOT DETECTED | `_cb_command` ×2 refs-only. D1. | fixture yargs-cli.js:16 only |
| js:C10 | cli-js.yaml:39 | `yargs.command({ command, describe, handler: fn })` | NOT DETECTED | refs-only. D2 (`args[0].handler`). | fixture yargs-cli.js:19 only |
| js:C11 | cli-js.yaml:39 | `yargs.command(require('./commands/lint'))`, where the module sets `exports.handler = function lintHandler(...)` | NOT DETECTED | The handler gets NO symbol, and its calls are credited to the file `commands/lint.js`. Needs D3. | fixture yargs-cli.js:22, commands/lint.js only |
| js:C12 | cli-js.yaml:43 | `yargs.commandDir('cmds')`, where `cmds/deploy.js` exports `{ command, handler: deployHandler }` | NOT DETECTED | `deployHandler` refs-only (`object_field_reference`). D3. | fixture yargs-cli.js:25, cmds/deploy.js only |
| js:C13 | (outside the row's regex) | `yargs(hideBin(process.argv)).command('x', d, b, handler)`: the receiver is a call | NOT DETECTED | refs-only. D1 needs a callee root that is a call to the `yargs` import. | fixture yargs-cli.js:28 only |
| js:C14 | cli-js.yaml:65 | cac `cli.command('dev [root]', d).action(namedFn)` | NOT DETECTED | refs-only. D1. | fixture cac-cli.js:10 only |
| js:C15 | cli-js.yaml:65 | cac `.action(inline)` | NOT DETECTED | `_cb_action` refs-only. D1. | fixture cac-cli.js:13; real by grep: `curriculum_repos/vite/packages/vite/src/node/cli.ts` 4 `cli.command(...)...action(async (...) =>` (lines 207, 343, 394, 430); rolldown `src/cli/arguments/index.ts` 1 |
| js:C16 | cli-js.yaml:74 | `inquirer.prompt(questions)` | NO USER CODE | Asks the user questions; registers no command. `command` was the wrong concept. | fixture ask.js:10 |
| js:C17 | cli-js.yaml:74 | a question's `validate: localFn` | NOT DETECTED | refs-only. Option callback, D4 (or D2). | fixture ask.js:13 only |
| js:C18 | cli-js.yaml:79 + :83 | `vorpal.command('foo', d).action(namedFn)` | NOT DETECTED | refs-only. D1. | fixture vorpal-cli.js:9 only |
| js:C19 | cli-js.yaml:79 + :83 | `vorpal.command('bar', d).action(function (args, cb) {...})` | NOT DETECTED | `_cb_action` refs-only. D1. | fixture vorpal-cli.js:12 only |
| js:C20 | cli-js.yaml:92 | `toolbox.print.info(...)`, `toolbox.prompt.ask(...)`, `toolbox.filesystem.write(...)` | NO USER CODE | Helpers called inside a gluegun command. | fixture `gluegun` src/commands/hello.js:11-13 |
| js:C21 | (outside the row's regex) | gluegun command module `module.exports = { name, run: async (toolbox) => {...} }` | NOT DETECTED | `run` gets NO symbol, and its calls are credited to the file, which then gets a false `script_module` entry (Finding 4). gluegun is not detected as cli-js (Finding 3). D3 plus the profile line. | fixture gluegun src/commands/hello.js:7-15 only |
| js:C22 | (outside the row's regex) | gluegun `module.exports = { name: 'bye', run: sayBye }` | NOT DETECTED | `sayBye` refs-only. D3. | fixture gluegun src/commands/bye.js only |
| js:L1 | electron.yaml:40 | `ipcMain.handle('settings:read', namedFn)`, with a renderer `invoke` | PARTLY | `ipc-linker` mints `ipc:receive:settings:read` with `event_publishes` from the preload's send. No edge from it reaches `readSettings` (refs-only), and there is no `ipc_handler` / `event_handler` entry. D5 or D1. Filed: WI-pahom. | fixture `electron` main.js:35 |
| js:L2 | electron.yaml:40 | `ipcMain.on('log', namedFn)`, with a renderer `send` | PARTLY | as L1 | fixture main.js:38 |
| js:L3 | electron.yaml:40 | `ipcMain.handleOnce('init', namedFn)` | PARTLY | as L1 | fixture main.js:41 |
| js:L4 | electron.yaml:40 | `ipcMain.handle('version:get', async () => ...)` | PARTLY | receive node; `_cb_handle` refs-only. | fixture main.js:44; real: `plazaflow_deep_repos/workadventure/desktop/electron` `src/ipc.ts`, measured: 13 inline `ipcMain.handle/on` registrations (12 found by the grep, 1 written across lines at ipc.ts:99), 13 `ipc:receive:*` nodes in src/ipc.ts, every `_cb_handle` / `_cb_on` `references:1`, 0 IPC entries (entries: electron_main 1, electron_preload 2, script_module 2); session-desktop 5 (grep) |
| js:L5 | electron.yaml:40 | `ipcMain.handle('unpaired', namedFn)` with no sender in the repo | NOT DETECTED | The IPC linker mints no receive node for an unpaired channel (documented in ipc.py). refs-only. D1. | fixture main.js:47 only |
| js:L6 | (outside the row's regex) | `ipcMain.once('ping', fn)` | PARTLY | as L1 (the linker regex includes `once`). | fixture main.js:50 only |
| js:L7 | (outside the row's regex) | `electron.ipcMain.handle('ns:call', fn)` | PARTLY | as L1 | fixture main.js:53 only |
| js:L8 | (outside the row's regex) | aliased import `const { ipcMain: ipc } = require('electron'); ipc.on('aliased', fn)` / `import { ipcMain as ipc }` | NOT DETECTED | No receive node, and the preload's `send('aliased')` joins nothing. `onAliased` refs-only. D8. | fixture main.js:56-58; real by grep: `plazaflow_deep_repos/session-desktop` `ts/mains/main_node.ts` 29 `ipc.on/handle(` under `ipcMain as ipc` (and `ts/util/logger/main_process_logging.ts`) |
| js:L9 | electron.yaml:88 | `win.webContents.on('did-finish-load', namedFn)` | NOT DETECTED | generic subscriber `did-finish-load`; handler refs-only. These are Electron lifecycle events, not IPC: the fitting concept is `event_handler` (the deleted row said `ipc_handler`, which maps to the same EVENT_HANDLER kind). D1. | fixture main.js:22; real: workadventure desktop `src/window.ts:105` (inline), measured: generic subscriber, `_cb_on` refs-only; session-desktop 3 (grep) |
| js:L10 | electron.yaml:88 | `win.webContents.on('render-process-gone', inline)` | NOT DETECTED | as L9 | fixture main.js:25 only |
| js:L11 | electron.yaml:88 | `win.webContents.send('status', data)` | NO USER CODE | A main → renderer SEND. The IPC linker records it as `ipc:send:status`, joined to the renderer receive. | fixture main.js:28; real: workadventure desktop 2, session-desktop 5 (grep) |
| js:L12 | (outside the row's regex) | `ipcRenderer.on('status', namedFn)`: the receive side of `webContents.send` | PARTLY | `ipc:receive:status` node joined from `ipc:send:status`. `showStatus` refs-only. D5. | fixture renderer.js:9, preload.js:13; real: workadventure desktop 2, session-desktop 8 (grep) |

### Elixir (phoenix, plug)

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| elixir:X1 | phoenix.yaml:59 | `use Phoenix.Controller`, an action a route names (`get "/", PageController, :index`) | DETECTED | Route marker by `routes` (`http_route` entry); `dispatches_to` marker -> `PageController.index` by `linker`. No `controller` concept; the module is `in: -`. | fixture `phoenix` (`index`, `embed` in: dispatches_to:1); real: plausible, 28 modules holding `use ... Controller` / `use PlausibleWeb, :controller`: their public functions 179 in: dispatches_to only, 21 dispatches_to+calls, 108 calls only, 29 in: -; livebook, 5 modules: public functions 11 in: dispatches_to, 24 calls only, 6 in: - |
| elixir:X2 | phoenix.yaml:62 | `use MyAppWeb, :controller`, an action a route names | DETECTED | as X1 (the linker does not read the `use`) | fixture `phoenix` (`UserController.index`); real: plausible as X1 (24 of the 28 are this form) |
| elixir:X3 | phoenix.yaml:59 / :62 | the `use` on a controller (or an action) no route names | NO USER CODE | `use Phoenix.Controller` hands Phoenix nothing: Phoenix calls an action only through a route (X1) or `action_fallback` (X4). `UnroutedController.index` and `UserController.show` are `in: -`, which is right. A `controller` concept on every `use` would make dead controllers entries. | fixture `phoenix` |
| elixir:X4 | (outside the row's regex) | `action_fallback MyAppWeb.FallbackController` -> its `call/2` | NOT DETECTED | `FallbackController.call` in: -; `action_fallback` records nothing. | fixture `phoenix`; real: 0 lines in plausible/lib, livebook/lib, thc_dev/lib (grep) |
| elixir:X5 | phoenix.yaml:66 | `use Phoenix.LiveView`, named by `live "/dash", DashLive` (no live_action) | PARTLY | `routes` writes action `mount` when none is written, so the LIVE marker `dispatches_to` `DashLive.mount` only. `render`, `handle_event`, `handle_info` get `dispatches_to` from the `DashLive` module (`table`), and the module is `in: -` (only a `library_export` entry). No `liveview` concept (no producer). | fixture `phoenix`; real: 0 instances of this shape in plausible and livebook (every `live` route there writes a live_action), see the split below |
| elixir:X6 | phoenix.yaml:69 | `use MyAppWeb, :live_view`, named by `live "/counter", CounterLive, :index` | DETECTED | LIVE marker -> the module (`linker` LiveView fallback, because `index` is the live_action, not a function); module -> `mount`, `render`, `handle_event`, `handle_params` (`table`, through the WI-nunar dispatcher reading). No `liveview` concept. | fixture `phoenix`; real: plausible 6 of 6 and livebook 12 of 12 LiveViews named by a `live` route have the module in: dispatches_to (split below) |
| elixir:X7 | (outside the row's regex) | a LiveView rendered by `live_render(conn, MyAppWeb.UnroutedLive)` (no `live` route) | NOT DETECTED | No edge from `PageController.embed` to `UnroutedLive`; the module is `in: -`, its `mount`/`render` hang off it. | fixture `phoenix`; real: plausible 24 of 30 and livebook 3 of 15 LiveView-using modules (web module included) are named by no route and are in: - (split below) |
| elixir:X8 | phoenix.yaml:80 | `use Phoenix.Channel`: `join/3`, `handle_in/3`, `terminate/2` | NOT DETECTED | Phoenix.Channel is in neither `USE_IMPLIED_BEHAVIOURS` nor `BEHAVIOUR_CALLBACKS`: no `implements`, no `dispatches_to`; `join`, `handle_in`, `terminate` in: -. The phoenix_ipc linker records the `handle_in("new_msg", ...)` clause as an `ipc_receive` marker (`phoenix:receive:new_msg`, matched to the `broadcast!` send), but the edge runs FROM `handle_in` to the marker (`uses`), the marker is no entry, and `handle_in` stays `in: -`; `LobbyChannel.handle_in("ping")` (no matching send) gets no marker. | fixture `phoenix`; real: livebook, 1 channel module: module in: -, `join`, `handle_in`, `handle_info` 1 each in: -; grep thc_dev/lib 5 `use Phoenix.Channel`, 6 `def handle_in(` |
| elixir:X9 | phoenix.yaml:83 | `use MyAppWeb, :channel` | NOT DETECTED | as X8 (the dispatcher resolves to Phoenix.Channel, which no table names). | fixture `phoenix`; real: 0 in plausible/lib, livebook/lib, thc_dev/lib |
| elixir:X10 | (outside the row's regex) | `channel "room:*", MyAppWeb.RoomChannel` in a `use Phoenix.Socket` module | NOT DETECTED | No usage context (`_iter_router_calls` yields calls of router modules only), no edge to the channel module. | fixture `phoenix`; real: grep livebook/lib 1, thc_dev/lib 4 |
| elixir:X11 | (outside the row's regex) | `socket "/socket", MyAppWeb.UserSocket` in the endpoint -> `connect/3`, `id/1` | NOT DETECTED | `UserSocket.connect`, `.id` in: -. | fixture `phoenix`; real: grep livebook/lib 3, thc_dev/lib 4 |
| elixir:X12 | phoenix.yaml:95 | `use Plug.Builder` (module with `plug :stamp`, `plug Plug.Logger`) | NOT DETECTED | `implements` -> Plug only (the injected `init`/`call` are not in source, so the `table` edge has no target); module in: -, `stamp` in: -. | fixture `phoenix` (`MyAppWeb.Plugs.Builder`); real: livebook, 2 modules holding `use Plug.Builder`, both in: - |
| elixir:X13 | phoenix.yaml:98 | `use Plug.Router` inside a Phoenix project | PARTLY | Its routes are detected (`GET /ping`, marker + `http_route`, by `routes`); the module is in: - and carries `implements` Plug only. | fixture `phoenix` (`MiniRouter`); real: 0 in the three app libs |
| elixir:X14 | phoenix.yaml:124 | `use GenServer`: `init`, `handle_call`, `handle_cast`, `handle_info` | PARTLY | `table` gives module -> each callback `dispatches_to`, plus `implements` GenServer; `increment`'s `GenServer.cast(__MODULE__, :inc)` adds a second `dispatches_to` to `handle_cast` (re-run). Missing: the module is in: -, no `task` concept / `background_task` entry; `start_link/1` (which the `use`-injected `child_spec/1` calls when the module is a supervisor child, X20) is in: -. | fixture `phoenix`; real: plausible 7 modules, all in: -; init 7/7, handle_call 4/4, handle_cast 2/2, handle_info 4/4 in: dispatches_to; start_link 5 in: -, 2 in: calls. livebook 32 modules, all in: -; init 34, handle_call 15, handle_cast 13, handle_info 30 in: dispatches_to; start_link 25 in: -, 8 in: calls |
| elixir:X15 | phoenix.yaml:132 | `use Agent` | NOT DETECTED | `use Agent` injects only `child_spec/1`; the user code it registers is `start_link/1`, in: -. The table's `Agent: ["init"]` names a callback Agent does not have (the analyzer's own `USE_IMPLIED_BEHAVIOURS` comment, 269-271, says Agent sets no behaviour). | fixture `phoenix` (`Cache`); real: plausible 1 module, in: - |
| elixir:X16 | phoenix.yaml:136 | `use Task` | PARTLY | `run/1` gets `dispatches_to` from the module by the table's `Task: ["run"]` (a naming convention, not a Task callback); `start_link/1` (called by the injected `child_spec/1`) in: -; module in: -. | fixture `phoenix` (`Warmup`); real: 0 `use Task` in the three app libs |
| elixir:X17 | phoenix.yaml:140 | `use Oban.Worker, queue: :mailers, ...` (options; the row's `^use Oban\.Worker$` did not name this) | NOT DETECTED | `perform/1` has no edge (Oban.Worker in neither table); module in: -. | fixture `phoenix` (`MailerWorker`); real: plausible 23 workers, all modules in: -; perform 11 in: -, 12 in: calls (calls from other code, not from Oban) |
| elixir:X18 | phoenix.yaml:140 | `use Oban.Worker` without options | NOT DETECTED | as X17 | fixture `phoenix` (`Bare`); real: counted with X17 |
| elixir:X19 | phoenix.yaml:144 | `use Broadway`: `handle_message/3`, `handle_batch/4` | NOT DETECTED | Broadway in neither table; both in: -. | fixture only (0 `use Broadway` in plausible/lib, livebook/lib, thc_dev/lib) |
| elixir:X20 | (outside the rows' regex) | a supervisor child spec `{MyApp.Workers.Counter, 0}` / `MyApp.Workers.Cache` in `children` -> `start_link/1` | NOT DETECTED | `Application.start` has one `calls` edge, to the external `Supervisor.start_link`; no edge to any child's `start_link`. Covers the start of every GenServer / Agent / Task / Broadway / Supervisor module. | fixture `phoenix`; real: plausible GenServer `start_link` 5 of 7 in: -; grep `children =` plausible/lib 4, livebook/lib 6, thc_dev/lib 10 |
| elixir:X21 | phoenix.yaml:148 | `use Absinthe.Schema`, resolvers `resolve &Resolvers.list_users/3` and `resolve fn ... end` | PARTLY | The capture gives a `calls` edge schema module -> `Resolvers.list_users` (Pass 2 call walk); the inline `fn` has no named code. Missing: the schema module is in: -, no `graphql_schema` concept / `graphql_server` entry; the mount `forward "/api/graphql", Absinthe.Plug, schema: MyApp.GraphQL.Schema` records nothing. | fixture only (0 `use Absinthe` in the three app libs) |
| elixir:X22 | phoenix.yaml:151 | `use Absinthe.Schema.Notation` (types module, `resolve &Resolvers.email/3`) | PARTLY | `calls` edge Types -> `Resolvers.email`; the Types module is in: - (`import_types MyApp.GraphQL.Types` in the schema records no edge); no `graphql_resolver` concept. | fixture only |
| elixir:L1 | plug.yaml:35 | `use Plug.Router` (module started by `{Plug.Cowboy, plug: MyPlug.Router}`) | PARTLY | Routes detected (`GET /hello`, by `routes`, `_ROUTER_MACROS["plug"]`); `implements` Plug. Module in: -; nothing reads `plug: MyPlug.Router` in the child spec. `forward "/admin", to: MyPlug.AdminRouter`: `AdminRouter` in: -, its route is recorded `GET /`, not `/admin`. | fixture `plug`; real: 0 `use Plug.Router` in the three app libs (the 0028 real check used deps/swoosh) |
| elixir:L2 | plug.yaml:39 | `use Plug.Builder` (`MyPlug.Pipeline`) | NOT DETECTED | as X12: `implements` Plug only; module in: -, its function plug `hello` in: -. | fixture `plug`; real: livebook/lib 1 |
| elixir:L3 | plug.yaml:43 | `plug :local_fun` naming a function of the same module (Plug.Router, Plug.Builder, Phoenix router `pipeline`) | NOT DETECTED | `put_request_tag`, `hello` (plug), `stamp`, `require_user` (phoenix) all in: -. No usage context for `plug`. | fixtures `plug`, `phoenix`; real: plausible 4 `plug :name` with a local def, 4 of 4 in: -, 32 others name no local def (L7); livebook 7 with a local def, 7 of 7 in: -, 9 others (L7) |
| elixir:L4 | plug.yaml:43 | `plug :load_page when action in [:index]` in a Phoenix controller (a `defp`) | NOT DETECTED | `load_page` in: -. | fixture `phoenix`; real: counted in L3 |
| elixir:L5 | plug.yaml:43 | `plug MyPlug.Auth, realm: "api"`, a local module with `@behaviour Plug` | PARTLY | `@behaviour Plug` gives `implements` and module -> `init`/`call` `dispatches_to` (`table`); the module is in: -, nothing reads the `plug` line. | fixture `plug` |
| elixir:L6 | plug.yaml:43 | `plug MyPlug.Timing` / `plug MyAppWeb.Plugs.Auth` / endpoint `plug MyAppWeb.Router`: a local module with `init/1` + `call/2` and no `@behaviour` | NOT DETECTED | `init`, `call` in: -; module in: -. | fixtures `plug`, `phoenix`; real: grep `plug <App>.*` lines: plausible/lib 36, livebook/lib 6, thc_dev/lib 60; `def call(conn` plausible/lib 18, thc_dev/lib 27 |
| elixir:L7 | plug.yaml:43 | `plug Plug.Logger`, `plug :match` / `:dispatch` (injected by `use Plug.Router`), `plug :accepts` / `:fetch_session` (imported from Phoenix) | NO USER CODE | A library module or a function the `use` / `import` brings in; no symbol of the repo. Measured: no edge, no usage context, nothing marked. | fixtures `plug`, `phoenix` |
| elixir:L8 | plug.yaml:51 | `use Plug.ErrorHandler` + `handle_errors/2` | NOT DETECTED | Plug.ErrorHandler in neither table; `handle_errors` in: -. (Plug's `__using__` wraps `call/2` and calls `&handle_errors/2`: deps/plug/lib/plug/error_handler.ex:58-93.) | fixture `plug`; real: plausible has 1 `use Plug.ErrorHandler`, inside a `quote` (a template, not an instance): not measured on a real instance |
| elixir:L9 | plug.yaml:55 | `use Plug.SSL` | NO USER CODE | The construct does not exist: `Plug.SSL` has no `__using__` (deps/plug/lib/plug/ssl.ex: `@behaviour Plug` at line 72, no `defmacro __using__`), so `use Plug.SSL` fails to compile. Taken to mean `plug Plug.SSL, rewrite_on: [...]` (in the fixture): a library plug. | fixture `plug`; real: 0 `plug Plug.SSL` in the three app libs |
| elixir:L10 | plug.yaml:59 | `plug Plug.Session, store: :cookie, ...` | NO USER CODE | A library plug configured by options. | fixture `plug` |
| elixir:L11 | plug.yaml:63 | `plug Plug.CSRFProtection` (Phoenix: `plug :protect_from_forgery`) | NO USER CODE | A library plug. | fixtures `plug`, `phoenix` |

### Go (cli-go, go-web)

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| go:C1 | cli-go.yaml:18 | package-level `var rootCmd = &cobra.Command{Use: ..., Run: h}` | PARTLY | Handlers are linked twice: `go.py` field-ref `calls` from the VARIABLE `rootCmd`, and the linker `dispatches_to` from the FILE node (F2). No `command` concept, no `cli_command` entry. The variable has no incoming edge: `rootCmd.Execute()` resolves to an external `Execute` (F5), and `AddCommand(serveCmd)` writes no edge to `serveCmd` (F6). Nothing reachable from `main` reaches any handler (WI-babuv). | fixture `cobra` (`rootCmd`, `serveCmd`: `in: -`; `main` reaches `main` and `cmd.Execute` only); real `beads/cmd/bd`: 226 package-level literals, 223 `*Cmd` variables, 0 with an incoming edge; 56 cobra `dispatches_to` (54 from a file node, 2 from a function); 0 handlers reachable from `main`; `main` reaches 1 function of 1,595 |
| go:C2 | cli-go.yaml:18 | literal in a constructor, `func newListCmd() *cobra.Command { c := &cobra.Command{RunE: runList}; ... }` | PARTLY | `dispatches_to` and field-ref `calls` from `newListCmd`. No entry. The handler is reachable only when the constructor's caller is: in the fixture `init()` calls it, and `init` is neither an entry nor called (F4). | fixture `cobra`; beads has 0 in-function literals (all 226 are package-level), so fixture only |
| go:C3 | cli-go.yaml:22 | `rootCmd.AddCommand(serveCmd)`, identifier arguments | PARTLY | `go.py._extract_go_usage_contexts` records a usage context `rootCmd.AddCommand` with `metadata.parent_command` / `child_command` and `symbol_ref: None`. No row reads it and no edge goes from the calling function to the child variable, so it links nothing. | fixture `cobra` (1 usage context); real beads/cmd/bd: 225 `*.AddCommand` usage contexts, 225 lines of identifier-only AddCommand calls |
| go:C4 | cli-go.yaml:22 | `rootCmd.AddCommand(newListCmd(), newVersionCmd())`, `rootCmd.AddCommand(o.cmd)` | PARTLY | No usage context (only `identifier` arguments are recorded). The constructor calls are ordinary `calls` edges from the enclosing function (`init` -> `newListCmd`), which links the command for as long as the enclosing function is reachable (here it is `init`, F4). The selector argument `o.cmd` records nothing. | fixture `cobra`; beads: 0 call-argument AddCommand lines, so fixture only |
| go:C5 | cli-go.yaml:26 | `Run: rootRun` (named function) | PARTLY | as C1 / C2 by where the literal sits: linked, no entry, unreachable from `main` in both fixture shapes. | fixture `cobra`; real beads: 60 named `Run:`/`RunE:` lines, 56 `dispatches_to`, 0 `cli_command` entries, 0 reachable from `main` |
| go:C6 | cli-go.yaml:26 | `Run: func(cmd, args) { ... }` (inline literal) | PARTLY | No symbol for the literal. Its calls are attributed to the enclosing variable (INV-nopoh) or function (`newVersionCmd` -> `versionHelper`). So the code is linked exactly as far as the variable or constructor is (C1 / C2). No entry. | fixture `cobra`; real beads: 140 inline `Run:`/`RunE:` literals, all under package-level variables with no incoming edge |
| go:C7 | cli-go.yaml:30 | `RunE: runServe` (named) | PARTLY | as C5 | fixture `cobra`; beads as C5 |
| go:C8 | cli-go.yaml:30 (value shape outside the row's regex) | `RunE: o.run`, a method value on an options struct (kubectl / helm style) | PARTLY | The linker strips `o.` and resolves the short name `run` (`migrateOptions.run`, 0.85: one candidate here; a repository with several `run` gets 0.5 fallback edges to all of them, INV-zuhub). `go.py` field-ref `calls` also binds it. No entry. | fixture `cobra` only |
| go:C9 | cli-go.yaml:34 | `PersistentPreRun: setupLogging` (named) | PARTLY | as C5 (the linker lists all ten hook fields). The deleted row's concept was `middleware`, which `entrypoints.py` turns into `middleware_handler`, labelled "HTTP middleware": the wrong kind for a CLI hook (F8). | fixture `cobra`; beads: 1 `PersistentPreRun: func`, 1 `PersistentPostRun: func` |
| go:C10 | cli-go.yaml:38 | `PersistentPostRun: func(...) { ... }` (inline) | PARTLY | as C6 (`rootCmd` -> `postInline`) | fixture `cobra`; beads as C9 |
| go:U1 | cli-go.yaml:43 | `app := &cli.App{Action: appAction, Commands: ...}` inside a function | PARTLY | field-ref `calls` `main` -> `appAction` (and every keyed function value in the literal); reachable from `main` in the fixture. No `command` concept, no entry. | fixture `urfave-cli` only |
| go:U2 | (outside the row's regex) | v1 assignment form `app := cli.NewApp(); app.Action = legacyAction` | PARTLY | fref `calls` `legacyApp` -> `legacyAction` (the META-mino local-binding arm); no entry. | fixture `urfave-cli`; real: sops `cmd/sops/main.go` and runc `main.go` both use `cli.NewApp()` + `app.X =` (sops 1 `app.Action =`, runc 4 `.Action =`, 1 `.Before =`) |
| go:U3 | cli-go.yaml:47 | `cli.Command{Name: "serve", Action: serveAction}` element (type elided) inside a function's `Commands: []*cli.Command{...}` | PARTLY | as U1 (field-ref `calls` from `main`). | fixture `urfave-cli` only |
| go:U4 | cli-go.yaml:47 | package-level `var listCommand = &cli.Command{Action: listAction}` named in `Commands: []*cli.Command{..., listCommand}` | PARTLY | `listAction` has a field-ref `calls` edge from the variable `listCommand` and nothing else. The variable has no incoming edge: an unkeyed element that names a VARIABLE gets no edge (META-mino binds only functions). So it is unreachable from `main`. No entry. | fixture `urfave-cli`; real runc: 17 package-level `cli.Command` variables (`app.Commands = []cli.Command{checkpointCommand, ...}`), 17 inline `Action: func`; 0 of the 17 variables have an incoming edge, 0 reachable from `main` |
| go:U5 | cli-go.yaml:55 | `Action: serveAction` (named) | PARTLY | field-ref `calls` from the enclosing function or variable (U1 / U4); no entry. | fixture `urfave-cli`; sops and runc have 0 named `Action:` |
| go:U6 | cli-go.yaml:55 | `Action: func(c *cli.Context) error { ... }` (inline) | PARTLY | calls attributed to the enclosing function (`main` -> `inlineHelper`) or variable (U4); no entry. | fixture `urfave-cli`; real sops: 16 inline `Action: func`, all inside `main` (lines 77-2110), whose edges go out from `main`, which is the entry; runc: 17, all under unreachable variables (U4) |
| go:U7 | (outside the row's regex) | `Before: beforeHook` (also `After:`) | PARTLY | as U5 (`main` -> `beforeHook`). | fixture `urfave-cli`; runc `app.Before = func ...` inline in `main` |
| go:K1 | cli-go.yaml:60 | the program start that calls `ctx := kong.Parse(&cli)` | DETECTED | `main` is a `main_function` entry from `main-functions.yaml` (Go `main` in `package main`), not from anything kong-specific. A `kong.Parse` outside `main` would not be marked: not measured. | fixture `kong` only (no local repository depends on `alecthomas/kong`) |
| go:K2 | cli-go.yaml:60 | the grammar struct `kong.Parse(&cli)` receives: fields tagged `cmd:""` whose types' `Run` methods kong calls by reflection (`func (s *ServeCmd) Run(g *Globals) error`) | NOT DETECTED | `ServeCmd.Run`, `ListCmd.Run`: `in: -`, no concept, no entry; their callees are unreachable. The command STRUCTS are `library_export` entries only because they are exported (in `package main`, F7). | fixture `kong` only |
| go:K3 | cli-go.yaml:64 | `ctx.Run(&cli.Globals)` on the `*kong.Context`, the dispatch call | NOT DETECTED | resolves to an external `Run`. Its targets are the K2 methods, which nothing marks. | fixture `kong` only |
| go:K4 | cli-go.yaml:64 | `*kong.Context` as the parameter of a command's `Run` (`func (l *ListCmd) Run(ctx *kong.Context) error`) | NOT DETECTED | `ListCmd.Run`: `in: -`, no concept. Kong also binds other parameter types (`*Globals` in `ServeCmd.Run`), so this shape covers only some commands. | fixture `kong` only |
| go:W1 | go-web.yaml:62 | gin `r.Use(requestID)`, named `func(c *gin.Context)` | PARTLY | fref-arg `calls` `ginServer` -> `requestID` (0.70); reachable from `main` here. No `middleware` concept, no `middleware_handler` entry. | fixture `go-web`; real `forgejo/routers/private` (analysed alone): `r.Use(CheckInternalToken)` gives fref-arg `calls` `Routes` -> `CheckInternalToken`; its only entry is `library_export` |
| go:W2 | go-web.yaml:62 | `r.Use(authRequired())`, a local factory that returns the closure (the common gin shape) | PARTLY | an ordinary `calls` edge to the factory; the closure is inside it. No entry. | fixture `go-web`; real `gin-realworld`: `v1.Use(users.AuthMiddleware(false))` and `(true)`: `AuthMiddleware` reachable from `main` by `calls` (ast_call), its only entry `library_export` |
| go:W3 | go-web.yaml:62 | `r.Use(func(c *gin.Context) { ... })` (inline) | PARTLY | calls attributed to the registering function (`ginServer` -> `inlineGinHelper`); no entry. | fixture `go-web`; real: forgejo `routers/api/packages/api.go:116` has this shape (not analysed) |
| go:W4 | go-web.yaml:62 | echo `e.Use(echoTiming)`, `func(next echo.HandlerFunc) echo.HandlerFunc` | PARTLY | as W1 (fref-arg `echoServer` -> `echoTiming`). | fixture `go-web` only (vpnkit is the one local echo user; not analysed) |
| go:W5 | go-web.yaml:62 | chi `router.Use(chiLogger)`, `func(next http.Handler) http.Handler` | PARTLY | as W1 (`chiServer` -> `chiLogger`). | fixture `go-web`; real as W1 (forgejo's router is chi-based) |
| go:W6 | (outside the row's regex) | `Use` on a receiver the regex does not name: a group `api.Use(apiOnly)`, `v1.Use`, `m.Use`, `ginHandler.Use` | PARTLY | as W1 (the edge does not depend on the receiver name). | fixture `go-web`; grep, non-test non-vendor: forgejo 9 `r.Use(`, 3 `m.Use(`, 4 other receivers; milvus 6 `ginHandler.Use(`, 1 `apiv1.Use(`; gin-realworld 2 `v1.Use(` |
| go:W7 | go-web.yaml:62 | the `.Middleware(...)` alternative of the regex | not measured | I found no framework or code with this call: 0 `.Middleware(` lines in forgejo, milvus or gin-realworld. I did not determine what the row meant. | none |
| go:W8 | go-web.yaml:66 | `r.Use(gin.Logger(), gin.Recovery())` | NO USER CODE | Gin's own middleware constructors; the fixture shows only external `Logger` / `Recovery` symbols. | fixture `go-web` |
| go:W9 | go-web.yaml:66 | `r.Group("/admin", gin.BasicAuth(gin.Accounts{"u": "p"}))` | NO USER CODE | library middleware configured with data (an accounts map). | fixture `go-web` |
| go:W10 | (outside the row's regex) | `r.Use(gin.CustomRecovery(recoverHandler))`: library middleware that calls a LOCAL handler | PARTLY | fref-arg `ginServer` -> `recoverHandler`; no entry. | fixture `go-web` only |
| go:W11 | go-web.yaml:70 | `echo.middleware.X`. Go has no such selector: Echo's middleware live in package `github.com/labstack/echo/v4/middleware`, called `middleware.Logger()`, `middleware.Recover()` | NO USER CODE | Library middleware; the regex as written matches no Go expression. | fixture `go-web` (`e.Use(middleware.Logger())`: external symbols only) |
| go:W12 | (outside the row's regex) | `e.Use(middleware.BasicAuth(validateUser))`: library middleware that calls a LOCAL validator | PARTLY | fref-arg `echoServer` -> `validateUser`; no entry. | fixture `go-web` only |
| go:W13 | go-web.yaml:90 | `context.WithTimeout(ctx, d)`, `WithCancel`, `WithValue`, `WithDeadline` | NO USER CODE | These derive a `context.Context` from values. No function is passed, so nothing runs later. (`context.AfterFunc(ctx, f)` does take a function; it is not in the regex and was not measured.) | fixture `go-web` |
| go:W14 | go-web.yaml:102 | `go worker(ch)` (named function) | DETECTED (as a call) | `go.py` walks the go statement's `call_expression` like any call: `calls` `startWorkers` -> `worker`, evidence `ast_call`, 0.85. The edge carries no goroutine marker (F10). No `background_task` entry, and none is needed. | fixture `go-web`; real runc (non-vendor, non-test): 5 of 5 `go <name>(...)` statements have a `calls` edge to the callee at that line |
| go:W15 | go-web.yaml:102 | `go p.loop()` (method on a typed local) | DETECTED (as a call) | as W14 (`startWorkers` -> `poller.loop`, `typed_receiver`). | fixture `go-web`; real runc: 3 `go t.copyIO(...)` among the 5 |
| go:W16 | go-web.yaml:102 | `go func() { ... }()` (inline) | DETECTED (as a call) | the literal's calls are attributed to the enclosing function (`startWorkers` -> `goInlineHelper`). | fixture `go-web`; runc has 21 such literals, not checked one by one |
| go:W17 | go-web.yaml:106 | robfig/cron `c.AddFunc("@every 1m", reportJob)` | PARTLY | fref-arg `startCron` -> `reportJob`; no `scheduled_task` concept or entry. robfig/cron is not a framework in `profile.py`; `go-web.yaml` loads only when gin / echo / fiber / chi / gorilla is detected (F11). | fixture `go-web` only; real: argo-cd has 1 AddFunc (`controller/metrics/metrics.go:333`, inline, receiver `m.cron`), not analysed |
| go:W18 | go-web.yaml:106 | `c.AddFunc("@hourly", func() { ... })` (inline) | PARTLY | calls attributed to `startCron`; no entry. | fixture `go-web` only |
| go:W19 | go-web.yaml:106 | `c.Schedule(cron.Every(5), cron.FuncJob(cleanupJob))` | PARTLY | fref-arg `startCron` -> `cleanupJob` (from the nested argument); no entry. | fixture `go-web` only |
| go:W20 | (outside the row's regex) | `c.AddJob("@daily", syncJob{})`: cron calls the local type's `Run()` | NOT DETECTED | `syncJob.Run`: `in: -`, no concept, no entry; unreachable. | fixture `go-web` only |
| go:W21 | (outside the row's regex) | the same `AddFunc` on a receiver not named `c` (`scheduler.AddFunc`, argo-cd `m.cron.AddFunc`) | PARTLY | as W17 (the edge does not depend on the receiver name; recorded in the fixture as `call_lines [33, 44]` on one edge). | fixture `go-web` |

### Ruby (cli-ruby, grape, sinatra, graphql-ruby)

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| ruby:T1 | cli-ruby.yaml:22 | Thor `desc "hello NAME", "..."` + the public method after it, class `< Thor` | PARTLY | The class is a `cli_command` entry (cli-ruby.yaml `base_class: ^Thor$`); the command method is not an entry and has no caller (`CLI#hello`, `CLI#version`, `Remote#add`, `Remote#list` all `in: -`). Missing: the method. | fixture `thor` |
| ruby:T2 | cli-ruby.yaml:22 | as T1 in a class that inherits an app base (`Accounts < Base`, `Base < Thor`) | NOT DETECTED | Neither class nor method: base_classes is `['Base']`, the row matches the text `Thor`. The abstract `Base` gets the `cli_command` entry instead (finding 2). | fixture `thor`; real `whole_bunch_of_repos/mastodon/lib/mastodon/cli` (copy + Gemfile): 17 classes `< Base`, 0 carry a concept; 48 methods follow a `desc`, 0 entries, 48 with no incoming edge; the one entry is `Base` |
| ruby:T3 | (outside the row's regex) | a public Thor method with no `desc` (Thor still makes it a command, with a warning) | PARTLY | as T1 (`CLI#undocumented` `in: -`). | fixture `thor` only |
| ruby:T4 | cli-ruby.yaml:38 | `subcommand "remote", Remote`, `Remote < Thor` | DETECTED | The registered class is a `cli_command` entry through the `^Thor$` base_class row (not through `subcommand`); its commands are T1. | fixture `thor` only: both local Thor apps register only app-base subclasses (T5) |
| ruby:T5 | cli-ruby.yaml:38 | `subcommand "accounts", Accounts`, `Accounts < Base < Thor` | NOT DETECTED | as T2. | fixture `thor`; real mastodon: 16 `subcommand` lines, 16 target classes `< Base`, 0 marked; `gitlab/gems/gitlab-backup-cli` (grep only): 2 `subcommand` lines, targets inherit `Commands::Command < Thor` |
| ruby:T6 | cli-ruby.yaml:42 | Thor `namespace :mycli` | NO USER CODE | Sets the namespace string under which the class's commands are looked up; names no code. | fixture `thor` |
| ruby:T7 | cli-ruby.yaml:89 | Thor `register(Generator, "generate", usage, desc)` | NOT DETECTED | `Generator < Thor::Group`: no concept, its methods `in: -`. | fixture `thor` only |
| ruby:T8 | (outside the row's regex) | a `Thor::Group` subclass: every public method runs in order | NOT DETECTED | No row names `Thor::Group` (base_classes `['Thor::Group']`). | fixture `thor` only |
| ruby:G1 | cli-ruby.yaml:63 | GLI `desc 'Add a task'` | NO USER CODE | A string describing the next `command`. | fixture `gli` |
| ruby:G2 | cli-ruby.yaml:47 | GLI `command :add do \|c\| ... end` | NO USER CODE | The block is a configuration block GLI runs while the file loads (its calls anchor on the class `App`); the code GLI calls on invocation is the action, G4. | fixture `gli` |
| ruby:G3 | cli-ruby.yaml:47 | nested `c.command :done do \|d\| ... end` (receiver `c.`, outside the row's bare name) | NO USER CODE | as G2. | fixture `gli` |
| ruby:G4 | cli-ruby.yaml:67 | GLI `c.action do \|global, options, args\| ... end` | NOT DETECTED | No symbol for the block; its calls are anchored on class `App` (`App -> Todo.add`), which has no entry and no caller. | fixture `gli` only (no local GLI repository) |
| ruby:G5 | (outside the row's regex) | GLI global hooks `pre do`, `on_error do` (also `post`, `around`, not in the fixture) | NOT DETECTED | as G4. | fixture `gli` only |
| ruby:G6 | (outside the row's regex) | the GLI app class, `extend GLI::App` (or top-level `include GLI::App`) | NOT DETECTED | `included_modules: ['GLI::App']` is recorded; no row reads it. | fixture `gli` only |
| ruby:C1 | cli-ruby.yaml:47 | Commander `command :hello do \|c\| ... end` | NO USER CODE | Configuration block, as G2. Commander is not detected at all (`commander` is not in the cli-ruby gem list). | fixture `commander` |
| ruby:C2 | cli-ruby.yaml:63 | Commander `c.description = '...'` (its spelling of desc) | NO USER CODE | A string. | fixture `commander` |
| ruby:C3 | cli-ruby.yaml:67 | Commander `c.action do \|args, options\| ... end` | NOT DETECTED | Framework not detected; block has no symbol. `GreetCLI#run` has only a `connectivity_based` entry (confidence 0.5). | fixture `commander` only |
| ruby:D1 | cli-ruby.yaml:89 | dry-cli `register 'version', Version`, `Version < Dry::CLI::Command` | PARTLY | On a dry-cli project nothing: cli-ruby is demoted to `dev_frameworks` (finding 4), 0 concepts. With cli-ruby loaded (`dry-cli-forced`) the class is a `cli_command` entry (base_class `^Dry::CLI::Command$`); its `#call`, the code dry-cli runs, is not an entry and has no caller. | fixtures `dry-cli`, `dry-cli-forced` only |
| ruby:D2 | cli-ruby.yaml:89 | `register 'generate' do \|prefix\| prefix.register 'config', Generate::Config end` | PARTLY | as D1 (`Config` entry only in `dry-cli-forced`; `Config#call` `in: -`). | fixtures `dry-cli`, `dry-cli-forced` only |
| ruby:D3 | cli-ruby.yaml:89 | registered class inherits an app base (`Start < Base < Dry::CLI::Command`) | NOT DETECTED | Even when loaded: `Start` has no concept; abstract `Base` is the entry (finding 2). | fixture `dry-cli-forced` only |
| ruby:P1 | cli-ruby.yaml:72 | `OptionParser.new do \|opts\| ... end` | NO USER CODE | The constructor yields the parser to configure it; the handlers are the `opts.on(...) { }` blocks, which the surviving `on` row (concept `option`) names and which cannot fire either (finding 11). Calls inside the `on` blocks are anchored on the enclosing method (`Runner.run -> Runner.set_name`). | fixture `optparse` |
| ruby:P2 | cli-ruby.yaml:80 | `parser.parse!(argv)` / `OptionParser.new { }.parse!` | NO USER CODE | Runs the `on` handlers registered before; registers nothing. The regex `^parse!?$` also names `JSON.parse`, `URI.parse`, `Date.parse`. | fixture `optparse` |
| ruby:P3 | (outside the row's regex) | the program start: a launcher with only top-level code, `bin/tool.rb`: `Tool::Runner.run(ARGV)` | NOT DETECTED | The file has no symbol, so `ruby.py` drops it before the edge pass (INV-zopiz): `Runner.run` has no caller and only a `connectivity_based` entry (0.5). The extensionless `exe/mycli` of the thor fixture is not discovered (WI-pogad). | fixtures `optparse`, `thor` |
| ruby:P4 | (outside the row's regex) | a script with top-level code and a `def` (`bin/inline_tool.rb`) | NOT DETECTED | The file is kept and its calls anchor on the file node, but no Ruby file is ever an entry (`script_module` is TS/JS only). | fixture `optparse` |
| ruby:R1 | grape.yaml:55 | Grape `before do ... end`, `after do ... end` | NOT DETECTED | No symbol for the block. Calls with arguments or a bang anchor on the API class (`authenticate!` has `calls:1`); a bare no-argument helper call gets no edge (`log_request` in `after do`: `in: -`). The API class itself is a `controller` entry (grape.yaml `base_class: ^Grape::API$`). | fixture `grape`; `gitlab/lib/api` (grep): 201 `before do/{`, 9 `after do/{` |
| ruby:R2 | grape.yaml:55 | `before_validation do`, `after_validation do` | NOT DETECTED | No symbol, no entry. The names collide with `RAILS_CALLBACK_METHODS` (ruby.py:194-213), so the Rails block-callback extractor gives `dispatches_to API -> coerce_params / check_params` (finding 8). | fixture `grape`; gitlab/lib/api (grep): 32 `after_validation`, 0 `before_validation` |
| ruby:R3 | grape.yaml:59 | `rescue_from NotFound, with: :handle_not_found` | NOT DETECTED | `handle_not_found` `in: -`, no concept. | fixture `grape`; gitlab/lib/api (grep): 2 (`import_github.rb:10-11`) |
| ruby:R4 | grape.yaml:59 | `rescue_from ArgumentError do \|e\| ... end` | NOT DETECTED | Inline block; calls anchor on the class. | fixture `grape`; gitlab/lib/api (grep): 39 |
| ruby:R5 | grape.yaml:59 | `rescue_from Teapot, with: ->(e) { ... }` | NOT DETECTED | Inline lambda. | fixture `grape`; gitlab/lib/api (grep): 0 |
| ruby:R6 | grape.yaml:67 | `format :json` | NO USER CODE | Selects a built-in format. | fixture `grape` |
| ruby:R7 | grape.yaml:67 | `content_type :csv, 'text/csv'` | NO USER CODE | Maps a format name to a MIME type. The same name inside an endpoint (`content_type 'application/xml'`, gitlab `nuget_project_packages.rb:182`) sets a response header. | fixture `grape` |
| ruby:R8 | grape.yaml:67 | `formatter :csv, CsvFormatter` (a local module with `self.call(object, env)`) | NOT DETECTED | `CsvFormatter.call` `in: -`. | fixture `grape`; gitlab/lib/api (grep): 1 (`api.rb:194`, the class lives in `lib/gitlab`) |
| ruby:R9 | grape.yaml:67 | `formatter :txt, ->(object, env) { ... }` | NOT DETECTED | Inline lambda. | fixture `grape`; gitlab/lib/api (grep): 2 (`ci/runner.rb:225`, `helm_packages.rb:23`) |
| ruby:X1 | (grape.yaml:59 construct, Rails) | Rails controller `rescue_from E, with: :render_not_found` | NOT DETECTED | `render_not_found` `in: -`; the `before_action :authenticate_user` next to it gets `dispatches_to` (`_extract_rails_callbacks`), `rescue_from` is not in that extractor's list. | fixture `rails-rescue`; `gitlab/app/controllers` (grep): 23 |
| ruby:X2 | (grape.yaml:59 construct, Rails) | Rails `rescue_from E do \|e\| ... end` | NOT DETECTED | Inline block; calls with arguments anchor on the class. | fixture `rails-rescue`; gitlab/app/controllers (grep): 18 |
| ruby:S1 | sinatra.yaml:44 | `before do`, `before '/admin/*' do`, `after do` in a `Sinatra::Base` subclass | NOT DETECTED | No symbol for the block; calls with arguments anchor on `App`, which has no concept or entry (sinatra.yaml has no base_class row). | fixture `sinatra` only (no local Sinatra repository) |
| ruby:S2 | sinatra.yaml:44 | classic top-level `before do ... end` | NOT DETECTED | Calls anchor on the file node. | fixture `sinatra` only |
| ruby:S3 | sinatra.yaml:52 | `error AppError do`, `error 500 do` | NOT DETECTED | as S1; the bare helper call `render_500` gets no edge (`in: -`), `render_error(...)` gets `calls` from `App`. | fixture `sinatra` only |
| ruby:S4 | sinatra.yaml:52 | `not_found do ... end` (class and classic) | NOT DETECTED | as S3 (`render_not_found` `in: -`). | fixture `sinatra` only |
| ruby:Q1 | graphql-ruby.yaml:69 | `field :full_name, ...` (also the block form `field :posts do argument ... end`) resolved by the same-named method of the type | PARTLY | The type class is a `graphql_server` entry only when its base TEXT is `Types::BaseObject` or `GraphQL::Schema::Object` (`QueryType`, written `< Types::BaseObject`); `UserType`, written `< BaseObject` inside `module Types`, has nothing (finding 6). The resolver methods are never entries and have no caller (`QueryType#user`, `UserType#full_name`, `UserType#posts` `in: -`). | fixture `graphql-ruby`; real `gitlab/app/graphql` (copy + gitlab Gemfile + one added file requiring graphql, see finding 3): 2973 `field :` lines, 392 with a same-named method in the file, 360 of those with no incoming edge, 0 entries; 27 of 1045 classes with a graphql base carry a concept |
| ruby:Q2 | graphql-ruby.yaml:69 | `field :email` with no method on the type, and `field :nickname, method: :nick`: graphql-ruby calls a method of the backing object | NOT DETECTED | The code run is the backing object's method (a model attribute or a repository method), whose class the type names nowhere. No static detector proposed. | fixture `graphql-ruby`; gitlab (grep): 352 `method: :x` options |
| ruby:Q3 | graphql-ruby.yaml:69 | `field :posts_count, Integer, resolver_method: :count_posts` | NOT DETECTED | `count_posts` `in: -`. | fixture `graphql-ruby`; gitlab (grep + map): 49 `resolver_method:`, 43 targets found in the same file, 43 with no incoming edge |
| ruby:Q4 | graphql-ruby.yaml:69 | `field :search, resolver: Resolvers::Search` | PARTLY | The resolver class is a `graphql_server` entry when its base text is `GraphQL::Schema::Resolver` or `Resolvers::Base` (fixture `Search`). On gitlab the resolvers inherit `BaseResolver`: 1 of 245 classes under `resolvers/` carries `graphql_resolver` (`BaseResolver` itself); their `#resolve` overrides do get `dispatches_to` from `BaseResolver#resolve` (type-hierarchy linker), 163 of 165 have an incoming edge. | fixture `graphql-ruby`; gitlab (forced load): 275 `resolver:` options |
| ruby:Q5 | graphql-ruby.yaml:69 | legacy `.define` DSL: `field :title, types.String do resolve ->(obj, args, ctx) { ... } end` | NOT DETECTED | Inline lambda; calls anchor on the file. The bare `resolve` call is bound to an unrelated `Search#resolve` (finding 1). The `.define` DSL was removed in graphql-ruby 2.0 (from memory, not checked here). | fixture `graphql-ruby`; gitlab (grep): 0 `.define do` |
| ruby:Q6 | graphql-ruby.yaml:73 | `argument :limit, Integer, required: false` | NO USER CODE | Declares an input argument. | fixture `graphql-ruby` |
| ruby:Q7 | (outside the row's regex as a shape) | `argument :sort, String, prepare: :normalize_sort` (also `prepare: ->`) | NOT DETECTED | `normalize_sort` `in: -`. | fixture `graphql-ruby`; gitlab (grep): 45 `prepare:`, all lambdas |
| ruby:Q8 | graphql-ruby.yaml:77 | class-API connection: `field :users, UserType.connection_type` + `def users` | PARTLY | as Q1 (`QueryType#users` `in: -`, class entry). The `connection` call itself exists only in the legacy DSL (Q9). | fixture `graphql-ruby`; gitlab (grep): 388 `connection_type` |
| ruby:Q9 | graphql-ruby.yaml:77 | legacy `connection :items, ItemType.connection_type do resolve ->(...) end` | NOT DETECTED | Inline lambda, as Q5. | fixture `graphql-ruby`; gitlab (grep): 0 |

### Scala and Kotlin (akka-http, play, ktor)

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| jvm:K4 | akka-http.yaml:42 | `parameter("q") { q => ... }` | NO USER CODE | Extracts a query parameter and passes its value into the inner route block; the block is part of the route tree (0028 K1/K2), not a registration of its own. Its call `search(q)` lands on an external symbol (F1). | fixture `akka-http` (ApiRoutes.scala:55) |
| jvm:K5 | akka-http.yaml:42 | `parameters("page".as[Int], "size".as[Int]) { (p, s) => ... }` | NO USER CODE | as K4 (`ApiRoutes.listPage in: -` through F1). | fixture `akka-http` (:59) |
| jvm:K6 | akka-http.yaml:42 | `formField("email") { ... }`, `formFields("user", "pass") { ... }` | NO USER CODE | as K4 for form fields (`ApiRoutes.login`, `ApiRoutes.register in: -` through F1). | fixture `akka-http` (:64, :69) |
| jvm:K7 | akka-http.yaml:46 | `entity(as[T]) { body => ... }` | NO USER CODE | Unmarshals the request body into the inner block, as K4. | fixture `akka-http` (:75, :143) |
| jvm:K8 | akka-http.yaml:46 | `handleWith(createItemBare)` (bare method name, automatic eta-expansion) | NOT DETECTED | The function IS the request handler (akka unmarshals, calls it, marshals its result). No edge ends on it in a `def` (`createItemBare in: -`, :154) or in a class-body `val` (`createItemFn in: -` on the first run, before :152 added an `f _` reference to it); no entry. Needs F2's reference edge (G2), plus a route (0028 K1) for a fitting kind. | fixture `akka-http` (ApiRoutes.scala:81, :154); real: 0 `handleWith(` in tapir and playframework |
| jvm:K9 | akka-http.yaml:46 | `handleWith(createItemFn _)` (explicit eta-expansion) | PARTLY | Inside a `def`: `references ApiRoutes.methodRoutes -> ApiRoutes.createItemFn` (`scala.py` postfix emitter, evidence `eta_expansion`). In a class-body `val` the emitter does not run (it needs an enclosing function). No entry, no route. | fixture `akka-http` (ApiRoutes.scala:152) |
| jvm:K10 | akka-http.yaml:54 | `handleWebSocketMessages(echoFlow)` with `val echoFlow: Flow[Message, Message, _]` | NOT DETECTED | The flow is the WebSocket handler. `ApiRoutes.echoFlow in: -` (class-body and `def` forms); no `websocket_handler` entry; the flow's body calls go to the class (`calls ApiRoutes -> map`). Needs G2 (field reference) and G5 (label). | fixture `akka-http` (:86, :156); real: tapir 3 `handleWebSocketMessages(` (2 in tapir's own interpreters with a parameter `flow`, 1 inline in perf-tests), playframework 0 |
| jvm:K11 | akka-http.yaml:54 | `handleWebSocketMessages(greeterFlow())` with `def greeterFlow(): Flow[...]` | NOT DETECTED | The call `greeterFlow()` in the class-body `val` resolves to an external symbol (F1), so `ApiRoutes.greeterFlow in: -`. Needs G1 (edge) and G5 (label). | fixture `akka-http` (:90) |
| jvm:K12 | akka-http.yaml:54 | `handleWebSocketMessages(Flow[Message].map(m => m))` inline | NOT DETECTED | Label only: no `websocket_handler` entry; its calls are attributed to the enclosing `val`/class. Needs G5. | fixture `akka-http` (:94); real: tapir perf-tests `PekkoHttp.scala:30`, 1 |
| jvm:K13 | akka-http.yaml:58 | `Http().newServerAt(h, p).bind(route)` inside `def main(args: Array[String])` | PARTLY | The enclosing program start IS an entry: `main_function` on `Main.main` (main-functions.yaml Scala row). Missing: no edge from `main` to the bound route (`new ApiRoutes().routes`: Scala emits no construction edge, WI-josik; no field-read edge), so `ApiRoutes` and `ApiRoutes.routes` read `in: -`. | fixture `akka-http` (Main.scala:9-13); real: no local server start in a `def main` (tapir's 19 example server starts are all K15) |
| jvm:K14 | akka-http.yaml:58 | the same start in the body of `object AppServer extends App` | NOT DETECTED | The object carries `base_classes: ['App']` and no concept; its body's calls are anchored on the object (`calls AppServer -> newServerAt`), which reads `in: -`. Also `appRoutes()` goes to an external symbol (F1). Needs G3b. | fixture `akka-http` (Main.scala:17-21); real: no akka start in an `App` object locally; `object X extends App` occurs in 1541 Scala files under `~/whole_bunch_of_repos` (1498 in the scala3 compiler's tests, 18 tapir, 16 pekko) |
| jvm:K15 | akka-http.yaml:58 | the same start (`.bindFlow(route)`) in a Scala 3 `@main def` | NOT DETECTED | `meta.decorators` holds `main`; no row matches it; no entry. Needs G3a. | fixture `akka-http` (Scala3Main.scala:9); real: `tapir/examples`, 46 functions with the `main` decorator, 0 entries (0 carry any concept); all 19 example files that call `newServerAt` start it from an `@main def` |
| jvm:K16 | (outside the row's regex) | `Http().bindAndHandle(route, host, port)` in `object LegacyServer { def start() }` | PARTLY | An entry exists, but by the object's NAME: `broker_lifecycle_by_name@naming-conventions` (naming-conventions.yaml:66, `\w*Server\.(startup\|start\|run\|shutdown)`) gives kind `controller`, label "Server lifecycle (by name)". Nothing reads `bindAndHandle`; renamed to `Boot.run` it would be no entry. | fixture `akka-http` (Main.scala:24-29); real: 0 `bindAndHandle(` in tapir and playframework |
| jvm:K17 | akka-http.yaml:74 | `cors() { route }` (akka-http-cors `CorsDirectives.cors`) | NO USER CODE | A library directive that adds CORS headers around the inner route; registers nothing of the repository. | fixture `akka-http` (:98) |
| jvm:K18 | akka-http.yaml:82 | `handleExceptions(apiExceptionHandler) { route }` with `val apiExceptionHandler = ExceptionHandler { case ... }` | NOT DETECTED | The handler value is what akka calls on an exception. `ApiRoutes.apiExceptionHandler in: -` (class-body and `def` forms); no `error_handler` entry. Needs G2 + G5. | fixture `akka-http` (:27, :104, :158); real: 0 `handleExceptions(` in tapir and playframework |
| jvm:K19 | akka-http.yaml:82 | `handleRejections(apiRejectionHandler) { route }` with `RejectionHandler.newBuilder().handle {...}.result()` | NOT DETECTED | as K18 (`ApiRoutes.apiRejectionHandler in: -`). | fixture `akka-http` (:32, :110); real: 0 in tapir and playframework |
| jvm:K20 | akka-http.yaml:82 | `handleExceptions(ExceptionHandler { case _ => ... }) { route }` inline | NOT DETECTED | Label only (no `error_handler` entry; calls attributed to the enclosing val/class). Needs G5. | fixture `akka-http` (:116) |
| jvm:K21 | (outside the row's regex) | `implicit def myExceptionHandler: ExceptionHandler = ExceptionHandler { ... }` in scope (picked up by `Route.seal` / the binding, no `handleExceptions` call) | NOT DETECTED | `ImplicitHandlers.myExceptionHandler in: -`, no entry (its callee `reportFailure` has `calls:1` from it). Nothing names it in the code, so no call-site detector reaches it; needs a return-type fact (a method or val whose declared type is `ExceptionHandler` / `RejectionHandler`). | fixture `akka-http` (:167); real: 0 `implicit (def\|val) x: ExceptionHandler` in tapir and playframework |
| jvm:K22 | akka-http.yaml:86 | `withRequestTimeout(5.seconds) { route }` | NO USER CODE | Sets the request timeout for the inner route. | fixture `akka-http` (:122) |
| jvm:K23 | akka-http.yaml:86 | `withRequestTimeoutResponse(timeoutResponse)` with `def timeoutResponse(r: HttpRequest): HttpResponse` | NOT DETECTED | Akka calls the function when the timeout fires. `ApiRoutes.timeoutResponse in: -`. Needs G2 (+ G5 for a label). | fixture `akka-http` (:48, :129); real: 0 in tapir and playframework |
| jvm:K24 | akka-http.yaml:86 | `withRequestTimeoutResponse(req => HttpResponse(...))` inline | NOT DETECTED | Label only (`line 136 calls ApiRoutes -> HttpResponse`). Needs G5. | fixture `akka-http` (:136) |
| jvm:K25 | akka-http.yaml:90 | `as[T]` (in `entity(as[String])`, `"page".as[Int]`) | NO USER CODE | Selects an unmarshaller by type. A user-written implicit `Unmarshaller` it would select was not in the fixture (see Not checked). | fixture `akka-http` |
| jvm:K26 | akka-http.yaml:90 | `toStrictEntity(3.seconds) { route }` | NO USER CODE | Buffers the entity for the inner route. | fixture `akka-http` (:142) |
| jvm:P5 | play.yaml:52 | `def socket = WebSocket.accept[String, String] { request => Flow... }` (inline flow) | PARTLY | The route comes from `conf/routes` (`play_routes`): marker `GET /chat`, entry `http_route` "HTTP GET /chat" (a WebSocket labelled HTTP GET, F9). The class is a `controller` entry (play.yaml:35 `base_class` row). Missing: the method `ChatController.socket` reads `in: -` (no `dispatches_to` from the marker, INV-nabas) and has no `websocket_handler` kind; the `WebSocket.accept[..]` call itself emits no edge (F8). The block's calls are attributed to `socket`. | fixture `play` (ChatController.scala:17); real: playframework sbt-test `http-backend-pekko-http`, 2 `WebSocket.accept` methods, 0 route markers (its routes file is `src/main/resources/routes`), both methods recorded as top-level `function`s after a parse error (F10), class `controller` entry |
| jvm:P6 | play.yaml:52 | `def socketOrReject = WebSocket.acceptOrResult[String, String] { request => Future(...) }` | PARTLY | as P5 (`GET /chat2`). | fixture `play` (:22) |
| jvm:P7 | play.yaml:52 | `WebSocket.accept[..] { _ => chatFlow() }` returning a named flow method | PARTLY | as P5 (`GET /chat3`); `chatFlow` has `calls:1` from `namedFlowSocket`, so it is reached exactly when the action method is. | fixture `play` (:29-31) |
| jvm:P8 | play.yaml:60 | `Form(mapping(...)(SignupData.fromForm)(SignupData.toForm))` (user-written mapping functions) | NOT DETECTED | Play calls `fromForm` when binding and `toForm` when filling. Both read `in: -`: a method named as a value gets no edge (F2). The fitting detection is a reference edge (G2), not an entry kind; the deleted row's concept `middleware` does not fit. | fixture `play` (SignupController.scala:30-34); real: lila, 8 lines `)(X.namedFn)(` with a non-`apply` function (grep) |
| jvm:P9 | play.yaml:60 | `Form(mapping(...)(PlainData.apply)(PlainData.unapply))` (case-class functions) | NO USER CODE | The compiler generates `apply` / `unapply` for a case class: no symbol exists to mark (the map has `class PlainData` only). | fixture `play` (:38-40); real: lila, 91 lines `)(X.apply)(` |
| jvm:P10 | play.yaml:60 | `number.verifying("too young", a => isAdult(a))` inline constraint in a Form `val` | NOT DETECTED | Play calls the constraint when binding. The lambda's call `isAdult(a)` sits in a class-body `val`, so it goes to an external symbol (F1): `SignupController.isAdult in: -`. Needs G1. | fixture `play` (:33); real: lila, 32 `verifying(` lines with an inline `=>` (of 196 `verifying(` lines) |
| jvm:P11 | play.yaml:60 | `Form(single("email" -> email))` | NO USER CODE | Builds a form from library mappings only. | fixture `play` (:43) |
| jvm:P12 | (outside the row's regex) | `form.bindFromRequest().fold(errors => ..., data => ...)` | NO USER CODE | The action calls `fold` itself; the closures run on the action's own call (`calls SignupController.submit -> BadRequest`, `-> Ok`). Not a registration. | fixture `play` (:52-55) |
| jvm:P13 | play.yaml:68 | `addToken(Action { ... })` (injected `CSRFAddToken`) | NO USER CODE | Wraps the action; the user code is the action method, which is 0028's P1 (route from `conf/routes`, method `in: -`, INV-nabas). | fixture `play` (:46) |
| jvm:P14 | play.yaml:68 | `checkToken(Action { ... })` (injected `CSRFCheck`) | NO USER CODE | as P13. | fixture `play` (:51) |
| jvm:P15 | play.yaml:68 | `CSRFCheck(Action { ... }, errorHandler)` (the row's literal spelling, companion apply) | NO USER CODE | as P13 for the wrapper itself; the error handler it takes is P16. | fixture `play` (:59) |
| jvm:P16 | (outside the row's regex) | a user `object NoopErrorHandler extends CSRF.ErrorHandler { def handle(...) }` passed to `CSRFCheck` | NOT DETECTED | Play calls `handle` when the token check fails. `NoopErrorHandler.handle in: -`, no entry; the object has `extends -> ErrorHandler` only. Needs a base-class row (`parent_base_class: ^(CSRF\.)?ErrorHandler$` + `method_name: ^handle$`). | fixture `play` (:71-73); real: 4 files in lila + playframework match `CSRF.ErrorHandler\|extends CSRFErrorHandler` (not read further) |
| jvm:P17 | play.yaml:92 | `Messages("key", ..)(messages)`, `messagesApi.preferred(request)`, `messages("key")` | NO USER CODE | i18n lookups inside an action. | fixture `play` (:62-65) |
| jvm:T3 | ktor.yaml:35 | `fun Application.module()` named by `embeddedServer(Netty, module = Application::module)` in `main` | NOT DETECTED | `module in: -`, no entry. `Application::module` is a `navigation_expression` with `::`; `kotlin.py` (~:2853) looks up `Application.module`, which does not exist (an extension function is registered as `module`), so no `references` edge (F6). `meta.extension_receiver = 'Application'` is recorded. Everything the module reaches (`userRoutes`, `chatRoutes`, `handleNotFound`, `shouldLog`, `auditRequest`) hangs off this one missing link. Needs G4a or G4b. | fixture `ktor` (Application.kt:36, :118); real: fixture only |
| jvm:T4 | ktor.yaml:35 | a module named only in `application.conf` (`ktor.application.modules = [ example.AdminModuleKt.adminModule ]`, `EngineMain`) | NOT DETECTED | `adminModule in: -`; nothing reads `ktor.application.modules` (the only build-file fact is `defines_target EngineMain` from `mainClass.set(...)`). The `@Suppress("unused")` spelling (`suppressedModule`) records `extension_receiver` too and is equally `in: -`. Needs G4a or G4c. | fixture `ktor` (AdminModule.kt:10, :42; application.conf:7); real: fixture only |
| jvm:T5 | ktor.yaml:35 | a module called inside the server lambda, `embeddedServer(Netty) { lambdaModule() }` | DETECTED | By call edges: `main -> startLambdaServer -> lambdaModule` (the lambda's call is attributed to `startLambdaServer`), and `main` is a `main_function` entry. No label, none needed for reachability. | fixture `ktor` (AdminModule.kt:22-28); real: fixture only (no Ktor repository locally) |
| jvm:T6 | ktor.yaml:35 | `routing { ... }` / `install(...)` written directly in the `embeddedServer { }` lambda of a top-level `fun main()` | DETECTED | The lambda's calls are attributed to `main` (`calls main -> routing`, `-> directGreeting`), and `main` is a `main_function` entry. The route verbs inside are 0028 T1. | fixture `ktor` (direct/DirectMain.kt:12-16); real: fixture only |
| jvm:T7 | ktor.yaml:35 | the same lambda inside `object DirectServer { @JvmStatic fun main(args) }` | NOT DETECTED | `DirectServer.main` is kind `method`; the Kotlin main row (main-functions.yaml:52) requires `symbol_kind: ^function$`, so no entry and `in: -`. Needs G3c. | fixture `ktor` (AdminModule.kt:31-38); real: `@JvmStatic` followed by `fun main(` in spring-boot 4, detekt 4, okhttp 3 places (grep, non-Ktor) |
| jvm:T8 | ktor.yaml:35 | `intercept(ApplicationCallPipeline.Plugins) { ... }` inline interceptor | NOT DETECTED | Label only (`calls module -> auditRequest`); no middleware entry. Needs G5. | fixture `ktor` (Application.kt:68-70) |
| jvm:T9 | (outside the row's regex) | `val RequestTimer = createApplicationPlugin("RequestTimer") { onCall { call -> ... } }` + `install(RequestTimer)` (Ktor 2's middleware form) | NOT DETECTED | The plugin body's calls are anchored on the FILE node (`calls src/main/kotlin/example/Application.kt -> onCall`); `RequestTimer in: -`; `install(RequestTimer)` gives no reference edge to the variable. Needs G5 (or a variable-reference edge). | fixture `ktor` (Application.kt:81-83, :77) |
| jvm:T10 | ktor.yaml:39 | `fun Route.userRoutes()` called from `routing { userRoutes() }` | NO USER CODE | The repository calls it, not Ktor: `calls module -> userRoutes`. It is reached exactly when `module` is (T3). A blanket "every `Route.` extension is an entry" row would be wrong: the fixture's `fun Route.loginRoutes()` is called by nothing and correctly reads `in: -`. | fixture `ktor` (Application.kt:86) |
| jvm:T11 | (outside the row's regex) | Ktor 1.x receiver: `fun Routing.adminRoutes()` | NO USER CODE | as T10 (`calls adminModule -> adminRoutes`; `extension_receiver = 'Routing'`). | fixture `ktor` (AdminModule.kt:17) |
| jvm:T12 | ktor.yaml:39 | the regex's `Route.get/post/put/delete/patch/head/options/route` alternatives: Ktor's own verb extensions with a handler lambda | NOT DETECTED | Same construct as 0028 T1/T2 (WI-dopuh): 0 usage contexts, no route marker on this fixture either (`get { ... }` in `userRoutes`, `post("/login")`). Not re-triaged. | fixture `ktor`; 0028 T1/T2 |
| jvm:T13 | ktor.yaml:47 | `install(Sessions) { cookie<UserSession>("user_session") }` | NO USER CODE | Plugin configuration; `UserSession` is a data class used as a type. (`cookie<T>(..)` emits no edge, F8.) | fixture `ktor` (Application.kt:53-55) |
| jvm:T14 | ktor.yaml:47 | `call.sessions.set(UserSession(..))` / `.get<T>()` inside a handler | NO USER CODE | A session accessor called inside a handler. | fixture `ktor` (Application.kt:110-113) |
| jvm:T15 | ktor.yaml:51 | `webSocket("/echo") { for (frame in incoming) ... }` inline session | NOT DETECTED | No `websocket_handler` entry, no route marker; the block's calls are attributed to `chatRoutes` (`calls chatRoutes -> echo`), reached only through T3. Needs G5 (and the T1 route walk for the path). | fixture `ktor` (Application.kt:95-99) |
| jvm:T16 | ktor.yaml:51 | `webSocket("/chat") { chatSession() }` with `suspend fun DefaultWebSocketServerSession.chatSession()` | NOT DETECTED | as T15; `chatSession` has `calls:1` from `chatRoutes`. Its `extension_receiver` is missing (the `suspend` modifier, WI-kafum), so a receiver-keyed row could not find it either. | fixture `ktor` (Application.kt:100, :104) |
| jvm:T17 | ktor.yaml:55 | `install(StatusPages) { exception<NotFoundException> { call, cause -> handleNotFound(call, cause) } }` | NOT DETECTED | Label only: `calls module -> handleNotFound`; no `error_handler` entry; the `exception<T>` call itself emits no edge (F8). Needs G6 + G5. | fixture `ktor` (Application.kt:60) |
| jvm:T18 | ktor.yaml:55 | `exception<Throwable> { call, cause -> call.respondText(...) }` inline | NOT DETECTED | Label only, as T17. | fixture `ktor` (Application.kt:61-63) |
| jvm:T19 | (outside the row's regex) | `status(HttpStatusCode.Unauthorized) { call, _ -> ... }` | NOT DETECTED | Label only (`calls module -> status`). Needs G5. | fixture `ktor` (Application.kt:64) |
| jvm:T20 | ktor.yaml:59 | `install(ContentNegotiation) { json() }` (`xml()` the same shape, not in the fixture) | NO USER CODE | Plugin configuration with a library serializer. | fixture `ktor` (Application.kt:38-40) |
| jvm:T21 | ktor.yaml:63 | `install(CORS) { anyHost(); allowHeader(..) }` | NO USER CODE | Plugin configuration. | fixture `ktor` (Application.kt:42-45) |
| jvm:T22 | ktor.yaml:67 | `install(CallLogging) { level = Level.INFO }` | NO USER CODE | Plugin configuration. | fixture `ktor` (Application.kt:48-51) |
| jvm:T23 | (outside the row's regex) | CallLogging `filter { call -> shouldLog(...) }` predicate | NOT DETECTED | Label only (`calls module -> shouldLog`). Ktor calls it per request. Needs G5. | fixture `ktor` (Application.kt:50) |

### Python, PHP, Swift, Rust (aiohttp, bottle, cli, flask-restful, graphql-python, slim, vapor, cli-rust)

| # | Row (old line) | Form (construct) | Verdict | Detected by / what is missing | Evidence |
|---|---|---|---|---|---|
| misc:A1 | aiohttp.yaml:57 | `app.cleanup_ctx.append(db_ctx)` (local async generator run at startup and cleanup) | NOT DETECTED | `db_ctx` refs only, no concept, no entry, 0 usage contexts; dead-code-maybe lists it. The row's concept `middleware` was wrong: a cleanup context is a lifecycle hook (closest entry kind: `event_handler`). Needs D1. | fixture `aiohttp`; real: 0 `cleanup_ctx.append` found locally (grep over whole_bunch_of_repos, curriculum_repos, repos, ALL_REPOS) |
| misc:A2 | aiohttp.yaml:57 (outside the row's regex) | `app.on_startup/on_shutdown/on_cleanup.append(fn)` | NOT DETECTED | `open_pool`, `flush_metrics`, `close_pool` refs only, dead-code lists all three. The surviving `lifecycle` row aiohttp.yaml:54 keys these on `decorator:` and cannot fire (F3). Needs D1. | fixture `aiohttp`; real: `curriculum_repos/uvloop/tests/test_aiohttp.py:80`, 1 occurrence (a test) |
| misc:A3 | aiohttp.yaml:57 (outside the row's regex) | `app.on_startup.append(lambda a: ...)` | NOT DETECTED | A Python lambda has no symbol: nothing marks it and nothing is reported dead. No detector needed for dead code. | fixture `aiohttp` |
| misc:A4 | aiohttp.yaml:73 | `aiohttp_session.setup(app, EncryptedCookieStorage(key))` (the row's comment: aiohttp_session) | NO USER CODE | Installs the library's session middleware with a library storage object. | fixture `aiohttp` |
| misc:A5 | aiohttp.yaml:73 | `aiohttp_jinja2.setup(app, loader=...)` | NO USER CODE | Configures the library's template environment. | fixture `aiohttp` |
| misc:A6 | aiohttp.yaml:73 | `aiohttp_jinja2.setup(app, ..., context_processors=[user_processor])` | NOT DETECTED | `user_processor` (called by aiohttp_jinja2 per render) refs only; dead-code lists it. Needs D1 (list element of a keyword). | fixture `aiohttp`; real: not counted |
| misc:B1 | bottle.yaml:52 | `app.install(stopwatch)` (local decorator-function plugin) | NOT DETECTED | `stopwatch` refs only (its inner `wrapper` has `dispatches_to` from it, so both are unreached). dead-code withholds them only for lack of call evidence. Needs D1. | fixture `bottle`; real: 0 `.install(` in the only local bottle users (ClickHouse tests/integration, 13 files import bottle) |
| misc:B2 | bottle.yaml:52 | `app.install(AuthPlugin())` (local plugin class: `setup`, `apply`, `close` called by bottle) | NOT DETECTED | Class has `instantiates`; `setup`/`apply`/`close` `in: -`. Needs D1 (a call argument that constructs a local class) plus D6 (class to methods). | fixture `bottle` only |
| misc:B3 | bottle.yaml:52 | `app.install(SQLitePlugin(dbfile=...))` | NO USER CODE | A library plugin instance. | fixture `bottle` |
| misc:B4 | bottle.yaml:52 | `bottle.install(fn)` (default app) | NOT DETECTED | refs only. Needs D1. | fixture `bottle` only |
| misc:B5 | bottle.yaml:52 (outside the row's regex) | bare `install(fn)` (`from bottle import install`) | NOT DETECTED | refs only. Needs D1. | fixture `bottle` only |
| misc:T1 | cli.yaml:53 | `app = typer.Typer()` | NO USER CODE | An app object. The variable carries `meta.constructed_from = "typer.Typer"`; no row reads it, none needs to (the commands are T2-T5). | fixture `cli` |
| misc:T2 | cli.yaml:53 (context: surviving rows) | `@app.command()` / `@app.callback()` | DETECTED | cli.yaml:49 / cli.yaml:53 (current lines) -> `command@cli` -> `cli_command`. | fixture `cli`; real: `whole_bunch_of_repos/beads/scripts/generate-newsletter.py` (copied with a `typer` requirements.txt): 1 of 1 `@app.command()` is a `cli_command` |
| misc:T3 | cli.yaml:53 (outside the row's regex) | typer sub-app under another name: `users_app = typer.Typer(); app.add_typer(users_app); @users_app.command("create")` | NOT DETECTED | The rows key the receiver NAME `app`; `create_user` `in: -`, reported dead. Needs D2. | fixture `cli`; real: 0 `add_typer(` locally |
| misc:T4 | cli.yaml:53 (outside the row's regex) | `typer.run(greet)` | NOT DETECTED | `greet` refs only, reported dead. Needs D1. | fixture `cli`; real: 0 `typer.run(` locally |
| misc:T5 | cli.yaml:53 (outside the row's regex) | call form `app.command()(fn)` / `app.command(name="chat")(Chat)` | NOT DETECTED | Not in the fixture; measured on real code. Needs D1 with a curried callee (`app.command()` as callee name). | real: `whole_bunch_of_repos/transformers/src/transformers/cli/transformers.py:28-34`, 7 registrations (5 functions, 2 classes), 0 `cli_command`; `download`, `env`, `version`, `add_new_model_like` `in: -` (subdir analysed alone: `cli` framework not detected, no manifest) |
| misc:T6 | cli.yaml:53 (outside the row's regex; click analogue) | click group subcommand `@cli.command()` / `@cli.group()` on `@click.group() def cli()` | PARTLY | Marked only when the function ALSO carries `@click.pass_context` (cli.yaml:36 stamps `command`); otherwise nothing (fixture `sync`, `remote`, `remote_add` `in: -`, reported dead). Needs D2. | fixture `cli`; real: `feast/sdk/python/feast/cli` (copy + `click` requirement): 38 `@<group>.command` functions, 36 with `command`, all 36 via `click.pass_context`, 2 without; `mlflow/mlflow` (24 files with `@<group>.command`, copied + `click`): 67 subcommands, 1 with `command`, 6 entries by other means, 60 neither |
| misc:F1 | cli.yaml:58 | `fire.Fire(add)` (local function) | NOT DETECTED | `add` refs only; the call is recorded as `instantiates run_function -> Fire`; reported dead. Needs D1. | fixture `cli` |
| misc:F2 | cli.yaml:58 | `fire.Fire(Calculator)` (local class: public methods become commands) | NOT DETECTED | Class `in: -` (a class passed as an argument gets no edge at all: `_emit_function_ref` keeps function/method only), methods `in: -`. WI-sigam (done) joins `subprocess.run(['tool','m'])` to such methods; it makes no entry. Needs D1 (with classes) + D6. | fixture `cli`; real: `whole_bunch_of_repos/virtualbox/.../mesa-24.0.2/.gitlab-ci/lava/lava_job_submitter.py:537` `fire.Fire(LAVAJobSubmitter)` and `bin/structured_logger.py:294` `fire.Fire(StructuredLogger)` (copied + `fire`): both classes `in: -`, `LAVAJobSubmitter.submit` `in: -`; 3 `fire.Fire(` calls in virtualbox (one file is a duplicate) |
| misc:F3 | cli.yaml:58 | `fire.Fire({"multiply": multiply})` | NOT DETECTED | refs only (dict value). Needs D1 (dict element). | fixture `cli` only |
| misc:F4 | cli.yaml:58 | `fire.Fire()` with no argument (every module-level callable is a command) | NOT DETECTED | `cube` `in: -`, reported dead; the file has a `main_guard` entry only. Needs D1b. | fixture `cli` only |
| misc:F5 | cli.yaml:58 | module-level `fire.Fire(solo)` with no `__main__` guard | NOT DETECTED | `solo` refs only; the file gets NO entry (module-level code without a guard). Needs D1 + a file entry. | fixture `cli` only |
| misc:P1 | cli.yaml:63 | `parser = argparse.ArgumentParser(...)` inside `main()` / `build_parser()` | NO USER CODE | Constructs a parser. The program start is already an entry by other means: `main` -> `main_function@main-functions`; `[project.scripts]` -> `package_executable`. | fixture `cli-argparse` (`argtool/cli.py`, `argtool/script.py`) |
| misc:P2 | cli.yaml:63 (outside the row's regex) | `sub.add_parser("init").set_defaults(func=cmd_init)`, dispatched by `args.func(args)` | PARTLY | Reachable: `argparse-dispatch-linker` writes `dispatches_to build_parser -> cmd_init`; not reported dead. No concept, no `cli_command` entry. Works only outside a test-like path (F1). | fixture `cli-argparse`; real: `whole_bunch_of_repos/v8/tools/mb/mb.py` (copied): 9 `set_defaults(func=self.CmdX)`, 9 `dispatches_to`, 0 `cli_command` |
| misc:P3 | cli.yaml:63 (outside the row's regex) | `add_argument("--jobs", type=positive_int)`, `add_argument(action=UpperAction)` (an `argparse.Action` subclass) | NOT DETECTED | `positive_int` refs only; `UpperAction.__call__` `in: -`; both reported dead. Needs D1 (keyword) / D3 (base class `argparse.Action`). | fixture `cli-argparse`; real: not counted |
| misc:R1 | flask-restful.yaml:50 | `abort(404, message=...)` inside a helper or a `Resource` method | NO USER CODE | A helper that raises an HTTPException; recorded as `calls ... -> abort` (external). | fixture `flask-restful` |
| misc:R2 | flask-restful.yaml:50 (outside the row's regex) | `class JsonErrorApi(Api): def handle_error(self, e)` (flask-restful's error hook) | NOT DETECTED | `handle_error` `in: -`, reported dead. `meta.parent_base_classes = ["Api"]` is already recorded. Needs D3 (one YAML row). | fixture `flask-restful` only |
| misc:R3 | flask-restful.yaml:50 (outside the row's regex; Flask's) | `app.register_error_handler(404, on_not_found)` | NOT DETECTED | refs only, reported dead; flask.yaml has only the decorator row `^(app\|blueprint\|bp)\.errorhandler$`. Needs D1. | fixture `flask-restful` only |
| misc:G1 | graphql-python.yaml:74 | `make_executable_schema(type_defs, query, mutation, user_type)` (ariadne) | NO USER CODE | Takes the SDL and the bindable objects; the resolvers reach those objects through G2-G4. | fixture `graphql-python` |
| misc:G2 | graphql-python.yaml:74 (context: surviving rows) | `@query.field("hello")`, `@mutation.field("rename")` | DETECTED | graphql-python.yaml:61 / :65 -> `graphql_resolver` -> `graphql_server`; the graphql-resolver-linker also makes markers `Query.hello`, `Mutation.rename`. | fixture `graphql-python` only (the one local ariadne import is a hypergumbo copy) |
| misc:G3 | graphql-python.yaml:74 (outside the row's regex) | `@user_type.field("fullName")` on `user_type = ObjectType("User")` | PARTLY | Marker `User.fullName` (graphql-resolver-linker regex `@(\w+)_type.field`, type name taken from the VARIABLE name); `resolve_full_name` has no concept, no entry, `in: -`; no edge marker -> function (by the linker's design). graphql-python.yaml:73 covers only `query_type\|mutation_type\|subscription_type`. Needs D2. A bindable named otherwise (`user = ObjectType("User")`) not measured. | fixture `graphql-python` only |
| misc:G4 | graphql-python.yaml:74 (outside the row's regex) | `query.set_field("version", resolve_version)` | NOT DETECTED | refs only. Needs D1. | fixture `graphql-python` only |
| misc:S1 | slim.yaml:39 | `$app->add(function ($request, $handler) {...})` | NOT DETECTED | The closure has no symbol; `php.py` records no usage context and no edge for file-scope `$app->` calls. Nothing is reported dead either. Needs D5 only if the closure should become a marker. | fixture `slim` |
| misc:S2 | slim.yaml:39 | `$app->add(new AuthMiddleware())` (local class implementing `MiddlewareInterface`) | PARTLY | Class -> `middleware@slim` -> `middleware_handler` by slim.yaml:41 (`base_class: ^MiddlewareInterface$`, from `implements`), whether or not it is registered. `AuthMiddleware.process` (what Slim calls) `in: -`; dead-code-maybe (seeds entrypoints) reaches 0 symbols in the fixture: a class entry does not reach its methods (D6). No edge from `index.php` to the class at all (F11). | fixture `slim` only |
| misc:S3 | slim.yaml:39 | `$app->add(TimingMiddleware::class)` (invokable class, no interface) | NOT DETECTED | Class and `__invoke` `in: -`, no concept. Needs D5. | fixture `slim` only |
| misc:S4 | slim.yaml:39 | `$app->add('logRequest')` (named function as a callable string) | NOT DETECTED | `logRequest` `in: -`. Needs D5. | fixture `slim` only |
| misc:S5 | slim.yaml:39 (outside the row's regex) | route middleware `$app->get('/health', 'health')->add(new AuthMiddleware())` | PARTLY | as S2 | fixture `slim` only |
| misc:S6 | slim.yaml:43 | `$app->addMiddleware(new CorsMiddleware())` | PARTLY | as S2 (`CorsMiddleware` entry by interface; `process` `in: -`). | fixture `slim` only |
| misc:S7 | slim.yaml:63 | `$errorMiddleware = $app->addErrorMiddleware(true, true, true)` | NO USER CODE | Adds Slim's built-in ErrorMiddleware. The user code hangs off its result: S8-S10. | fixture `slim` |
| misc:S8 | slim.yaml:63 (outside the row's regex) | `$errorMiddleware->setDefaultErrorHandler(new HttpErrorHandler(...))` (`extends Slim\Handlers\ErrorHandler`, overrides `respond`) | NOT DETECTED | Class and `respond` `in: -`; the surviving error_handler row keys `^ErrorMiddleware$`, not `ErrorHandler`. Needs D3 (base-class row) + D6, or D5. | fixture `slim` only |
| misc:S9 | slim.yaml:63 (outside the row's regex) | `$errorMiddleware->setErrorHandler(HttpNotFoundException::class, 'notFoundHandler')` | NOT DETECTED | `notFoundHandler` `in: -`. Needs D5. | fixture `slim` only |
| misc:S10 | slim.yaml:63 (outside the row's regex) | `setErrorHandler(..., function (...) {...})` | NOT DETECTED | Closure, no symbol (as S1). | fixture `slim` only |
| misc:S11 | slim.yaml:67 | `$app->addRoutingMiddleware()` | NO USER CODE | Slim's built-in routing middleware, no argument. | fixture `slim` |
| misc:S12 | slim.yaml:71 | `$app->addBodyParsingMiddleware()` | NO USER CODE | Slim's built-in body parser. | fixture `slim` |
| misc:S13 | slim.yaml:71 | `$app->addBodyParsingMiddleware(['application/x-yaml' => 'parseYaml'])` (custom parser callables) | NOT DETECTED | `parseYaml` `in: -`. Needs D5. | fixture `slim` only |
| misc:V1 | vapor.yaml:59 | `app.webSocket("echo") { req, ws in ... }` | NOT DETECTED | `swift._extract_vapor_usage_contexts` handles `get/post/put/delete/patch`, `on`, `grouped`, `group` only: no usage context, no marker, no entry. Needs D4. | fixture `vapor`; real: `whole_bunch_of_repos/vapor/Tests/VaporTests`, 8 `webSocket(` registrations, all inline closures (tests); 0 in `Sources/Development` |
| misc:V2 | vapor.yaml:59 | `app.webSocket("chat", onUpgrade: chatHandler)` | NOT DETECTED | `chatHandler` `in: -`. Needs D4 with `onUpgrade:` resolution. | fixture `vapor` only |
| misc:V3 | vapor.yaml:59 | on a grouped builder: `let api = app.grouped("api"); api.webSocket("live") { ... }` | NOT DETECTED | as V1 | fixture `vapor` only |
| misc:V4 | vapor.yaml:59 | `routes.webSocket("feed", onUpgrade: self.feed)` in a `RouteCollection` | NOT DETECTED | `FeedController.feed` `in: -`, reported dead (as is `boot`, although `FeedController` is a `controller` entry: D6). | fixture `vapor` only |
| misc:C1 | cli-rust.yaml:34 | `command!().arg(arg!(...)).get_matches()` in `main` | NO USER CODE | A builder; registers no function. `main` -> `main_function`; `greet` has `calls` from `main`. | fixture `cli-rust-builder` (`src/bin/simple.rs`) |
| misc:C2 | cli-rust.yaml:42 | `Command::new("git").subcommand(Command::new("clone"))` in `fn cli()`, then `match matches.subcommand() { Some(("clone", s)) => run_clone(..) }` in `main` | NO USER CODE | clap registers no handler; the user calls `run_clone`/`run_push` from `main` (`calls` edges). Control: `std::process::Command::new("ls")` has the same path `Command::new`. | fixture `cli-rust-builder` |
| misc:C3 | cli-rust.yaml:42 (outside the row's regex) | `Arg::new("port").value_parser(parse_port)` | NOT DETECTED | `parse_port` refs only, reported dead. Needs a Rust analogue of D1. | fixture `cli-rust-builder` only |
| misc:C4 | cli-rust.yaml:34/:42 (context: surviving rows) | derive form `#[derive(Parser)] #[command(...)] struct Cli`, `#[derive(Subcommand)] enum Commands`, dispatched by `match` in `main` | NO USER CODE | The derive generates the parser; the user dispatches in `main` (`add`/`remove` have `calls`). BUT the surviving annotation rows mark nothing (F4): `Cli` has no concept, and `Cli` / `Commands` are `in: -` (`Cli::parse()` resolves to nothing). | fixture `cli-rust`; real: `whole_bunch_of_repos/sherpa-onnx/rust-api-examples`: 13 `derive(Parser)` structs, 0 concepts, 13 with no in-edge |
| misc:C5 | cli-rust.yaml:34/:42 (outside the row's regex) | `#[arg(value_parser = parse_level)]` | NOT DETECTED | `parse_level` `in: -` (attribute arguments are not scanned for references), reported dead. | fixture `cli-rust` only |

Counts: 348 forms over the 136 rows. DETECTED 13, PARTLY 66, NOT DETECTED 185, NO USER CODE 83,
not measured 1 (go:W7, the `.Middleware(` alternative: 0 occurrences in the three repositories searched).

| Group | Rows | Forms | DETECTED | PARTLY | NOT DETECTED | NO USER CODE | not measured |
|---|---|---|---|---|---|---|---|
| js | 44 | 114 | 1 | 10 | 75 | 28 | 0 |
| elixir | 22 | 33 | 3 | 8 | 17 | 5 | 0 |
| go | 17 | 42 | 4 | 29 | 4 | 4 | 1 |
| ruby | 17 | 48 | 1 | 7 | 29 | 11 | 0 |
| jvm | 20 | 57 | 2 | 6 | 27 | 22 | 0 |
| misc | 16 | 54 | 2 | 6 | 33 | 13 | 0 |

## What the same change builds

One mechanism carries most of it. An analyzer that finds a call handing a LOCAL callable to code outside the
repository asks `framework_patterns.usage_row_names_call(name, language, position)`: could a shipped `usage:` row
match a call of that name at that argument? If so it records a usage context, and the row stamps the concept.
No analyzer holds a table of framework call names any more for these constructs: a new row is enough. JavaScript,
Python, Go, Ruby and Elixir record such contexts. Elixir modules also record what they `use`
(`meta["adopted_modules"]`), read by a new row key, `adopts:` (it also reads Ruby's `included_modules`).

**js**
- registration usage context in js_ts.py + usage rows (express, koa, fastify, hapi, restify, cli-js, electron, socketio): E1, E2, E3, E4, E7, E8, E9, E10, E11, E25, E26, E27, E28, E30, F1, F3, F9, F10, F12, F13, F14, F15, F16, F17, H1, H2, H3, H4, H5, H6, H7, O1, O2, O3, O5, O6, O7, O9, O10, O22, C1, C2, C4, C5, C6, C8, C9, C10, C13, C14, C15, C18, C19, L1, L2, L3, L4, L5, L6, L7, L9, L10, L12 (63)

**elixir**
- `adopted_modules` on the module + `adopts:` rows (phoenix, plug, otp, oban, broadway, absinthe), callback tables: X5, X8, X9, X12, X13, X14, X15, X16, X17, X18, X19, X21, X22, L2, L8 (15)
- registration usage context in elixir.py (a macro naming a local module or function) + usage rows: X4, X7, X10, X11, L3, L4, L5, L6 (8)

**go**
- registration usage context in go.py (literal field, field assignment, call argument) + usage rows (cli-go, go-web, go-cron): C1, C2, C5, C7, C8, U1, U2, U3, U4, U5, W1, W4, W5, W6, W10, W12, W17, W19, W21 (19)

**ruby**
- class-body registration usage context in ruby.py + usage rows (grape, rails, graphql-ruby, cli-ruby): R3, X1, Q1, Q3, Q4, Q7, Q8, T5, T7, D1, D2, D3, R8 (13)
- `parent_base_classes` on public methods + rows (Thor command methods, dry-cli `#call`): T1, T3, T8 (3)
- `adopts:` rows and detection (the GLI and Commander app classes; dry-cli detected): G6 (1)

**jvm**
- program-start rows (main-functions.yaml): K14, K15, T7 (3)
- Ktor module row + `Type::extensionFunction` resolution in kotlin.py: T3, T4 (2)
- Scala: a class-body call reaches the type's own method: K11, P10 (2)
- Scala: a method or field passed as a value is referenced: K8, K9, K10, K18, K19, K23, P8 (7)
- Play CSRF error-handler row: P16 (1)

**misc**
- registration usage context in py.py + usage rows (aiohttp, bottle, cli, flask, graphql-python): A1, A2, B1, B2, B4, B5, T4, T5, F1, F2, F3, F5, G4, R3 (14)
- `decorator_receivers` in py.py + `meta_match` rows (typer sub-apps, click subcommands, ariadne bindables): T3, T6, G3 (3)
- rows over facts already recorded (flask-restful `handle_error`, Slim `ErrorHandler` subclasses): R2, S8 (2)
- Vapor `webSocket` and `use:` handlers in swift.py: V2, V4 (2)

158 of the 251 forms that were NOT DETECTED or PARTLY are served. A form is listed when its named
user code becomes an entry point or gains the caller it lacked on the fixture; what remains short on a listed
form is said in the group's build record.

Measured on real code, base against the change (each group's own runs; stable ids unchanged in every one):

| Repository (tree analysed) | Usage contexts | Map size | Entries gained |
|---|---|---|---|
| polis `server` (express) | 196 → 204 | +0.07% | 7 `middleware_handler` |
| zksync-era `private-rpc` (fastify) | 6 → 9 | +0.27% | 3 `middleware_handler` (plugins) |
| workadventure `desktop/electron` | 4 → 18 | +3.06% | 13 IPC handlers, 1 `webContents` event handler; 13 channel → handler `dispatches_to` |
| knex (commander) | 2 → 13 | +0.02% | 11 `cli_command` |
| beads (cobra, whole repository) | 225 → 284 | +0.05% | 52 `cli_command` (56 handlers carry the concept); 60 cobra `dispatches_to` now start at the command variable |
| forgejo `cmd/` (urfave/cli) | 0 → 63 | +1.19% | 28 `cli_command` (63 handlers carry the concept) |
| milvus `internal/distributed/proxy` (gin) | 5 → 9 | +0.05% | 3 `middleware_handler` |
| mastodon `lib/mastodon/cli` (Thor) | 0 → 16 | +0.87% | 16 `cli_command` (the subcommand classes) |
| mastodon `app/workers` (Sidekiq) | not measured | +1.21% | 45 `background_task` (91 workers carry the concept) |
| gitlab `app/graphql` (graphql-ruby) | 0 → 767 | +2.62% | 76 `graphql_server` net (610 resolvers carry the concept; 23 class-level entries left the capped list) |
| gitlab `app/controllers` (Rails) | 0 → 18 | +0.33% | 16 `error_handler` (`rescue_from ... with:`) |
| plausible (Phoenix) | 280 → 347 | +0.52% | 31 `background_task`, 32 LiveViews, 24 `middleware_handler`, 5 `error_handler`; `http_route` entries in the capped list 49 → 28 |
| livebook (Phoenix) | 84 → 100 | +0.32% | 28 `background_task`, 14 LiveViews, 12 `middleware_handler`, 2 `websocket_handler`; `http_route` 34 → 26 |
| tapir `examples` (Scala 3) | 0 → 0 | +1.7% | 29 `main_function` (`@main`; 46 carry the concept); 78 `references`, 4 calls now reach a local method |
| mlflow click commands (24 files) | 0 → 0 | +1.5% | 37 `cli_command` (67 of 67 subcommands carry the concept, 1 before) |
| transformers `cli` (typer) | 0 → 7 | +0.44% | 7 `cli_command` |
| airflow `providers/fab` (Flask) | 0 → 3 | +0.04% | 3 `error_handler` |

On the two Phoenix trees the routes that left the list were not out-ranked. Every kept entry ranks 1.0, the cap
deals tied places one per source file in turn, and nearly all route markers of a Phoenix application sit in one
file (`router.ex`: 240 of 241 on plausible), while each worker, LiveView and plug has its own.

## What it does not build

- **Inline handlers.** A block, closure or lambda passed inline has no symbol in Ruby, Go, Elixir, Scala,
  Kotlin, PHP or Swift, and only some have one in JavaScript. Its calls are attributed to the enclosing code, so
  nothing in it is unreachable; what is missing is the label. Every inline form stays NOT DETECTED (tracker item
  WI-tavum holds the same decision for tests written as calls).
- **Scala and Kotlin usage contexts** (jvm G5): the akka-http, Play and Ktor labels wait on the mechanism that
  WI-dopuh and WI-pohis hold for the route forms.
- **PHP** (misc D5): `php.py` records no edge for a file-scope call and no usage context for a `$app->` call.
- **A class handed over by value, and reflection** (go D3): `AddJob(T{})`, kong's `cmd:""` struct tags.
- **Reachability from `main`** (go D4, tracker item WI-babuv): the new command entries are islands; a slice
  from `main` still stops at `rootCmd.Execute()`.
- **Supervisor child specs** (elixir D4), **command modules by convention** (js D3: yargs `commandDir`,
  gluegun), **`fastify.decorate`** (js D7), **import-aliased receivers** (js D8), **base rows matched against
  resolved ancestors** (ruby D: a Thor class that inherits an application base class).
- **Fastify websocket routes** (js F19) stay `http_route`: recording the method `WS` renames the route marker and
  moves its stable id.
- **Rails callbacks** (`before_action :x`): the two usage rows that named them were removed instead of given a
  producer. The targets already have a caller (`dispatches_to` from the class), and 527 new entries on one
  controller tree is a ranking decision, not a detection one.
- **Phoenix controller modules**: the `controller` row was not restored. On plausible it pushed 33 of 49 route
  entries out of the capped entry list, and none of its 32 controllers is unrouted.

## Diagnostic findings

- **Entry lists are capped, so every new kind displaces something.** beads: 52 `cli_command` entries in, 33
  `library_export`, 15 `shell_script`, 3 `script_module` and 1 `http_route` out. gitlab `app/graphql`: 23
  class-level schema entries out. tapir examples: 29 `@main` entries took the places of 28 IOApp entries.
  mlflow: 9 click group functions out. The rank formula was not changed.
- **An entry on a class does not make its methods reached** in `dead-code-maybe` (misc D6): a Slim middleware
  class and a Vapor controller are entries while their methods are still listed.
- **Every public module and function of an Elixir APPLICATION is a `library_export` entry** (elixir F1):
  3,898 concepts on plausible. It hid most of the gaps this audit measures on small trees.
- **Rows that still cannot fire.** The surviving call-keyed `decorator:` rows of the same files (WI-sisom's set);
  all seven cli-rust.yaml annotation rows (`rust.py` records `#[derive(Parser)]` as the name `derive`); the
  `lifecycle` concept has no entry mapping.
- **Wrong output seen while measuring**, none changed here: the IPC linker reads comments (WI-pisur); a
  receiverless Ruby DSL call is bound by name to a method of an unrelated class; Go exported symbols in
  `package main` are `library_export` entries; a Play WebSocket endpoint is labelled "HTTP GET".
- **The middleware-chain linker now has more to chain, and chains it by definition order.** It links the
  `middleware` symbols of one file in source-line order. For middleware registered by a call, the order of the
  definitions is not the order of the registrations: on polis `server` its 5 new `references` edges follow the
  order the functions are written in `server-middleware.ts`, while `app.ts` registers them in another order;
  two routers defined in one file are joined into one chain. Not changed here.
- **Framework detection gaps**: an argparse-only project detects no framework; gems Bundler loads in a Rails
  app without a `require` are demoted to development frameworks (graphql-ruby and grape on gitlab).

## What this document does not cover

- Real repositories do not exist locally for hapi, `@koa/router`, vorpal, gluegun, kong, Sinatra, GLI,
  Commander, dry-cli, Ktor, akka-http applications, Slim, ariadne, bottle plugins, or a Vapor WebSocket with a
  named handler. Those verdicts, and the rows built for them, rest on the fixtures.
- Forms not in a fixture are listed in each group's triage notes under "Not checked".
- `dead-code-maybe` and `test-coverage` output were not compared between the arms.
- Whether a registration recorded in a test file should mark production code (Go: 8 contexts on forgejo came
  from `_test.go` files, each naming a real command) is not decided.

## Related

- [0028](0028-deleted-route-rows-triage.md) — the first tranche (the 28 `route` rows).
- `07066b91c` (WI-jilik) — the commit that deleted the rows.
- Triage notes, fixtures, raw tables, build records and A/B output:
  `~/hypergumbo_lab_notebook/movur_dibin_11222026/movur/`.
