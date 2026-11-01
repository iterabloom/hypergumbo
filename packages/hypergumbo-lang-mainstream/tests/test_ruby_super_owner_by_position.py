# SPDX-License-Identifier: AGPL-3.0-or-later
"""``super`` in Ruby names the superclass of the class that contains it (WI-gikuk).

WI-misir hands a ``super`` call to the inherited-calls linker with the
SUPERCLASS as its ``enclosing_class``. The superclass was read off the class
looked up by its bare name in the run-wide registry, which drops the module
nesting: ``module A; class Foo < BaseA`` and ``module B; class Foo < BaseB``
are two classes, and both ``super`` calls named whichever ``Foo`` registered
last. The class is now the one whose declaration contains the call (by
POSITION). Ruby does reopen a class by its full constant path, so a reopening
``class Foo`` with no superclass of its own still reaches the superclass the
other declaration names, looked up by the QUALIFIED name.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _super_parents(root: Path) -> dict[str, str | None]:
    """``{caller method id's file: enclosing_class hint}`` per ``super`` edge."""
    result = analyze_ruby(root)
    by_id = {s.id: s for s in result.symbols}
    out: dict[str, str | None] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in by_id and e.dst.endswith(":go:unresolved"):
            out[Path(by_id[e.src].path).name] = (e.meta or {}).get("enclosing_class")
    return out


@pytest.mark.parametrize("call", ["super", "super()"])
def test_two_classes_of_one_name_in_two_modules_keep_their_superclass(
    tmp_path: Path, call: str,
) -> None:
    for mod, base in (("A", "BaseA"), ("B", "BaseB")):
        (tmp_path / f"{mod.lower()}.rb").write_text(
            f"module {mod}\n  class Foo < {base}\n    def go\n      {call}\n"
            "    end\n  end\nend\n")
    assert _super_parents(tmp_path) == {"a.rb": "BaseA", "b.rb": "BaseB"}


@pytest.mark.parametrize("reopen_file", ["a.rb", "z.rb"])
def test_a_reopened_class_reaches_the_superclass_declared_elsewhere(
    tmp_path: Path, reopen_file: str,
) -> None:
    """Either file order: the reopening names no superclass of its own."""
    declaring = "m.rb"
    (tmp_path / declaring).write_text("class Foo < Base\n  def run\n  end\nend\n")
    (tmp_path / reopen_file).write_text("class Foo\n  def go\n    super\n  end\nend\n")
    assert _super_parents(tmp_path) == {reopen_file: "Base"}


def test_a_reopening_in_another_module_is_another_class(tmp_path: Path) -> None:
    """``B::Foo`` is not the top-level ``Foo`` reopened: it has no superclass."""
    (tmp_path / "a.rb").write_text("class Foo < Base\n  def run\n  end\nend\n")
    (tmp_path / "b.rb").write_text(
        "module B\n  class Foo\n    def go\n      super\n    end\n  end\nend\n")
    assert _super_parents(tmp_path) == {"b.rb": None}


def test_super_in_a_module_method_names_no_superclass(tmp_path: Path) -> None:
    (tmp_path / "m.rb").write_text(
        "class Foo < Base\nend\nmodule Foo2\n  def go\n    super\n  end\nend\n")
    assert _super_parents(tmp_path) == {"m.rb": None}


def test_super_in_class_self_block_names_the_class_superclass(tmp_path: Path) -> None:
    (tmp_path / "s.rb").write_text(
        "class Foo < Base\n  class << self\n    def go\n      super\n    end\n  end\nend\n")
    assert _super_parents(tmp_path) == {"s.rb": "Base"}
