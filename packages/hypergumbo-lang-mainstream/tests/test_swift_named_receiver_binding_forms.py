# SPDX-License-Identifier: AGPL-3.0-or-later
"""A NAMED Swift receiver is typed by every binding form the binding pass can read (WI-mofil).

``x.m()`` where ``x`` is a lowercase name and ``_type_of`` returns None was sized
by cause on five repositories (vapor, hummingbird, Kingfisher, Alamofire,
swift-argument-parser). Four of the causes are binding forms the pass already had
the information for and misread or skipped; each test class below is one of them.

* **Optional-binding conditions with more than one clause.** The arm took the
  condition's FIRST ``simple_identifier`` as the bound name and its FIRST ``=`` as
  the RHS. ``if flag, let s = store.session()`` therefore typed ``flag`` -- a Bool
  -- as ``URLSession``, a confidently wrong stamp (the emit site acts on it), and
  left ``s`` untyped; ``if let s = a(), let m = b()`` never bound ``m``; a ``case``
  pattern's enum-case name (``.some``) was bound as a variable. ``while let`` had
  no arm at all.
* **A generic constructor** ``ManagedAtomic<Bool>(false)`` parses as
  ``constructor_expression``, not ``call_expression``, so neither the declaration
  reader nor the receiver-expression walker typed it -- while ``Store()`` was
  typed. The walker now names it the same way: ``_swift_bare_type`` of its
  ``user_type``, so a collection constructor stays a refusal.
* **An identifier initialiser** (``let t = s``, ``guard let t = s``) carries the
  type of the name it copies; the walker had no arm for a bare name.
* **An annotated closure parameter** ``{ (fm: FileManager) in fm.x() }`` is
  bound like a function parameter. A closure is now a SCOPE of its own, the way a
  function is: its parameters and locals never type a same-named receiver outside
  it, which also stops a closure-local ``let`` leaking into the enclosing function
  (it did before this change).

The A/B on five repositories then exposed three defects of the scope map
itself, fixed here because the arms above made them bite: a binding was found by
NAME alone, so a declaration retyped uses ABOVE it; only ``func`` bodies were
scopes, so ``init`` / subscript / accessor parameters and locals typed their
names file-wide; and the in-repo bind looked a generic receiver type up by its
full spelling (``Result<Success, Failure>.tryMap``).

Out of scope, sized and filed: an UNANNOTATED closure parameter (its type is the
callee's closure signature), tuple destructuring, ``case let`` payloads, and
collection iteration (WI-bodav).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift

STORE = (
    "import Foundation\n"
    "class Store {\n"
    "    func session() -> URLSession { return URLSession.shared }\n"
    "    func mgr() -> FileManager { return FileManager.default }\n"
    "}\n"
)


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(src)
    return analyze_swift(root).edges


def _one(edges: list[Edge], method: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


def _hint(edge: Edge) -> object:
    return (edge.meta or {}).get("receiver_type_hint")


class TestEveryClauseOfAnOptionalBindingCondition:
    def test_a_leading_boolean_clause_is_not_bound(self, tmp_path: Path) -> None:
        """``flag`` is a Bool. Binding it to the ``let`` clause's RHS stamped
        ``URLSession`` on ``flag.fileExists`` -- a wrong type, not a missing one
        (observed on dev 8cfcab7b07: ``swift:URLSession:0-0:fileExists``). The
        parameter's own declared type is what must stand."""
        edges = _edges(tmp_path / "f", STORE + (
            "func go(store: Store, flag: Bool) {\n"
            "    if flag, let s = store.session() {\n"
            "        flag.fileExists(atPath: \"x\")\n"
            "        s.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "fileExists").dst == "swift:Bool:0-0:fileExists:unresolved"
        assert _one(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_the_second_let_clause_is_bound(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "i", STORE + (
            "func go(store: Store, p: String, u: URL) {\n"
            "    if let s = store.session(), let m = store.mgr() {\n"
            "        m.fileExists(atPath: p)\n"
            "        s.dataTask(with: u)\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_guard_binds_every_clause_into_the_enclosing_scope(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "g", STORE + (
            "func go(store: Store, p: String) {\n"
            "    guard let s = store.session(), var m = store.mgr() else { return }\n"
            "    m.removeItem(atPath: p)\n"
            "    s.finishTasksAndInvalidate()\n"
            "}\n"
        ))
        assert _one(edges, "removeItem").dst == "swift:FileManager:0-0:removeItem:unresolved"
        assert _one(edges, "finishTasksAndInvalidate").dst == (
            "swift:URLSession:0-0:finishTasksAndInvalidate:unresolved"
        )

    def test_a_clause_annotation_names_the_type(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "a", STORE + (
            "func go(p: String) {\n"
            "    if let m: FileManager = lookup() {\n"
            "        m.fileExists(atPath: p)\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"

    def test_while_let_is_an_optional_binding_too(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "w", STORE + (
            "func go(store: Store) {\n"
            "    while let s = store.session() {\n"
            "        s.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_a_case_pattern_binds_nothing_and_its_case_name_is_not_a_variable(
        self, tmp_path: Path,
    ) -> None:
        """``if case let .some(x) = y``: ``x`` is an enum PAYLOAD (filed, not
        built) and ``some`` is a case name. The first-identifier rule bound
        ``some`` to ``y``'s type."""
        edges = _edges(tmp_path / "c", STORE + (
            "func go(y: URLSession?) {\n"
            "    if case let .some(x) = y {\n"
            "        some.zork()\n"
            "        x.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(_one(edges, "zork")) is None
        assert _hint(_one(edges, "invalidateAndCancel")) is None

    def test_a_shorthand_clause_keeps_the_outer_type(self, tmp_path: Path) -> None:
        """``if let s`` (Swift 5.7) re-binds ``s`` to itself, unwrapped: the outer
        declaration already types it, and the shorthand must not displace it."""
        edges = _edges(tmp_path / "s", STORE + (
            "func go(s: URLSession?, store: Store) {\n"
            "    if let s, let m = store.mgr() {\n"
            "        s.invalidateAndCancel()\n"
            "        m.fileExists(atPath: \"x\")\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )
        assert _one(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"


class TestAGenericConstructorNamesItsType:
    def test_a_local_initialised_by_a_generic_constructor(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "l", (
            "import Atomics\n"
            "func go() {\n"
            "    let a = ManagedAtomic<Bool>(false)\n"
            "    a.load(ordering: .relaxed)\n"
            "}\n"
        ))
        assert _one(edges, "load").dst == "swift:ManagedAtomic:0-0:load:unresolved"

    def test_a_generic_constructor_receiver_expression(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "r", (
            "import Atomics\n"
            "func go() {\n"
            "    ManagedAtomic<Int>(0).wrappingIncrement(ordering: .relaxed)\n"
            "}\n"
        ))
        assert _one(edges, "wrappingIncrement").dst == (
            "swift:ManagedAtomic:0-0:wrappingIncrement:unresolved"
        )

    def test_a_collection_constructor_is_still_refused(self, tmp_path: Path) -> None:
        """``[String](repeating:count:)``: ``_swift_bare_type`` is the one rule for
        which spellings are receiver types, and a collection literal type is not one."""
        edges = _edges(tmp_path / "c", (
            "func go() {\n"
            "    let xs = [String](repeating: \"a\", count: 2)\n"
            "    xs.frobnicate()\n"
            "}\n"
        ))
        assert _hint(_one(edges, "frobnicate")) is None


class TestAnIdentifierInitialiserCopiesTheType:
    def test_let_from_a_typed_name(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "l", STORE + (
            "func go(store: Store, u: URL) {\n"
            "    let s = store.session()\n"
            "    let t = s\n"
            "    t.dataTask(with: u)\n"
            "}\n"
        ))
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_guard_let_from_an_optional_parameter(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "g", (
            "import Foundation\n"
            "func go(maybe: FileManager?, p: String) {\n"
            "    guard let fm = maybe else { return }\n"
            "    fm.removeItem(atPath: p)\n"
            "}\n"
        ))
        assert _one(edges, "removeItem").dst == "swift:FileManager:0-0:removeItem:unresolved"

    def test_an_untyped_name_copies_nothing(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "u", (
            "func go() {\n"
            "    let t = somethingElse\n"
            "    t.frobnicate()\n"
            "}\n"
        ))
        assert _hint(_one(edges, "frobnicate")) is None


class TestAnAnnotatedClosureParameter:
    def test_the_annotation_types_the_receiver_inside_the_closure(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "p", STORE + (
            "func go(store: Store) {\n"
            "    store.each { (fm: FileManager, n) -> Void in\n"
            "        fm.createFile(atPath: \"x\", contents: nil)\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "createFile").dst == "swift:FileManager:0-0:createFile:unresolved"

    def test_a_closure_parameter_does_not_retype_the_enclosing_functions_name(
        self, tmp_path: Path,
    ) -> None:
        """The closure's ``fm`` shadows the function's ``fm`` INSIDE the closure
        only; after it the function's own parameter type stands."""
        edges = _edges(tmp_path / "s", STORE + (
            "func go(store: Store, fm: URLSession) {\n"
            "    store.each { (fm: FileManager) in\n"
            "        fm.createFile(atPath: \"x\", contents: nil)\n"
            "    }\n"
            "    fm.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _one(edges, "createFile").dst == "swift:FileManager:0-0:createFile:unresolved"
        assert _one(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_a_closure_local_does_not_leak_into_the_enclosing_function(
        self, tmp_path: Path,
    ) -> None:
        """Before closures were scopes, a ``let`` inside a trailing closure was
        bound in the FUNCTION's map and typed an unrelated ``s`` after it."""
        edges = _edges(tmp_path / "k", STORE + (
            "func go(store: Store, u: URL) {\n"
            "    store.each {\n"
            "        let s = store.session()\n"
            "        s.dataTask(with: u)\n"
            "    }\n"
            "    s.frobnicate()\n"
            "}\n"
        ))
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"
        assert _hint(_one(edges, "frobnicate")) is None

    def test_an_enclosing_functions_name_is_visible_inside_the_closure(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "v", STORE + (
            "func go(store: Store, u: URL) {\n"
            "    let s = store.session()\n"
            "    store.each { s.dataTask(with: u) }\n"
            "}\n"
        ))
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_an_unannotated_closure_parameter_stays_untyped(self, tmp_path: Path) -> None:
        """Its type is the CALLEE's closure signature: filed, not inferred here."""
        edges = _edges(tmp_path / "n", STORE + (
            "func go(store: Store) {\n"
            "    store.each { fm in fm.createFile(atPath: \"x\", contents: nil) }\n"
            "}\n"
        ))
        assert _hint(_one(edges, "createFile")) is None


class TestABindingIsVisibleOnlyAfterItIsDeclared:
    """A scope map keyed by NAME alone let a later declaration retype an earlier use.

    Measured: once the generic-constructor arm typed hummingbird's
    ``let container = KDC<NestedKey>(...)``, the ``container.values[...]`` two
    lines ABOVE it -- the struct's FIELD -- was stamped ``KDC``. Swift accepts that
    code; the earlier use names the field. A binding is visible from the end of
    its declaration (a parameter's, a clause's RHS), so the declaration's own
    initialiser still sees the outer name.
    """

    def test_a_use_before_a_shadowing_local_sees_the_field(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "f", (
            "import Foundation\n"
            "class Holder {\n"
            "    var container: FileManager = FileManager.default\n"
            "    func go(p: String) {\n"
            "        container.fileExists(atPath: p)\n"
            "        let container = URLSession.shared\n"
            "        container.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"
        assert _one(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_an_initialiser_sees_the_name_it_shadows(self, tmp_path: Path) -> None:
        """``let s = s.session()``: the RHS ``s`` is the ``Store`` parameter. Read
        by name alone it was the local's own type, ``URLSession.session``."""
        edges = _edges(tmp_path / "i", STORE + (
            "func go(s: Store, u: URL) {\n"
            "    let s = s.session()\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        ))
        session = [e for e in edges if e.edge_type == "calls" and "session" in e.dst]
        assert [e.dst for e in session] == ["swift:a.swift:3-3:Store.session:method"]
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"

    def test_the_latest_preceding_binding_wins(self, tmp_path: Path) -> None:
        """Two ``if let s`` in one function: each body sees its own."""
        edges = _edges(tmp_path / "l", STORE + (
            "func go(store: Store, p: String, u: URL) {\n"
            "    if let s = store.session() {\n"
            "        s.dataTask(with: u)\n"
            "    }\n"
            "    if let s = store.mgr() {\n"
            "        s.fileExists(atPath: p)\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"
        assert _one(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"


class TestEveryCallableBodyIsAScope:
    """An ``init`` / ``deinit`` / ``subscript`` / accessor body is a scope too.

    Only ``function_declaration`` was one, so the parameters and locals of every
    other callable body landed in the FILE-level map and typed same-named
    receivers in every method of the file. Measured on Kingfisher's
    AnimatedImageView.swift: ``init(image: UIImage?, ...)`` typed ``image`` file-wide.
    """

    def test_an_init_local_and_parameter_stay_in_the_init(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "i", STORE + (
            "class Holder {\n"
            "    init(store: Store, mgr: FileManager, u: URL) {\n"
            "        let s = store.session()\n"
            "        s.dataTask(with: u)\n"
            "    }\n"
            "    func go() {\n"
            "        s.frobnicate()\n"
            "        mgr.zap()\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "dataTask").dst == "swift:URLSession:0-0:dataTask:unresolved"
        assert _hint(_one(edges, "frobnicate")) is None
        assert _hint(_one(edges, "zap")) is None

    def test_an_accessor_local_stays_in_the_accessor(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "a", STORE + (
            "class Holder {\n"
            "    var store = Store()\n"
            "    var n: Int {\n"
            "        get {\n"
            "            let s = store.session()\n"
            "            s.flush()\n"
            "            return 1\n"
            "        }\n"
            "    }\n"
            "    subscript(i: Int) -> Int {\n"
            "        let m = store.mgr()\n"
            "        m.sweep()\n"
            "        return i\n"
            "    }\n"
            "    func go() {\n"
            "        s.frobnicate()\n"
            "        m.zap()\n"
            "    }\n"
            "}\n"
        ))
        assert _one(edges, "flush").dst == "swift:URLSession:0-0:flush:unresolved"
        assert _one(edges, "sweep").dst == "swift:FileManager:0-0:sweep:unresolved"
        assert _hint(_one(edges, "frobnicate")) is None
        assert _hint(_one(edges, "zap")) is None


class TestAGenericReceiverTypeResolvesByItsBareName:
    """``let result: Box<Int>`` names the type ``Box``: the in-repo bind looks up
    ``Box.open``, not ``Box<Int>.open``. Measured on Alamofire once its ``init``
    parameters stopped leaking file-wide: ``result.tryMap`` against the field
    ``let result: Result<Success, Failure>`` lost its resolution to the in-repo
    ``Result.tryMap`` extension, because the field registry keeps the spelling."""

    def test_a_generic_field_type_resolves_in_repo(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "g", (
            "class Box<T> {\n"
            "    func open() { }\n"
            "}\n"
            "class Holder {\n"
            "    let result: Box<Int> = Box<Int>()\n"
            "    func go() {\n"
            "        result.open()\n"
            "    }\n"
            "}\n"
        ))
        opens = [e.dst for e in edges if e.edge_type == "calls" and "open" in e.dst]
        assert opens == ["swift:a.swift:2-2:Box.open:method"], opens
