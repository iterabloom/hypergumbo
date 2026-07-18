# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the receiver_type_dispatch linker (INV-vigaf / WI-panah).

The linker resolves unresolved ``calls`` edges carrying a
``receiver_type_hint`` to an extension method (``meta.extension_receiver``)
or a UFCS free function (``meta.ufcs_receiver_type``) whose declared
receiver/first-parameter type matches the hint and whose short name matches
the callee. It owns the receiver-type-keyed search that analyzers must NOT do
themselves (the INV-nilud contract extended to the non-hierarchy family).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.receiver_type_dispatch import (
    link_receiver_type_dispatch,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _callable(
    sid: str,
    name: str,
    *,
    receiver_meta_key: str | None = None,
    receiver_type: str | None = None,
    kind: str = "function",
    language: str = "kotlin",
    path: str = "/a.kt",
) -> Symbol:
    meta = None
    if receiver_meta_key is not None:
        meta = {receiver_meta_key: receiver_type}
    return Symbol(
        id=sid, name=name, kind=kind, language=language, path=path,
        span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=meta,
    )


def _caller(sid: str = "sym:caller", language: str = "kotlin") -> Symbol:
    return Symbol(
        id=sid, name="caller", kind="function", language=language,
        path="/caller.kt",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run", meta=None,
    )


def _unresolved_call(
    src_id: str, callee_short: str, receiver_type_hint: str,
    *, language: str = "kotlin", line: int = 1,
) -> Edge:
    return make_unresolved_edge(
        lang=language, src_id=src_id, callee_name=callee_short,
        line=line, pass_id="test-pass", run_id="test-run",
        receiver_type_hint=receiver_type_hint,
    )


class TestExtensionResolution:
    def test_resolves_extension_method_via_receiver_type(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        call = _unresolved_call(caller.id, "pretty", "JSON")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call])
        result = link_receiver_type_dispatch(ctx)
        resolved = [e for e in result.edges
                    if e.src == caller.id and e.dst == ext.id]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_extension"
        assert resolved[0].is_resolved is True
        assert resolved[0].confidence == 0.8
        assert resolved[0].confidence_source == "evidence_derived"
        assert resolved[0].line == 1
        assert resolved[0].derived_from == [caller.id, ext.id]

    def test_resolves_via_qualified_def_name(self) -> None:
        """A qualified definition name still matches on its short name."""
        ext = _callable("sym:ext.pretty", "JSON.pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        call = _unresolved_call(caller.id, "pretty", "JSON")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call])
        result = link_receiver_type_dispatch(ctx)
        assert [e for e in result.edges if e.dst == ext.id]

    def test_wrong_receiver_type_no_match(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        call = _unresolved_call(caller.id, "pretty", "XML")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call])
        assert link_receiver_type_dispatch(ctx).edges == []


class TestUfcsResolution:
    def test_resolves_ufcs_free_function_via_first_param_type(self) -> None:
        fn = _callable("sym:d.exists", "exists",
                       receiver_meta_key="ufcs_receiver_type",
                       receiver_type="File", language="d", path="/a.d")
        caller = _caller(language="d")
        call = _unresolved_call(caller.id, "exists", "File", language="d")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[fn, caller], edges=[call])
        result = link_receiver_type_dispatch(ctx)
        resolved = [e for e in result.edges if e.dst == fn.id]
        assert len(resolved) == 1
        assert resolved[0].evidence_type == "ast_call_ufcs"
        assert resolved[0].confidence == 0.8


class TestGuards:
    def test_ambiguous_receiver_type_withholds(self) -> None:
        a = _callable("sym:a.foo", "foo",
                      receiver_meta_key="extension_receiver",
                      receiver_type="T")
        b = _callable("sym:b.foo", "foo",
                      receiver_meta_key="extension_receiver",
                      receiver_type="T")
        caller = _caller()
        call = _unresolved_call(caller.id, "foo", "T")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[a, b, caller], edges=[call])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_no_receiver_hint_ignored(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        call = make_unresolved_edge(
            lang="kotlin", src_id=caller.id, callee_name="pretty",
            line=1, pass_id="p", run_id="r",
        )
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_resolved_edge_not_reprocessed(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        already = Edge.create(
            src=caller.id, dst=ext.id, edge_type="calls", line=1,
            origin="test", origin_run_id="test", is_resolved=True,
            meta={"receiver_type_hint": "JSON"},
        )
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[already])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_non_calls_edge_ignored(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        imports_edge = Edge.create(
            src=caller.id, dst=ext.id, edge_type="imports", line=1,
            origin="test", origin_run_id="test", is_resolved=False,
            meta={"receiver_type_hint": "JSON"},
        )
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[imports_edge])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_cross_language_not_resolved(self) -> None:
        """INV-milud: a kotlin def does not resolve a swift call site."""
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON", language="kotlin")
        caller = _caller(language="swift")
        call = _unresolved_call(caller.id, "pretty", "JSON",
                                language="swift")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_missing_src_symbol_skipped(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        call = _unresolved_call("sym:ghost", "pretty", "JSON")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext], edges=[call])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_unparseable_dst_skipped(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        bad = Edge.create(
            src=caller.id, dst="not-an-unresolved-id", edge_type="calls",
            line=1, origin="test", origin_run_id="test", is_resolved=False,
            meta={"receiver_type_hint": "JSON"},
        )
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[bad])
        assert link_receiver_type_dispatch(ctx).edges == []

    def test_existing_resolved_pair_not_duplicated(self) -> None:
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        call = _unresolved_call(caller.id, "pretty", "JSON")
        already = Edge.create(
            src=caller.id, dst=ext.id, edge_type="calls", line=1,
            origin="test", origin_run_id="test", is_resolved=True,
        )
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call, already])
        result = link_receiver_type_dispatch(ctx)
        assert [e for e in result.edges
                if e.src == caller.id and e.dst == ext.id] == []

    def test_two_identical_calls_resolve_once(self) -> None:
        """The second identical unresolved call dedups against the first."""
        ext = _callable("sym:ext.pretty", "pretty",
                        receiver_meta_key="extension_receiver",
                        receiver_type="JSON")
        caller = _caller()
        call_a = _unresolved_call(caller.id, "pretty", "JSON")
        call_b = _unresolved_call(caller.id, "pretty", "JSON")
        ctx = LinkerContext(repo_root=Path("/"),
                            symbols=[ext, caller], edges=[call_a, call_b])
        result = link_receiver_type_dispatch(ctx)
        assert len([e for e in result.edges if e.dst == ext.id]) == 1
