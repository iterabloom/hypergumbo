# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-mozas: an Elixir call edge is anchored to the def clause that CONTAINS it.

Elixir emits one symbol per ``def`` clause, and the symbol name carries no
arity, so ``get_timeout/0`` and ``get_timeout/2`` -- and two modules in one
file that each define ``init`` -- share a short name. The enclosing-function
lookup used to key on that short name, which resolves to the clause that
registered last. On phoenix and plausible that mis-anchored 22-27% of calls.

The property checked everywhere below is the item's statement: the edge's
line lies inside its ``src`` symbol's span.
"""
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir

OVERLOADED = """\
defmodule Mod do
  def get_timeout() do
    System.get_env("X")
  end

  def other() do
    :ok
  end

  def get_timeout(a, b) do
    get_timeout()
  end
end
"""


def _analyze(tmp_path: Path, text: str):
    (tmp_path / "mod.ex").write_text(text, encoding="utf-8")
    result = analyze_elixir(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    calls = [e for e in result.edges if e.edge_type == "calls"]
    return by_id, calls


def _src_contains_line(by_id, edge) -> bool:
    src = by_id[edge.src]
    return src.span.start_line <= edge.line <= src.span.end_line


def test_a_call_is_anchored_to_the_clause_that_contains_it(tmp_path: Path) -> None:
    by_id, calls = _analyze(tmp_path, OVERLOADED)
    env = [e for e in calls if ":get_env:" in e.dst]
    assert len(env) == 1  # reach
    assert by_id[env[0].src].span.start_line == 2
    assert _src_contains_line(by_id, env[0])


def test_a_def_head_is_not_a_call(tmp_path: Path) -> None:
    """``def get_timeout(a, b) do`` declares; it does not call get_timeout."""
    by_id, calls = _analyze(tmp_path, OVERLOADED)
    assert [e for e in calls if e.line in (2, 6, 10)] == []


def test_calls_inside_a_def_head_are_still_calls(tmp_path: Path) -> None:
    """Only the head node is excluded: a default argument still calls."""
    text = """\
defmodule G do
  def f(x, y \\\\ Config.default()) when is_atom(x) do
    y
  end
end
"""
    by_id, calls = _analyze(tmp_path, text)
    on_head_line = sorted(e.dst for e in calls if e.line == 2)
    assert any(":default:" in d for d in on_head_line), on_head_line
    assert not any(":G.f:" in d for d in on_head_line), on_head_line
    assert all(by_id[e.src].name == "G.f" for e in calls if e.line == 2)


def test_same_short_name_in_two_modules_of_one_file(tmp_path: Path) -> None:
    text = """\
defmodule A do
  def init(x) do
    Logger.info(x)
  end
end

defmodule B do
  def init(y) do
    y
  end
end
"""
    by_id, calls = _analyze(tmp_path, text)
    info = [e for e in calls if ":info:" in e.dst]
    assert len(info) == 1
    assert by_id[info[0].src].name == "A.init"


def test_every_call_edge_lies_inside_its_source_symbol(tmp_path: Path) -> None:
    text = OVERLOADED + """
defmodule Other do
  def get_timeout(x), do: Enum.map(x, &to_string/1)
  defp get_timeout(x, y) when is_list(x), do: helper(y)
  defp helper(z), do: String.upcase(z)
end
"""
    by_id, calls = _analyze(tmp_path, text)
    assert len(calls) >= 5  # reach
    outside = [(e.line, e.src) for e in calls if not _src_contains_line(by_id, e)]
    assert outside == []


def test_a_quoted_def_falls_through_to_the_macro_that_contains_it(
    tmp_path: Path,
) -> None:
    """A def inside ``quote`` has no symbol (INV-sinah), so keep walking up.

    The quoted def is injected into the module that runs ``use``; this file
    only CONTAINS its text, inside ``__using__``. Stopping at the first def
    keyword dropped these calls outright (121 on phoenix + plausible), where
    the containing macro is a symbol that does contain them.
    """
    text = """\\
defmodule Lib do
  defmacro __using__(_opts) do
    quote do
      def start_link(arg) do
        GenServer.start_link(__MODULE__, arg)
      end
    end
  end
end
"""
    by_id, calls = _analyze(tmp_path, text)
    start = [e for e in calls if ":start_link:" in e.dst]
    assert len(start) == 1
    assert by_id[start[0].src].name == "Lib.__using__"
    assert _src_contains_line(by_id, start[0])
