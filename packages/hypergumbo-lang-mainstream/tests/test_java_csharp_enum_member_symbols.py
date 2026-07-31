# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-duguk: Java and C# enum-constant symbols — the last two parity holes.

Both analyzers emitted the ``enum`` CONTAINER but none of its constants, so the
containment linker had nothing to root and a reverse slice from an enum returned
the container alone. Java additionally emitted an enum's *methods*
(``Color.label``), which is precisely what made the gap easy to miss: an enum
with one method already looks populated to any "does this container have a
member" probe while every constant is invisible.

Both languages are covered by one file because the change is the same shape in
each — a named constant in the container's body body becomes a ``kind="field"``
symbol named ``Color.RED``, matching each analyzer's existing class-field
naming. Only the node types differ: Java parses constants as ``enum_constant``
under an ``enum_body``, C# as ``enum_member_declaration`` under an
``enum_member_declaration_list``.

Java's ``enum_body`` also holds an ``enum_body_declarations`` child carrying the
enum's methods and fields; the walk reads only its direct ``enum_constant``
children, so those keep flowing through their existing branches.
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


class TestJavaEnumConstants:
    """Java ``enum_constant`` members."""

    def test_constants_emit_as_fields(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.java", "java", """
public enum Color {
    RED,
    GREEN,
    BLUE
}
""")
        fields = _named(result, "field")
        assert "Color.RED" in fields
        assert "Color.GREEN" in fields
        assert "Color.BLUE" in fields

    def test_constant_with_arguments_emits(self, tmp_path: Path) -> None:
        """`RED("r")` carries an argument_list after the identifier."""
        result = _analyze(tmp_path, "Color.java", "java", """
public enum Color {
    RED("r"),
    GREEN("g");

    private final String code;
    Color(String code) { this.code = code; }
}
""")
        fields = _named(result, "field")
        assert "Color.RED" in fields
        assert "Color.GREEN" in fields

    def test_enum_methods_still_emit(self, tmp_path: Path) -> None:
        """Regression: the members that already worked keep working.

        This pairing is what hid the gap — an enum with a method looked healthy
        while its constants were missing.
        """
        result = _analyze(tmp_path, "Color.java", "java", """
public enum Color {
    RED;
    public String label() { return name(); }
}
""")
        assert "Color.label" in _named(result, "method")
        assert "Color.RED" in _named(result, "field")

    def test_constant_span_is_nested_in_the_enum_span(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.java", "java", """
public enum Color {
    RED,
    GREEN
}
""")
        container = _by_name(result, "Color")
        member = _by_name(result, "Color.RED")
        assert container.span is not None and member.span is not None
        assert member.span.start_line >= container.span.start_line
        assert member.span.end_line <= container.span.end_line

    def test_empty_enum_emits_no_field(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "E.java", "java", "public enum E {}\n")
        assert "E" in _named(result, "enum")
        assert not [
            s for s in result.symbols
            if s.kind == "field" and s.name.startswith("E.")
        ]

    def test_class_fields_unaffected(self, tmp_path: Path) -> None:
        """Regression guard on the branch this mirrors."""
        result = _analyze(tmp_path, "Sq.java", "java", """
class Sq {
    int side;
    public String draw() { return "s"; }
}
""")
        assert "Sq.side" in _named(result, "field")
        assert "Sq.draw" in _named(result, "method")


class TestCSharpEnumMembers:
    """C# ``enum_member_declaration`` members."""

    def test_members_emit_as_fields(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.cs", "csharp", """
public enum Color
{
    Red,
    Green,
    Blue,
}
""")
        fields = _named(result, "field")
        assert "Color.Red" in fields
        assert "Color.Green" in fields
        assert "Color.Blue" in fields

    def test_explicitly_valued_member_emits(self, tmp_path: Path) -> None:
        """`Green = 2` carries the literal after the identifier."""
        result = _analyze(tmp_path, "Color.cs", "csharp", """
public enum Color
{
    Red = 1,
    Green = 2,
}
""")
        fields = _named(result, "field")
        assert "Color.Red" in fields
        assert "Color.Green" in fields

    def test_member_span_is_nested_in_the_enum_span(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Color.cs", "csharp", """
public enum Color
{
    Red,
    Green,
}
""")
        container = _by_name(result, "Color")
        member = _by_name(result, "Color.Red")
        assert container.span is not None and member.span is not None
        assert member.span.start_line >= container.span.start_line
        assert member.span.end_line <= container.span.end_line

    def test_empty_enum_emits_no_field(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "E.cs", "csharp", "public enum E { }\n")
        assert "E" in _named(result, "enum")
        assert not [
            s for s in result.symbols
            if s.kind == "field" and s.name.startswith("E.")
        ]

    def test_class_fields_unaffected(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "Sq.cs", "csharp", """
public class Sq
{
    public int Side;
    public string Draw() { return "s"; }
}
""")
        assert "Sq.Side" in _named(result, "field")
        assert "Sq.Draw" in _named(result, "method")
