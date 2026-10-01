# SPDX-License-Identifier: AGPL-3.0-or-later
"""The declared ``call_construct`` vocabulary and its crosswalk to row kinds (WI-dapap).

The crosswalk is read by ``verify_claims.method_starved_modules``; the gate's
behaviour on real analyzer output is pinned in
``test_blind_language_method_starvation.py``. This module pins the table itself:
every row kind is a legal ADR-0059 kind, the two values that are not call
syntax are declared, and an undeclared value is refused where it can be.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.call_constructs import (
    CALL_CONSTRUCT_VALUES,
    all_call_construct_values,
    reached_row_kind,
    require_declared,
)
from hypergumbo_core.io_primitive_kinds import (
    KIND_FUNCTION,
    KIND_METHOD,
    all_io_primitive_kind_names,
    read_not_called,
)


def test_names_are_unique() -> None:
    names = [v.name for v in CALL_CONSTRUCT_VALUES]
    assert len(names) == len(set(names))


def test_every_row_kind_is_a_called_io_primitive_kind() -> None:
    """A construct reaches a CALLED row or none; ``attribute`` is read, not
    called, and no construct value describes a read."""
    for value in CALL_CONSTRUCT_VALUES:
        if value.row_kind is None:
            continue
        assert value.row_kind in all_io_primitive_kind_names(), value
        assert not read_not_called(value.row_kind), value


@pytest.mark.parametrize("construct, kind", [
    ("method", KIND_METHOD),
    ("function", KIND_FUNCTION),
    # Emitted only on a METHOD-kind row reached through a typed instance.
    ("assignment", KIND_METHOD),
    ("protocol", KIND_METHOD),
    ("pipe", KIND_FUNCTION),
    ("remote", KIND_FUNCTION),
])
def test_the_crosswalk(construct: str, kind: str) -> None:
    assert reached_row_kind(construct) == kind


@pytest.mark.parametrize("construct", [None, "constructor", "macro_body", "bogus"])
def test_no_row_kind_without_evidence_of_one(construct) -> None:
    """Absent, a construction, an ejected value and an unknown one all map to
    None: none says which kind of row the call could match."""
    assert reached_row_kind(construct) is None


def test_the_two_non_call_constructs_are_declared() -> None:
    """The amended axiom (a construct that PRODUCED a call-family edge) is what
    admits them; under "call construct" both would be off-axis."""
    assert {"constructor", "assignment"} <= all_call_construct_values()


def test_require_declared_passes_a_declared_value_through() -> None:
    assert require_declared("assignment") == "assignment"


def test_require_declared_refuses_an_undeclared_value() -> None:
    with pytest.raises(ValueError, match="not declared"):
        require_declared("macro_body")


def test_make_unresolved_edge_refuses_an_undeclared_construct() -> None:
    """The run-time half of the allowlist: a value a helper computes reaches
    ``Edge.meta`` only through here, where the static scan cannot see it."""
    with pytest.raises(ValueError, match="not declared"):
        make_unresolved_edge(
            "javascript", "javascript:a.js:1-2:f:function", "onmessage", 1,
            "p", "r", module_hint="WebSocket", call_construct="handler",
        )


def test_make_unresolved_edge_stamps_a_declared_construct() -> None:
    edge = make_unresolved_edge(
        "javascript", "javascript:a.js:1-2:f:function", "onmessage", 1,
        "p", "r", module_hint="WebSocket", call_construct="assignment",
    )
    assert edge.meta["call_construct"] == "assignment"
