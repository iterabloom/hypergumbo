# SPDX-License-Identifier: AGPL-3.0-or-later
"""Erlang behaviours as a type relationship (WI-hidit, sibling of WI-vitas).

A behaviour is Erlang's interface mechanism: ``-callback area() -> integer().``
declares a function an implementing module must export, and
``-behaviour(shape).`` declares that the module implements ``shape``. Before
this item only the five OTP behaviours in ``OTP_BEHAVIOUR_CALLBACKS`` got
anything (module -> callback ``dispatches_to``), and a custom behaviour got
one ``imports`` edge:

1. ``-callback`` produced NO symbol, so the required function did not exist
   and there was nothing to dispatch FROM;
2. ``-behaviour(shape)`` produced no ``implements`` edge and no callback ->
   implementation ``dispatches_to``.

What these tests pin -- the shape Elixir has (``test_elixir_behaviours.py``):

- a callback symbol named ``name/arity`` (kind ``function``,
  ``modifiers=["abstract"]``) for both attribute spellings, multi-clause specs
  included, marked ``optional`` when ``-optional_callbacks`` lists it;
- a callback symbol is never a CALL target and never a call SOURCE;
- ``implements`` from the module to its behaviour: the in-repo module, else
  an unresolved ``erlang:external:0-0:<behaviour>:unresolved`` target -- OTP's
  ``gen_server`` included, whose callback rooting is kept;
- ``dispatches_to`` from each callback to the implementing function of the
  SAME name/arity (an Erlang function is its name and arity, so ``area/1``
  does not implement ``area/0``).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.erlang import analyze_erlang

SHAPE = """-module(shape).
-callback area() -> integer().
-callback(scale(S :: term(), F :: number()) -> term()).
-callback init(term()) -> ok; (atom()) -> error.
-optional_callbacks([scale/2]).
"""

SQUARE = """-module(square).
-behaviour(shape).
-export([area/0, area/1, scale/2]).
area() -> 1.
area(X) -> X.
scale(S, _F) -> S.
"""


def _analyze(tmp_path: Path, files: dict[str, str]):
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    return analyze_erlang(tmp_path)


def _one(result, name: str, *, abstract: bool | None = None):
    found = [
        s for s in result.symbols
        if s.name == name
        and (abstract is None or ("abstract" in s.modifiers) == abstract)
    ]
    assert len(found) == 1, f"expected one {name!r}, got {found}"
    return found[0]


def _edges(result, edge_type: str):
    return [e for e in result.edges if e.edge_type == edge_type]


class TestCallbackSymbols:
    """``-callback`` mints a ``name/arity`` symbol on its module."""

    def test_filed_repro_callback_is_a_symbol(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE})
        area = _one(result, "area/0")
        assert area.kind == "function"
        assert area.modifiers == ["abstract"]
        assert area.path == "shape.erl"
        assert area.span.start_line == 2
        assert area.signature == "() -> integer()"
        assert area.meta == {"arity": 0, "base_name": "area"}

    def test_parenthesised_spelling_and_arity(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE})
        scale = _one(result, "scale/2")
        assert scale.modifiers == ["abstract"]
        assert scale.signature == "(S :: term(), F :: number()) -> term()"

    def test_optional_callback_is_marked(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE})
        assert _one(result, "scale/2").meta["optional"] is True
        assert "optional" not in _one(result, "area/0").meta

    def test_multi_clause_spec_is_one_symbol(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE})
        init = _one(result, "init/1")
        assert init.signature == "(term()) -> ok; (atom()) -> error"

    def test_optional_callbacks_alone_mints_nothing(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"m.erl": "-module(m).\n-optional_callbacks([f/1]).\n"})
        assert {s.kind for s in result.symbols} == {"file", "module"}

    def test_callback_is_never_a_call_target(self, tmp_path: Path) -> None:
        """A bare ``area()`` in the behaviour module calls nothing that exists."""
        result = _analyze(tmp_path, {"shape.erl": SHAPE + "go() -> area(), shape:area().\n"})
        callback_ids = {s.id for s in result.symbols if "abstract" in s.modifiers}
        assert callback_ids
        assert not [e for e in result.edges if e.dst in callback_ids
                    and e.edge_type == "calls"]

    def test_callback_does_not_shadow_a_same_named_function(self, tmp_path: Path) -> None:
        """A module may declare a callback AND define the same name/arity."""
        result = _analyze(tmp_path, {"shape.erl": SHAPE + "area() -> 2.\ngo() -> area().\n"})
        real = _one(result, "area/0", abstract=False)
        calls = [e for e in _edges(result, "calls") if e.line == 7]
        assert [e.dst for e in calls] == [real.id]


class TestImplementsEdge:
    """``-behaviour(X)`` is a type relationship, module -> behaviour."""

    def test_filed_repro_in_repo_behaviour(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE, "square.erl": SQUARE})
        shape = _one(result, "shape")
        square = _one(result, "square")
        implements = _edges(result, "implements")
        assert [(e.src, e.dst) for e in implements] == [(square.id, shape.id)]
        assert implements[0].evidence_type == "ast_implements"
        assert implements[0].line == 2

    def test_behavior_spelling(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {
            "shape.erl": SHAPE,
            "square.erl": SQUARE.replace("-behaviour(", "-behavior("),
        })
        assert len(_edges(result, "implements")) == 1

    def test_external_behaviour_is_unresolved(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"square.erl": SQUARE})
        implements = _edges(result, "implements")
        assert [e.dst for e in implements] == ["erlang:external:0-0:shape:unresolved"]
        assert implements[0].is_resolved is False

    def test_otp_behaviour_gets_implements_and_keeps_rooting(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"srv.erl": """-module(srv).
-behaviour(gen_server).
-export([init/1]).
init(_) -> {ok, #{}}.
"""})
        assert [e.dst for e in _edges(result, "implements")] == [
            "erlang:external:0-0:gen_server:unresolved",
        ]
        module = _one(result, "srv")
        rooted = [e for e in _edges(result, "dispatches_to") if e.src == module.id]
        assert [e.dst for e in rooted] == [_one(result, "init/1").id]

    def test_module_naming_itself_is_not_its_own_behaviour(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"m.erl": "-module(m).\n-behaviour(m).\n"})
        assert _edges(result, "implements") == []

    def test_behaviour_without_module_attribute_emits_nothing(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"inc.hrl": "-behaviour(shape).\n"})
        assert _edges(result, "implements") == []

    def test_repeated_behaviour_is_one_edge(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {
            "shape.erl": SHAPE,
            "square.erl": SQUARE.replace(
                "-behaviour(shape).", "-behaviour(shape).\n-behaviour(shape)."),
        })
        assert len(_edges(result, "implements")) == 1
        assert len([e for e in _edges(result, "dispatches_to")
                    if e.evidence_type == "behaviour_callback"]) == 2


class TestCallbackDispatch:
    """Each callback ``dispatches_to`` the implementation of the same name/arity."""

    def test_filed_repro_area_dispatches_to_implementation(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE, "square.erl": SQUARE})
        by_id = {s.id: s for s in result.symbols}
        pairs = sorted(
            (by_id[e.src].name, by_id[e.dst].name, by_id[e.dst].path)
            for e in _edges(result, "dispatches_to")
            if e.evidence_type == "behaviour_callback"
        )
        # area/1 is a different function and implements nothing; init/1 is
        # not defined by square.
        assert pairs == [
            ("area/0", "area/0", "square.erl"),
            ("scale/2", "scale/2", "square.erl"),
        ]
        for e in _edges(result, "dispatches_to"):
            assert "abstract" in by_id[e.src].modifiers
            assert e.meta == {"mechanism": "callback"}
            assert e.line == 2

    def test_ifdef_alternatives_both_implement(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, {"shape.erl": SHAPE, "sq.erl": """-module(sq).
-behaviour(shape).
-ifdef(FAST).
area() -> 1.
helper() -> ok.
-else.
area() -> 2.
-endif.
"""})
        callback = _one(result, "area/0", abstract=True)
        dsts = {e.dst for e in _edges(result, "dispatches_to") if e.src == callback.id}
        assert len(dsts) == 2


class TestCallbackStableId:
    """ADR-0035: a callback's stable_id is non-null and unique in its file."""

    def test_callback_and_same_named_function_both_keep_an_identity(
        self, tmp_path: Path,
    ) -> None:
        """The floor key would collide (same kind/path/name) and null both."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids

        result = _analyze(tmp_path, {"shape.erl": SHAPE + "area() -> 2.\n"})
        symbols = list(result.symbols)
        populate_kind_stable_ids(symbols)
        callback = _one(result, "area/0", abstract=True)
        real = _one(result, "area/0", abstract=False)
        assert callback.stable_id is not None
        assert real.stable_id is not None
        assert callback.stable_id != real.stable_id
        ids = [s.stable_id for s in symbols if s.path == "shape.erl"]
        assert len(ids) == len(set(ids))
