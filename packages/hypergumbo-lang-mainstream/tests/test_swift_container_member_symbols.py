# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-duguk: Swift enum-case and protocol-requirement symbols.

The Swift analyzer emitted an enum's METHODS and computed PROPERTIES
(``Color.label``, ``Color.name``) but never its **cases**, and emitted nothing
at all inside a ``protocol``. Without member symbols the containment linker has
nothing to root, so a reverse slice from either returns the container alone.

The enum half is a worked example of why "the container has a member" is not a
safe check: an enum carrying one method already looked healthy on a span-nesting
probe while every one of its cases was invisible. The parity fixture's enum
declares cases only, which is what makes its cell measure case emission.

Shape follows the analyzer's existing type-member branches, which name members
``Owner.member`` and distinguish Swift's two value-member kinds — ``field`` for
a stored property, ``property`` for a computed one:

* an ``enum_entry`` -> one ``kind="field"`` per name it declares;
* a ``protocol_function_declaration`` -> ``kind="method"``;
* a ``protocol_property_declaration`` -> ``kind="property"``, since a protocol
  property requirement is a computed-access contract (``{ get }``), never
  storage.

``case green, blue`` is a SINGLE ``enum_entry`` node carrying TWO
``simple_identifier`` children, so the walk emits per identifier rather than
per entry — a per-entry loop would silently drop every case after the first
comma.
"""

from pathlib import Path

from hypergumbo_core.analyze.registry import ensure_discovered, run_analyzer


def _analyze(tmp_path: Path, content: str):
    (tmp_path / "m.swift").write_text(content)
    ensure_discovered()
    return run_analyzer("swift", tmp_path)


def _named(result, kind):
    return {s.name for s in result.symbols if s.kind == kind}


def _by_name(result, name):
    return next((s for s in result.symbols if s.name == name), None)


class TestEnumCaseSymbols:
    """An enum's cases are emitted as its members."""

    def test_simple_cases_emit(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """
public enum Color {
    case red
    case green
}
""")
        fields = _named(result, "field")
        assert "Color.red" in fields
        assert "Color.green" in fields

    def test_comma_separated_entry_emits_every_name(self, tmp_path: Path) -> None:
        """One `enum_entry`, two names — the case a per-entry loop drops."""
        result = _analyze(tmp_path, "enum Color {\n    case green, blue\n}\n")
        fields = _named(result, "field")
        assert "Color.green" in fields
        assert "Color.blue" in fields

    def test_associated_value_case_emits(self, tmp_path: Path) -> None:
        """`case rgb(Int, Int)` carries enum_type_parameters after the name."""
        result = _analyze(tmp_path, "enum Color {\n    case rgb(Int, Int)\n}\n")
        assert "Color.rgb" in _named(result, "field")

    def test_case_span_is_nested_in_the_enum_span(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "enum Color {\n    case red\n    case green\n}\n")
        container = _by_name(result, "Color")
        member = _by_name(result, "Color.red")
        assert container.span is not None and member.span is not None
        assert member.span.start_line >= container.span.start_line
        assert member.span.end_line <= container.span.end_line

    def test_enum_without_cases_emits_no_field(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "enum Empty {\n    func f() -> Int { return 1 }\n}\n")
        assert "Empty" in _named(result, "enum")
        assert not [
            s for s in result.symbols
            if s.kind == "field" and s.name.startswith("Empty.")
        ]

    def test_enum_methods_and_properties_still_emit(self, tmp_path: Path) -> None:
        """Regression: the members that already worked must keep working.

        This is the pairing that made the gap invisible — an enum with a method
        looked healthy while its cases were missing.
        """
        result = _analyze(tmp_path, """
enum Color {
    case red
    func label() -> String { return "c" }
    var name: String { return "n" }
}
""")
        assert "Color.label" in _named(result, "method")
        assert "Color.name" in _named(result, "property")
        assert "Color.red" in _named(result, "field")


class TestProtocolRequirementSymbols:
    """A protocol's requirements are emitted and owned by it."""

    def test_function_requirement_emits_a_method(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """
public protocol Drawable {
    func draw() -> String
    func area() -> Double
}
""")
        methods = _named(result, "method")
        assert "Drawable.draw" in methods
        assert "Drawable.area" in methods

    def test_property_requirement_emits_a_property(self, tmp_path: Path) -> None:
        """`{ get }` is a computed-access contract, never storage."""
        result = _analyze(tmp_path, """
public protocol Drawable {
    var label: String { get }
    var size: Int { get set }
}
""")
        props = _named(result, "property")
        assert "Drawable.label" in props
        assert "Drawable.size" in props

    def test_function_requirement_carries_its_signature(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "protocol D {\n    func draw() -> String\n}\n")
        method = _by_name(result, "D.draw")
        assert method is not None
        assert method.signature

    def test_empty_protocol_emits_no_member(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "protocol Marker {}\n")
        assert "Marker" in _named(result, "protocol")
        assert not [s for s in result.symbols if s.name.startswith("Marker.")]


class TestTypePathUnchanged:
    """Regression guard: the struct/class member branches this mirrors."""

    def test_struct_members_keep_their_owner_and_kinds(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """
public struct Sq {
    public var side: Double = 0
    public func draw() -> String { return "s" }
    public var label: String { return "l" }
}
""")
        assert "Sq.side" in _named(result, "field")
        assert "Sq.draw" in _named(result, "method")
        assert "Sq.label" in _named(result, "property")

    def test_protocol_and_conforming_type_do_not_cross_owners(
        self, tmp_path: Path
    ) -> None:
        """Same member names in one file, two containers — where a span-based
        owner lookup slips."""
        result = _analyze(tmp_path, """
protocol Drawable {
    func draw() -> String
}

struct Sq: Drawable {
    func draw() -> String { return "s" }
}
""")
        methods = _named(result, "method")
        assert "Drawable.draw" in methods
        assert "Sq.draw" in methods
