# SPDX-License-Identifier: AGPL-3.0-or-later
"""C++ classes record which members are virtual, overriding or static (WI-digaj).

``type_hierarchy`` decides C++ dispatch per method, and the keywords that
decide it live only on the IN-CLASS declaration: an out-of-line definition
(``int Square::area() { ... }``, usually in a ``.cc`` apart from its header)
carries no ``virtual`` / ``override`` / ``static``. So the class symbol, which
the header produces, records them by member name, and an inline in-class
definition also carries its own keywords as modifiers.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp

_SRC = """\
class Shape {
 public:
  virtual int area() = 0;
  virtual int peri();
  int describe();
  static Shape* Create();
  static int count;
  virtual const Shape& self() const;
  virtual int vinl() { return 1; }
  int inl() { return 0; }
};
struct Square : public Shape {
  int area() override;
  int peri() final;
  int describe();
  static Shape* Create();
  int vinl() override { return 2; }
  static int helper() { return 3; }
};
class Empty {};
"""


def _symbols(tmp_path: Path) -> dict[str, object]:
    # The real layout: declarations in a header, definitions in a ``.cc``. A
    # ``.h`` with no C++ sibling is read as C, so the ``.cc`` is load-bearing.
    (tmp_path / "shape.h").write_text(_SRC)
    (tmp_path / "shape.cc").write_text(
        '#include "shape.h"\nint Shape::peri() { return 0; }\n'
    )
    return {s.name: s for s in analyze_cpp(tmp_path).symbols}


def test_a_class_records_its_member_dispatch_keywords(tmp_path: Path) -> None:
    syms = _symbols(tmp_path)
    shape = syms["Shape"].meta or {}
    assert shape["virtual_members"] == ["area", "peri", "self", "vinl"]
    assert shape["static_members"] == ["Create"], "a static DATA member is not a method"
    assert "override_members" not in shape
    square = syms["Square"].meta or {}
    assert square["virtual_members"] == ["area", "peri", "vinl"]
    assert square["override_members"] == ["area", "peri", "vinl"]
    assert square["static_members"] == ["Create", "helper"]
    assert square["base_classes"] == ["Shape"], "existing meta is kept"


def test_a_class_with_no_such_members_records_nothing(tmp_path: Path) -> None:
    assert _symbols(tmp_path)["Empty"].meta is None


def test_an_inline_definition_carries_its_keywords(tmp_path: Path) -> None:
    syms = _symbols(tmp_path)
    assert "virtual" in syms["Shape::vinl"].modifiers
    assert "override" in syms["Square::vinl"].modifiers
    assert "static" in syms["Square::helper"].modifiers
    assert not {"virtual", "override", "static"} & set(syms["Shape::inl"].modifiers or [])
    # An out-of-line definition has no keywords of its own; the class records them.
    assert not {"virtual", "override", "static"} & set(syms["Shape::peri"].modifiers or [])
