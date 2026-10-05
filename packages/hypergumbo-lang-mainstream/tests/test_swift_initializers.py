# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sogof: a Swift initialiser, deinitialiser and property observer is its own call source.

The analyzer emitted NO symbol for ``init`` / ``convenience init`` / ``required
init`` or ``deinit``, and a ``willSet`` / ``didSet`` observer was not in the set
of declarations a call is anchored on. So every call in those bodies was
credited to the enclosing TYPE (INV-bamij's fallback), and a constructor call
``T(...)`` could not land on the initialiser it runs: it landed on the type.

Now:

* an ``init_declaration`` is ``kind="constructor"`` named ``Type.init``, with its
  argument labels (``meta["arg_labels"]``) and its parameters
  (``meta["parameters"]``, ADR-0058's arity home, defaults and variadics
  marked); a protocol's ``init`` requirement is one too;
* a ``deinit_declaration`` is ``kind="method"`` named ``Type.deinit`` (the C# /
  C++ destructor precedent; the registry has no destructor kind);
* a call in an observer is credited to the stored property that owns it, as a
  computed property's accessor calls already were -- but a call in the
  property's INITIALISER stays on the type (INV-bamij);
* a construction -- ``T(...)``, ``T.init(...)``, ``self.init(...)``,
  ``Self.init(...)``, ``super.init(...)`` -- lands on the ``init`` whose labels
  admit the call, an exact binding before one that needs defaults (Swift's
  own ranking). Several that admit is an ambiguous record
  (``resolution_quality="ambiguous"``, confidence scaled); none that admits
  (a memberwise, inherited or library initialiser) keeps the old landing.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.swift import analyze_swift

_NODE = """\
func helper() -> Int { return 1 }
func helper2() -> Int { return 2 }

class Node {
    var key: Int
    init(key: Int) {
        self.key = key
        _ = helper()
    }
    convenience init() {
        self.init(key: 0)
    }
    required init?(coder: Int, extra: String = "") {
        self.key = coder
    }
    deinit {
        _ = helper2()
    }
    var watched: Int = helper() {
        willSet { _ = helper() }
        didSet { _ = helper2() }
    }
}

struct Plain {
    var a: Int
}

class Sub: Node {
    override init(key: Int) {
        super.init(key: key)
    }
}

func make() {
    let n = Node(key: 3)
    let m = Node()
    let p = Plain(a: 1)
    let q = Node.init(key: 4)
    let r = Node(coder: 1)
    let s = Node(coder: 1, extra: "x")
    _ = (n, m, p, q, r, s)
}

protocol Maker {
    init(seed: Int)
}
"""


def _analyze(tmp_path: Path, files: dict[str, str]):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    result = analyze_swift(tmp_path)
    return result, {s.id: s for s in result.symbols}


def _calls(result, by_id) -> list[tuple[int, str, str, int | None]]:
    """``(line, src name, dst name, dst start line)`` for every ``calls`` edge."""
    out = []
    for e in result.edges:
        if e.edge_type != "calls":
            continue
        src = by_id.get(e.src)
        dst = by_id.get(e.dst)
        out.append((
            e.line,
            src.name if src else e.src,
            dst.name if dst else e.dst,
            dst.span.start_line if dst else None,
        ))
    return sorted(out)


def _at(calls, line: int) -> list[tuple[int, str, str, int | None]]:
    return [c for c in calls if c[0] == line]


class TestInitialiserSymbols:
    def test_each_init_is_a_constructor_symbol(self, tmp_path: Path) -> None:
        result, _ = _analyze(tmp_path, {"A.swift": _NODE})
        inits = sorted(
            (s.span.start_line, s.name, s.kind, s.meta["arg_labels"])
            for s in result.symbols if s.name.endswith(".init")
        )
        assert inits == [
            (6, "Node.init", "constructor", ["key"]),
            (10, "Node.init", "constructor", []),
            (13, "Node.init", "constructor", ["coder", "extra"]),
            (30, "Sub.init", "constructor", ["key"]),
            (46, "Maker.init", "constructor", ["seed"]),
        ]

    def test_parameters_record_defaults(self, tmp_path: Path) -> None:
        result, _ = _analyze(tmp_path, {"A.swift": _NODE})
        required = next(
            s for s in result.symbols
            if s.name == "Node.init" and s.span.start_line == 13
        )
        assert required.meta["parameters"] == [
            {"name": "coder", "type": "Int", "default": False},
            {"name": "extra", "type": "String", "default": True},
        ]
        assert "required" in required.modifiers
        assert required.qualified_name == "Node.init"
        assert required.signature == "(coder: Int, extra: String)"
        assert required.stable_id

    def test_modifiers_and_exports(self, tmp_path: Path) -> None:
        result, _ = _analyze(tmp_path, {"A.swift": _NODE})
        by_line = {s.span.start_line: s for s in result.symbols if s.kind == "constructor"}
        assert "convenience" in by_line[10].modifiers
        assert "override" in by_line[30].modifiers
        assert by_line[6].is_exported is False
        # A protocol requirement is as reachable as its protocol.
        assert by_line[46].is_exported is True

    def test_deinit_is_a_method_symbol(self, tmp_path: Path) -> None:
        result, _ = _analyze(tmp_path, {"A.swift": _NODE})
        deinit = [s for s in result.symbols if s.name == "Node.deinit"]
        assert len(deinit) == 1
        assert deinit[0].kind == "method"
        assert (deinit[0].span.start_line, deinit[0].span.end_line) == (16, 18)

    def test_variadic_parameter_is_marked(self, tmp_path: Path) -> None:
        result, _ = _analyze(tmp_path, {"A.swift": """\
struct V {
    init(_ items: Int..., label: String = "x") {}
}
"""})
        init = next(s for s in result.symbols if s.kind == "constructor")
        assert init.meta["arg_labels"] == [None, "label"]
        assert init.meta["parameters"] == [
            {"name": "items", "type": "Int", "default": False, "variadic": True},
            {"name": "label", "type": "String", "default": True},
        ]


class TestBodiesAnchorOnTheirDeclaration:
    def test_init_deinit_and_observer_calls(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": _NODE})
        calls = _calls(result, by_id)
        assert _at(calls, 8) == [(8, "Node.init", "helper", 1)]
        assert _at(calls, 17) == [(17, "Node.deinit", "helper2", 2)]
        assert _at(calls, 20) == [(20, "Node.watched", "helper", 1)]
        assert _at(calls, 21) == [(21, "Node.watched", "helper2", 2)]
        # INV-bamij's choice stands for the property's INITIALISER.
        assert _at(calls, 19) == [(19, "Node", "helper", 1)]
        # And the source really holds the call (INV-midag): containment.
        for e in result.edges:
            if e.edge_type == "calls" and e.src in by_id:
                span = by_id[e.src].span
                assert span.start_line <= e.line <= span.end_line, e

    def test_no_call_is_anchored_on_the_class(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": _NODE})
        on_class = [c for c in _calls(result, by_id) if c[1] in ("Node", "Sub")]
        assert on_class == [(19, "Node", "helper", 1)]

    def test_top_level_observer_anchors_on_the_variable(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
func ping() {}
var level: Int = 0 {
    didSet { ping() }
}
"""})
        assert _at(_calls(result, by_id), 3) == [(3, "level", "ping", 1)]


class TestConstructionLandsOnTheInit:
    def test_constructions_choose_the_init_by_labels(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": _NODE})
        calls = _calls(result, by_id)
        assert _at(calls, 11) == [(11, "Node.init", "Node.init", 6)]   # self.init(key:)
        assert _at(calls, 31) == [(31, "Sub.init", "Node.init", 6)]    # super.init(key:)
        assert _at(calls, 36) == [(36, "make", "Node.init", 6)]        # Node(key:)
        assert _at(calls, 37) == [(37, "make", "Node.init", 10)]       # Node()
        assert _at(calls, 39) == [(39, "make", "Node.init", 6)]        # Node.init(key:)
        assert _at(calls, 40) == [(40, "make", "Node.init", 13)]       # default omitted
        assert _at(calls, 41) == [(41, "make", "Node.init", 13)]

    def test_a_type_with_no_init_keeps_the_type(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": _NODE})
        assert _at(_calls(result, by_id), 38) == [(38, "make", "Plain", 25)]

    def test_no_init_admits_keeps_the_type(self, tmp_path: Path) -> None:
        # The struct's memberwise initialiser survives an init declared in an
        # extension; ``S(a:)`` is that one, which has no symbol.
        result, by_id = _analyze(tmp_path, {"A.swift": """\
struct S {
    var a: Int
}
extension S {
    init(text: String) { self.a = 0 }
}
func use() {
    _ = S(a: 1)
    _ = S(text: "x")
}
"""})
        calls = _calls(result, by_id)
        assert _at(calls, 8) == [(8, "use", "S", 1)]
        assert _at(calls, 9) == [(9, "use", "S.init", 5)]

    def test_cross_file_construction(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {
            "A.swift": "class Store {\n    init(path: String) {}\n    init(url: Int) {}\n}\n",
            "B.swift": "func open() {\n    _ = Store(url: 1)\n}\n",
        })
        hits = [
            (by_id[e.src].name, by_id[e.dst].name, by_id[e.dst].span.start_line, e.confidence)
            for e in result.edges
            if e.edge_type == "calls" and e.dst in by_id and e.line == 2
            and by_id[e.src].path == "B.swift"
        ]
        assert [h[:3] for h in hits] == [("open", "Store.init", 3)]

    def test_trailing_closure_and_variadic(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
struct V {
    init(_ items: Int...) {}
    init(count: Int = 0, body: () -> Void) {}
}
func use() {
    _ = V(1, 2, 3)
    _ = V { }
    _ = V(count: 2) { }
}
"""})
        calls = _calls(result, by_id)
        assert _at(calls, 6) == [(6, "use", "V.init", 2)]
        assert _at(calls, 7) == [(7, "use", "V.init", 3)]
        assert _at(calls, 8) == [(8, "use", "V.init", 3)]

    def test_several_that_admit_is_ambiguous(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
struct T {
    init(_ a: Int) {}
    init(_ b: String) {}
    init(named: String) {}
}
func use() {
    _ = T(1)
    _ = T(named: "x")
}
"""})
        edges = {
            e.line: e for e in result.edges
            if e.edge_type == "calls" and e.line in (7, 8)
        }
        amb = edges[7]
        assert by_id[amb.dst].name == "T.init"
        assert by_id[amb.dst].span.start_line == 2   # the first by position
        assert amb.meta["resolution_quality"] == "ambiguous"
        sure = edges[8]
        assert by_id[sure.dst].span.start_line == 4
        assert "resolution_quality" not in sure.meta
        assert amb.confidence < sure.confidence

    def test_an_unknown_super_keeps_the_old_edge(self, tmp_path: Path) -> None:
        # An EXTERNAL superclass: nothing to land on, the edge stays unresolved.
        result, by_id = _analyze(tmp_path, {"A.swift": """\
class View: NSObject {
    override init() {
        super.init()
    }
}
"""})
        hits = [e for e in result.edges if e.edge_type == "calls" and e.line == 3]
        assert len(hits) == 1
        assert hits[0].dst.endswith(":init:unresolved")
        assert by_id[hits[0].src].name == "View.init"

    def test_an_exact_binding_outranks_a_defaulted_one(self, tmp_path: Path) -> None:
        # Alamofire's HTTPHeaders: ``HTTPHeaders()`` is ``init()``, not the
        # variadic ``init(dictionaryLiteral:)`` that also admits no argument.
        result, by_id = _analyze(tmp_path, {"A.swift": """\
struct H {
    init(dictionaryLiteral elements: (String, String)...) {}
    init(x: Int = 0) {}
    init() {}
}
func use() {
    _ = H()
    _ = H(x: 1)
}
"""})
        edges = {e.line: e for e in result.edges if e.edge_type == "calls" and e.line in (7, 8)}
        assert by_id[edges[7].dst].span.start_line == 4
        assert "resolution_quality" not in edges[7].meta
        assert by_id[edges[8].dst].span.start_line == 3

    def test_an_undeclared_type_takes_only_an_exact_binding(self, tmp_path: Path) -> None:
        # Alamofire's ``extension OperationQueue``: the all-defaulted
        # convenience init's ``self.init()`` is Foundation's ``init()``, which
        # the run cannot see -- not the extension's init, and not itself.
        result, by_id = _analyze(tmp_path, {"A.swift": """\
extension OperationQueue {
    convenience init(name: String? = nil) {
        self.init()
    }
    convenience init(label: String) {
        self.init(name: label)
    }
    convenience init(flag: Bool = false) {
        self.init(label: "x")
    }
}
"""})
        calls = _calls(result, by_id)
        # ``init(flag:)`` admits ``()`` only through its default: not evidence.
        assert _at(calls, 3)[0][2].endswith(":init:unresolved")
        assert _at(calls, 6) == [(6, "OperationQueue.init", "OperationQueue.init", 2)]

    def test_self_init_does_not_delegate_to_itself(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
class Q {
    init(x: Int = 0) {}
    convenience init(y: Int = 0) {
        self.init()
    }
}
"""})
        assert _at(_calls(result, by_id), 4) == [(4, "Q.init", "Q.init", 2)]
        # Not a pick between two: the init it is in was never a candidate.
        edge = next(e for e in result.edges if e.edge_type == "calls" and e.line == 4)
        assert "resolution_quality" not in edge.meta

    def test_same_named_types_keep_their_own_inits(self, tmp_path: Path) -> None:
        # vapor declares a ``Storage`` class per ``Application`` extension.
        result, by_id = _analyze(tmp_path, {
            "A.swift": """\
struct Box {
    init() {}
}
func a() {
    _ = Box()
}
""",
            "B.swift": """\
struct Box {
    init() {}
}
func b() {
    _ = Box()
}
""",
        })
        hits = sorted(
            (by_id[e.src].path, by_id[e.dst].path, e.meta.get("resolution_quality"))
            for e in result.edges
            if e.edge_type == "calls" and e.line == 5 and e.dst in by_id
        )
        assert hits == [("A.swift", "A.swift", None), ("B.swift", "B.swift", None)]


class TestExplicitInitReceivers:
    def test_nested_type_init_and_Self_init(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
enum Outer {
    struct Inner {
        init(a: Int) {}
        static func make() -> Inner { Self.init(a: 2) }
    }
}
enum Other {
    struct Inner {
        init(a: Int) {}
    }
}
func use() {
    _ = Outer.Inner.init(a: 1)
}
"""})
        calls = _calls(result, by_id)
        assert _at(calls, 4) == [(4, "Inner.make", "Inner.init", 3)]
        # ``Other.Inner.init(a:)`` admits the labels too but is another type's.
        assert _at(calls, 13) == [(13, "use", "Inner.init", 3)]

    def test_receivers_that_name_no_type_stay_unresolved(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
class Base {
    init() {}
}
class Orphan {
    init() {
        super.init()
    }
}
func use(b: Base) {
    _ = type(of: b).init()
}
"""})
        calls = _calls(result, by_id)
        assert _at(calls, 6)[0][2].endswith(":init:unresolved")   # no superclass
        assert any(c[2].endswith(":init:unresolved") for c in _at(calls, 10))

    def test_a_capitalised_variable_is_not_a_type(self, tmp_path: Path) -> None:
        result, by_id = _analyze(tmp_path, {"A.swift": """\
struct Kind {
    init() {}
}
func use() {
    let Kind2 = Kind.self
    _ = Kind2.init()
}
"""})
        calls = _calls(result, by_id)
        assert all(not c[2] == "Kind.init" for c in _at(calls, 6)), calls

    def test_an_init_outside_any_type_is_not_emitted(self, tmp_path: Path) -> None:
        result, _ = _analyze(tmp_path, {"A.swift": "init(x: Int) { }\n"})
        assert [s for s in result.symbols if s.kind == "constructor"] == []
