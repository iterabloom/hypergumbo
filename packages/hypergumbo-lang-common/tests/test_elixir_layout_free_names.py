# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py
"""An Elixir module alias written with spaces does not move its functions' ids.

The Elixir analyzer reads a module's name as its alias text, so
``defmodule Foo . Bar`` named ``Foo . Bar`` and every function's qualified
name carried the spacing; the untyped formula hashes names
``without_layout`` in every language but the content ones (WI-sidus).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir


def _ids(tmp_path: Path, module: str) -> dict[str, str | None]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "m.ex").write_text(f"defmodule {module} do\n  def f(x), do: x\n  def g(y), do: y\nend\n")
    return {s.name.rsplit(".", 1)[-1]: s.stable_id for s in analyze_elixir(tmp_path).symbols
            if s.kind == "function"}


def test_a_spaced_module_alias_keeps_its_functions_ids(tmp_path: Path) -> None:
    tight = _ids(tmp_path, "Foo.Bar")
    spaced = _ids(tmp_path, "Foo . Bar")
    assert set(tight) == {"f", "g"} and all(tight.values())  # reach
    assert tight == spaced


def test_another_module_is_another_id(tmp_path: Path) -> None:
    assert _ids(tmp_path, "Foo.Bar")["f"] != _ids(tmp_path, "Foo.Baz")["f"]
