# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the method-call-recovery linker (WI-gigoz / Path B').

When a caller has both:
  - a `calls`/`instantiates` edge to a Class node, and
  - a sibling `calls`-to-unresolved edge whose bare name matches a method
    contained in that Class,
the linker emits a synthetic `calls -> Class.method` edge so that forward
slice can traverse from the caller to the actual method without fanning out
through every sibling method on the class (which the slice intentionally
suppresses; see test_forward_slice_class_reaches_method_then_no_sibling_explosion).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.method_call_recovery import (
    _parse_unresolved_name,
    _short_name,
    link_method_call_recovery,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    sid: str,
    name: str,
    kind: str,
    language: str = "kotlin",
    path: str = "App.kt",
    start: int = 1,
    end: int = 5,
) -> Symbol:
    return Symbol(
        id=sid,
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        origin="test",
        origin_run_id="test-run",
        meta=None,
    )


def _edge(src: str, dst: str, edge_type: str, line: int = 10,
          evidence_type: str = "ast_call_direct") -> Edge:
    return Edge.create(
        src=src,
        dst=dst,
        edge_type=edge_type,
        line=line,
        origin="test",
        evidence_type=evidence_type,
    )


def _ctx(symbols: list[Symbol], edges: list[Edge]) -> LinkerContext:
    return LinkerContext(repo_root=Path("/test"), symbols=symbols, edges=edges)


class TestMethodCallRecovery:
    """Path B' rewrites for chained calls like ``CliRunner().run(args)``."""

    def test_recovers_method_call_from_unresolved_sibling(self) -> None:
        """The canonical WI-gigoz / BUG-14 case."""
        main = _sym("k:App.kt:5-9:main:function", "main", "function", start=5, end=9)
        cls = _sym("k:CliRunner.kt:1-30:CliRunner:class", "CliRunner", "class",
                   path="CliRunner.kt", start=1, end=30)
        run_method = _sym("k:CliRunner.kt:10-20:CliRunner.run:method",
                          "CliRunner.run", "method", path="CliRunner.kt",
                          start=10, end=20)

        edges = [
            # Constructor invocation lands on the class.
            _edge(main.id, cls.id, "calls", line=7),
            # Containment from class -> method (provided by containment linker).
            _edge(cls.id, run_method.id, "contains", line=10),
            # Sibling unresolved-call edge with the bare method name in dst.
            _edge(main.id, "kotlin:external:0-0:run:unresolved",
                  "calls", line=7, evidence_type="ast_method_unresolved"),
        ]

        result = link_method_call_recovery(_ctx([main, cls, run_method], edges))

        assert len(result.edges) == 1
        new_edge = result.edges[0]
        assert new_edge.src == main.id
        assert new_edge.dst == run_method.id
        assert new_edge.edge_type == "calls"
        assert new_edge.evidence_type == "method_call_recovery"

    def test_no_recovery_when_method_not_in_class(self) -> None:
        """If the unresolved name is not contained in the called class, skip."""
        main = _sym("py:m.py:1-5:main:function", "main", "function", language="python")
        cls = _sym("py:m.py:10-20:Foo:class", "Foo", "class", language="python")
        # Foo contains 'bar', not 'baz'.
        bar = _sym("py:m.py:11-15:Foo.bar:method", "Foo.bar", "method",
                   language="python", start=11, end=15)
        edges = [
            _edge(main.id, cls.id, "calls", line=2),
            _edge(cls.id, bar.id, "contains", line=11),
            _edge(main.id, "python:external:0-0:baz:unresolved", "calls", line=2),
        ]
        result = link_method_call_recovery(_ctx([main, cls, bar], edges))
        assert result.edges == []

    def test_no_recovery_without_unresolved_sibling(self) -> None:
        """A bare ``calls -> Class`` with no unresolved sibling is left alone."""
        main = _sym("py:m.py:1-5:main:function", "main", "function", language="python")
        cls = _sym("py:m.py:10-20:Foo:class", "Foo", "class", language="python")
        bar = _sym("py:m.py:11-15:Foo.bar:method", "Foo.bar", "method",
                   language="python")
        edges = [
            _edge(main.id, cls.id, "calls", line=2),
            _edge(cls.id, bar.id, "contains", line=11),
        ]
        result = link_method_call_recovery(_ctx([main, cls, bar], edges))
        assert result.edges == []

    def test_disambiguation_by_membership(self) -> None:
        """When two classes are called and only one contains the method,
        the linker picks the matching class even if the other is closer."""
        main = _sym("j:M.java:1-10:main:method", "main", "method", language="java",
                    path="M.java")
        # ClassA has no 'launch' method
        cls_a = _sym("j:A.java:1-10:A:class", "A", "class", language="java",
                     path="A.java")
        # ClassB contains 'launch'
        cls_b = _sym("j:B.java:1-30:B:class", "B", "class", language="java",
                     path="B.java")
        launch = _sym("j:B.java:5-10:B.launch:method", "B.launch", "method",
                      language="java", path="B.java", start=5, end=10)
        edges = [
            _edge(main.id, cls_a.id, "calls", line=3),  # closer
            _edge(main.id, cls_b.id, "calls", line=8),  # farther
            _edge(cls_b.id, launch.id, "contains", line=5),
            _edge(main.id, "java:external:0-0:launch:unresolved", "calls", line=4),
        ]
        result = link_method_call_recovery(_ctx([main, cls_a, cls_b, launch], edges))
        assert len(result.edges) == 1
        assert result.edges[0].dst == launch.id

    def test_line_proximity_tiebreaker(self) -> None:
        """When multiple candidate classes contain the method, pick the
        class whose call edge is closest in line number to the unresolved
        edge."""
        main = _sym("py:m.py:1-30:main:function", "main", "function",
                    language="python")
        cls_far = _sym("py:m.py:1-10:Far:class", "Far", "class",
                       language="python", path="far.py")
        cls_near = _sym("py:m.py:1-10:Near:class", "Near", "class",
                        language="python", path="near.py")
        method_far = _sym("py:far.py:5-9:Far.run:method", "Far.run", "method",
                          language="python", path="far.py", start=5, end=9)
        method_near = _sym("py:near.py:5-9:Near.run:method", "Near.run", "method",
                           language="python", path="near.py", start=5, end=9)
        edges = [
            _edge(main.id, cls_far.id, "calls", line=2),
            _edge(main.id, cls_near.id, "calls", line=15),
            _edge(cls_far.id, method_far.id, "contains", line=5),
            _edge(cls_near.id, method_near.id, "contains", line=5),
            _edge(main.id, "python:external:0-0:run:unresolved", "calls", line=16),
        ]
        result = link_method_call_recovery(_ctx(
            [main, cls_far, cls_near, method_far, method_near], edges,
        ))
        assert len(result.edges) == 1
        assert result.edges[0].dst == method_near.id

    def test_instantiates_edge_also_triggers_recovery(self) -> None:
        """The class hint can come from an ``instantiates`` edge (e.g. JS/TS
        ``new Foo()``) just as well as from a fallback ``calls`` edge."""
        main = _sym("ts:m.ts:1-5:main:function", "main", "function",
                    language="typescript", path="m.ts")
        cls = _sym("ts:F.ts:1-30:Foo:class", "Foo", "class",
                   language="typescript", path="F.ts")
        bark = _sym("ts:F.ts:10-15:Foo.bark:method", "Foo.bark", "method",
                    language="typescript", path="F.ts", start=10, end=15)
        edges = [
            _edge(main.id, cls.id, "instantiates", line=2),
            _edge(cls.id, bark.id, "contains", line=10),
            _edge(main.id, "typescript:external:0-0:bark:unresolved",
                  "calls", line=2),
        ]
        result = link_method_call_recovery(_ctx([main, cls, bark], edges))
        assert len(result.edges) == 1
        assert result.edges[0].dst == bark.id

    def test_does_not_duplicate_existing_resolved_edge(self) -> None:
        """If the caller already has a direct ``calls`` edge to the method,
        the linker does not emit a duplicate."""
        main = _sym("py:m.py:1-5:main:function", "main", "function",
                    language="python")
        cls = _sym("py:m.py:10-20:Foo:class", "Foo", "class", language="python")
        bar = _sym("py:m.py:11-15:Foo.bar:method", "Foo.bar", "method",
                   language="python")
        edges = [
            _edge(main.id, cls.id, "calls", line=2),
            _edge(cls.id, bar.id, "contains", line=11),
            _edge(main.id, bar.id, "calls", line=2),  # already resolved
            _edge(main.id, "python:external:0-0:bar:unresolved", "calls", line=2),
        ]
        result = link_method_call_recovery(_ctx([main, cls, bar], edges))
        assert result.edges == []

    def test_contains_edge_from_non_class_is_ignored(self) -> None:
        """A ``contains`` edge whose src is not a class symbol is ignored
        (e.g., module->function) — only class-owned methods are eligible."""
        main = _sym("py:m.py:1-5:main:function", "main", "function",
                    language="python")
        cls = _sym("py:m.py:10-20:Foo:class", "Foo", "class", language="python")
        bar = _sym("py:m.py:11-15:Foo.bar:method", "Foo.bar", "method",
                   language="python")
        # A non-class container also "contains" something with name "bar"
        module = _sym("py:m.py:1-100:m:module", "m", "module", language="python")
        bystander = _sym("py:m.py:50-60:bar:function", "bar", "function",
                         language="python")
        edges = [
            _edge(main.id, cls.id, "calls", line=2),
            _edge(cls.id, bar.id, "contains", line=11),
            # Non-class containment that should be ignored:
            _edge(module.id, bystander.id, "contains", line=50),
            _edge(main.id, "python:external:0-0:bar:unresolved", "calls", line=2),
        ]
        result = link_method_call_recovery(_ctx(
            [main, cls, bar, module, bystander], edges,
        ))
        assert len(result.edges) == 1
        assert result.edges[0].dst == bar.id

    def test_contains_edge_with_dangling_dst_is_ignored(self) -> None:
        """A ``contains`` edge whose dst symbol id is not in the symbol set
        is silently skipped (defensive against malformed graphs)."""
        main = _sym("py:m.py:1-5:main:function", "main", "function",
                    language="python")
        cls = _sym("py:m.py:10-20:Foo:class", "Foo", "class", language="python")
        edges = [
            _edge(main.id, cls.id, "calls", line=2),
            # contains-edge dst points at a symbol that does not exist
            _edge(cls.id, "py:missing:0-0:Foo.ghost:method", "contains", line=11),
            _edge(main.id, "python:external:0-0:ghost:unresolved", "calls", line=2),
        ]
        result = link_method_call_recovery(_ctx([main, cls], edges))
        assert result.edges == []

    def test_unresolved_dst_with_unparseable_format_is_skipped(self) -> None:
        """An unresolved dst that doesn't fit the bare-name pattern is
        ignored without crashing."""
        main = _sym("py:m.py:1-5:main:function", "main", "function",
                    language="python")
        cls = _sym("py:m.py:10-20:Foo:class", "Foo", "class", language="python")
        bar = _sym("py:m.py:11-15:Foo.bar:method", "Foo.bar", "method",
                   language="python")
        edges = [
            _edge(main.id, cls.id, "calls", line=2),
            _edge(cls.id, bar.id, "contains", line=11),
            # malformed unresolved dst (only 2 parts before :unresolved suffix)
            _edge(main.id, "garbled:unresolved", "calls", line=2),
        ]
        result = link_method_call_recovery(_ctx([main, cls, bar], edges))
        assert result.edges == []


class TestHelpers:
    """Direct unit tests for the small helpers."""

    def test_short_name_no_separator(self) -> None:
        assert _short_name("bare") == "bare"

    def test_short_name_dot_separator(self) -> None:
        assert _short_name("Foo.bar") == "bar"

    def test_short_name_double_colon(self) -> None:
        assert _short_name("Foo::bar") == "bar"

    def test_short_name_hash(self) -> None:
        assert _short_name("Foo#bar") == "bar"

    def test_parse_unresolved_name_happy_path(self) -> None:
        assert _parse_unresolved_name(
            "kotlin:external:0-0:run:unresolved",
        ) == "run"

    def test_parse_unresolved_name_missing_suffix(self) -> None:
        assert _parse_unresolved_name("kotlin:external:0-0:run") is None

    def test_parse_unresolved_name_too_few_parts(self) -> None:
        assert _parse_unresolved_name("garbled:unresolved") is None
