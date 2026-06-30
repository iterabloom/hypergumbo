# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-loguk: Elixir callable Symbols carry non-null cyclomatic_complexity.

Verifies the homoiconic head-symbol walker against the REAL tree-sitter-elixir
grammar. Elixir control forms are ``call`` nodes whose first named child is an
``identifier`` head (if/unless/cond/case/with/for/receive/try). ``&&``/``||``/
``and``/``or`` are ``binary_operator`` nodes (NOT calls) and are conservatively
NOT counted in v1. The analyzer emits one Symbol per def clause, so the walker
is applied per-clause. The ``module`` symbol stays None.
"""
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir

_CALLABLE_KINDS = {"function", "macro"}

_FIXTURE = '''defmodule Demo do
  def classify(x) do
    if x > 0 do
      case x do
        1 -> :one
        _ -> :many
      end
    else
      cond do
        x == 0 -> :zero
        true -> :neg
      end
    end
  end

  def transform(list, flag) do
    unless flag do
      for item <- list do
        item * 2
      end
    end
  end

  def orchestrate(a) do
    with {:ok, v} <- a do
      receive do
        {:msg, m} ->
          try do
            v + m
          rescue
            _ -> :err
          end
      end
    end
  end

  def gate(a, b) do
    a && b || c
  end

  def plain(a) do
    a + 1
  end

  defp helper(a) do
    if a, do: :y, else: :n
  end

  defmacro mymac(x) do
    quote do
      if unquote(x), do: 1, else: 2
    end
  end
end
'''


def _callables_by_name(symbols: list) -> dict:
    return {s.name: s for s in symbols if s.kind in _CALLABLE_KINDS}


def test_elixir_callable_cyclomatic_complexity(tmp_path: Path) -> None:
    (tmp_path / "demo.ex").write_text(_FIXTURE)
    result = analyze_elixir(tmp_path)
    assert not result.skipped
    callables = _callables_by_name(result.symbols)
    assert callables["Demo.classify"].cyclomatic_complexity == 4
    assert callables["Demo.transform"].cyclomatic_complexity == 3
    assert callables["Demo.orchestrate"].cyclomatic_complexity == 4
    # && / || are binary_operator nodes, not head-symbol forms -> not counted.
    assert callables["Demo.gate"].cyclomatic_complexity == 1
    assert callables["Demo.plain"].cyclomatic_complexity == 1
    assert callables["Demo.helper"].cyclomatic_complexity == 2
    assert callables["Demo.mymac"].cyclomatic_complexity == 2


def test_elixir_callables_have_non_null_loc(tmp_path: Path) -> None:
    (tmp_path / "demo.ex").write_text(_FIXTURE)
    result = analyze_elixir(tmp_path)
    for sym in result.symbols:
        if sym.kind in _CALLABLE_KINDS:
            assert sym.cyclomatic_complexity is not None
            assert sym.line_span is not None


def test_elixir_module_stays_null(tmp_path: Path) -> None:
    (tmp_path / "demo.ex").write_text(_FIXTURE)
    result = analyze_elixir(tmp_path)
    non_callables = [s for s in result.symbols if s.kind not in _CALLABLE_KINDS]
    assert any(s.kind == "module" for s in non_callables)
    for sym in non_callables:
        assert sym.cyclomatic_complexity is None
        assert sym.line_span is None
