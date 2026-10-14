# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rodiz, elixir: a local call reaches the function its ARITY names.

Elixir names a function by name AND arity: ``get_timeout/0`` and
``get_timeout/2`` are two functions. Their symbols share the name (no arity in
it), and a local call fanned a ``calls`` edge out to every same-named symbol in
the file, whatever the arity, so ``get_timeout()`` inside ``get_timeout/2``
drew a self-loop. Erlang had the same wrong-callee shape and INV-mozas keyed it
on name/arity.

Clauses of ONE arity are one function and, since WI-tusad, one symbol spanning
them, so a call reaches that symbol; default arguments (``b \\\\ 1``) make one
function callable at several arities; a pipe passes its left side as the first
argument; a ``do`` block is one more.

Each test asserts REACH (an edge exists at the call line) before asserting
which function it reaches.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir


def _callees_at(result, line: int) -> list[tuple[str, int]]:
    """Sorted (dst name, dst start line) of the calls edges at ``line``."""
    by_id = {s.id: s for s in result.symbols}
    return sorted(
        (by_id[e.dst].name, by_id[e.dst].span.start_line)
        for e in result.edges
        if e.edge_type == "calls" and e.line == line and e.dst in by_id
    )


def _write(tmp_path: Path, text: str) -> None:
    (tmp_path / "m.ex").write_text(text)


def test_a_call_of_another_arity_is_not_a_self_loop(tmp_path: Path) -> None:
    """The filed instance."""
    _write(tmp_path, """\
defmodule Conn do
  def get_timeout(opts, key) do
    Keyword.get(opts, key, get_timeout())
  end

  def get_timeout do
    5000
  end
end
""")
    assert _callees_at(analyze_elixir(tmp_path), 3) == [("Conn.get_timeout", 6)]


def test_the_function_of_the_called_arity_and_no_other(tmp_path: Path) -> None:
    """livebook's do_transform: recursion reaches the /2 function (both of its
    clauses, folded into the symbol at line 2), not the /3."""
    _write(tmp_path, """\
defmodule T do
  def do_transform(acc, []), do: acc

  def do_transform(acc, [h | t]) do
    do_transform([h | acc], t)
  end

  def do_transform(acc, list, opts), do: do_transform(acc, list) ++ opts
end
""")
    result = analyze_elixir(tmp_path)
    assert _callees_at(result, 5) == [("T.do_transform", 2)]
    assert _callees_at(result, 8) == [("T.do_transform", 2)]


def test_a_default_argument_makes_the_shorter_call_reach(tmp_path: Path) -> None:
    """A bodiless head declares the defaults of every clause after it: head and
    clauses are one fetch/2 symbol, reached by the one-argument call."""
    _write(tmp_path, """\
defmodule D do
  def fetch(key, default \\\\ nil)
  def fetch(:a, default), do: default
  def fetch(_key, default), do: default

  def fetch, do: :none

  def run(x) do
    fetch(x)
  end
end
""")
    assert _callees_at(analyze_elixir(tmp_path), 9) == [("D.fetch", 2)]


def test_a_pipe_passes_one_more_argument(tmp_path: Path) -> None:
    _write(tmp_path, """\
defmodule P do
  def step(x), do: x
  def step(x, n), do: x + n

  def run(x) do
    x |> step(1)
    x |> step
    x |> step()
    x
    |> step(1)
    # a comment between stages is a child of the pipe too
    |> step()
  end
end
""")
    result = analyze_elixir(tmp_path)
    assert _callees_at(result, 6) == [("P.step", 3)]
    assert _callees_at(result, 7) == [("P.step", 2)]
    assert _callees_at(result, 8) == [("P.step", 2)]
    assert _callees_at(result, 10) == [("P.step", 3)]
    assert _callees_at(result, 12) == [("P.step", 2)]


def test_a_do_block_is_one_more_argument(tmp_path: Path) -> None:
    """``with_lock(l) do .. end`` is ``with_lock(l, do: ..)``; a keyword list
    already in the arguments absorbs the ``do`` instead of adding one."""
    _write(tmp_path, """\
defmodule B do
  def with_lock(l), do: l
  def with_lock(l, opts), do: {l, opts}

  def run(l) do
    with_lock(l) do
      :ok
    end
    with_lock l, timeout: 1 do
      :ok
    end
  end
end
""")
    result = analyze_elixir(tmp_path)
    assert _callees_at(result, 6) == [("B.with_lock", 3)]
    assert _callees_at(result, 9) == [("B.with_lock", 3)]


def test_no_local_arity_falls_through_to_the_other_resolutions(tmp_path: Path) -> None:
    """``inspect(x, opts)`` with only a local ``inspect/1`` is ``Kernel.inspect/2``:
    no local clause can take it, so it is not drawn to the local one."""
    _write(tmp_path, """\
defmodule K do
  def inspect(x), do: x

  def run(x) do
    inspect(x, limit: 3)
  end
end
""")
    assert _callees_at(analyze_elixir(tmp_path), 5) == []


def test_a_spliced_argument_list_reaches_every_arity(tmp_path: Path) -> None:
    """``unquote_splicing`` passes an unknown number of arguments: arity cannot
    exclude a function, so the fan-out is what it was."""
    _write(tmp_path, """\
defmodule Q do
  def f(a), do: a
  def f(a, b), do: {a, b}

  defmacro call_f(args) do
    quote do
      f(unquote_splicing(args))
    end
  end
end
""")
    assert _callees_at(analyze_elixir(tmp_path), 7) == [("Q.f", 2), ("Q.f", 3)]


def test_functions_carry_their_parameters(tmp_path: Path) -> None:
    _write(tmp_path, """\
defmodule S do
  def a, do: 1
  def b(x, y \\\\ 2) when is_integer(x), do: x + y
  defmacro c(%{key: v} = m), do: {v, m}
  def d(%{"type" => "message", "payload" => payload} = msg), do: {payload, msg}
end
""")
    params = {
        s.name: (s.meta or {}).get("parameters")
        for s in analyze_elixir(tmp_path).symbols if s.kind in ("function", "macro")
    }
    assert params["S.a"] == []
    assert params["S.b"] == [
        {"name": "x", "type": None, "default": False},
        {"name": "y", "type": None, "default": True},
    ]
    assert params["S.c"] == [{"name": "%{key: v} = m", "type": None, "default": False}]
    # A long pattern is recorded by its first 37 characters and an ellipsis.
    assert params["S.d"] == [{
        "name": '%{"type" => "message", "payload" => p...',
        "type": None, "default": False,
    }]
