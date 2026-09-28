# SPDX-License-Identifier: AGPL-3.0-or-later
"""The mode seam reads a flag expression by its names (WI-ninuz).

``stamp_io_mode_from_call`` read a mode only from a string literal, which is
right for ``open(p, "w")`` and ``fopen(p, "w")`` and says nothing for Go, whose
``os.OpenFile(name, flag, perm)`` spells its mode as package constants OR-ed
together. ``ModeArgument.flag_modes`` names each flag's mode; any write flag
makes the call a write, ``O_RDONLY`` alone a read, and a flag held in a
variable names no flag and stamps nothing -- the same absence a non-literal
mode string records.
"""

from __future__ import annotations

import pytest
import tree_sitter
import tree_sitter_go

from hypergumbo_core.analyze.base import stamp_io_mode_from_call
from hypergumbo_core.ir import Edge

_PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))


def _mode(call_text: str) -> str | None:
    src = f"package p\n\nfunc f(p string, flag int) {{\n\t{call_text}\n}}\n".encode()
    tree = _PARSER.parse(src)
    stack, call = [tree.root_node], None
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            call = node
            break
        stack.extend(node.children)
    assert call is not None
    edges = [Edge.create(src="go:p.go:3-5:f:function", dst="go:os:0-0:OpenFile:unresolved",
                         edge_type="calls", line=4, evidence_type="ast_call",
                         origin="go-v1", origin_run_id="uuid:test")]
    stamp_io_mode_from_call(edges, 0, call, src, "go")
    return (edges[0].meta or {}).get("io_mode")


@pytest.mark.parametrize("call,expected", [
    ("os.OpenFile(p, os.O_WRONLY|os.O_CREATE, 0o644)", "w"),
    ("os.OpenFile(p, os.O_RDWR, 0o644)", "w"),
    ("os.OpenFile(p, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)", "w"),
    ("os.OpenFile(p, os.O_RDONLY, 0)", "r"),
    ("os.OpenFile(p, flag, 0)", None),
])
def test_the_flags_decide_the_mode(call: str, expected: str | None) -> None:
    assert _mode(call) == expected
