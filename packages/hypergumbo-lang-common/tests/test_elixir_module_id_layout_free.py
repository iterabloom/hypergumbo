# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py
"""An Elixir module's stable id does not depend on how its name is laid out.

A module took no producer stable id; the run's backstop gave it the table
formula (``module:elixir:path:name``) over its name as written, so
``defmodule Foo . Bar`` and ``defmodule Foo.\\n  Bar`` hashed ``Foo . Bar``
and ``Foo.\\n  Bar``: three stable ids for one module, though
its functions' ids were already layout-free (WI-radim). The producer now
mints the formula over the name in canonical spacing (``layout_free``); a
name written the usual way hashes what it did. The displayed name is left
as written.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.overload_arity import scoped_table_stable_id
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.elixir import analyze_elixir


def _modules(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "m.ex").write_text(text)
    return sorted(
        (s for s in analyze_elixir(root).symbols if s.kind == "module"),
        key=lambda s: s.span.start_line,
    )


_SPELLINGS = {
    "spaced-dot": "defmodule Foo . Bar do\n  def f(x), do: x\nend\n",
    "line-broken": "defmodule Foo.\n  Bar do\n  def f(x), do: x\nend\n",
    "left-spaced": "defmodule Foo .Bar do\n  def f(x), do: x\nend\n",
}


@pytest.mark.parametrize("spelling", sorted(_SPELLINGS))
def test_a_module_name_laid_out_otherwise_keeps_its_id(tmp_path: Path, spelling: str) -> None:
    plain = _modules(tmp_path / "a", "defmodule Foo.Bar do\n  def f(x), do: x\nend\n")
    other = _modules(tmp_path / "b", _SPELLINGS[spelling])
    assert len(plain) == len(other) == 1
    assert plain[0].stable_id is not None
    assert other[0].stable_id == plain[0].stable_id, spelling


def test_a_usual_name_hashes_what_it_did(tmp_path: Path) -> None:
    """The table formula over the name as written, for a name with no
    layout in it: the value the backstop gave it before."""
    mods = _modules(tmp_path / "r", "defmodule Foo.Bar do\n  defmodule Baz do\n  end\nend\n")
    assert [m.name for m in mods] == ["Foo.Bar", "Foo.Bar.Baz"]
    for m in mods:
        assert m.stable_id == scoped_table_stable_id(
            kind="module", lang="elixir", path="m.ex", name=m.name)


def test_two_names_stay_two_ids(tmp_path: Path) -> None:
    mods = _modules(
        tmp_path / "r", "defmodule Foo.Bar do\nend\ndefmodule Foo.Baz do\nend\n")
    assert len({m.stable_id for m in mods}) == 2


def test_a_nested_protocol_and_impl_named_with_layout(tmp_path: Path) -> None:
    """``defprotocol`` / ``defimpl`` define modules the same way."""
    plain = _modules(
        tmp_path / "a",
        "defprotocol Size.Of do\n  def size(x)\nend\n"
        "defimpl Size.Of, for: Foo.Bar do\n  def size(_), do: 0\nend\n")
    spaced = _modules(
        tmp_path / "b",
        "defprotocol Size . Of do\n  def size(x)\nend\n"
        "defimpl Size . Of, for: Foo . Bar do\n  def size(_), do: 0\nend\n")
    assert len(plain) == len(spaced) == 2
    assert all(m.stable_id is not None for m in plain)
    assert [m.stable_id for m in spaced] == [m.stable_id for m in plain]
