# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/swift.py
"""Vapor's named handlers: ``webSocket(..., onUpgrade: h)`` and ``get(..., use: h)``.

``app.webSocket("chat", onUpgrade: chatHandler)`` and, in a RouteCollection,
``routes.webSocket("feed", onUpgrade: self.feed)`` hand Vapor a function it
calls on each upgraded connection; nothing in the repository calls it. The
Vapor extractor (a builder-chain walk) knew the HTTP verbs, ``on``, ``grouped``
and ``group`` only, so a WebSocket handler had no caller. It now records a
``webSocket`` call on a route builder as a UsageContext (position
``kwargs[onUpgrade]``, the composed path, ``symbol_ref`` the handler) and
vapor.yaml stamps ``websocket_handler`` on the handler.

The same resolver gives a route's ``use:`` handler to its marker
(``handler_ref``), from which the route-handler linker draws a
``dispatches_to``: ``app.get("hello", use: hello)`` had a marker with no
handler (audit 0028, V2/V3). Not to the route's UsageContext: the vapor.yaml
route row would then stamp ``route`` on the handler and the materializer mint
a second marker for the same route.

The resolver follows Swift: inside a type a bare name is a member of the type
first (implicit ``self``), then a top-level function; ``self.f`` is the
member. A name this file does not declare is left to the name-based resolver
after every file is analysed (``metadata["handler"]``).

Controls: an inline closure (no symbol), a ``webSocket`` call on something
that is not a route builder, and a file that does not import Vapor.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import (
    clear_pattern_cache,
    enrich_symbols,
    resolve_deferred_symbol_refs,
)
from hypergumbo_core.ir import Symbol, UsageContext
from hypergumbo_lang_mainstream.swift import analyze_swift

ROUTES = '''\
import Vapor

func chatHandler(req: Request, ws: WebSocket) {
    ws.send("hi")
}

func hello(req: Request) async throws -> String {
    "Hello"
}

func routes(_ app: Application) throws {
    app.get("hello", use: hello)
    app.on(.OPTIONS, "probe", use: hello)
    app.webSocket("echo") { req, ws in
        ws.send("echo")
    }
    app.webSocket("chat", onUpgrade: chatHandler)
    let api = app.grouped("api")
    api.webSocket("live", onUpgrade: chatHandler)
    app.webSocket("remote", onUpgrade: remoteHandler)
    app.webSocket("labeled", onUpgrade: { req, ws in ws.send("x") })
    let controller = MockClient()
    app.get("other", use: controller.index)
    let client = MockClient()
    client.webSocket("ignored", onUpgrade: chatHandler)
}

struct MockClient {
    func webSocket(_ path: String, onUpgrade: (Request, WebSocket) -> ()) {}
}
'''

CONTROLLER = '''\
import Vapor

struct FeedController: RouteCollection {
    func boot(routes: RoutesBuilder) throws {
        routes.webSocket("feed", onUpgrade: self.feed)
        let todos = routes.grouped("todos")
        todos.get(use: index)
        let helper = Helper()
        routes.get("helped", use: helper.index)
    }

    func feed(req: Request, ws: WebSocket) {
        ws.send("feed")
    }

    func index(req: Request) async throws -> [String] { [] }
}
'''

REMOTE = '''\
import Vapor

func remoteHandler(req: Request, ws: WebSocket) {
    ws.send("remote")
}
'''


def _analyze(tmp_path: Path, files: dict[str, str]):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return analyze_swift(tmp_path)


def _symbol(result, name: str) -> Symbol:
    found = [s for s in result.symbols if s.name == name]
    assert len(found) == 1, (name, [(s.name, s.kind) for s in found])
    return found[0]


def _websockets(result) -> list[UsageContext]:
    return [u for u in result.usage_contexts if u.context_name.endswith(".webSocket")]


class TestWebSocket:
    def test_a_named_handler_on_a_builder(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"routes.swift": ROUTES, "Remote.swift": REMOTE})
        by_path = {u.metadata["route_path"]: u for u in _websockets(result)}
        chat = by_path["chat"]
        assert chat.context_name == "app.webSocket"
        assert chat.position == "kwargs[onUpgrade]"
        assert chat.symbol_ref == _symbol(result, "chatHandler").id
        assert by_path["api/live"].symbol_ref == _symbol(result, "chatHandler").id

    def test_self_member_in_a_route_collection(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"FeedController.swift": CONTROLLER})
        [feed] = _websockets(result)
        assert feed.metadata["route_path"] == "feed"
        assert feed.symbol_ref == _symbol(result, "FeedController.feed").id

    def test_a_handler_of_another_file_is_left_to_the_name_resolver(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, {"routes.swift": ROUTES, "Remote.swift": REMOTE})
        [remote] = [u for u in _websockets(result) if u.metadata["route_path"] == "remote"]
        assert remote.metadata["handler"] == "remoteHandler"
        resolve_deferred_symbol_refs(list(result.symbols), list(result.usage_contexts))
        assert remote.symbol_ref == _symbol(result, "remoteHandler").id

    def test_inline_closures_and_non_builder_are_not_recorded(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"routes.swift": ROUTES, "Remote.swift": REMOTE})
        paths = sorted(u.metadata["route_path"] for u in _websockets(result))
        # reach: the named forms of the same file are recorded
        assert paths == ["api/live", "chat", "remote"]

    def test_no_vapor_import_no_record(self, tmp_path: Path) -> None:
        source = ROUTES.replace("import Vapor", "import Foundation")
        result = _analyze(tmp_path, {"routes.swift": source})
        assert _websockets(result) == []


class TestRouteUseHandler:
    def test_use_handler_on_context_and_marker(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"routes.swift": ROUTES, "Remote.swift": REMOTE})
        [ctx] = [
            u for u in result.usage_contexts
            if u.context_name == "app.get" and u.metadata["route_path"] == "hello"
        ]
        hello = _symbol(result, "hello")
        # not on the context: the route row would mint a second marker
        assert ctx.symbol_ref is None
        [marker] = [
            s for s in result.symbols
            if (s.meta or {}).get("framework_role") == "route"
            and (s.meta or {}).get("route_path") == "/hello"
        ]
        assert marker.meta["handler_ref"] == hello.id
        [probe] = [
            s for s in result.symbols
            if (s.meta or {}).get("framework_role") == "route"
            and (s.meta or {}).get("route_path") == "/probe"
        ]
        assert probe.meta["handler_ref"] == hello.id
        # a member of another value is not guessed
        [other] = [
            s for s in result.symbols
            if (s.meta or {}).get("framework_role") == "route"
            and (s.meta or {}).get("route_path") == "/other"
        ]
        assert "handler_ref" not in other.meta

    def test_bare_name_in_a_type_is_the_member(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"FeedController.swift": CONTROLLER})
        markers = {
            s.meta["route_path"]: s for s in result.symbols
            if (s.meta or {}).get("framework_role") == "route"
        }
        assert markers["/todos"].meta["handler_ref"] == _symbol(
            result, "FeedController.index",
        ).id
        # another value's member is not the type's own, whatever its name
        assert "handler_ref" not in markers["/helped"].meta


def test_vapor_row_stamps_websocket_handler(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"routes.swift": ROUTES, "FeedController.swift": CONTROLLER})
    symbols = list(result.symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    clear_pattern_cache()
    enrich_symbols(symbols, {"vapor"}, list(result.usage_contexts))

    def concepts(name: str) -> list[str]:
        [s] = [s for s in symbols if s.name == name]
        return [c["concept"] for c in (s.meta or {}).get("concepts", [])]

    assert "websocket_handler" in concepts("chatHandler")
    assert "websocket_handler" in concepts("FeedController.feed")
    assert "websocket_handler" not in concepts("hello")
    assert "websocket_handler" not in concepts("MockClient.webSocket")
