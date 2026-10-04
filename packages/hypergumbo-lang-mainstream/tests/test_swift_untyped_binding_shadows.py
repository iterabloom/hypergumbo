# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift receiver name resolves to the binding in scope at the use's POSITION (WI-silos).

Two defects of the binding pass's scope map, both stamping one variable's type
on another:

* **An untyped declaration did not shadow.** The pass recorded a name only when
  it could TYPE it, so a declaration it could not type -- an unannotated closure
  parameter, a local whose initialiser no registry types, a parameter of an
  unextractable type (``[String]``), a loop variable, a ``catch`` / ``case let``
  / tuple binding -- left no entry, and ``_type_of`` fell THROUGH it to the
  outer binding, the file level or the enclosing type's field. ABSENT is not
  EMPTY: a declaration is a positive claim that the name means something new
  here, even when its type is unknown. Each is now recorded with type ``None``,
  and the innermost visible binding decides, typed or not.
* **A block was not a scope.** Only callable bodies were, so a ``let`` inside an
  ``if`` / ``for`` / ``do`` / ``switch`` body, and an ``if let`` / ``while let``
  clause, typed a same-named receiver after the block (and in its ``else``).

Every test pairs the shadowed use with a control in the same fixture that must
still be typed, so a fix that stops typing everything cannot pass.
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


def _hint(edges: list[Edge], method: str) -> object:
    return (_one(edges, method).meta or {}).get("receiver_type_hint")


class TestAnUntypedDeclarationShadows:
    def test_an_untyped_local_shadows_the_parameter_it_reads(self, tmp_path: Path) -> None:
        """The item's fixture. ``makeSession`` has no return-type row, so the local
        ``s`` is untyped; ``s.dataTask`` was stamped FileManager from the
        parameter the local shadows. The initialiser's own ``s`` IS the
        parameter and stays FileManager (the control)."""
        edges = _edges(tmp_path / "l", (
            "import Foundation\n"
            "func go(s: FileManager, store: Int, u: URL) {\n"
            "    let s = s.makeSession(store)\n"
            "    s.dataTask(with: u)\n"
            "}\n"
        ))
        assert _hint(edges, "makeSession") == "FileManager"
        assert _hint(edges, "dataTask") is None

    def test_an_unannotated_closure_parameter_shadows_the_enclosing_local(
        self, tmp_path: Path,
    ) -> None:
        """hummingbird URI.swift:46-47's shape: the closure's ``query`` was typed
        through the OUTER ``guard var query``."""
        edges = _edges(tmp_path / "c", (
            "func go(_query: FileManager?, queries: [Int]) {\n"
            "    guard var query = _query else { return }\n"
            "    query.removeItem(atPath: \"x\")\n"
            "    queries.map { query -> Int in query.zork() }\n"
            "}\n"
        ))
        assert _hint(edges, "removeItem") == "FileManager"
        assert _hint(edges, "zork") is None

    def test_an_untyped_parameter_shadows_the_field(self, tmp_path: Path) -> None:
        """``[String]`` is a type the parameter reader does not extract; the
        parameter still is not the field ``fm``."""
        edges = _edges(tmp_path / "p", (
            "import Foundation\n"
            "class Holder {\n"
            "    var fm: FileManager = FileManager.default\n"
            "    func go(fm: [String]) {\n"
            "        fm.zap()\n"
            "    }\n"
            "    func other() {\n"
            "        fm.removeItem(atPath: \"x\")\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "zap") is None
        assert _hint(edges, "removeItem") == "FileManager"

    def test_an_untyped_local_shadows_the_field_after_its_declaration(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "f", (
            "import Foundation\n"
            "class Holder {\n"
            "    var container: FileManager = FileManager.default\n"
            "    func go(p: String) {\n"
            "        container.fileExists(atPath: p)\n"
            "        let container = compute()\n"
            "        container.zap()\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "fileExists") == "FileManager"
        assert _hint(edges, "zap") is None

    def test_an_untyped_condition_clause_shadows(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "i", (
            "import Foundation\n"
            "func go(s: URLSession) {\n"
            "    if let s = compute() {\n"
            "        s.zap()\n"
            "    }\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "zap") is None
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_tuple_destructuring_shadows(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "t", (
            "import Foundation\n"
            "func go(s: URLSession, pair: Int) {\n"
            "    s.invalidateAndCancel()\n"
            "    let (s, t) = pair\n"
            "    s.zap()\n"
            "}\n"
        ))
        assert _hint(edges, "invalidateAndCancel") == "URLSession"
        assert _hint(edges, "zap") is None

    def test_a_multi_binding_declaration_types_no_name_from_another_clause(
        self, tmp_path: Path,
    ) -> None:
        """``let a = FileManager(), b = bar()``: the declaration reader took the
        LAST pattern's name and the FIRST constructor's type, stamping
        FileManager on ``b`` and left ``x`` unbound. Each clause is now read as
        a declaration of its own: ``x`` is FileManager, ``b`` is untyped."""
        edges = _edges(tmp_path / "m", (
            "import Foundation\n"
            "func go(a: URLSession) {\n"
            "    let x = FileManager(), b = bar()\n"
            "    b.zork()\n"
            "    x.zap()\n"
            "    a.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "zork") is None
        assert _hint(edges, "zap") == "FileManager"
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_loop_variable_shadows_and_ends_with_the_loop(self, tmp_path: Path) -> None:
        """The sequence expression is OUTSIDE the loop variable's scope: ``for s
        in s.tasks`` reads the parameter."""
        edges = _edges(tmp_path / "f", (
            "import Foundation\n"
            "func go(s: URLSession) {\n"
            "    for s in s.allTasks() where s.ok() {\n"
            "        s.frob()\n"
            "    }\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "allTasks") == "URLSession"
        assert _hint(edges, "ok") is None
        assert _hint(edges, "frob") is None
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_catch_binding_shadows(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "e", (
            "import Foundation\n"
            "func go(e: URLSession) {\n"
            "    do { try thing() } catch let e { e.frob() }\n"
            "    e.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "frob") is None
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_case_let_payload_shadows_in_a_switch(self, tmp_path: Path) -> None:
        """A payload bound by ``let`` shadows; a bare name in a case pattern is an
        EXPRESSION pattern (it reads the outer name) and binds nothing; and the
        enum case name is never a variable, even under ``let`` (``case let
        .reset``)."""
        edges = _edges(tmp_path / "s", (
            "import Foundation\n"
            "func go(w: URLSession, g: FileManager, reset: FileManager, v: Int) {\n"
            "    switch v {\n"
            "    case .some(let w): w.frob()\n"
            "    case let .other(w, k): w.frob2()\n"
            "    case .third(g): g.removeItem(atPath: \"x\")\n"
            "    case let .reset: reset.createFile(atPath: \"x\", contents: nil)\n"
            "    default: w.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "frob") is None
        assert _hint(edges, "frob2") is None
        assert _hint(edges, "removeItem") == "FileManager"
        assert _hint(edges, "createFile") == "FileManager"
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_case_let_payload_shadows_in_a_condition(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "k", (
            "import Foundation\n"
            "func go(x: URLSession, p: FileManager, y: Int) {\n"
            "    if case let .some(x) = y { x.frob() }\n"
            "    guard case .a(let p, var q) = y else { return }\n"
            "    p.frob2()\n"
            "    x.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "frob") is None
        assert _hint(edges, "frob2") is None
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_shorthand_clause_still_keeps_the_outer_type(self, tmp_path: Path) -> None:
        """``if let s`` re-binds ``s`` to ITSELF: it is not an untyped declaration
        and must not become one."""
        edges = _edges(tmp_path / "h", (
            "import Foundation\n"
            "func go(s: URLSession?) {\n"
            "    if let s { s.invalidateAndCancel() }\n"
            "    guard let s else { return }\n"
            "    s.finishTasksAndInvalidate()\n"
            "}\n"
        ))
        assert _hint(edges, "invalidateAndCancel") == "URLSession"
        assert _hint(edges, "finishTasksAndInvalidate") == "URLSession"

    def test_a_protocol_requirement_parameter_types_nothing_outside_it(
        self, tmp_path: Path,
    ) -> None:
        """A protocol method's parameters reached the FILE-level map: ``fm`` in a
        free function was stamped FileManager from ``protocol P { func f(fm:
        FileManager) }``."""
        edges = _edges(tmp_path / "r", (
            "import Foundation\n"
            "protocol P { func f(fm: FileManager) }\n"
            "func go() { fm.removeItem(atPath: \"x\") }\n"
            "func ok(fm: FileManager) { fm.createFile(atPath: \"x\", contents: nil) }\n"
        ))
        assert _hint(edges, "removeItem") is None
        assert _hint(edges, "createFile") == "FileManager"


class TestASelfPropertyInitialiserIsTyped:
    """``if let body = self.body`` / ``let s = self.store.session()``.

    The binding pass typed an initialiser WITHOUT the field resolver the emit
    site uses for a ``self.<property>`` receiver, so these bindings were
    untyped. Before WI-silos that cost nothing visible: the untyped binding
    left no entry and the use fell through to the same-named FIELD, right only
    because the names matched. As a declaration that shadows, it must carry the
    type itself (measured on vapor: ``if var body = self.body`` in
    ClientResponse.swift).
    """

    def test_an_optional_binding_of_a_self_property(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "o", (
            "import Foundation\n"
            "class Holder {\n"
            "    var body: FileManager?\n"
            "    func go() {\n"
            "        if let b = self.body { b.fileExists(atPath: \"x\") }\n"
            "        guard let body = self.body else { return }\n"
            "        body.removeItem(atPath: \"x\")\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "fileExists") == "FileManager"
        assert _hint(edges, "removeItem") == "FileManager"

    def test_a_local_initialised_from_a_self_property_chain(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "c", STORE + (
            "class Holder {\n"
            "    var store = Store()\n"
            "    func go(u: URL) {\n"
            "        let s = self.store.session()\n"
            "        s.dataTask(with: u)\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "dataTask") == "URLSession"


class TestAForceUnwrapCarriesTheWrappedType:
    """``let m = self.manager!``: ``x!`` has ``x``'s (unwrapped) type. Untyped, the
    local shadowed the field ``manager`` it copies and lost the type the
    fall-through used to supply by name (Kingfisher KingfisherManagerTests, 13
    sites). Only ``!`` is stripped: another postfix operator may change the type."""

    def test_a_force_unwrapped_self_property(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "u", (
            "import Foundation\n"
            "class Holder {\n"
            "    var manager: FileManager!\n"
            "    func go(maybe: FileManager?) {\n"
            "        let manager = self.manager!\n"
            "        manager.removeItem(atPath: \"x\")\n"
            "        let other = maybe!\n"
            "        other.fileExists(atPath: \"x\")\n"
            "    }\n"
            "}\n"
        ))
        assert _hint(edges, "removeItem") == "FileManager"
        assert _hint(edges, "fileExists") == "FileManager"

    def test_another_postfix_operator_is_not_its_operand(self, tmp_path: Path) -> None:
        """A custom postfix operator (``g++``, ``postfix_expression`` like ``g!``)
        returns whatever its declaration says, not ``g``'s type."""
        edges = _edges(tmp_path / "r", (
            "import Foundation\n"
            "func go(fm: URLSession, g: FileManager) {\n"
            "    let fm = g++\n"
            "    fm.zap()\n"
            "}\n"
        ))
        assert _hint(edges, "zap") is None


class TestABlockIsAScope:
    def test_an_if_let_binding_ends_with_the_then_branch(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "i", (
            "import Foundation\n"
            "func go(maybe: FileManager?, s: URLSession) {\n"
            "    if let s = maybe { s.fileExists(atPath: \"x\") } else { s.flush() }\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "fileExists") == "FileManager"
        assert _hint(edges, "flush") == "URLSession"
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_later_clause_sees_an_earlier_one(self, tmp_path: Path) -> None:
        """The condition is inside the ``if``'s scope: ``let b = a.mgr()`` reads
        the clause ``a``, not the parameter."""
        edges = _edges(tmp_path / "c", STORE + (
            "func go(a: URLSession, maybe: Store?, p: String) {\n"
            "    if let a = maybe, let b = a.mgr() {\n"
            "        b.fileExists(atPath: p)\n"
            "    }\n"
            "    a.invalidateAndCancel()\n"
            "}\n"
        ))
        mgr = [e for e in edges if e.edge_type == "calls" and "mgr" in e.dst]
        assert [e.dst for e in mgr] == ["swift:a.swift:4-4:Store.mgr:method"]
        assert _hint(edges, "fileExists") == "FileManager"
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_while_let_binding_ends_with_the_loop(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "w", STORE + (
            "func go(store: Store, s: FileManager) {\n"
            "    while let s = store.session() { s.flush() }\n"
            "    s.removeItem(atPath: \"x\")\n"
            "}\n"
        ))
        assert _hint(edges, "flush") == "URLSession"
        assert _hint(edges, "removeItem") == "FileManager"

    def test_a_local_inside_any_block_ends_with_the_block(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "b", (
            "import Foundation\n"
            "func go(flag: Bool, xs: [Int], v: Int) {\n"
            "    if flag { let a = URLSession.shared; a.dataTask() }\n"
            "    for x in xs { let b = FileManager.default; b.zap() }\n"
            "    do { let c = URLSession.shared; c.flush() }\n"
            "    switch v { default: let d = FileManager.default; d.sweep() }\n"
            "    a.frob1()\n"
            "    b.frob2()\n"
            "    c.frob3()\n"
            "    d.frob4()\n"
            "}\n"
        ))
        assert _hint(edges, "dataTask") == "URLSession"
        assert _hint(edges, "zap") == "FileManager"
        assert _hint(edges, "flush") == "URLSession"
        assert _hint(edges, "sweep") == "FileManager"
        for m in ("frob1", "frob2", "frob3", "frob4"):
            assert _hint(edges, m) is None, m

    def test_an_outer_binding_is_visible_in_a_nested_block(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "n", (
            "import Foundation\n"
            "func go(flag: Bool, xs: [Int]) {\n"
            "    let s = URLSession.shared\n"
            "    if flag { for x in xs { s.flush() } }\n"
            "}\n"
        ))
        assert _hint(edges, "flush") == "URLSession"

    def test_a_block_local_shadows_only_inside_the_block(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "s", (
            "import Foundation\n"
            "func go(flag: Bool, s: URLSession) {\n"
            "    if flag {\n"
            "        let s = compute()\n"
            "        s.zap()\n"
            "    }\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _hint(edges, "zap") is None
        assert _hint(edges, "invalidateAndCancel") == "URLSession"

    def test_a_guard_binding_still_reaches_the_rest_of_its_block(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "g", (
            "import Foundation\n"
            "func go(maybe: FileManager?, flag: Bool) {\n"
            "    if flag {\n"
            "        guard let fm = maybe else { return }\n"
            "        fm.removeItem(atPath: \"x\")\n"
            "    }\n"
            "    fm.zap()\n"
            "}\n"
        ))
        assert _hint(edges, "removeItem") == "FileManager"
        assert _hint(edges, "zap") is None


class TestAnErrorRecoveredBlockIsNotAScope:
    def test_a_declaration_in_a_block_under_an_error_stays_file_level(
        self, tmp_path: Path,
    ) -> None:
        """An unclosed ``do {`` parses as ERROR > statements. That ``statements``
        is a body error recovery built, not one the source wrote, so it is not a
        block scope: its declarations fall under the ERROR rule and are
        file-level, visible throughout, as before blocks were scopes (INV-bisok:
        a recovered ERROR region can hold a type's properties, which every
        method of the type must see wherever it sits)."""
        edges = _edges(tmp_path / "e", (
            "import Foundation\n"
            "do {\n"
            " fm.early()\n"
            " let fm = FileManager.default\n"
            " fm.late()\n"
        ))
        assert _hint(edges, "early") == "FileManager"
        assert _hint(edges, "late") == "FileManager"
