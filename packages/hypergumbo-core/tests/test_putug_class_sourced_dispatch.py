# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-putug: the taint walk must advance only along value-carrying edges.

A ``dispatches_to`` edge is call-shaped when a dispatcher hands data to a
handler it will actually invoke (INV-zuhig: argparse / cobra / decorator
dispatch, whose source is the enclosing FUNCTION). It is NOT call-shaped when
its source is a TYPE — ``django_orm_dispatch`` and its four siblings emit
``class -> framework-called override``, which says the framework may call these
methods on instances of this class, not that a value passes between them.

A type node holds no value; an instance does. Measured on pretix (measurement
0016): 33 of 33 class-node hops the walk traversed sat on such a pair, while
the 16,010 ``contains``-only pairs were never crossed, and 20 of the 47
situations that round added were this shape.

The discriminator is the SOURCE node's kind, not the emitting linker: two
dispatch linkers that look class-shaped are not — ``di_resolution`` emits from
an interface METHOD and ``type_hierarchy`` from a parent METHOD, both genuine
virtual dispatch that does carry a value.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.symbol_kinds import type_like_kind_names
from hypergumbo_core.taint import _build_adjacency, _is_taint_call_edge


def _edge(etype: str, src: str, dst: str) -> dict:
    return {"type": etype, "src": src, "dst": dst}


CLASS_SRC = "python:app/models.py:10-40:Invoice:class"
METHOD_DST = "python:app/models.py:20-25:Invoice.save:method"
FUNC_SRC = "python:app/cli.py:5-9:build_parser:function"
HANDLER_DST = "python:app/cli.py:30-35:cmd_sketch:function"


class TestATypeNodeHoldsNoValue:
    def test_class_sourced_dispatch_is_not_a_taint_call_edge(self) -> None:
        assert not _is_taint_call_edge(
            _edge("dispatches_to", CLASS_SRC, METHOD_DST)
        )

    def test_class_sourced_dispatch_creates_no_adjacency_hop(self) -> None:
        fwd, rev = _build_adjacency(
            [_edge("dispatches_to", CLASS_SRC, METHOD_DST)]
        )
        assert fwd == {}
        assert rev == {}

    @pytest.mark.parametrize("kind", sorted(type_like_kind_names()))
    def test_every_registry_type_kind_is_refused(self, kind: str) -> None:
        """Derived from the registry, never hand-listed.

        ``type_like_kind_names``'s own docstring records audit 0018 finding 26
        hand-written copies of this vocabulary, five of which omitted
        ``protocol``. A restated set here would be the 27th.
        """
        src = f"python:app/models.py:10-40:Thing:{kind}"
        assert not _is_taint_call_edge(_edge("dispatches_to", src, METHOD_DST))


class TestValueCarryingDispatchIsUntouched:
    """INV-zuhig is satisfied on function-sourced dispatch; keep it that way."""

    def test_function_sourced_dispatch_still_carries_taint(self) -> None:
        assert _is_taint_call_edge(
            _edge("dispatches_to", FUNC_SRC, HANDLER_DST)
        )

    def test_method_sourced_dispatch_still_carries_taint(self) -> None:
        """di_resolution (interface method) and type_hierarchy (parent method)."""
        src = "java:app/Svc.java:10-14:Svc.handle:method"
        assert _is_taint_call_edge(_edge("dispatches_to", src, METHOD_DST))

    def test_a_plain_call_from_a_type_node_is_not_collateral_damage(self) -> None:
        """Only the dispatch family is narrowed — `calls` keeps its meaning."""
        assert _is_taint_call_edge(_edge("calls", CLASS_SRC, METHOD_DST))

    def test_instantiates_from_a_type_node_still_carries(self) -> None:
        assert _is_taint_call_edge(_edge("instantiates", CLASS_SRC, METHOD_DST))


class TestTheGateFailsOpen:
    """An unparseable id must not silently delete an edge (WI-jizil shape)."""

    @pytest.mark.parametrize("src", [
        "",
        "notanid",
        "python:app.py:1-2:name",          # four slots, no kind
        "external",
    ])
    def test_an_id_without_a_kind_slot_is_admitted(self, src: str) -> None:
        assert _is_taint_call_edge(_edge("dispatches_to", src, METHOD_DST))

    def test_a_colon_bearing_path_still_parses_from_the_right(self) -> None:
        """ADR-0036: the path slot is colon-tolerant; kind is the LAST slot."""
        src = "rust:src/std::cmp/mod.rs:1-9:Ordering:enum"
        assert not _is_taint_call_edge(_edge("dispatches_to", src, METHOD_DST))

    def test_a_five_slot_id_whose_span_anchor_fails_is_admitted(self) -> None:
        r"""The anchor is the SPAN, not the slot count.

        A colon-bearing path makes the count vary (Rust ``std::cmp`` ids), so a
        string with five-or-more colon parts whose third-from-last is not
        ``\d+-\d+`` is not an ADR-0036 id at all and its last part is not a
        kind. Reading it as one would refuse edges on a coincidence.
        """
        src = "a:b:c:d:class"
        assert _is_taint_call_edge(_edge("dispatches_to", src, METHOD_DST))
