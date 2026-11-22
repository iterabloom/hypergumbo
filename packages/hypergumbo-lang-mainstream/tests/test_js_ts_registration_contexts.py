# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""A call that hands a local function to a library is recorded (WI-movur).

``app.use(auth)``, ``ipcMain.handle('ch', h)``, ``program.command('x').action(run)``:
the library calls the function, so the repository holds no caller of it and
it reads as dead. The JS/TS analyzer records each such hand-over as a
registration UsageContext (kind ``call``, the callee as written, the
argument's position, the handler's symbol), and the framework files'
``usage:`` rows say which are a middleware, a command, an IPC handler.

The emission gate is ``usage_row_names_call``: a call is recorded only when a
shipped row names it, so the same shape under a name no row has is not. The
callee must be a library's (a call this pass resolves to a local method is
ordinary code), the argument must be repository code (a library object or a
library factory's result is not), and an inline function is recorded only
where the analyzer already made it a symbol.

Every test runs the analyzer on source. The row tests give its symbols and
contexts to the shipped rows (``enrich_symbols``), one per framework file.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol, UsageContext
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _analyze(tmp_path: Path, files: dict[str, str]):
    for relative, source in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return analyze_javascript(tmp_path)


def _registrations(result) -> list[UsageContext]:
    """The registration contexts: the ones that carry a handler's arity."""
    return [c for c in result.usage_contexts if "handler_arity" in (c.metadata or {})]


def _by_id(result) -> dict[str, Symbol]:
    return {s.id: s for s in result.symbols}


def _seen(result) -> list[tuple[str, str, str]]:
    """(context_name, position, handler name) of each registration context."""
    symbols = _by_id(result)
    return [
        (c.context_name, c.position, symbols[c.symbol_ref].name)
        for c in _registrations(result)
    ]


def _concepts(result, frameworks: set[str]) -> dict[str, set[str]]:
    """Enrich with the shipped rows; concept names per symbol name."""
    symbols = list(result.symbols)
    clear_pattern_cache()
    enrich_symbols(symbols, frameworks, list(result.usage_contexts))
    out: dict[str, set[str]] = {}
    for symbol in symbols:
        for concept in (symbol.meta or {}).get("concepts", []):
            out.setdefault(symbol.name, set()).add(concept["concept"])
    return out


class TestWhatIsRecorded:
    def test_a_named_middleware_and_its_receiver_binding(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const express = require('express');\n"
            "const app = express();\n"
            "function auth(req, res, next) { next(); }\n"
            "function onError(err, req, res, next) { res.end(); }\n"
            "app.use('/api', auth);\n"
            "app.use(onError);\n"
        )})
        contexts = _registrations(result)
        assert _seen(result) == [("app.use", "args[1]", "auth"), ("app.use", "args[0]", "onError")]
        first, second = contexts
        assert first.kind == "call"
        assert first.span.start_line == 5
        assert first.metadata == {
            "receiver": "app", "method": "use", "handler_arity": 3,
            "first_string_arg": "/api",
            "receiver_source": "express()", "receiver_module": "express",
        }
        assert second.metadata["handler_arity"] == 4

    def test_an_inline_handler_is_its_callback_symbol(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const app = require('express')();\n"
            "app.use((req, res, next) => { next(); });\n"
        )})
        assert _seen(result) == [("app.use", "args[0]", "_cb_use")]

    def test_a_handler_imported_from_another_file(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {
            "mw.js": "function auth(req, res, next) { next(); }\nmodule.exports = { auth };\n",
            "app.js": (
                "const { auth } = require('./mw');\n"
                "const app = require('express')();\n"
                "app.use(auth);\n"
            ),
        })
        [context] = _registrations(result)
        assert _by_id(result)[context.symbol_ref].path.endswith("mw.js")

    def test_a_chain_is_collapsed_to_its_root(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"cli.js": (
            "const { program } = require('commander');\n"
            "const yargs = require('yargs');\n"
            "const io = require('socket.io')();\n"
            "function build(dir) {}\n"
            "function serve(argv) {}\n"
            "function onAdmin(socket) {}\n"
            "program.command('build <dir>').description('d').action(build);\n"
            "yargs(process.argv).command('serve', 'd', (y) => y, serve).parse();\n"
            "io.of('/admin').on('connection', onAdmin);\n"
        )})
        seen = _seen(result)
        assert ("program.action", "args[0]", "build") in seen
        assert ("yargs.command", "args[3]", "serve") in seen
        assert ("io.on", "args[1]", "onAdmin") in seen

    def test_functions_in_option_objects_and_arrays(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"server.js": (
            "const fastify = require('fastify')();\n"
            "function stamp(request, h) { return h.continue; }\n"
            "function admin(request, reply) {}\n"
            "function show(request, reply) {}\n"
            "const server = {};\n"
            "server.ext({ type: 'onRequest', method: stamp });\n"
            "server.ext([{ type: 'onPreAuth', method: stamp }]);\n"
            "fastify.get('/admin', { preHandler: [admin] }, show);\n"
        )})
        seen = _seen(result)
        assert ("server.ext", "args[0].method", "stamp") in seen
        assert ("server.ext", "args[0][0].method", "stamp") in seen
        assert ("fastify.get", "args[1].preHandler[0]", "admin") in seen
        # The route's own handler is the route extractor's, not recorded twice.
        assert not [s for s in seen if s[2] == "show"]

    def test_a_bare_callee_is_written_as_its_import(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const mount = require('koa-mount');\n"
            "async function hello(ctx) {}\n"
            "mount('/hello', hello);\n"
        )})
        [context] = _registrations(result)
        assert (context.context_name, context.position) == ("koa-mount", "args[1]")
        assert context.metadata["receiver"] is None
        assert context.metadata["method"] == "mount"


class TestWhatIsNotRecorded:
    def test_a_name_no_row_has_is_not_recorded(self, tmp_path: Path) -> None:
        # The same shape as app.use(auth), under names no shipped row names.
        result = _analyze(tmp_path, {"app.js": (
            "const bus = require('mitt')();\n"
            "function auth(req, res, next) { next(); }\n"
            "bus.subscribe(auth);\n"
            "process.on('exit', auth);\n"
            "const app = require('express')();\n"
            "app.use(auth);\n"
        )})
        assert _seen(result) == [("app.use", "args[0]", "auth")]

    def test_a_call_to_local_code_is_not_a_registration(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "class Pipeline { use(fn) { this.fn = fn; } }\n"
            "const app = new Pipeline();\n"
            "function step() {}\n"
            "app.use(step);\n"
            "const lib = require('express')();\n"
            "lib.use(step);\n"
        )})
        assert _seen(result) == [("lib.use", "args[0]", "step")]

    def test_library_objects_are_not_handlers(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const express = require('express');\n"
            "const helmet = require('helmet');\n"
            "const app = express();\n"
            "const router = express.Router();\n"
            "function auth(req, res, next) { next(); }\n"
            "app.use(helmet());\n"
            "app.use('/v1', router);\n"
            "app.use(auth);\n"
        )})
        assert _seen(result) == [("app.use", "args[0]", "auth")]

    def test_a_bare_callee_bound_by_no_import(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const koaMount = require('koa-mount');\n"
            "async function hello(ctx) {}\n"
            "function mount(path, fn) {}\n"
            "mount('/hello', hello);\n"
            "koaMount('/hello', hello);\n"
            "render(hello);\n"
        )})
        # ``render`` is a name a shipped row has (``^render$``), but no import
        # binds it here: an unbound bare name is not written as itself.
        assert [(c.context_name, c.span.start_line) for c in _registrations(result)] == [
            ("koa-mount", 5)]

    def test_an_inline_function_without_a_symbol(self, tmp_path: Path) -> None:
        # An object-property arrow is not a symbol: nothing to point at.
        result = _analyze(tmp_path, {"server.js": (
            "const server = require('@hapi/hapi').server();\n"
            "server.ext({ type: 'onRequest', method: (request, h) => h.continue });\n"
        )})
        assert _registrations(result) == []


class TestShippedRowsStampTheHandler:
    def test_express(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const app = require('express')();\n"
            "function auth(req, res, next) { next(); }\n"
            "function loadUser(req, res, next, id) { next(); }\n"
            "function helper() {}\n"
            "function addToken(config) { return config; }\n"
            "app.use(auth);\n"
            "app.param('id', loadUser);\n"
            "const axios = require('axios');\n"
            "axios.interceptors.request.use(addToken);\n"
        )})
        concepts = _concepts(result, {"express"})
        assert concepts["auth"] == {"middleware"}
        assert concepts["loadUser"] == {"middleware"}
        assert "helper" not in concepts
        # An Axios interceptor is no middleware of the app (found on
        # workadventure/map-storage); no row names the call, so it is not
        # even recorded.
        assert "addToken" not in concepts
        assert all("interceptors" not in c.context_name for c in result.usage_contexts)

    def test_socketio_without_express(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"server.js": (
            "const io = require('socket.io')(3000);\n"
            "function onChat(msg) {}\n"
            "function onConnection(socket) { socket.on('chat', onChat); }\n"
            "function onExit() {}\n"
            "io.on('connection', onConnection);\n"
            "process.on('exit', onExit);\n"
        )})
        concepts = _concepts(result, {"socketio"})
        assert concepts["onConnection"] == {"websocket_handler"}
        assert concepts["onChat"] == {"websocket_handler"}
        assert "onExit" not in concepts

    def test_fastify(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"server.js": (
            "const fastify = require('fastify')();\n"
            "async function plugin(instance, opts) {}\n"
            "async function stamp(request, reply) {}\n"
            "async function admin(request, reply) {}\n"
            "async function show(request, reply) {}\n"
            "function onError(error, request, reply) {}\n"
            "function notFound(request, reply) {}\n"
            "function parseCsv(request, body, done) {}\n"
            "function onSocket(socket, req) {}\n"
            "fastify.register(plugin);\n"
            "fastify.addHook('onRequest', stamp);\n"
            "fastify.get('/admin', { preHandler: [admin] }, show);\n"
            "fastify.setErrorHandler(onError);\n"
            "fastify.setNotFoundHandler(notFound);\n"
            "fastify.addContentTypeParser('text/csv', { parseAs: 'string' }, parseCsv);\n"
            "fastify.get('/ws', { websocket: true }, onSocket);\n"
        )})
        symbols = list(result.symbols)
        clear_pattern_cache()
        enrich_symbols(symbols, {"fastify"}, list(result.usage_contexts))
        concepts = {
            s.name: {c["concept"] for c in (s.meta or {}).get("concepts", [])}
            for s in symbols if (s.meta or {}).get("concepts")
        }
        assert concepts["plugin"] == concepts["stamp"] == concepts["admin"] == {"middleware"}
        assert concepts["parseCsv"] == {"middleware"}
        assert concepts["onError"] == concepts["notFound"] == {"error_handler"}
        assert concepts["show"] == {"route"}
        kinds = {
            (e.kind, e.symbol_id) for e in detect_entrypoints(symbols, list(result.edges))
        }
        on_socket = next(s.id for s in symbols if s.name == "onSocket")
        assert (EntrypointKind.WEBSOCKET_HANDLER, on_socket) in kinds
        assert (EntrypointKind.HTTP_ROUTE, on_socket) not in kinds

    def test_hapi(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"server.js": (
            "const server = require('@hapi/hapi').server();\n"
            "function stamp(request, h) { return h.continue; }\n"
            "function translate(request, h) { return h.continue; }\n"
            "function onResponse(request) {}\n"
            "server.ext('onRequest', stamp);\n"
            "server.ext({ type: 'onPreResponse', method: translate });\n"
            "server.events.on('response', onResponse);\n"
        )})
        concepts = _concepts(result, {"hapi"})
        assert concepts["stamp"] == concepts["translate"] == {"middleware"}
        assert concepts["onResponse"] == {"event_handler"}

    def test_koa(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"server.js": (
            "const Koa = require('koa');\n"
            "const Router = require('@koa/router');\n"
            "const mount = require('koa-mount');\n"
            "const app = new Koa();\n"
            "const router = new Router();\n"
            "async function timing(ctx, next) { await next(); }\n"
            "async function loadUser(id, ctx, next) { return next(); }\n"
            "async function hello(ctx) {}\n"
            "app.use(timing);\n"
            "router.param('user', loadUser);\n"
            "app.use(mount('/hello', hello));\n"
        )})
        concepts = _concepts(result, {"koa"})
        assert concepts["timing"] == concepts["loadUser"] == concepts["hello"] == {"middleware"}

    def test_cli_js(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"cli.js": (
            "const { program } = require('commander');\n"
            "const yargs = require('yargs');\n"
            "function build(dir) {}\n"
            "function builder(y) { return y; }\n"
            "function serve(argv) {}\n"
            "function clean(argv) {}\n"
            "program.command('build <dir>').action(build);\n"
            "yargs.command('serve', 'd', builder, serve);\n"
            "yargs.command({ command: 'clean', handler: clean });\n"
        )})
        concepts = _concepts(result, {"cli-js"})
        assert concepts["build"] == concepts["serve"] == concepts["clean"] == {"command"}
        # The builder is recorded (args[2]) but no row calls it a command.
        assert "builder" not in concepts

    def test_electron(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {
            "main.js": (
                "const { ipcMain, BrowserWindow } = require('electron');\n"
                "async function readSettings(event, key) {}\n"
                "function loaded() {}\n"
                "ipcMain.handle('settings:read', readSettings);\n"
                "const win = new BrowserWindow({});\n"
                "win.webContents.on('did-finish-load', loaded);\n"
            ),
            "renderer.js": (
                "const { ipcRenderer } = require('electron');\n"
                "function showStatus(event, status) {}\n"
                "ipcRenderer.on('status', showStatus);\n"
            ),
        })
        concepts = _concepts(result, {"electron"})
        assert concepts["readSettings"] == concepts["showStatus"] == {"ipc_handler"}
        assert concepts["loaded"] == {"event_handler"}


class TestEdgeShapes:
    """Shapes the recorder declines, and the helpers' edge cases."""

    def test_receivers_that_are_not_written_names(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const express = require('express');\n"
            "function auth(req, res, next) { next(); }\n"
            "const apps = [express()];\n"
            "apps[0].use(auth);\n"
            "(0, apps[0].use)(auth);\n"
            "class Server {\n"
            "  #app = express();\n"
            "  #use(fn) {}\n"
            "  start() { this.#app.use(auth); this.#use(auth); }\n"
            "}\n"
            "new Server().start();\n"
        )})
        assert _registrations(result) == []

    def test_a_name_declared_twice_differently_has_no_binding(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"app.js": (
            "const express = require('express');\n"
            "const Koa = require('koa');\n"
            "function auth(req, res, next) { next(); }\n"
            "function a() { const app = express(); app.use(auth); }\n"
            "function b() { const app = new Koa(); app.use(auth); }\n"
            "function c() { const app = express(); app.use(auth); }\n"
        )})
        contexts = _registrations(result)
        assert len(contexts) == 3
        assert all("receiver_source" not in c.metadata for c in contexts)

    def test_a_named_import_callee_is_module_dot_export(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import (
            _get_parser_for_lang,
            _registration_call_name,
        )
        parser = _get_parser_for_lang(False)
        assert parser is not None
        source = b"m('/x', h);\n"
        call = parser.parse(source).root_node.children[0].children[0]
        assert _registration_call_name(call, source, {}, {"m": "koa-mount"}, {"m": "mount"}) == (
            "koa-mount.mount")

    def test_signature_arity(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _signature_arity
        assert _signature_arity("(a, b = f(x, y), ...c)") == 3
        assert _signature_arity("()") == 0
        assert _signature_arity(None) is None
        assert _signature_arity("x") is None
        assert _signature_arity("(a, b") is None
