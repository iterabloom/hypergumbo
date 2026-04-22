# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-hopug: relativizing absolute paths in IR objects.

Covers the private ``_relativize_ir_paths`` helper that ``run_behavior_map``
invokes after analysis to strip the machine-specific ``repo_root`` prefix
from Symbol IDs, Edge endpoints, and UsageContext fields before any
downstream consumer (ranking, entrypoint detection, handler slices, or
final JSON emit) observes the IR.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.cli import _relativize_ir_paths
from hypergumbo_core.ir import (
    Edge,
    Span,
    Symbol,
    UsageContext,
    _compute_usage_context_id,
)


def _sym(id: str, path: str) -> Symbol:
    return Symbol(
        id=id,
        name="f",
        kind="function",
        language="python",
        path=path,
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
    )


def _edge(src: str, dst: str) -> Edge:
    return Edge(id="e1", src=src, dst=dst, edge_type="calls", line=1)


class TestRelativizeIRPaths:
    def test_symbol_id_and_path_become_relative(self) -> None:
        repo = Path("/repo/root")
        s = _sym(id="python:/repo/root/src/a.py:1-2:f:function", path="/repo/root/src/a.py")
        _relativize_ir_paths(repo, [s], [], [])
        assert s.id == "python:src/a.py:1-2:f:function"
        assert s.path == "src/a.py"

    def test_edge_src_and_dst_rewritten_in_place(self) -> None:
        repo = Path("/repo/root")
        e = _edge(
            src="python:/repo/root/src/a.py:1-2:f:function",
            dst="python:/repo/root/src/b.py:3-4:g:function",
        )
        _relativize_ir_paths(repo, [], [e], [])
        assert e.src == "python:src/a.py:1-2:f:function"
        assert e.dst == "python:src/b.py:3-4:g:function"

    def test_paths_outside_repo_root_left_untouched(self) -> None:
        """External-dependency symbols sit outside repo_root and must survive."""
        repo = Path("/repo/root")
        external = _sym(id="python:external:0-0:foo:unresolved", path="external")
        keep = _sym(id="go:/other/repo/src/x.go:1-2:y:function", path="/other/repo/src/x.go")
        _relativize_ir_paths(repo, [external, keep], [], [])
        assert external.id == "python:external:0-0:foo:unresolved"
        assert external.path == "external"
        assert keep.id == "go:/other/repo/src/x.go:1-2:y:function"
        assert keep.path == "/other/repo/src/x.go"

    def test_usage_context_path_symbol_ref_and_id_recomputed(self) -> None:
        """UsageContext.id is derived from path; relativizing must regenerate it."""
        repo = Path("/repo/root")
        uc = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="/repo/root/src/urls.py",
            span=Span(start_line=7, end_line=7, start_col=0, end_col=0),
            symbol_ref="python:/repo/root/src/views.py:1-2:list_users:function",
        )
        absolute_id = uc.id
        expected_relative_id = _compute_usage_context_id(
            "src/urls.py", 7, "path", "args[1]",
        )
        _relativize_ir_paths(repo, [], [], [uc])
        assert uc.path == "src/urls.py"
        assert uc.symbol_ref == "python:src/views.py:1-2:list_users:function"
        assert uc.id == expected_relative_id
        assert uc.id != absolute_id

    def test_usage_context_with_none_symbol_ref_is_fine(self) -> None:
        repo = Path("/repo/root")
        uc = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="/repo/root/src/urls.py",
            span=Span(start_line=7, end_line=7, start_col=0, end_col=0),
            symbol_ref=None,
        )
        _relativize_ir_paths(repo, [], [], [uc])
        assert uc.path == "src/urls.py"
        assert uc.symbol_ref is None

    def test_usage_context_outside_repo_keeps_original_id(self) -> None:
        repo = Path("/repo/root")
        uc = UsageContext.create(
            kind="call",
            context_name="path",
            position="args[1]",
            path="/other/repo/urls.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )
        saved_id = uc.id
        _relativize_ir_paths(repo, [], [], [uc])
        assert uc.path == "/other/repo/urls.py"
        assert uc.id == saved_id

    def test_empty_inputs_do_nothing(self) -> None:
        _relativize_ir_paths(Path("/repo/root"), [], [], [])
