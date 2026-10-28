# SPDX-License-Identifier: AGPL-3.0-or-later
"""One container-kind set, derived from the registry (WI-totam).

slice expands an entry of a container kind to its members (through
``contains`` edges), and the containment linker decides which kinds own
members at all. Each kept a hand-written copy, and slice's lacked ``object``:
``slice --entry Registry --reverse`` on a Kotlin ``object`` returned only
``{Registry:object}`` while the same code as a ``class`` returned its members
and their callers. Measured 2026-10-28 on 30 recent surveys, ``object`` was the
source of 40,820 ``contains`` edges. Both now read
:func:`symbol_kinds.container_kind_names`: the nominal type kinds plus
:data:`SCOPE_CONTAINER_KINDS`.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.containment import CONTAINER_KINDS
from hypergumbo_core.slice import SliceQuery, slice_graph
from hypergumbo_core.symbol_kinds import (
    all_symbol_kind_names,
    container_kind_names,
    type_like_kind_names,
)

#: Every kind that was the source of a ``contains`` edge on the 2026-10-28
#: 30-survey measurement, except ``external_symbol`` (a stand-in for code
#: outside the project, deliberately not expanded).
_MEASURED_CONTAINERS = frozenset({
    "class", "file", "object", "trait", "struct", "interface", "enum", "type",
    "instance", "contract", "protocol", "module",
})


def test_the_linker_and_slice_read_one_set() -> None:
    assert CONTAINER_KINDS == container_kind_names()


def test_every_nominal_type_is_a_container() -> None:
    assert type_like_kind_names() <= container_kind_names()
    assert {"actor", "primitive", "object", "record"} <= container_kind_names()  # reach


def test_every_measured_container_kind_is_in_the_set() -> None:
    assert _MEASURED_CONTAINERS <= container_kind_names()


def test_every_member_is_a_registered_kind() -> None:
    assert container_kind_names() <= all_symbol_kind_names()


def _sym(name: str, kind: str, line: int) -> Symbol:
    return Symbol(
        id=f"kotlin:app.kt:{line}-{line + 1}:{name}:{kind}", name=name, kind=kind,
        language="kotlin", path="app.kt", span=Span(line, line + 1, 0, 0),
        origin="kotlin", origin_run_id="r",
    )


def _edge(src: Symbol, dst: Symbol, edge_type: str) -> Edge:
    return Edge.create(src=src.id, dst=dst.id, edge_type=edge_type, line=src.span.start_line,
                       origin="kotlin", origin_run_id="r")


@pytest.mark.parametrize("kind", ["object", "class", "actor"])
def test_a_reverse_slice_from_a_container_reaches_its_members_callers(kind: str) -> None:
    holder = _sym("Registry", kind, 1)
    member = _sym("Registry.get", "method", 2)
    caller = _sym("main", "function", 10)
    edges = [_edge(holder, member, "contains"), _edge(caller, member, "calls")]
    result = slice_graph([holder, member, caller], edges,
                         SliceQuery(entrypoint="Registry", reverse=True, max_hops=3))
    assert holder.id in result.node_ids  # reach
    assert {member.id, caller.id} <= result.node_ids
