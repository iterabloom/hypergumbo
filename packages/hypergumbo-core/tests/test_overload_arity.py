# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared overload-by-arity chooser (WI-hilum, WI-rodiz, WI-bivab) and the
declared-arity identity helper built on the same fact (WI-tusad).

Language-free: symbols are built by hand with ``meta["parameters"]`` in the
shape :func:`parameter_entry` writes. What each analyzer does with a choice is
tested beside that analyzer.
"""
from __future__ import annotations

import math

from hypergumbo_core.analyze.base import (
    ArityFlags, assemble_stable_id, make_file_stable_id, make_typed_stable_id,
)
from hypergumbo_core.behaviours import CALLBACK_MODIFIER, callback_stable_id
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.overload_arity import (
    ARITY_KEY,
    PARAMETERS_KEY,
    ArityBounds,
    DeclaredArity,
    arity_bounds,
    bounds_of_entries,
    choose_overload,
    declared_arity,
    declared_arity_of_entries,
    declared_arity_stable_id,
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


# -- declared arity as identity input (WI-tusad) ------------------------------


def test_declared_arity_reads_parameters_before_a_bare_count() -> None:
    meta = {PARAMETERS_KEY: _p(2, defaults=1), ARITY_KEY: 5}
    assert declared_arity(meta) == DeclaredArity(2, "True,False,False")


def test_declared_arity_falls_back_to_the_bare_count_with_no_flags() -> None:
    assert declared_arity({ARITY_KEY: 3}) == DeclaredArity(3, "")
    assert declared_arity({ARITY_KEY: 0}) == DeclaredArity(0, "")


def test_declared_arity_absent_or_malformed_is_unknown() -> None:
    assert declared_arity(None) is None
    assert declared_arity({}) is None
    assert declared_arity({ARITY_KEY: True}) is None
    assert declared_arity({ARITY_KEY: -1}) is None
    assert declared_arity({ARITY_KEY: "2"}) is None
    assert declared_arity({PARAMETERS_KEY: ["x"]}) is None


def test_empty_parameter_list_is_zero_not_unknown() -> None:
    assert declared_arity({PARAMETERS_KEY: []}) == DeclaredArity(0, "False,False,False")


def test_entries_flags_match_arity_flags_for_python_spelling() -> None:
    entries = [
        parameter_entry("a"),
        parameter_entry("b", default=True),
        {"name": "*args", "type": None, "default": False},
        {"name": "**kw", "type": None, "default": False},
    ]
    got = declared_arity_of_entries(entries)
    assert got == DeclaredArity(
        4, ArityFlags(4, True, True, True).as_flags_str(),
    )


def test_a_declared_type_spells_every_parameter() -> None:
    entries = [
        parameter_entry("x", "Int"),
        parameter_entry("y", default=True),
        parameter_entry("rest", "String", variadic=True),
    ]
    got = declared_arity_of_entries(entries)
    assert got is not None
    assert got.types == ("Int", "_=", "*String")
    assert got.type_signature == "(Int,_=,*String)"
    assert DeclaredArity(1, "").type_signature == ""


def test_untyped_id_equals_the_compute_stable_id_formula() -> None:
    """Same parameters, same value as the tree-sitter untyped tier: no scheme bump."""
    arity = declared_arity({PARAMETERS_KEY: _p(2)})
    got = declared_arity_stable_id(
        kind="function", lang="elixir", path="lib/demo.ex", name="Demo.greet",
        qualified_name="Demo.greet", arity=arity,
    )
    anchor = make_file_stable_id("elixir", "lib/demo.ex")
    assert got == assemble_stable_id(
        "function", 2, ArityFlags(2, False, False, False).as_flags_str(), "",
        anchor, "Demo.greet", "Demo.greet",
    )


def test_unknown_arity_takes_the_zero_parameter_formula() -> None:
    got = declared_arity_stable_id(
        kind="function", lang="x", path="a.x", name="f", qualified_name="f",
        arity=None,
    )
    assert got == assemble_stable_id(
        "function", 0, "False,False,False", "",
        make_file_stable_id("x", "a.x"), "f", "f",
    )


def test_typed_overloads_of_one_arity_hash_apart_on_the_typed_tier() -> None:
    def sid(type_: str) -> str:
        return declared_arity_stable_id(
            kind="function", lang="julia", path="src/f.jl", name="f",
            qualified_name="f",
            arity=declared_arity({PARAMETERS_KEY: [parameter_entry("x", type_)]}),
        )

    assert sid("Int") != sid("String")
    assert sid("Int") == make_typed_stable_id(
        "function", "(Int)", "", make_file_stable_id("julia", "src/f.jl"), "",
        name="f", qualified_name="f",
    )


def test_arities_and_scopes_hash_apart() -> None:
    def sid(n: int, containing: str = "") -> str:
        return declared_arity_stable_id(
            kind="function", lang="elixir", path="lib/a.ex", name="A.f",
            qualified_name="A.f", arity=DeclaredArity(n, ""),
            containing_stable_id=containing,
        )

    assert len({sid(0), sid(1), sid(2), sid(1, "sha256:0000000000000000")}) == 4


def test_callback_stable_id_is_byte_identical_through_the_shared_helper() -> None:
    """WI-hidit's values, pinned before the helper took them over: a changed
    producer value would be a scheme bump (ADR-0035 §6)."""
    assert callback_stable_id("elixir", "lib/shape.ex", "Shape.area", 1) == (
        "sha256:35261acfab20d1d1"
    )
    assert callback_stable_id("erlang", "src/shape.erl", "area", 0, "macro") == (
        "sha256:e47ba7cc993e0988"
    )
    assert callback_stable_id("elixir", "lib/shape.ex", "Shape.area", 1) == (
        assemble_stable_id(
            "function", 1, "", CALLBACK_MODIFIER,
            make_file_stable_id("elixir", "lib/shape.ex"), "Shape.area",
            "Shape.area",
        )
    )
