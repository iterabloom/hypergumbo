# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bare call is attributed to the module that actually exports it (WI-jozap).

WHAT WAS WRONG, and why pinning the ordering is not the fix. The branch read
``source_module = next(iter(imported_modules))`` — an arbitrary element of a
``set[str]``, and Python randomises string hashing per process, so the same code
over the same repository did not produce the same answer twice (67 and 166 edges
moved between identical runs on phoenix-framework). Sorting the set would make
the answer reproducible and still wrong: `is_atom` is a Kernel auto-import,
available with no ``import`` directive at all, so attributing it to `Plug.Conn`
is false under every ordering.

THE POPULATION WAS TWO POPULATIONS. ``_ELIXIR_STDLIB_FUNCTIONS`` unioned Kernel
auto-imports with names that genuinely come from an imported module (IO.puts,
Enum.map, String.split, Logger.error) and the branch treated them alike. The
ownership was already written down beside the names as prose comments and never
used as data.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.elixir import ElixirAnalyzer


def _edges(tmp_path: Path, source: str) -> list:
    (tmp_path / "demo.ex").write_text(source, encoding="utf-8")
    return ElixirAnalyzer().analyze(tmp_path).edges


def _dsts(edges: list, name: str) -> set[str]:
    return {e.dst for e in edges if e.dst.endswith(f":{name}:unresolved")}


class TestAKernelAutoImportIsNeverTheImportedModulesFunction:
    def test_is_atom_gets_no_module_keyed_edge(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Plug.Conn
  import MyApp.Helpers

  def run(x) do
    is_atom(x)
  end
end
''')
        assert _dsts(edges, "is_atom") == set()

    def test_the_whole_kernel_family_abstains(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Plug.Conn

  def run(x) do
    inspect(x)
    raise("no")
    elem(x, 0)
    apply(x, :f, [])
    map_size(x)
  end
end
''')
        for name in ("inspect", "raise", "elem", "apply", "map_size"):
            assert _dsts(edges, name) == set(), name


class TestAnImportedNameTakesTheModuleThatExportsIt:
    def test_puts_binds_IO_not_the_other_import(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Plug.Conn
  import IO

  def run(x) do
    puts(x)
  end
end
''')
        assert _dsts(edges, "puts") == {"elixir:IO:0-0:puts:unresolved"}

    def test_error_binds_Logger(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Logger
  import Plug.Conn

  def run(x) do
    error(x)
  end
end
''')
        assert _dsts(edges, "error") == {"elixir:Logger:0-0:error:unresolved"}

    def test_an_importable_name_whose_owner_is_NOT_imported_abstains(
        self, tmp_path: Path,
    ) -> None:
        # `puts` is IO's. If IO is not imported, the call is not IO's — and it
        # is certainly not Plug.Conn's. Withholding is the safe direction.
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Plug.Conn

  def run(x) do
    puts(x)
  end
end
''')
        assert _dsts(edges, "puts") == set()


class TestTheOnlyListWinsOverEverything:
    def test_an_only_list_names_the_module(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Plug.Conn
  import Ecto.Query, only: [split: 2]

  def run(x) do
    split(x, 2)
  end
end
''')
        # `split` is String's by default ownership, but an explicit `only:`
        # directive is direct evidence and outranks the default table.
        assert _dsts(edges, "split") == {"elixir:Ecto.Query:0-0:split:unresolved"}

    def test_only_macros_is_not_enumerable_and_is_skipped(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Bitwise, only: :macros

  def run(x) do
    is_atom(x)
  end
end
''')
        assert _dsts(edges, "is_atom") == set()

    def test_except_does_not_establish_ownership(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, '''
defmodule Demo do
  import MyApp.Helpers, except: [foo: 1]

  def run(x) do
    inspect(x)
  end
end
''')
        assert _dsts(edges, "inspect") == set()


class TestImportShapesTheReaderMustSurvive:
    """Shapes the grammar produces that are not `import M, only: [f: n]`."""

    def test_an_import_whose_argument_is_not_an_alias(self, tmp_path: Path) -> None:
        # `import :erlang` names an ATOM, not an alias node. The reader must
        # skip it rather than assume every import carries an alias child.
        edges = _edges(tmp_path, '''
defmodule Demo do
  import :erlang
  import IO

  def run(x) do
    puts(x)
  end
end
''')
        assert _dsts(edges, "puts") == {"elixir:IO:0-0:puts:unresolved"}

    def test_a_second_keyword_beside_only(self, tmp_path: Path) -> None:
        # `only:` is not always the sole keyword; the reader walks the whole
        # keywords list and skips the comma tokens between pairs.
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Ecto.Query, only: [split: 2], warn: false

  def run(x) do
    split(x, 2)
  end
end
''')
        assert _dsts(edges, "split") == {"elixir:Ecto.Query:0-0:split:unresolved"}

    def test_an_only_list_naming_more_than_one_function(
        self, tmp_path: Path,
    ) -> None:
        # Two entries means a comma token inside the inner keywords list.
        edges = _edges(tmp_path, '''
defmodule Demo do
  import Ecto.Query, only: [split: 2, join: 3]

  def run(x) do
    split(x, 2)
    join(x, 3)
  end
end
''')
        assert _dsts(edges, "split") == {"elixir:Ecto.Query:0-0:split:unresolved"}
        assert _dsts(edges, "join") == {"elixir:Ecto.Query:0-0:join:unresolved"}


class TestDeterminism:
    def test_the_answer_does_not_depend_on_set_iteration_order(
        self, tmp_path: Path,
    ) -> None:
        """The defect's own signature: many imports, one bare call.

        With ``next(iter(<set>))`` the module chosen here depended on string
        hashing. There is now no set iteration on this path at all, so a single
        in-process run suffices to pin the RULE; the cross-process property is
        measured on a real repository in the lab record.
        """
        edges = _edges(tmp_path, '''
defmodule Demo do
  import A.One
  import B.Two
  import C.Three
  import D.Four
  import E.Five
  import IO

  def run(x) do
    puts(x)
    is_atom(x)
  end
end
''')
        assert _dsts(edges, "puts") == {"elixir:IO:0-0:puts:unresolved"}
        assert _dsts(edges, "is_atom") == set()
