# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-mozas: an Erlang call edge is anchored to the function that CONTAINS it.

Erlang names a function by name AND arity, so ``get_timeout/0`` and
``get_timeout/2`` are two functions. The enclosing-function lookup used to
key on the bare atom, which every arity registers under, so a call inside
``get_timeout/0`` was anchored to whichever arity registered last. On
rabbitmq that put an ``application:get_env`` source 1,900 lines away from
the symbol it was attributed to.

The property checked everywhere below is the item's statement: the edge's
line lies inside its ``src`` symbol's span.
"""
from pathlib import Path

from hypergumbo_lang_common.erlang import analyze_erlang

OVERLOADED = """\
-module(mod).
-export([get_timeout/0, get_timeout/2, other/0]).

get_timeout() ->
    application:get_env(rabbit, consumer_timeout),
    ok.

other() ->
    ok.

get_timeout(A, B) ->
    get_timeout().
"""


def _analyze(tmp_path: Path, text: str):
    (tmp_path / "mod.erl").write_text(text, encoding="utf-8")
    result = analyze_erlang(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    calls = [e for e in result.edges if e.edge_type == "calls"]
    return by_id, calls


def _src_contains_line(by_id, edge) -> bool:
    src = by_id[edge.src]
    return src.span.start_line <= edge.line <= src.span.end_line


def test_a_remote_call_is_anchored_to_the_arity_that_contains_it(tmp_path: Path) -> None:
    by_id, calls = _analyze(tmp_path, OVERLOADED)
    env = [e for e in calls if ":get_env:" in e.dst]
    assert len(env) == 1  # reach: the source call is emitted at all
    assert by_id[env[0].src].name == "get_timeout/0"
    assert _src_contains_line(by_id, env[0])


def test_a_local_call_resolves_to_the_arity_it_is_called_with(tmp_path: Path) -> None:
    """``get_timeout()`` is a call to get_timeout/0, not a self-loop on /2."""
    by_id, calls = _analyze(tmp_path, OVERLOADED)
    local = [e for e in calls if e.line == 12]
    assert len(local) == 1
    assert by_id[local[0].src].name == "get_timeout/2"
    assert by_id[local[0].dst].name == "get_timeout/0"


def test_a_call_in_a_later_clause_is_anchored_to_the_coalesced_function(
    tmp_path: Path,
) -> None:
    """Clauses coalesce into one symbol; the anchor must survive that."""
    text = """\
-module(mod).
-export([f/1, f/0]).

f(0) ->
    ok;
f(N) ->
    io:format("~p", [N]).

f() ->
    ok.
"""
    by_id, calls = _analyze(tmp_path, text)
    fmt = [e for e in calls if ":format:" in e.dst]
    assert len(fmt) == 1
    assert by_id[fmt[0].src].name == "f/1"
    assert _src_contains_line(by_id, fmt[0])


def test_ifdef_alternatives_anchor_to_the_branch_that_contains_the_call(
    tmp_path: Path,
) -> None:
    """-ifdef/-else defines one name/arity twice; each keeps its own calls.

    ``teardown/0`` sits between the two so they are NOT coalesced into one
    symbol -- the shape of ejabberd's misc.erl, where the ``-ifdef`` copy of
    ``json_encode/1`` never reached Pass 2 because the name-keyed dict held
    only the ``-else`` copy, and its ``jiffy:encode`` call was lost.
    """
    text = """\
-module(mod).
-export([setup/0, teardown/0]).

-ifdef(FEATURE).
setup() ->
    feature:start().
teardown() ->
    feature:stop().
-else.
setup() ->
    other:start().
teardown() ->
    ok.
-endif.
"""
    by_id, calls = _analyze(tmp_path, text)
    setups = sorted(s.span.start_line for s in by_id.values() if s.name == "setup/0")
    assert setups == [5, 10]  # reach: two symbols, not one coalesced
    feature = [e for e in calls if e.dst.endswith(":start:function") and ":feature:" in e.dst]
    other = [e for e in calls if ":other:" in e.dst]
    assert len(feature) == 1 and len(other) == 1
    assert by_id[feature[0].src].span.start_line == 5
    assert by_id[other[0].src].span.start_line == 10
    assert all(_src_contains_line(by_id, e) for e in calls)


def test_every_call_edge_lies_inside_its_source_symbol(tmp_path: Path) -> None:
    text = OVERLOADED + """
run(X) ->
    lists:map(fun(Y) -> get_timeout(X, Y) end, [1]),
    run().

run() ->
    erlang:now().
"""
    by_id, calls = _analyze(tmp_path, text)
    assert len(calls) >= 5  # reach
    outside = [(e.line, e.src) for e in calls if not _src_contains_line(by_id, e)]
    assert outside == []
