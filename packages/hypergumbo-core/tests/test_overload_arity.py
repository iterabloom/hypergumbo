# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared overload-by-arity chooser (WI-hilum, WI-rodiz, WI-bivab).

Language-free: symbols are built by hand with ``meta["parameters"]`` in the
shape :func:`parameter_entry` writes. What each analyzer does with a choice is
tested beside that analyzer.
"""
from __future__ import annotations

import math

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.overload_arity import (
    ArityBounds,
    arity_bounds,
    bounds_of_entries,
    choose_overload,
    narrow_by_arity,
    parameter_entry,
)


def _sym(name: str, line: int, params, path: str = "a.x") -> Symbol:
    meta = None if params is None else {"parameters": params}
    return Symbol(
        id=f"x:{path}:{line}-{line}:{name}:function", name=name, kind="function",
        language="x", path=path, span=Span(line, line, 0, 1), meta=meta,
    )


def _p(n: int, *, defaults: int = 0, variadic: bool = False) -> list:
    entries = [
        parameter_entry(f"p{i}", default=i >= n - defaults) for i in range(n)
    ]
    if variadic:
        entries.append(parameter_entry("rest", variadic=True))
    return entries


def test_parameter_entry_writes_variadic_only_when_true() -> None:
    assert parameter_entry("a", "int") == {"name": "a", "type": "int", "default": False}
    assert parameter_entry("r", variadic=True)["variadic"] is True


def test_bounds_read_defaults_and_variadics() -> None:
    assert bounds_of_entries(_p(3, defaults=1)) == ArityBounds(2, 3)
    assert bounds_of_entries(_p(1, variadic=True)) == ArityBounds(1, None)
    assert bounds_of_entries([]) == ArityBounds(0, 0)
    # py.py's spelling of a variadic: the ``*`` in the name.
    py = [{"name": "a", "type": None, "default": False},
          {"name": "*args", "type": None, "default": False}]
    assert bounds_of_entries(py) == ArityBounds(1, None)
    assert bounds_of_entries(["not-an-entry"]) is None


def test_admits_respects_both_ends() -> None:
    b = ArityBounds(1, 2)
    assert [b.admits(n) for n in range(4)] == [False, True, True, False]
    assert ArityBounds(1, None).admits(99)


def test_absent_parameters_is_unknown_not_zero() -> None:
    """ABSENT is not EMPTY: no key is unknown arity; ``[]`` takes nothing."""
    assert arity_bounds(_sym("f", 1, None)) is None
    assert arity_bounds(_sym("f", 1, [])) == ArityBounds(0, 0)
    weird = _sym("f", 1, [])
    weird.meta = {"parameters": "(int a)"}
    assert arity_bounds(weird) is None


def test_narrow_keeps_unknowns_and_everything_for_an_unknown_count() -> None:
    zero, one, unknown = _sym("f", 1, []), _sym("f", 2, _p(1)), _sym("f", 3, None)
    assert narrow_by_arity([zero, one, unknown], 1) == [one, unknown]
    assert narrow_by_arity([zero, one], None) == [zero, one]


def test_one_admitting_candidate_is_certain() -> None:
    zero, one = _sym("f", 1, []), _sym("f", 2, _p(1))
    choice = choose_overload([[zero, one]], 0)
    assert choice.symbol is zero and choice.confidence == 1.0
    assert not choice.ambiguous and choice.admitted == (zero,)


def test_several_admitting_is_ambiguous_and_prefers_the_callers_pick() -> None:
    a, b, c = _sym("f", 1, _p(1)), _sym("f", 2, _p(1)), _sym("f", 3, [])
    choice = choose_overload([[c, b, a]], 1)
    assert choice.ambiguous and choice.symbol is a  # first by position
    assert math.isclose(choice.confidence, 1 / math.sqrt(2))
    assert choice.admitted == (a, b)
    assert choose_overload([[a, b]], 1, preferred=b).symbol is b
    # A preferred candidate arity excludes is not kept.
    assert choose_overload([[a, b, c]], 1, preferred=c).symbol is a


def test_a_nearer_tier_wins_only_when_it_can_take_the_call() -> None:
    near = _sym("f", 1, [], path="near.x")
    far = _sym("f", 1, _p(2), path="far.x")
    assert choose_overload([[near], [near, far]], 0).symbol is near
    assert choose_overload([[near], [near, far]], 2).symbol is far
    assert choose_overload([[], [far]], 2).symbol is far


def test_nothing_admits_is_no_choice() -> None:
    choice = choose_overload([[_sym("f", 1, _p(2))]], 5)
    assert choice.symbol is None and choice.admitted == ()


def test_position_ordering_survives_a_spanless_candidate() -> None:
    spanless = _sym("f", 1, _p(1), path="b.x")
    spanless.span = None
    other = _sym("f", 4, _p(1), path="a.x")
    assert choose_overload([[spanless, other]], 1).symbol is other
