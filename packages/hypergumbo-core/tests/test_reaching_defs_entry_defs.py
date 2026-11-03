# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sosov: ``solve_reaching_defs`` takes ENTRY definitions, the parameters.

No def/use extractor defines a function's parameters (py_def_use: "Function
parameters are not defined by this extractor"), so a use of ``req`` had no
reaching definition and no DDG edge, and the walk could never follow a value
that arrives as a parameter. ``entry_defs`` numbers ``(variable, line)`` pairs
as definitions of the entry block placed before its first statement: they
reach every use a statement definition does not kill, and they add NOTHING
else -- no CFG statement, so ``stmt_defuse``, ``unaccounted_names`` and the
coverage gate read exactly what they read before.
"""
from __future__ import annotations

from hypergumbo_core.cfg import (
    BasicBlock, CfgEdge, CfgStatement, FunctionCfg, solve_reaching_defs,
)


def _stmt(line: int, defines: tuple[str, ...] = (), uses: tuple[str, ...] = ()) -> CfgStatement:
    return CfgStatement(
        line=line, col=0, node_type="expression_statement", code_snippet="",
        defines=list(defines), uses=list(uses),
    )


def _cfg(
    blocks: dict[str, list[CfgStatement]], edges: tuple[tuple[str, str], ...] = (),
) -> FunctionCfg:
    built = {bid: BasicBlock(id=bid, symbol_id="t:f", statements=stmts) for bid, stmts in blocks.items()}
    for src, dst in edges:
        built[src].successors.append(CfgEdge(target_block=dst, edge_type="always"))
    ids = list(blocks)
    return FunctionCfg(symbol_id="t:f", entry_block=ids[0], exit_block=ids[-1], blocks=built)


def _edges(result) -> set[tuple[str, int, int]]:
    return {(e.variable, e.def_line, e.use_line) for e in result.ddg_edges}


def test_an_entry_definition_reaches_a_use() -> None:
    cfg = _cfg({"b0": [_stmt(2, uses=("req",)), _stmt(3, uses=("req", "res"))]})
    result = solve_reaching_defs(cfg, entry_defs=[("req", 1), ("res", 1)])
    assert _edges(result) == {("req", 1, 2), ("req", 1, 3), ("res", 1, 3)}
    assert result.definition_count == 2 and not result.bailed_out


def test_a_statement_definition_kills_the_entry_definition() -> None:
    cfg = _cfg({"b0": [_stmt(2, defines=("a",)), _stmt(3, uses=("a",))]})
    assert _edges(solve_reaching_defs(cfg, entry_defs=[("a", 1)])) == {("a", 2, 3)}


def test_a_redefinition_in_the_entry_block_replaces_it_for_the_successors() -> None:
    """The gen set of the entry block must carry the statement's definition,
    not the entry one, once the statement redefines the name."""
    cfg = _cfg(
        {"b0": [_stmt(2, defines=("a",))], "b1": [_stmt(3, uses=("a",))]},
        edges=(("b0", "b1"),),
    )
    assert _edges(solve_reaching_defs(cfg, entry_defs=[("a", 1)])) == {("a", 2, 3)}


def test_the_entry_definition_survives_the_path_that_does_not_redefine() -> None:
    cfg = _cfg(
        {
            "b0": [_stmt(2, uses=("x",))],
            "b1": [_stmt(3, defines=("a",))],
            "b2": [],
            "b3": [_stmt(5, uses=("a",))],
        },
        edges=(("b0", "b1"), ("b0", "b2"), ("b1", "b3"), ("b2", "b3")),
    )
    assert _edges(solve_reaching_defs(cfg, entry_defs=[("a", 1)])) == {("a", 1, 5), ("a", 3, 5)}


def test_an_unused_entry_definition_emits_no_edge_but_is_counted() -> None:
    cfg = _cfg({"b0": [_stmt(2, uses=("other",))]})
    result = solve_reaching_defs(cfg, entry_defs=[("res", 1)])
    assert _edges(result) == set() and result.definition_count == 1


def test_without_entry_definitions_nothing_changes() -> None:
    cfg = _cfg({"b0": [_stmt(2, defines=("a",)), _stmt(3, uses=("a", "req"))]})
    before = _edges(solve_reaching_defs(cfg))
    assert before == {("a", 2, 3)}
    assert _edges(solve_reaching_defs(cfg, entry_defs=())) == before
    assert solve_reaching_defs(cfg).definition_count == 1
