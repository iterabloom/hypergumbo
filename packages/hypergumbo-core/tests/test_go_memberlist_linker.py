# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Go hashicorp/memberlist delegate callback linker (WI-lojuf).

Memberlist is the gossip-based cluster membership library used by
alertmanager, consul, nomad, serf, and vault. The linker bridges
``memberlist.Create(...)`` construction sites (the function that
triggers the runtime to start invoking delegates) to the delegate
callback methods defined on delegate types (``NotifyJoin``,
``NotifyLeave``, ``NotifyUpdate``, ``NotifyMsg``, ``GetBroadcasts``,
``LocalState``, ``MergeRemoteState``, and the sibling
ConflictDelegate/MergeDelegate/AliveDelegate/PingDelegate methods).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.go_memberlist import (
    _DELEGATE_METHOD_NAMES,
    _MEMBERLIST_CREATE_ANCHOR,
    _file_imports_memberlist,
    _find_memberlist_create_lines,
    go_memberlist_linker,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestLowLevelHelpers:
    """Helpers for import detection and create-site extraction."""

    def test_file_imports_memberlist_true(self) -> None:
        source = b'import (\n  "fmt"\n  "github.com/hashicorp/memberlist"\n)\n'
        assert _file_imports_memberlist(source) is True

    def test_file_imports_memberlist_false(self) -> None:
        source = b'import "github.com/hashicorp/raft"\n'
        assert _file_imports_memberlist(source) is False

    def test_create_anchor_matches(self) -> None:
        assert _MEMBERLIST_CREATE_ANCHOR.search(
            b"list, err := memberlist.Create(config)",
        ) is not None

    def test_create_anchor_rejects_unrelated(self) -> None:
        assert _MEMBERLIST_CREATE_ANCHOR.search(
            b"memberlist.CreateMock",
        ) is None  # no opening paren

    def test_find_create_lines_returns_line_numbers(self) -> None:
        source = (
            b"package cluster\n\n"
            b"func Start(c *memberlist.Config) {\n"
            b"    list, err := memberlist.Create(c)\n"
            b"    _ = list; _ = err\n"
            b"}\n"
        )
        assert _find_memberlist_create_lines(source) == [4]

    def test_delegate_method_names_canonical_set(self) -> None:
        # Sanity: the name set covers the 12 canonical delegate
        # interface methods listed in the memberlist godoc.
        assert len(_DELEGATE_METHOD_NAMES) == 12
        assert "NotifyJoin" in _DELEGATE_METHOD_NAMES
        assert "NotifyLeave" in _DELEGATE_METHOD_NAMES
        assert "GetBroadcasts" in _DELEGATE_METHOD_NAMES


class TestGoMemberlistLinkerIntegration:
    """Integration tests running the full linker over tmp_path Go trees."""

    def _write_go_file(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "cluster" / "node.go"
        p.parent.mkdir(parents=True)
        p.write_text(body)
        return p

    def test_links_create_anchor_to_delegate_methods(
        self, tmp_path: Path,
    ) -> None:
        """Anchor function calling memberlist.Create dispatches to
        each delegate method in the same file."""
        file_path = self._write_go_file(
            tmp_path,
            'package cluster\n\n'
            'import "github.com/hashicorp/memberlist"\n\n'
            'type Delegate struct{}\n\n'
            'func (d *Delegate) NotifyMsg(buf []byte) {}\n'
            'func (d *Delegate) NotifyJoin(n *memberlist.Node) {}\n'
            'func (d *Delegate) NotifyLeave(n *memberlist.Node) {}\n\n'
            'func Start(cfg *memberlist.Config) error {\n'
            '    _, err := memberlist.Create(cfg)\n'
            '    return err\n'
            '}\n',
        )

        start_sym = Symbol(
            id=f"go:{file_path}:10-13:Start:function",
            name="Start",
            kind="function",
            language="go",
            path=str(file_path),
            span=Span(start_line=10, end_line=13, start_col=0, end_col=0),
        )
        notify_msg = Symbol(
            id=f"go:{file_path}:6-6:NotifyMsg:method",
            name="Delegate.NotifyMsg",
            kind="method",
            language="go",
            path=str(file_path),
            span=Span(start_line=6, end_line=6, start_col=0, end_col=0),
        )
        notify_join = Symbol(
            id=f"go:{file_path}:7-7:NotifyJoin:method",
            name="Delegate.NotifyJoin",
            kind="method",
            language="go",
            path=str(file_path),
            span=Span(start_line=7, end_line=7, start_col=0, end_col=0),
        )
        notify_leave = Symbol(
            id=f"go:{file_path}:8-8:NotifyLeave:method",
            name="Delegate.NotifyLeave",
            kind="method",
            language="go",
            path=str(file_path),
            span=Span(start_line=8, end_line=8, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[start_sym, notify_msg, notify_join, notify_leave],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)

        # One edge per delegate method.
        dst_ids = {e.dst for e in result.edges}
        assert notify_msg.id in dst_ids
        assert notify_join.id in dst_ids
        assert notify_leave.id in dst_ids
        # All edges originate at the Start anchor.
        assert all(e.src == start_sym.id for e in result.edges)
        # Metadata captures the delegate method name.
        methods = {e.meta["delegate_method"] for e in result.edges if e.meta}
        assert methods == {"NotifyMsg", "NotifyJoin", "NotifyLeave"}

    def test_noop_without_go_language(self, tmp_path: Path) -> None:
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"python"},
        )
        result = go_memberlist_linker(ctx)
        assert result.edges == []

    def test_noop_without_memberlist_import(self, tmp_path: Path) -> None:
        """File with NotifyJoin but no memberlist import → no edges."""
        p = tmp_path / "other.go"
        p.write_text(
            'package other\n\n'
            'type T struct{}\n'
            'func (t *T) NotifyJoin() {}\n',
        )
        sym = Symbol(
            id=f"go:{p}:4-4:NotifyJoin:method",
            name="T.NotifyJoin",
            kind="method",
            language="go",
            path=str(p),
            span=Span(start_line=4, end_line=4, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[sym],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)
        assert result.edges == []

    def test_noop_when_no_delegate_methods(self, tmp_path: Path) -> None:
        """File imports memberlist + calls Create but has no delegate
        methods defined in it."""
        p = tmp_path / "main.go"
        p.write_text(
            'package main\n\n'
            'import "github.com/hashicorp/memberlist"\n\n'
            'func main() {\n'
            '    cfg := memberlist.DefaultLocalConfig()\n'
            '    _, _ = memberlist.Create(cfg)\n'
            '}\n',
        )
        main_sym = Symbol(
            id=f"go:{p}:5-8:main:function",
            name="main",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=5, end_line=8, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[main_sym],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)
        assert result.edges == []

    def test_fallback_anchor_without_create_call(
        self, tmp_path: Path,
    ) -> None:
        """A file with delegate methods but no memberlist.Create() anchor
        falls back to the first non-delegate function as the dispatch source."""
        p = tmp_path / "cluster" / "delegate.go"
        p.parent.mkdir(parents=True)
        p.write_text(
            'package cluster\n\n'
            'import "github.com/hashicorp/memberlist"\n\n'
            'type D struct{}\n\n'
            'func (d *D) NotifyJoin(n *memberlist.Node) {}\n'
            'func Init() *D { return &D{} }\n',
        )
        init_sym = Symbol(
            id=f"go:{p}:8-8:Init:function",
            name="Init",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=8, end_line=8, start_col=0, end_col=0),
        )
        join_sym = Symbol(
            id=f"go:{p}:7-7:NotifyJoin:method",
            name="D.NotifyJoin",
            kind="method",
            language="go",
            path=str(p),
            span=Span(start_line=7, end_line=7, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[init_sym, join_sym],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == init_sym.id
        assert result.edges[0].dst == join_sym.id

    def test_no_anchor_candidates_skips_delegates(
        self, tmp_path: Path,
    ) -> None:
        """When a file has delegate methods but no non-delegate function
        symbols to use as an anchor, emit nothing."""
        p = tmp_path / "only_delegate.go"
        p.write_text(
            'package cluster\n\n'
            'import "github.com/hashicorp/memberlist"\n\n'
            'type D struct{}\n'
            'func (d *D) NotifyJoin(n *memberlist.Node) {}\n',
        )
        join_sym = Symbol(
            id=f"go:{p}:6-6:NotifyJoin:method",
            name="D.NotifyJoin",
            kind="method",
            language="go",
            path=str(p),
            span=Span(start_line=6, end_line=6, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[join_sym],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)
        assert result.edges == []

    def test_multiple_anchor_functions_emit_cartesian_edges(
        self, tmp_path: Path,
    ) -> None:
        """Two different anchor functions each calling memberlist.Create
        in the same file produce edges from both anchors to the shared
        delegate (Cartesian product within one file)."""
        p = tmp_path / "cluster.go"
        p.write_text(
            'package cluster\n\n'
            'import "github.com/hashicorp/memberlist"\n\n'
            'type D struct{}\n\n'
            'func (d *D) NotifyJoin(n *memberlist.Node) {}\n\n'
            'func StartA() error {\n'
            '    _, _ = memberlist.Create(nil)\n'
            '    return nil\n'
            '}\n\n'
            'func StartB() error {\n'
            '    _, _ = memberlist.Create(nil)\n'
            '    return nil\n'
            '}\n',
        )
        start_a = Symbol(
            id=f"go:{p}:9-12:StartA:function",
            name="StartA",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=9, end_line=12, start_col=0, end_col=0),
        )
        start_b = Symbol(
            id=f"go:{p}:14-17:StartB:function",
            name="StartB",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=14, end_line=17, start_col=0, end_col=0),
        )
        join_sym = Symbol(
            id=f"go:{p}:7-7:NotifyJoin:method",
            name="D.NotifyJoin",
            kind="method",
            language="go",
            path=str(p),
            span=Span(start_line=7, end_line=7, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[start_a, start_b, join_sym],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)
        pairs = {(e.src, e.dst) for e in result.edges}
        assert pairs == {
            (start_a.id, join_sym.id),
            (start_b.id, join_sym.id),
        }

    def test_dedupe_when_multiple_create_calls(
        self, tmp_path: Path,
    ) -> None:
        """Two memberlist.Create calls in the same function emit a single
        edge per delegate (not two)."""
        p = tmp_path / "cluster.go"
        p.write_text(
            'package cluster\n\n'
            'import "github.com/hashicorp/memberlist"\n\n'
            'type D struct{}\n\n'
            'func (d *D) NotifyJoin(n *memberlist.Node) {}\n\n'
            'func Start() error {\n'
            '    _, _ = memberlist.Create(nil)\n'
            '    _, _ = memberlist.Create(nil)\n'
            '    return nil\n'
            '}\n',
        )
        start_sym = Symbol(
            id=f"go:{p}:9-13:Start:function",
            name="Start",
            kind="function",
            language="go",
            path=str(p),
            span=Span(start_line=9, end_line=13, start_col=0, end_col=0),
        )
        join_sym = Symbol(
            id=f"go:{p}:7-7:NotifyJoin:method",
            name="D.NotifyJoin",
            kind="method",
            language="go",
            path=str(p),
            span=Span(start_line=7, end_line=7, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[start_sym, join_sym],
            detected_languages={"go"},
        )
        result = go_memberlist_linker(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == start_sym.id
        assert result.edges[0].dst == join_sym.id
