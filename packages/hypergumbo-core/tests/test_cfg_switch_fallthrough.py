# SPDX-License-Identifier: AGPL-3.0-or-later
"""A switch / match with no matching arm falls through (WI-sozob).

``CfgBuilder._process_switch`` gave the scrutinee block no "no case matched"
edge, so in a switch without a default a definition killed in EVERY arm did
not reach the code after it::

    t := source()
    switch x {
    case 1:
        t = "c"
    }
    sink(t)        // the source's t reaches here whenever x != 1

That REMOVES reaching definitions -- the direction that can manufacture a walk
``False`` and, since WI-kabif, delete a real flow. The scrutinee now always
has a fall-through edge. An exhaustive switch (a default arm, a Rust match)
gains a path that never runs: it adds definitions, so it can only produce
FEWER ``False`` answers, which is the conservative side.
"""
from __future__ import annotations

from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import (
    FunctionCfg,
    build_function_cfg,
    clear_cfg_mapping_cache,
    load_cfg_mapping,
    solve_reaching_defs,
)

_FUNCTION_TYPES = {"go": "function_declaration", "python": "function_definition"}


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_cfg_mapping_cache()


def _build(lang: str, source: str) -> FunctionCfg:
    parser = tree_sitter.Parser(get_language(lang))
    src = source.encode("utf-8")
    stack: list[Any] = [parser.parse(src).root_node]
    while stack:
        node = stack.pop()
        if node.type == _FUNCTION_TYPES[lang]:
            mapping = load_cfg_mapping(lang)
            assert mapping is not None
            return build_function_cfg(
                node.child_by_field_name("body"), src, mapping, f"{lang}:t:1-99:f:function",
            )
        stack.extend(reversed(node.children))
    raise AssertionError("no function in fixture")  # pragma: no cover


def _reaching(cfg: FunctionCfg, defs: dict[str, list[str]], uses: dict[str, list[str]]) -> set:
    for block in cfg.blocks.values():
        for stmt in block.statements:
            for snippet, names in defs.items():
                if stmt.code_snippet.startswith(snippet):
                    stmt.defines = names
            for snippet, names in uses.items():
                if stmt.code_snippet.startswith(snippet):
                    stmt.uses = names
    return {(e.variable, e.def_line, e.use_line) for e in solve_reaching_defs(cfg).ddg_edges}


_GO = """package p
func f(x int) {
\tt := source()
\tswitch x {
\tcase 1:
\t\tt = "c"
\t}
\tsink(t)
}
"""

_PY = """def f(x):
    t = source()
    match x:
        case 1:
            t = "c"
    sink(t)
"""


def test_a_go_switch_without_default_lets_the_earlier_definition_through() -> None:
    edges = _reaching(_build("go", _GO), {"t := source()": ["t"], 't = "c"': ["t"]}, {"sink(t)": ["t"]})
    assert ("t", 3, 8) in edges
    assert ("t", 6, 8) in edges


def test_a_python_match_without_a_wildcard_lets_it_through() -> None:
    edges = _reaching(_build("python", _PY), {"t = source()": ["t"], 't = "c"': ["t"]}, {"sink(t)": ["t"]})
    assert ("t", 2, 6) in edges
    assert ("t", 5, 6) in edges
