# SPDX-License-Identifier: AGPL-3.0-or-later
"""BEAM behaviour bridge: Elixir ``@behaviour :mod`` -> in-repo Erlang behaviour.

WI-hidit. The Elixir analyzer can only point ``@behaviour :ejabberd_auth`` at
the unresolved target ``elixir:external:0-0:ejabberd_auth:unresolved``; when
the Erlang module is in the run, this linker states the relationship and links
each Erlang ``-callback`` to the Elixir function implementing it. Symbols are
built by hand: core tests do not import the language packages.

Also pins ``hypergumbo_core.behaviours``, the two-edge shape the Erlang
and Elixir analyzers and this linker share.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hypergumbo_core.behaviours import (
    behaviour_implements_edge,
    callback_dispatch_edge,
    callback_stable_id,
)
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.beam_behaviour import beam_behaviour_linker
from hypergumbo_core.linkers.registry import LinkerContext, get_all_linkers


def _sym(
    lang: str, path: str, line: int, name: str, kind: str, **extra: Any,
) -> Symbol:
    return Symbol(
        id=f"{lang}:{path}:{line}-{line}:{name}:{kind}",
        name=name, kind=kind, language=lang, path=path,
        span=Span(start_line=line, end_line=line, start_col=0, end_col=1),
        origin=f"{lang}-v1", **extra,
    )


def _params(n: int, default: int = 0) -> dict[str, Any]:
    return {"parameters": [
        {"name": f"p{i}", "type": None, "default": i >= n - default}
        for i in range(n)
    ]}


ERL_MOD = _sym("erlang", "src/ejabberd_auth.erl", 1, "ejabberd_auth", "module")
ERL_START = _sym(
    "erlang", "src/ejabberd_auth.erl", 3, "start/1", "function",
    modifiers=["abstract"], meta={"arity": 1, "base_name": "start"},
)
ERL_STORE = _sym(
    "erlang", "src/ejabberd_auth.erl", 4, "store_type/1", "function",
    modifiers=["abstract"], meta={"arity": 1, "base_name": "store_type"},
)
# A real function in the behaviour module is not a callback.
ERL_HELPER = _sym(
    "erlang", "src/ejabberd_auth.erl", 9, "start/1", "function",
    meta={"arity": 1, "base_name": "start"},
)
EX_MOD = _sym("elixir", "lib/example.ex", 1, "Ejabberd.Auth.Example", "module")
EX_START = _sym(
    "elixir", "lib/example.ex", 5, "Ejabberd.Auth.Example.start", "function",
    meta=_params(1),
)
EX_START0 = _sym(
    "elixir", "lib/example.ex", 6, "Ejabberd.Auth.Example.start", "function",
    meta=_params(0),
)
EX_STORE_PRIVATE = _sym(
    "elixir", "lib/example.ex", 8, "Ejabberd.Auth.Example.store_type", "function",
    modifiers=["private"], meta=_params(1),
)


def _stub(src: Symbol, behaviour: str, line: int = 2) -> Edge:
    return behaviour_implements_edge(
        src=src.id, lang="elixir", behaviour=behaviour, target_id=None,
        line=line, origin="elixir-v1", run_id="r",
    )


def _run(symbols: list[Symbol], edges: list[Edge]):
    ctx = LinkerContext(
        repo_root=Path("/repo"), symbols=symbols, edges=edges,
        detected_languages={"elixir", "erlang"},
    )
    return beam_behaviour_linker(ctx)


ALL = [ERL_MOD, ERL_START, ERL_STORE, ERL_HELPER, EX_MOD, EX_START, EX_START0,
       EX_STORE_PRIVATE]


class TestSharedEdgeShapes:
    def test_unresolved_implements(self) -> None:
        edge = _stub(EX_MOD, "gen_cycle")
        assert edge.edge_type == "implements"
        assert edge.dst == "elixir:external:0-0:gen_cycle:unresolved"
        assert edge.is_resolved is False
        assert edge.evidence_type == "ast_implements"

    def test_resolved_implements_and_dispatch(self) -> None:
        implements = behaviour_implements_edge(
            src="a", lang="erlang", behaviour="b", target_id="t", line=3,
            origin="p", run_id="r", derived_from=["x"],
        )
        assert (implements.dst, implements.is_resolved) == ("t", True)
        assert implements.derived_from == ["x"]
        dispatch = callback_dispatch_edge(
            callback_id="c", impl_id="i", line=3, origin="p", run_id="r",
        )
        assert dispatch.edge_type == "dispatches_to"
        assert dispatch.evidence_type == "behaviour_callback"
        assert dispatch.meta == {"mechanism": "callback"}


    def test_callback_stable_id_separates_what_the_floor_conflates(self) -> None:
        """Arity, kind and file each change the id; the name alone does not
        make it collide with anything but another callback."""
        base = callback_stable_id("erlang", "src/shape.erl", "area/0", 0)
        assert base.startswith("sha256:")
        assert base == callback_stable_id("erlang", "src\\shape.erl", "area/0", 0)
        assert base != callback_stable_id("erlang", "src/shape.erl", "area/0", 1)
        assert base != callback_stable_id("erlang", "src/other.erl", "area/0", 0)
        assert base != callback_stable_id(
            "erlang", "src/shape.erl", "area/0", 0, kind="macro",
        )


class TestBridge:
    def test_atom_behaviour_reaches_the_erlang_module(self) -> None:
        stub = _stub(EX_MOD, "ejabberd_auth")
        result = _run(ALL, [stub])
        implements = [e for e in result.edges if e.edge_type == "implements"]
        assert [(e.src, e.dst) for e in implements] == [(EX_MOD.id, ERL_MOD.id)]
        assert implements[0].derived_from == [stub.id]
        assert implements[0].line == 2

    def test_callbacks_dispatch_by_name_and_arity(self) -> None:
        """start/1 -> the 1-ary clause only; store_type/1's only candidate is
        private; the behaviour module's own start/1 is not a callback."""
        result = _run(ALL, [_stub(EX_MOD, "ejabberd_auth")])
        dispatches = [(e.src, e.dst) for e in result.edges
                      if e.edge_type == "dispatches_to"]
        assert dispatches == [(ERL_START.id, EX_START.id)]

    def test_unknown_arity_clause_is_kept(self) -> None:
        unknown = _sym("elixir", "lib/example.ex", 7,
                       "Ejabberd.Auth.Example.store_type", "function")
        result = _run(ALL + [unknown], [_stub(EX_MOD, "ejabberd_auth")])
        dsts = {e.dst for e in result.edges if e.src == ERL_STORE.id}
        assert dsts == {unknown.id}

    def test_every_same_named_erlang_module_and_no_duplicates(self) -> None:
        twin = _sym("erlang", "test/ejabberd_auth.erl", 1, "ejabberd_auth", "module")
        stub = _stub(EX_MOD, "ejabberd_auth")
        result = _run(ALL + [twin], [stub, stub])
        implements = sorted(e.dst for e in result.edges if e.edge_type == "implements")
        assert implements == sorted([ERL_MOD.id, twin.id])
        assert len([e for e in result.edges if e.edge_type == "dispatches_to"]) == 1

    def test_not_bridged(self) -> None:
        """No Erlang module of that name; an alias-spelled target; a resolved
        implements; another edge type; a callback in a file with no module."""
        orphan = _sym("erlang", "include/x.hrl", 1, "f/0", "function",
                      modifiers=["abstract"], meta={"arity": 0, "base_name": "f"})
        edges = [
            _stub(EX_MOD, "gen_cycle"),
            _stub(EX_MOD, "Ejabberd_auth"),
            behaviour_implements_edge(
                src=EX_MOD.id, lang="elixir", behaviour="x", target_id=ERL_MOD.id,
                line=2, origin="elixir-v1", run_id="r",
            ),
            Edge.create(src=EX_MOD.id, dst=_stub(EX_MOD, "ejabberd_auth").dst,
                        edge_type="imports", line=2, origin="elixir-v1",
                        origin_run_id="r"),
            _stub(_sym("elixir", "gone.ex", 1, "Gone", "module"), "ejabberd_auth"),
        ]
        assert _run(ALL + [orphan], edges).edges == []

    def test_registered_as_a_bridge_for_the_language_pair(self) -> None:
        linker = next(x for x in get_all_linkers() if x.name == "beam-behaviour-linker")
        assert linker.activation.language_pairs == [("elixir", "erlang")]
        assert linker.depends_on == [["elixir"], ["erlang"]]
        assert linker.supersedes_consumed_stub is False
