# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tusad (B2): one Elixir function per name/arity, with a producer stable_id.

Elixir identifies a function by its module, name and arity. Several ``def``
clauses of one name and arity are ONE function (pattern-matched in order), and
``f/1`` and ``f/2`` are two. The analyzer used to emit a symbol per clause, all
named ``M.f``; the ADR-0035 backstop floor keys on ``(kind, language, path,
name)``, so every multi-clause or multi-arity function in a file shared a floor
key and was left with ``stable_id = null`` (699 of 2,221 nodes on phoenix/lib).

Now the clauses of a name/arity fold into one node (the Erlang shape), whose
stable_id is the ADR-0035 producer formula over the module scope chain and the
declared arity (``overload_arity.declared_arity_stable_id``). The name stays
``M.f``: arity is in ``meta["parameters"]``. Deleting a clause, or a function
of another arity, moves no other id.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import floor_stable_id_key
from hypergumbo_core.overload_arity import declared_arity, declared_arity_stable_id
from hypergumbo_lang_common.elixir import analyze_elixir

GREET = """\
defmodule Demo do
  defstruct [:a, :b]

  def greet(:a), do: 1
  def greet(:b) do
    helper(2)
  end
  def greet(x, y), do: {x, y}

  defp helper(n) when is_integer(n), do: n
  defp helper(n), do: n

  defmacro mk(x), do: x
  defmacro mk(x, y), do: {x, y}
end
"""


def _analyze(tmp_path: Path, text: str, name: str = "demo.ex"):
    (tmp_path / "lib").mkdir(exist_ok=True)
    (tmp_path / "lib" / name).write_text(text, encoding="utf-8")
    return analyze_elixir(tmp_path)


def _callables(result) -> list:
    return [s for s in result.symbols if s.kind in ("function", "macro")]


def _arity(sym) -> int:
    return len((sym.meta or {})["parameters"])


def test_clauses_of_one_arity_are_one_node(tmp_path: Path) -> None:
    result = _analyze(tmp_path, GREET)
    shape = sorted(
        (s.name, s.kind, _arity(s), s.span.start_line, s.span.end_line)
        for s in _callables(result)
    )
    assert shape == [
        ("Demo.greet", "function", 1, 4, 7),
        ("Demo.greet", "function", 2, 8, 8),
        ("Demo.helper", "function", 1, 10, 11),
        ("Demo.mk", "macro", 1, 13, 13),
        ("Demo.mk", "macro", 2, 14, 14),
    ]
    helper = next(s for s in _callables(result) if s.name == "Demo.helper")
    assert helper.modifiers == ["private"]
    assert helper.line_span == 2


def test_every_callable_and_field_has_a_producer_id(tmp_path: Path) -> None:
    """No floor key, no null: the producer formula, distinct per name/arity."""
    result = _analyze(tmp_path, GREET)
    gated = [s for s in result.symbols if s.kind in ("function", "macro", "field")]
    assert len(gated) == 7
    for sym in gated:
        assert sym.stable_id is not None, sym.name
        assert sym.stable_id != floor_stable_id_key(
            sym.kind, "elixir", sym.path, sym.name,
        ), sym.name
    assert len({s.stable_id for s in gated}) == len(gated)


def test_the_id_is_the_shared_declared_arity_formula(tmp_path: Path) -> None:
    result = _analyze(tmp_path, GREET)
    greet2 = next(
        s for s in _callables(result) if s.name == "Demo.greet" and _arity(s) == 2
    )
    assert greet2.stable_id == declared_arity_stable_id(
        kind="function", lang="elixir", path="lib/demo.ex", name="greet",
        qualified_name="Demo.greet", arity=declared_arity(greet2.meta),
    )


def test_deleting_a_clause_or_a_sibling_moves_no_other_id(tmp_path: Path) -> None:
    def ids(text: str) -> dict[tuple[str, str, int], str]:
        result = _analyze(tmp_path, text)
        return {
            (s.kind, s.name, _arity(s) if s.kind != "field" else 0): s.stable_id
            for s in result.symbols if s.kind in ("function", "macro", "field")
        }

    full = ids(GREET)
    no_first_clause = ids(GREET.replace("  def greet(:a), do: 1\n", ""))
    no_greet2 = ids(GREET.replace("  def greet(x, y), do: {x, y}\n", ""))
    assert no_first_clause == full
    assert no_greet2 == {k: v for k, v in full.items() if k != ("function", "Demo.greet", 2)}


def test_clauses_split_by_another_definition_are_still_one_function(
    tmp_path: Path,
) -> None:
    """The compiler warns ("clauses ... should be grouped together") and still
    compiles ONE function; a call in the later clause anchors on it."""
    text = """\
defmodule Split do
  def run(:a), do: 1
  def other, do: 2
  def run(:b) do
    other()
  end
end
"""
    result = _analyze(tmp_path, text)
    runs = [s for s in _callables(result) if s.name == "Split.run"]
    assert [(s.span.start_line, s.span.end_line) for s in runs] == [(2, 6)]
    calls = [e for e in result.edges if e.edge_type == "calls" and e.line == 5]
    assert len(calls) == 1  # reach
    assert calls[0].src == runs[0].id


def test_defaults_make_one_function_callable_at_several_arities(
    tmp_path: Path,
) -> None:
    """``def f(a, b \\\\ 1)`` is ONE function reachable as f/1 and f/2: one node
    whose parameters mark the default, and its clauses fold into it."""
    text = """\
defmodule D do
  def fetch(key, default \\\\ nil)
  def fetch(:a, default), do: default
  def fetch(_key, default), do: default
end
"""
    result = _analyze(tmp_path, text)
    (fetch,) = _callables(result)
    assert (fetch.span.start_line, fetch.span.end_line) == (2, 4)
    assert fetch.meta["parameters"] == [
        {"name": "key", "type": None, "default": False},
        {"name": "default", "type": None, "default": True},
    ]


def test_cyclomatic_complexity_aggregates_the_clauses(tmp_path: Path) -> None:
    """The Erlang rule: one entry path, plus each clause's own decisions."""
    text = """\
defmodule C do
  def f(x) when x > 0 do
    if x > 1, do: :big, else: :one
  end
  def f(x) do
    case x do
      0 -> :zero
      _ -> :neg
    end
  end
end
"""
    (f,) = _callables(_analyze(tmp_path, text))
    assert f.cyclomatic_complexity == 3


def test_anonymous_function_clauses_are_not_definitions(tmp_path: Path) -> None:
    text = """\
defmodule A do
  def run(xs) do
    Enum.map(xs, fn
      0 -> :zero
      n -> n
    end)
  end
end
"""
    assert [s.name for s in _callables(_analyze(tmp_path, text))] == ["A.run"]
