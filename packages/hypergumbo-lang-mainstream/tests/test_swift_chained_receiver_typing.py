# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift receiver that is an EXPRESSION rather than a name carries its type
into the module slot (WI-higob slice 2).

``store.session().invalidateAndCancel()`` names no receiver variable, so
``_extract_call_target`` reports ``receiver_hint=None`` and every such site fell
to the ``external`` placeholder -- while the two-step form
(``let s = store.session(); s.invalidateAndCancel()``) has been typed through the
return-type registry since PR #760. That asymmetry is the gap: the SAME call, one
binding apart, is catalogue-reachable in one spelling and unmatchable in the other.

The receiver expression family, by mechanism rather than by example: a call result
(recursively -- ``a.b().c().d()``), a constructor (``Store()``), a parenthesised
expression, a cast (``o as! FileManager``, whose ``as`` names the type outright),
and any of those under ``try`` / ``await``. A receiver expression whose type the
registry cannot name stays untyped, which is what an unknown result means.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(src)
    return analyze_swift(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


STORE = (
    "import Foundation\n"
    "class Store {\n"
    "    func session() -> URLSession { return URLSession.shared }\n"
    "    func mgr() -> FileManager { return FileManager.default }\n"
    "    func inner() -> Store { return self }\n"
    "    func untyped() { }\n"
    "}\n"
)


class TestAChainedReceiverCarriesItsType:
    def test_two_step_form_is_the_positive_control(self, tmp_path: Path) -> None:
        """The binding that ALREADY works, in the same fixture as the ones that do not."""
        edges = _edges(tmp_path / "ctl", STORE + (
            "func go(store: Store) {\n"
            "    let s = store.session()\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        edge = _call(edges, "invalidateAndCancel")
        assert edge.dst == "swift:URLSession:0-0:invalidateAndCancel:unresolved"

    def test_call_result_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "chain", STORE + (
            "func go(store: Store) {\n"
            "    store.session().invalidateAndCancel()\n"
            "}\n"
        ))
        edge = _call(edges, "invalidateAndCancel")
        assert edge.dst == "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        assert (edge.meta or {}).get("receiver_type_hint") == "URLSession"

    def test_constructor_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "ctor", STORE + (
            "func go() {\n"
            "    Store().session().invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_two_hops_of_call_results(self, tmp_path: Path) -> None:
        """``a.b().c().d()`` -- the recursion, not one hard-coded level."""
        edges = _edges(tmp_path / "hops", STORE + (
            "func go(store: Store) {\n"
            "    store.inner().session().invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_try_await_wrapped_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "ta", STORE + (
            "func go(store: Store) async throws {\n"
            "    try await store.session().invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_parenthesised_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "paren", STORE + (
            "func go(store: Store) {\n"
            "    (store.session()).invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_cast_receiver_names_its_type(self, tmp_path: Path) -> None:
        """``(o as! FileManager).removeItem`` -- the cast IS the type evidence."""
        edges = _edges(tmp_path / "cast", STORE + (
            "func go(o: Any) {\n"
            "    (o as! FileManager).removeItem(atPath: \"/x\")\n"
            "}\n"
        ))
        edge = _call(edges, "removeItem")
        assert edge.dst == "swift:FileManager:0-0:removeItem:unresolved"

    def test_a_project_type_result_rides_in_the_hint_only(self, tmp_path: Path) -> None:
        """ADR-0051: a project type is a symbol, not a module."""
        edges = _edges(tmp_path / "proj", STORE + (
            "func go(store: Store) {\n"
            "    store.inner().nosuchmethod()\n"
            "}\n"
        ))
        edge = _call(edges, "nosuchmethod")
        assert edge.dst == "swift:external:0-0:nosuchmethod:unresolved"
        assert (edge.meta or {}).get("receiver_type_hint") == "Store"

    def test_an_unregistered_result_stays_untyped(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "unk", STORE + (
            "func go(store: Store) {\n"
            "    store.untyped().invalidateAndCancel()\n"
            "}\n"
        ))
        edge = _call(edges, "invalidateAndCancel")
        assert edge.dst == "swift:external:0-0:invalidateAndCancel:unresolved"
        assert "receiver_type_hint" not in (edge.meta or {})

    def test_a_cast_to_a_collection_is_not_a_receiver_type(self, tmp_path: Path) -> None:
        """``_swift_bare_type`` is the one rule for which spellings are receiver types."""
        edges = _edges(tmp_path / "coll", STORE + (
            "func go(o: Any) {\n"
            "    (o as! [String]).removeItem(atPath: \"/x\")\n"
            "}\n"
        ))
        edge = _call(edges, "removeItem")
        assert edge.dst == "swift:external:0-0:removeItem:unresolved"
        assert "receiver_type_hint" not in (edge.meta or {})

    def test_a_parenthesised_tuple_is_not_a_receiver_expression(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "tup", STORE + (
            "func go(a: Int, b: Int) {\n"
            "    (a, b).invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:external:0-0:invalidateAndCancel:unresolved"
        )

    def test_a_literal_receiver_names_no_type(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "lit", STORE + (
            "func go() {\n"
            "    \"abc\".invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:external:0-0:invalidateAndCancel:unresolved"
        )

    def test_a_chained_initialiser_types_the_local_too(self, tmp_path: Path) -> None:
        """The same walker serves ``let s = Store().session()`` (WI-higob slice 1's path)."""
        edges = _edges(tmp_path / "init", STORE + (
            "func go() {\n"
            "    let s = Store().session()\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        assert _call(edges, "invalidateAndCancel").dst == (
            "swift:URLSession:0-0:invalidateAndCancel:unresolved"
        )

    def test_a_cast_initialiser_types_the_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "castinit", STORE + (
            "func go(o: Any) {\n"
            "    let fm = o as! FileManager\n"
            "    fm.removeItem(atPath: \"/x\")\n"
            "}\n"
        ))
        assert _call(edges, "removeItem").dst == (
            "swift:FileManager:0-0:removeItem:unresolved"
        )

    def test_an_unnameable_hop_stops_the_walk(self, tmp_path: Path) -> None:
        """``store.untyped().session()`` -- hop 1 names no type, so hop 2 has no owner."""
        edges = _edges(tmp_path / "stop", STORE + (
            "func go(store: Store) {\n"
            "    store.untyped().session().invalidateAndCancel()\n"
            "}\n"
        ))
        edge = _call(edges, "invalidateAndCancel")
        assert edge.dst == "swift:external:0-0:invalidateAndCancel:unresolved"
        assert "receiver_type_hint" not in (edge.meta or {})


class TestScopingUnderAnErrorRecoveredParse:
    """A declaration whose ancestor chain hits an ERROR node before a function is
    treated as file-level, because error recovery re-parents declarations
    arbitrarily and a byte span under an ERROR proves nothing about scope (PR #760).

    The fixture is the delta-debugged minimum of VernissageServer's
    `ActivityPubService.swift`: tree-sitter-swift 0.7.3 cannot parse `try` inside an
    `if` condition, so the `let` in the body is parented by the ERROR rather than by
    the enclosing function.
    """

    def test_a_declaration_under_an_error_node_still_types_its_receiver(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "err", (
            "import Foundation\n"
            "func handle(svc: Store, id: Int) async throws {\n"
            "    if let u = try await svc.find(id: id) {\n"
            "        let fm = FileManager.default\n"
            "        fm.removeItem(atPath: \"/tmp/x\")\n"
            "    }\n"
            "}\n"
        ))
        edge = _call(edges, "removeItem")
        assert edge.dst == "swift:FileManager:0-0:removeItem:unresolved", edge.dst
