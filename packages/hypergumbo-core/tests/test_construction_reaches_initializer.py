# SPDX-License-Identifier: AGPL-3.0-or-later
"""Construction reaches the initializer, whatever dst the analyzer chose
(INV-rolok).

THE GAP. INV-kahig made ``instantiates`` confer reachability in the dead-code
BFS. That was necessary and not sufficient, because it settled which EDGE
TYPES are traversable and left open WHICH NODE the edge lands on. ruby resolves
``Klass.new`` to ``Klass#initialize``; csharp (WI-fagit, PR #689) and java land
on a ``constructor`` symbol. py/js_ts/dart land on the **class**, and the only
edges leaving a class node are ``contains`` / ``extends`` / ``decorated_by`` --
none of which confer reachability, correctly, because containing is not
calling. So on those languages the BFS arrives at the class and stops, and the
initializer plus everything downstream of it reads as dead.

Measured at tip (self-analysis, 46,203 nodes / 183,507 edges): of 11,428
``instantiates`` edges, 9,217 land on a ``class`` and 2,211 on an
``external_symbol`` -- and **zero** on a function or method. All 69 python
``__init__`` methods have exactly one incoming edge type, ``contains``.

WHY THE INV-kahig ARM DID NOT CATCH IT. Its fixture is
``main -[instantiates]-> init -[calls]-> polish``: the construction edge is
handed straight to the initializer, which is a shape py/js_ts/dart never emit.
The arm proves the edge TYPE is traversable and is silent on the dst, so it
passed throughout.

THE LICENCE IS THE CONJUNCTION, NEVER ``contains`` ALONE. Making ``contains``
traversable would make every method of every instantiated class
unconditionally reachable and destroy dead-code's precision wholesale. What
licenses the hop here is (the class was CONSTRUCTED) **and** (this member is
the code that runs on construction). Either half alone licenses nothing, and
``TestContainmentStillConfersNothing`` pins that.

WHY NOT RE-POINT THE dst (the item's option A). WI-fagit is the precedent and
it moved csharp onto the constructor -- but ``method_call_recovery`` keys its
class hint on ``e.dst in class_ids``, so re-pointing python's dst would
silently remove python from the linker that produced 89 edges on this repo, a
regression in the opposite direction, for a measured gain of 3 rows. Re-pointing
also perturbs entrypoint detection: minting the edges as a linker cost 5 route
seeds on pretix (seed_count 1174 -> 1173), because ``detect_entrypoints`` reads
the edge set. Augmenting the BFS's OWN graph, after entrypoint detection has
run, changes no emitted edge and no other consumer -- the precedent being
``is_grpc_rpc_implementation``: the edges are correct as they stand, only this
walk's reading of them changes.
"""
from __future__ import annotations

from typing import Any, ClassVar

from hypergumbo_core.cli import (
    _bfs_reachable,
    _construction_initializer_edges,
)
from hypergumbo_core.symbol_kinds import is_initializer


class TestTheInitializerPredicate:
    """Which contained member is "the code that runs on construction" is a
    per-language fact, and two different mechanisms already encode it."""

    def test_the_constructor_kind_is_an_initializer_in_any_language(self) -> None:
        # java / csharp / apex / pony emit kind="constructor" -- the registry
        # entry already reads "Constructor / __init__ / init method".
        assert is_initializer("constructor", "Widget.Widget", "csharp")
        assert is_initializer("constructor", "Widget", "java")

    def test_python_dunder_init_is_an_initializer(self) -> None:
        # py.py emits it as kind="method"; the NAME is the only signal.
        assert is_initializer("method", "Widget.__init__", "python")

    def test_javascript_constructor_method_is_an_initializer(self) -> None:
        # 625 of these in the pretix survey, all kind="method".
        assert is_initializer("method", "Widget.constructor", "javascript")
        assert is_initializer("method", "Widget.constructor", "typescript")

    def test_ruby_uses_its_own_separator(self) -> None:
        # The `#` separator comes from member_names, not a hand-rolled copy.
        assert is_initializer("method", "Widget#initialize", "ruby")

    def test_php_construct(self) -> None:
        assert is_initializer("method", "Widget.__construct", "php")

    def test_an_unqualified_initializer_name_still_binds(self) -> None:
        assert is_initializer("method", "__init__", "python")

    def test_an_ordinary_method_is_not_an_initializer(self) -> None:
        assert not is_initializer("method", "Widget.save", "python")

    def test_the_name_is_scoped_to_ITS_language(self) -> None:
        """A python method literally named ``constructor`` is not an
        initializer, and a javascript one named ``__init__`` is not either.
        Without scoping the table degenerates into a global name blocklist."""
        assert not is_initializer("method", "Widget.constructor", "python")
        assert not is_initializer("method", "Widget.__init__", "javascript")

    def test_an_unknown_language_falls_back_to_the_kind_only(self) -> None:
        assert not is_initializer("method", "Widget.__init__", "brainfuck")
        assert is_initializer("constructor", "Widget.Widget", "brainfuck")

    def test_a_class_is_never_its_own_initializer(self) -> None:
        """Guards the augmentation against looping a class back onto itself
        when a contains edge is self-referential."""
        assert not is_initializer("class", "Widget", "python")


def _n(nid: str, kind: str, name: str, lang: str = "python") -> dict[str, Any]:
    return {"id": nid, "kind": kind, "name": name, "language": lang,
            "path": "src/app.py"}


def _e(src: str, dst: str, etype: str) -> dict[str, Any]:
    return {"src": src, "dst": dst, "type": etype}


class TestTheWalkEntersTheInitializer:
    """The defect, at the layer that consumes it."""

    _NODES: ClassVar[list[dict[str, Any]]] = [
        _n("main", "function", "main"),
        _n("Widget", "class", "Widget"),
        _n("Widget.__init__", "method", "Widget.__init__"),
        _n("polish", "function", "polish"),
    ]
    _EDGES: ClassVar[list[dict[str, Any]]] = [
        _e("main", "Widget", "instantiates"),      # py.py's real shape
        _e("Widget", "Widget.__init__", "contains"),
        _e("Widget.__init__", "polish", "calls"),
    ]

    def _reachable(self, *, augmented: bool) -> set[str]:
        graph: dict[str, list[str]] = {}
        for edge in self._EDGES:
            if edge["type"] in ("calls", "instantiates"):
                graph.setdefault(edge["src"], []).append(edge["dst"])
        if augmented:
            for src, dst in _construction_initializer_edges(
                self._NODES, self._EDGES,
            ):
                graph.setdefault(src, []).append(dst)
        return set(_bfs_reachable({"main"}, graph))

    def test_without_the_hop_the_walk_stops_at_the_class(self) -> None:
        """POSITIVE CONTROL — pins the defect, so the arm below is shown to
        depend on the fix rather than passing for an unrelated reason."""
        assert self._reachable(augmented=False) == {"main", "Widget"}

    def test_with_the_hop_the_initializer_and_its_callee_are_reached(
        self,
    ) -> None:
        assert self._reachable(augmented=True) == {
            "main", "Widget", "Widget.__init__", "polish",
        }


class TestContainmentStillConfersNothing:
    """The precision guarantee. If this class of test fails, the fix has
    become "make ``contains`` traversable", which the item rules out."""

    def test_an_ordinary_method_of_a_constructed_class_stays_unreached(
        self,
    ) -> None:
        nodes = [
            _n("main", "function", "main"),
            _n("Widget", "class", "Widget"),
            _n("Widget.__init__", "method", "Widget.__init__"),
            _n("Widget.save", "method", "Widget.save"),
        ]
        edges = [
            _e("main", "Widget", "instantiates"),
            _e("Widget", "Widget.__init__", "contains"),
            _e("Widget", "Widget.save", "contains"),
        ]
        hops = set(_construction_initializer_edges(nodes, edges))
        assert hops == {("Widget", "Widget.__init__")}

    def test_a_class_that_is_never_constructed_gets_no_hop(self) -> None:
        """The other half of the conjunction. Without a construction edge the
        initializer is correctly dead -- an uninstantiated class's ``__init__``
        does not run."""
        nodes = [
            _n("Widget", "class", "Widget"),
            _n("Widget.__init__", "method", "Widget.__init__"),
        ]
        edges = [_e("Widget", "Widget.__init__", "contains")]
        assert list(_construction_initializer_edges(nodes, edges)) == []

    def test_containment_from_a_non_class_owner_is_ignored(self) -> None:
        """A module ``contains`` its functions too. Only a CLASS that was
        constructed licenses the hop."""
        nodes = [
            _n("main", "function", "main"),
            _n("app.py", "file", "app.py"),
            _n("app.py.__init__", "function", "__init__"),
        ]
        edges = [
            _e("main", "app.py", "instantiates"),
            _e("app.py", "app.py.__init__", "contains"),
        ]
        assert list(_construction_initializer_edges(nodes, edges)) == []


class TestItWorksForEveryDstShapeAnalyZersEmit:
    """A fix justified by python and shipped as a general rule has to be
    correct generally."""

    def test_a_javascript_class_reaches_its_constructor_method(self) -> None:
        nodes = [
            _n("main", "function", "main", "javascript"),
            _n("W", "class", "W", "javascript"),
            _n("W.constructor", "method", "W.constructor", "javascript"),
        ]
        edges = [
            _e("main", "W", "instantiates"),
            _e("W", "W.constructor", "contains"),
        ]
        assert list(_construction_initializer_edges(nodes, edges)) == [
            ("W", "W.constructor"),
        ]

    def test_a_java_class_reaches_its_constructor_KIND(self) -> None:
        nodes = [
            _n("main", "method", "Main.main", "java"),
            _n("W", "class", "W", "java"),
            _n("W.ctor", "constructor", "W", "java"),
        ]
        edges = [
            _e("main", "W", "instantiates"),
            _e("W", "W.ctor", "contains"),
        ]
        assert list(_construction_initializer_edges(nodes, edges)) == [
            ("W", "W.ctor"),
        ]

    def test_a_dst_that_is_ALREADY_the_initializer_needs_no_hop(self) -> None:
        """ruby / csharp shape. The walk already enters the initializer, so
        the augmentation must add nothing rather than double up."""
        nodes = [
            _n("main", "method", "Main.run", "ruby"),
            _n("W#initialize", "method", "W#initialize", "ruby"),
        ]
        edges = [_e("main", "W#initialize", "instantiates")]
        assert list(_construction_initializer_edges(nodes, edges)) == []

    def test_an_unresolved_external_class_dst_is_skipped(self) -> None:
        """2,211 of this repo's 11,428 construction edges land on an
        ``external_symbol`` with no node behind it. Skipped, not crashed."""
        nodes = [_n("main", "function", "main")]
        edges = [_e("main", "python:ext:0-0:Widget:external_symbol",
                    "instantiates")]
        assert list(_construction_initializer_edges(nodes, edges)) == []

    def test_the_hops_are_deterministic(self) -> None:
        """Two classes, two initializers — the output order must not depend on
        set iteration, or an A/B on it is unreadable."""
        nodes = [
            _n("main", "function", "main"),
            _n("A", "class", "A"), _n("A.__init__", "method", "A.__init__"),
            _n("B", "class", "B"), _n("B.__init__", "method", "B.__init__"),
        ]
        edges = [
            _e("main", "B", "instantiates"), _e("main", "A", "instantiates"),
            _e("B", "B.__init__", "contains"),
            _e("A", "A.__init__", "contains"),
        ]
        first = list(_construction_initializer_edges(nodes, edges))
        assert first == sorted(first)
        assert first == list(_construction_initializer_edges(nodes, edges))


class TestTheRefusalBranchesAreReachable:
    """Each guard in the helper needs an arm that actually takes it — a branch
    that has never executed has never been observed to work."""

    def test_an_UNCONSTRUCTED_class_contributes_no_hop_alongside_a_constructed_one(
        self,
    ) -> None:
        """Exercises the ``owner not in constructed`` refusal with a NON-empty
        constructed set, which the early return would otherwise mask."""
        nodes = [
            _n("main", "function", "main"),
            _n("Used", "class", "Used"),
            _n("Used.__init__", "method", "Used.__init__"),
            _n("Unused", "class", "Unused"),
            _n("Unused.__init__", "method", "Unused.__init__"),
        ]
        edges = [
            _e("main", "Used", "instantiates"),
            _e("Used", "Used.__init__", "contains"),
            _e("Unused", "Unused.__init__", "contains"),
        ]
        assert list(_construction_initializer_edges(nodes, edges)) == [
            ("Used", "Used.__init__"),
        ]

    def test_a_contained_id_with_no_node_behind_it_is_skipped(self) -> None:
        """A dangling ``contains`` dst must be skipped, not crash the walk."""
        nodes = [
            _n("main", "function", "main"),
            _n("Widget", "class", "Widget"),
        ]
        edges = [
            _e("main", "Widget", "instantiates"),
            _e("Widget", "python:gone:0-0:__init__:method", "contains"),
        ]
        assert list(_construction_initializer_edges(nodes, edges)) == []


class TestEndToEndThroughTheCommand:
    """The helper being right is not the same as it being WIRED. This arm goes
    through ``cmd_dead_code_maybe`` itself."""

    @staticmethod
    def _map() -> dict[str, Any]:
        return {
            "schema_version": "0.2.0",
            "nodes": [
                {"id": "py:src/app.py:1-3:main:function", "name": "main",
                 "kind": "function", "language": "python", "path": "src/app.py",
                 "span": {"start_line": 1, "end_line": 3},
                 "supply_chain": {"is_exported": True}},
                {"id": "py:src/app.py:5-9:Widget:class", "name": "Widget",
                 "kind": "class", "language": "python", "path": "src/app.py",
                 "span": {"start_line": 5, "end_line": 9}},
                {"id": "py:src/app.py:6-7:Widget.__init__:method",
                 "name": "Widget.__init__", "kind": "method",
                 "language": "python", "path": "src/app.py",
                 "span": {"start_line": 6, "end_line": 7}},
                {"id": "py:src/app.py:11-12:polish:function", "name": "polish",
                 "kind": "function", "language": "python", "path": "src/app.py",
                 "span": {"start_line": 11, "end_line": 12}},
                {"id": "py:src/app.py:14-15:orphan:function", "name": "orphan",
                 "kind": "function", "language": "python", "path": "src/app.py",
                 "span": {"start_line": 14, "end_line": 15}},
            ],
            "edges": [
                {"src": "py:src/app.py:1-3:main:function",
                 "dst": "py:src/app.py:5-9:Widget:class", "type": "instantiates"},
                {"src": "py:src/app.py:5-9:Widget:class",
                 "dst": "py:src/app.py:6-7:Widget.__init__:method",
                 "type": "contains"},
                {"src": "py:src/app.py:6-7:Widget.__init__:method",
                 "dst": "py:src/app.py:11-12:polish:function", "type": "calls"},
            ],
        }

    def test_the_command_no_longer_reports_the_initializer_or_its_callee(
        self, tmp_path: Any,
    ) -> None:
        import argparse
        import io
        import json as _json
        import sys

        from hypergumbo_core.cli import cmd_dead_code_maybe

        bm = tmp_path / "hg.json"
        bm.write_text(_json.dumps(self._map()))
        args = argparse.Namespace(
            path=str(tmp_path), input=str(bm), format="json",
            seeds="exports", min_confidence=0.0, cross_lang_threshold=0,
        )
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cmd_dead_code_maybe(args)
        finally:
            sys.stdout, sys.stderr = old
        assert rc == 0
        dead = {d["name"] for d in _json.loads(out.getvalue())["dead_candidates"]}
        # `orphan` is the CONTROL: nothing constructs or calls it, so the fix
        # must leave it flagged. If it disappeared, the walk had become
        # indiscriminate rather than targeted.
        assert "orphan" in dead
        assert "Widget.__init__" not in dead
        assert "polish" not in dead
