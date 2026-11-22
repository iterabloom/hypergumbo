# SPDX-License-Identifier: AGPL-3.0-or-later
"""The EUnit and ExUnit rows, asked of what the Erlang and Elixir analyzers record.

Both sets of rows in ``frameworks/test-frameworks.yaml`` match nothing, for
two different reasons, and this file pins both so that a change to either
analyzer or to the rows shows up here by name.

EUnit. The rows read a test by ``_test$`` and a generator by ``_test_$``.
erlang.py names a function ``name/arity``, so neither matches: cowboy's 25
EUnit functions and rebar3's 14 are no test functions. The repair is known
(``_test/0$`` and ``_test_/0$``: EUnit's rule is the name and no argument)
and is NOT made, because of where EUnit tests live: in ``src/``, beside the
code they test. The entry list takes a ``test_function`` for an entry point
and demotes it only in a file ``paths.is_test_file`` calls a test file, so
with the repaired rows cowboy's 25 EUnit functions became its first 25
entry points. The rows and that ranking rule have to change together.

ExUnit. The three rows describe ``test "..." do``, ``describe "..." do`` and
``setup do``. Each is a call with a block, for which elixir.py emits no
symbol, and it names a function ``Module.function``: the rows cannot match.

Every test runs the analyzer and hands its symbols to the shipped rows; a
symbol built by hand shows only what its author believed the analyzer records.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.elixir import analyze_elixir
from hypergumbo_lang_common.erlang import analyze_erlang

_ERLANG = (
    "-module(calc).\n"
    "-export([add/2, add_test/0, add_test_/0]).\n\n"
    "add(A, B) -> A + B.\n\n"
    "add_test() -> 3 = add(1, 2).\n\n"
    "add_test_() -> [fun() -> 3 = add(1, 2) end].\n"
)

_EXUNIT = (
    "defmodule MathTest do\n"
    "  use ExUnit.Case\n\n"
    "  setup do\n"
    "    {:ok, n: 1}\n"
    "  end\n\n"
    '  describe "addition" do\n'
    '    test "adds", %{n: n} do\n'
    "      assert n + 2 == 3\n"
    "    end\n"
    "  end\n\n"
    "  def helper, do: 1\n"
    "end\n"
)


def _test_concepts(symbol: Symbol) -> set[str]:
    return {
        c["concept"] for c in (symbol.meta or {}).get("concepts", [])
        if c["concept"].startswith("test")
    }


def _functions(symbols: list[Symbol]) -> dict[str, Symbol]:
    clear_pattern_cache()
    enrich_symbols(symbols, set())
    return {s.name: s for s in symbols if s.kind == "function"}


def test_an_eunit_function_is_named_with_its_arity_and_is_not_marked(tmp_path: Path) -> None:
    (tmp_path / "calc.erl").write_text(_ERLANG)

    got = _functions(list(analyze_erlang(tmp_path).symbols))

    assert sorted(got) == ["add/2", "add_test/0", "add_test_/0"]
    assert _test_concepts(got["add_test/0"]) == set()
    assert _test_concepts(got["add_test_/0"]) == set()


def test_an_exunit_test_block_is_not_a_symbol(tmp_path: Path) -> None:
    (tmp_path / "math_test.exs").write_text(_EXUNIT)

    got = _functions(list(analyze_elixir(tmp_path).symbols))

    assert sorted(got) == ["MathTest.helper"]
    assert _test_concepts(got["MathTest.helper"]) == set()
