# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/compact.py
"""A selection with budget left takes symbols no entry point reaches.

``select_by_connectivity`` grows a frontier out from its seeds and used to
stop when the frontier was empty. A call graph is rarely one component:
Express assigns most of its API as ``res.send = function ...``, which no
edge ties to an entry point. Its 4k, 16k and 64k survey tiers each held the
same ten nodes at 3.7k tokens, omitting forty first-party functions, among
them the most central ones.

When the seeds' component is exhausted, the best symbol not yet reached now
starts the next one. The order reads centrality and id, never the budget, so
a selection at a smaller budget is still a prefix of the one at a larger.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.compact import select_by_connectivity
from hypergumbo_core.ir import Edge, Span, Symbol


def _symbol(name: str) -> Symbol:
    return Symbol(id=f"python:lib/{name}.py:1-9:{name}:function", name=name, kind="function",
                  language="python", path=f"lib/{name}.py", span=Span(1, 9, 0, 0))


def _edge(src: Symbol, dst: Symbol) -> Edge:
    return Edge(id=f"e:{src.name}>{dst.name}", src=src.id, dst=dst.id, edge_type="calls",
                line=1, confidence=0.9, origin="test", origin_run_id="test")


def _graph() -> tuple[list[Symbol], list[Edge], dict[str, float], Symbol]:
    """Three components: the seed's (3 symbols), one it does not reach (4),
    and two isolated symbols. The unreached component holds the most
    central symbol of all."""
    names = ["seed", "a1", "a2", "hub", "b1", "b2", "b3", "lone_high", "lone_low"]
    s = {name: _symbol(name) for name in names}
    edges = [_edge(s["seed"], s["a1"]), _edge(s["a1"], s["a2"]),
             _edge(s["b1"], s["hub"]), _edge(s["b2"], s["hub"]), _edge(s["b3"], s["hub"])]
    centrality = {s[n].id: v for n, v in {
        "seed": 0.2, "a1": 0.3, "a2": 0.1, "hub": 0.9, "b1": 0.05, "b2": 0.04,
        "b3": 0.03, "lone_high": 0.5, "lone_low": 0.0}.items()}
    return list(s.values()), edges, centrality, s["seed"]


def _selected(interleave: bool, budget: int) -> list[str]:
    symbols, edges, centrality, seed = _graph()
    result = select_by_connectivity(
        symbols, edges, [seed.id], budget, centrality=centrality, interleave=interleave)
    return [s.name for s in result.included.symbols]


@pytest.mark.parametrize("interleave", [False, True])
def test_a_selection_with_room_takes_every_component(interleave: bool) -> None:
    """Reach: the seed's own component is three of the nine symbols."""
    everything = _selected(interleave, 100)
    assert sorted(everything) == sorted(
        ["seed", "a1", "a2", "hub", "b1", "b2", "b3", "lone_high", "lone_low"])
    # The seed's component first, then the best unreached symbol and what
    # it connects to, before a lesser isolated one.
    assert set(everything[:3]) == {"seed", "a1", "a2"}
    assert everything[3] == "hub"
    assert everything.index("lone_high") < everything.index("lone_low")
    assert everything[-1] == "lone_low"


@pytest.mark.parametrize("interleave", [False, True])
def test_a_smaller_selection_is_a_prefix_of_a_larger_one(interleave: bool) -> None:
    """Containment across budgets survives the restarts."""
    full = _selected(interleave, 100)
    # Without interleaving the budget counts additions beyond the seed.
    offset = 0 if interleave else 1
    for budget in range(1, len(full) + 2):
        assert _selected(interleave, budget) == full[:budget + offset], budget


@pytest.mark.parametrize("interleave", [False, True])
def test_a_connected_graph_is_selected_as_before(interleave: bool) -> None:
    symbols, edges, centrality, seed = _graph()
    connected = [s for s in symbols if s.name in ("seed", "a1", "a2")]
    result = select_by_connectivity(
        connected, edges[:2], [seed.id], 100, centrality=centrality, interleave=interleave)
    assert [s.name for s in result.included.symbols] == ["seed", "a1", "a2"]
    assert result.omitted.count == 0
