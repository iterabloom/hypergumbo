# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift local bound to a method-call RESULT takes the callee's declared return
type from the return-type registry (WI-higob, INV-dihos phase 6 for swift).

Pass 1 registers ``<Owner>.<method> -> bare return type`` for every
``func ... -> T`` (optional unwrapped, generics dropped); Pass 2 reads it when a
``property_declaration`` is initialised with a call: ``store.session()`` through
the receiver's type, ``Store.make()`` through the type head, a bare
``makeSession()`` through the enclosing type and then the free function. Library
rows arrive through the same dict (WI-lalot), so one fixture feeds a row by hand
to prove the consumption path is the same one.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_swift(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


STORE = (
    "import Foundation\n"
    "struct Store {\n"
    "    func session() -> URLSession { return URLSession.shared }\n"
    "    static func make() -> URLSession { return URLSession.shared }\n"
    "    func maybe() -> FileManager? { return nil }\n"
    "    func names() -> [String] { return [] }\n"
    "}\n"
    "func makeSession() -> URLSession { return URLSession.shared }\n"
)


class TestInRepoReturnTypes:
    def test_instance_method_result(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "i", {"store.swift": STORE, "app.swift": (
            "func go(store: Store, u: URL) {\n"
            "    let s = store.session()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_static_method_result(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "s", {"store.swift": STORE, "app.swift": (
            "func go(u: URL) {\n"
            "    let s = Store.make()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_bare_call_through_the_enclosing_type_then_free_function(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "b", {"store.swift": STORE, "app.swift": (
            "class Client {\n"
            "    func makeSession() -> FileManager { return FileManager.default }\n"
            "    func go(u: URL, p: String) {\n"
            "        let s = makeSession()\n"
            "        s.fileExists(atPath: p)\n"
            "    }\n"
            "}\n"
            "func free(u: URL) {\n"
            "    let s = makeSession()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        )})
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_optional_return_is_unwrapped_and_collections_are_not_receiver_types(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "o", {"store.swift": STORE, "app.swift": (
            "func go(store: Store, p: String) {\n"
            "    let fm = store.maybe()\n"
            "    fm?.fileExists(atPath: p)\n"
            "    let ns = store.names()\n"
            "    ns.missing()\n"
            "}\n"
        )})
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"
        assert _call(edges, "missing").dst == "swift:external:0-0:missing:unresolved"

    def test_try_await_wrappers_are_unwrapped(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "t", {"store.swift": STORE, "app.swift": (
            "func go(store: Store, u: URL) async throws {\n"
            "    let s = try await store.session()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_unknown_callee_stays_untyped(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "u", {"app.swift": (
            "func go(u: URL) {\n"
            "    let s = somewhere()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        )})
        e = _call(edges, "dataTask")
        assert e.dst == "swift:external:0-0:dataTask:unresolved"
        assert e.dst_ref is None


class TestLibraryRowsFeedTheSameDict:
    def test_a_fed_row_types_the_local(self, tmp_path: Path) -> None:
        """The consumption path is the registry dict itself: a row WI-lalot's
        loader would feed (``URLSession.shared`` is a property, so here a
        method row ``Foo.build``) types the local the same way."""
        from hypergumbo_lang_mainstream import swift as m
        root = tmp_path / "lib"
        root.mkdir()
        (root / "app.swift").write_text(
            "func go(f: Foo, u: URL) {\n"
            "    let s = f.build()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        )
        parser = m._analyzer._create_parser()
        source = (root / "app.swift").read_bytes()
        tree = parser.parse(source)
        from hypergumbo_core.symbol_resolution import NameResolver
        analysis = m._extract_symbols_from_file(tree, source, "app.swift", "run")
        edges = m._extract_edges_from_file(
            tree, source, "app.swift", analysis.symbol_by_name, {}, "run",
            NameResolver({}), {},
            method_return_type_registry={"Foo.build": "URLSession"},
        )
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"


class TestReceiverTypesAreFunctionScoped:
    def test_a_later_function_does_not_rebind_this_ones_local(self, tmp_path: Path) -> None:
        """Two functions each declare ``s``; each call sees its own type."""
        edges = _edges(tmp_path / "scope", {"app.swift": (
            "import Foundation\n"
            "func a(u: URL) {\n"
            "    let s = URLSession(configuration: .default)\n"
            "    s.dataTask(with: u)\n"
            "}\n"
            "func b(p: String) {\n"
            "    let s = FileManager.default\n"
            "    s.fileExists(atPath: p)\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"

    def test_file_level_declaration_is_visible_inside_functions(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "file", {"app.swift": (
            "import Foundation\n"
            "let shared = URLSession(configuration: .default)\n"
            "func a(u: URL) {\n"
            "    shared.dataTask(with: u)\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"


class TestOptionalAnnotationsNameTheirWrappedType:
    def test_optional_property_and_parameter(self, tmp_path: Path) -> None:
        """``var task: URLSessionTask?`` and ``t: URLSessionTask?`` type their receivers.

        Alamofire's ``Request.cancel()`` reads ``task`` after a ``guard let``; the
        file-wide leak used to type it from another function's parameter, and
        the scoping fix exposed that the optional property itself was unread."""
        edges = _edges(tmp_path / "opt", {"app.swift": (
            "import Foundation\n"
            "class Request {\n"
            "    var task: URLSessionTask?\n"
            "    func cancel() {\n"
            "        task?.resume()\n"
            "    }\n"
            "    func poke(t: URLSessionTask?) {\n"
            "        t?.cancel()\n"
            "    }\n"
            "}\n"
        )})
        assert _call(edges, "resume").dst == "swift:URLSessionTask:0-0:resume:unresolved"
        assert _call(edges, "cancel").dst == "swift:URLSessionTask:0-0:cancel:unresolved"


class TestScopeFollowsTheParentChain:
    def test_class_property_is_visible_in_every_method(self, tmp_path: Path) -> None:
        """A class-level ``let q: DispatchQueue`` types ``q.async`` in each method,
        including one declared before the property."""
        edges = _edges(tmp_path / "prop", {"app.swift": (
            "import Foundation\n"
            "class Session {\n"
            "    func early() { q.async {} }\n"
            "    let q: DispatchQueue = .main\n"
            "    func late(u: URL) {\n"
            "        let local = URLSession(configuration: .default)\n"
            "        q.async { local.dataTask(with: u) }\n"
            "    }\n"
            "}\n"
        )})
        asyncs = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":async:unresolved")]
        assert len(asyncs) == 2 and all(e.dst == "swift:DispatchQueue:0-0:async:unresolved" for e in asyncs), [e.dst for e in asyncs]
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_a_local_never_leaks_into_a_sibling_method(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "leak", {"app.swift": (
            "import Foundation\n"
            "class C {\n"
            "    func a(u: URL) {\n"
            "        let t = URLSession(configuration: .default)\n"
            "        t.dataTask(with: u)\n"
            "    }\n"
            "    func b() {\n"
            "        t.missing()\n"
            "    }\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"
        assert _call(edges, "missing").dst == "swift:external:0-0:missing:unresolved"


class TestErrorRecoveredBodies:
    def test_a_parameter_survives_a_parse_error_in_its_body(self, tmp_path: Path) -> None:
        """A syntax error inside the body must not cut the call off from the
        function's own parameter (VernissageServer's inbox handlers)."""
        edges = _edges(tmp_path / "err", {"app.swift": (
            "import Foundation\n"
            "func inbox(fm: FileManager, p: String) {\n"
            "    let broken = try? ;\n"
            "    fm.fileExists(atPath: p)\n"
            "}\n"
        )})
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"


class TestInheritedAndCrossFileProperties:
    def test_property_declared_in_a_base_class_in_another_file(self, tmp_path: Path) -> None:
        """Alamofire's tests: ``session.request(...)`` with ``private var session:
        Session?`` in BaseTestCase.swift, another file, one class up."""
        edges = _edges(tmp_path / "inh", {
            "Base.swift": (
                "import Foundation\n"
                "class BaseCase {\n"
                "    var session: URLSession?\n"
                "}\n"
            ),
            "Case.swift": (
                "import Foundation\n"
                "final class DataCase: BaseCase {\n"
                "    func go(u: URL) {\n"
                "        session?.dataTask(with: u)\n"
                "    }\n"
                "}\n"
            ),
        })
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_extension_in_another_file_sees_the_property(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "ext", {
            "Svc.swift": (
                "import Foundation\n"
                "final class Svc {\n"
                "    let fm = FileManager.default\n"
                "}\n"
            ),
            "Svc+Extras.swift": (
                "import Foundation\n"
                "extension Svc {\n"
                "    func check(p: String) -> Bool { return fm.fileExists(atPath: p) }\n"
                "}\n"
            ),
        })
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"

    def test_a_local_shadows_the_property(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "shadow", {"app.swift": (
            "import Foundation\n"
            "final class Svc {\n"
            "    let fm = FileManager.default\n"
            "    func go(u: URL) {\n"
            "        let fm = URLSession(configuration: .default)\n"
            "        fm.dataTask(with: u)\n"
            "    }\n"
            "}\n"
        )})
        assert _call(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"


class TestRegistryEdges:
    def test_generic_and_collection_returns(self, tmp_path: Path) -> None:
        """``-> Set<Int>`` registers ``Set``; ``-> [String]?`` registers nothing."""
        edges = _edges(tmp_path / "gen", {"app.swift": (
            "struct Store {\n"
            "    func tags() -> Set<Int> { return [] }\n"
            "    func maybeNames() -> [String]? { return nil }\n"
            "}\n"
            "func go(store: Store) {\n"
            "    let t = store.tags()\n"
            "    t.missing()\n"
            "    let n = store.maybeNames()\n"
            "    n?.missing2()\n"
            "}\n"
        )})
        assert _call(edges, "missing").dst == "swift:Set:0-0:missing:unresolved"
        assert _call(edges, "missing2").dst == "swift:external:0-0:missing2:unresolved"

    def test_edge_pass_runs_without_either_registry(self, tmp_path: Path) -> None:
        from hypergumbo_core.symbol_resolution import NameResolver
        from hypergumbo_lang_mainstream import swift as m
        root = tmp_path / "noreg"
        root.mkdir()
        (root / "app.swift").write_text("func go(u: URL) {\n    let s = build()\n    s.dataTask(with: u)\n}\n")
        parser = m._analyzer._create_parser()
        source = (root / "app.swift").read_bytes()
        tree = parser.parse(source)
        analysis = m._extract_symbols_from_file(tree, source, "app.swift", "run")
        edges = m._extract_edges_from_file(
            tree, source, "app.swift", analysis.symbol_by_name, {}, "run", NameResolver({}), {},
        )
        assert _call(edges, "dataTask").dst == "swift:external:0-0:dataTask:unresolved"

    def test_inheritance_walk_revisits_nothing_and_misses_honestly(self, tmp_path: Path) -> None:
        """A diamond (``P`` reached twice) is walked once; an unknown bare receiver
        inside a class WITH registered fields still ends untyped."""
        edges = _edges(tmp_path / "diamond", {
            "Base.swift": (
                "import Foundation\n"
                "protocol P {}\n"
                "class Base: P {\n"
                "    let fm = FileManager.default\n"
                "}\n"
                "class Mid: Base, P {}\n"
            ),
            "Leaf.swift": (
                "import Foundation\n"
                "final class Leaf: Mid, P {\n"
                "    func go(p: String) {\n"
                "        fm.fileExists(atPath: p)\n"
                "        nobody.missing()\n"
                "    }\n"
                "}\n"
            ),
        })
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"
        assert _call(edges, "missing").dst == "swift:external:0-0:missing:unresolved"

    def test_declaration_under_an_error_node_is_file_level(self, tmp_path: Path) -> None:
        """A ``#if`` block inside a class body breaks tree-sitter-swift's parse
        (INV-bisok's root cause: Alamofire's Session.swift); error recovery
        re-parents the class's members under ERROR nodes. Such a declaration is
        bound file-level, so every method -- in the class and in an extension --
        still types ``rootQueue``."""
        edges = _edges(tmp_path / "iferr", {"app.swift": (
            "import Foundation\n"
            "open class Session {\n"
            "    public let rootQueue: DispatchQueue = .main\n"
            "    #if canImport(Darwin)\n"
            "    open func ws() {}\n"
            "    #endif\n"
            "    func perform() { rootQueue.async {} }\n"
            "}\n"
            "extension Session {\n"
            "    func later() { rootQueue.async {} }\n"
            "}\n"
        )})
        asyncs = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":async:unresolved")]
        assert len(asyncs) == 2 and all(e.dst == "swift:DispatchQueue:0-0:async:unresolved" for e in asyncs), [e.dst for e in asyncs]


class TestATypeNameOnlyValidInsideTheDeclaration:
    """``Self`` and a generic parameter name a type only from inside the
    declaration. Registering either LITERALLY put a meaningless module in the
    slot -- read back on Alamofire, where every ``-> Self`` fluent builder
    (76 chained sites) shipped ``swift:Self:0-0:<m>:unresolved``."""

    def test_self_return_resolves_to_the_enclosing_type(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "selfret", {"a.swift": (
            "import Foundation\n"
            "class Request {\n"
            "    func validate() -> Self { return self }\n"
            "}\n"
            "func go(r: Request) {\n"
            "    r.validate().nosuchmethod()\n"
            "}\n"
        )})
        edge = _call(edges, "nosuchmethod")
        # Request is a PROJECT type: it rides in the hint, never the module.
        assert edge.dst == "swift:external:0-0:nosuchmethod:unresolved"
        assert (edge.meta or {}).get("receiver_type_hint") == "Request"

    def test_a_generic_parameter_return_registers_nothing(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "gen", {"a.swift": (
            "import Foundation\n"
            "class Box {\n"
            "    func pick<U>(_ f: Int) -> U { fatalError() }\n"
            "}\n"
            "func go(b: Box) {\n"
            "    b.pick(1).nosuchmethod()\n"
            "}\n"
        )})
        edge = _call(edges, "nosuchmethod")
        assert edge.dst == "swift:external:0-0:nosuchmethod:unresolved"
        assert "receiver_type_hint" not in (edge.meta or {})

    def test_an_enclosing_type_parameter_registers_nothing(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "encgen", {"a.swift": (
            "import Foundation\n"
            "class Box<Element> {\n"
            "    func first() -> Element { fatalError() }\n"
            "}\n"
            "func go(b: Box) {\n"
            "    b.first().nosuchmethod()\n"
            "}\n"
        )})
        assert "receiver_type_hint" not in (_call(edges, "nosuchmethod").meta or {})

    def test_a_constrained_parameters_BOUND_is_still_a_real_type(
        self, tmp_path: Path,
    ) -> None:
        """``<V: Codable>`` -- ``Codable`` is the bound, not the parameter name."""
        edges = _edges(tmp_path / "bound", {"a.swift": (
            "import Foundation\n"
            "class Box {\n"
            "    func pick<V: Codable>(_ f: Int) -> JSONDecoder { return JSONDecoder() }\n"
            "}\n"
            "func go(b: Box) {\n"
            "    b.pick(1).nosuchmethod()\n"
            "}\n"
        )})
        assert _call(edges, "nosuchmethod").dst == (
            "swift:JSONDecoder:0-0:nosuchmethod:unresolved"
        )
