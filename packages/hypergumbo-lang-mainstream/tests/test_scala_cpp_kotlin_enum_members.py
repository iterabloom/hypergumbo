# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-dorop: Scala, C++ and Kotlin enum-member symbols.

The three analyzers the G2 emission-parity matrix cannot see. Each emitted the
enum CONTAINER and none of its members, so the containment linker had nothing
to root and a reverse slice from the enum returned the container alone — the
defect WI-duguk drained for the eight gated languages.

Each language needs its own member naming, because the containment linker
splits on the FIRST of ``#``, ``::``, ``.`` that appears in the name and each
analyzer already picked one for its own fields and methods:

* scala  — ``.``  (``Color.Red``), matching ``f"{owner}.{prop_name}"``
* cpp    — ``::`` (``Color::Red``), matching ``f"{owner_name}::{field_name}"``
* kotlin — ``.``  (``Color.RED``), matching ``f"{enclosing_class}.{prop_name}"``

**Kotlin does not emit ``kind="enum"``.** An ``enum class`` is emitted as
``kind="class"`` with ``"enum"`` among its modifiers, and there is no
``kind="enum"`` anywhere in ``kotlin.py``. That is why the member loop gates on
the presence of an ``enum_class_body`` node rather than on the modifier string
or the kind — and why a query keyed on the enum kind will never surface a
Kotlin enum at all. ``class`` is in both ``containment.CONTAINER_KINDS`` and
the slicer's set, so the members still root; the kind question is a separate
decision this item deliberately does not take.

Per-language body shapes, each verified against the live grammar rather than
assumed:

* scala  ``enum_body`` → ``enum_case_definitions`` → ``simple_enum_case`` /
  ``full_enum_case``. TWO member node types, and ``case Green, Blue`` is ONE
  ``enum_case_definitions`` holding TWO siblings — so the walk iterates cases,
  not case-definition groups.
* cpp    ``enumerator_list`` → ``enumerator``. Scoped (``enum class``) and
  unscoped are the same ``enum_specifier``; the scope keyword is just a token.
* kotlin ``enum_class_body`` → ``enum_entry``. Unlike Swift, each constant is
  its own node, so a per-entry loop is correct here.
"""

from pathlib import Path

from hypergumbo_core.analyze.registry import ensure_discovered, run_analyzer


def _analyze(tmp_path: Path, filename: str, analyzer: str, content: str):
    (tmp_path / filename).write_text(content)
    ensure_discovered()
    return run_analyzer(analyzer, tmp_path)


def _named(result, kind):
    return {s.name for s in result.symbols if s.kind == kind}


def _by_name(result, name):
    return next((s for s in result.symbols if s.name == name), None)


class TestScalaEnumCases:
    """Scala 3 ``enum`` cases, named with ``.`` like scala fields."""

    def test_simple_cases_emit(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.scala", "scala", """
enum Color:
  case Red
  case Green
""")
        fields = _named(result, "field")
        assert "Color.Red" in fields
        assert "Color.Green" in fields

    def test_comma_separated_cases_all_emit(self, tmp_path: Path) -> None:
        """One `enum_case_definitions` node, two `simple_enum_case` siblings."""
        result = _analyze(tmp_path, "Color.scala", "scala", """
enum Color:
  case Green, Blue
""")
        fields = _named(result, "field")
        assert "Color.Green" in fields
        assert "Color.Blue" in fields

    def test_parameterised_case_emits(self, tmp_path: Path) -> None:
        """`case Node(l: Tree)` parses as `full_enum_case`, not `simple_`."""
        result = _analyze(tmp_path, "Tree.scala", "scala", """
enum Tree:
  case Leaf
  case Node(value: Int)
""")
        fields = _named(result, "field")
        assert "Tree.Leaf" in fields
        assert "Tree.Node" in fields

    def test_case_span_is_nested_in_the_enum_span(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.scala", "scala",
                          "enum Color:\n  case Red\n  case Green\n")
        container = _by_name(result, "Color")
        member = _by_name(result, "Color.Red")
        assert container.span is not None and member.span is not None
        assert member.span.start_line >= container.span.start_line
        assert member.span.end_line <= container.span.end_line

    def test_enum_without_cases_emits_no_member(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "E.scala", "scala", "enum Empty:\n  def f: Int = 1\n")
        assert not [
            s for s in result.symbols
            if s.kind == "field" and s.name.startswith("Empty.")
        ]


class TestCppEnumerators:
    """C++ enumerators, named with ``::`` like cpp fields and methods."""

    def test_scoped_enum_members_emit(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "shapes.cpp", "cpp", """
enum class Color { Red, Green, Blue };
""")
        fields = _named(result, "field")
        assert "Color::Red" in fields
        assert "Color::Green" in fields
        assert "Color::Blue" in fields

    def test_unscoped_enum_members_emit(self, tmp_path: Path) -> None:
        """Same `enum_specifier` node; the scope keyword is just a token."""
        result = _analyze(tmp_path, "old.cpp", "cpp", "enum Old { A, B };\n")
        fields = _named(result, "field")
        assert "Old::A" in fields
        assert "Old::B" in fields

    def test_explicitly_valued_member_emits(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "v.cpp", "cpp", "enum class E { A = 1, B = 2 };\n")
        assert "E::A" in _named(result, "field")

    def test_anonymous_enum_emits_no_orphan_members(self, tmp_path: Path) -> None:
        """An anonymous enum emits no CONTAINER, so it must emit no members.

        Emitting them would produce symbols whose dotted owner does not exist —
        orphans by construction, which is worse than the recall miss.
        """
        result = _analyze(tmp_path, "anon.cpp", "cpp",
                          "typedef enum { P, Q } Anon;\n")
        assert not [s for s in result.symbols if s.kind == "field"]

    def test_forward_declaration_emits_nothing(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "fwd.cpp", "cpp", "enum class Color : int;\n")
        assert not _named(result, "field")


class TestKotlinEnumEntries:
    """Kotlin ``enum_entry`` constants, named with ``.`` like kotlin fields.

    Note the container assertions use ``kind="class"``: kotlin emits an
    ``enum class`` as a class carrying an ``enum`` modifier.
    """

    def test_entries_emit(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.kt", "kotlin", """
package com.ex
enum class Color { RED, GREEN }
""")
        fields = _named(result, "field")
        assert "Color.RED" in fields
        assert "Color.GREEN" in fields

    def test_container_is_a_class_with_an_enum_modifier(self, tmp_path: Path) -> None:
        """Pins the fact that defeats any enum-kind-keyed query."""
        result = _analyze(tmp_path, "Color.kt", "kotlin",
                          "package com.ex\nenum class Color { RED }\n")
        container = _by_name(result, "Color")
        assert container is not None
        assert container.kind == "class"
        assert "enum" in (container.modifiers or [])
        assert not _named(result, "enum")

    def test_entry_with_constructor_args_emits(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "P.kt", "kotlin", """
package com.ex
enum class Planet(val mass: Double) { MERCURY(3.3), VENUS(4.8) }
""")
        fields = _named(result, "field")
        assert "Planet.MERCURY" in fields
        assert "Planet.VENUS" in fields

    def test_entry_qualified_name_carries_the_package(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.kt", "kotlin",
                          "package com.ex\nenum class Color { RED }\n")
        member = _by_name(result, "Color.RED")
        assert member is not None
        assert member.qualified_name == "com.ex.Color.RED"

    def test_plain_class_emits_no_enum_members(self, tmp_path: Path) -> None:
        """The structural gate is `enum_class_body`, which a plain class lacks."""
        result = _analyze(tmp_path, "S.kt", "kotlin",
                          "package com.ex\nclass Svc { val x: Int = 1 }\n")
        assert "Svc.x" in _named(result, "field")
        assert not [
            s for s in result.symbols
            if s.kind == "field" and s.name in ("Svc.RED", "Svc.GREEN")
        ]
