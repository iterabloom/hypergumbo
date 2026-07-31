# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-duguk: TypeScript enum-member and interface-member symbols.

The JS/TS analyzer emitted the ``enum`` and ``interface`` CONTAINERS but nothing
inside them, while emitting class members (``Sq.side``, ``Sq.draw``) correctly.
Without member symbols the containment linker has nothing to root, so a reverse
slice from an enum or an interface returns the container alone — which a
consumer reads as "this type has no users".

Shape follows the analyzer's own class-member branches, which name members
``Owner.member`` with ``.`` (the JS/TS separator the containment linker splits
on) rather than Rust's ``::``:

* an ``enum_assignment`` (``Red = 'red'``) or a bare ``property_identifier``
  (``Green``) in an ``enum_body`` -> ``kind="field"`` named ``Color.Red``;
* a ``method_signature`` in an ``interface_body`` -> ``kind="method"``;
* a ``property_signature`` -> ``kind="field"``, mirroring the class
  ``public_field_definition`` branch.

Two members of an ``interface_body`` are deliberately NOT emitted:
``construct_signature`` (``new (x: number): D``) and ``index_signature``
(``[key: string]: number``) carry no ``property_identifier``, so there is no
name to anchor. Skipping them is a fails-safe recall miss; inventing a name
would be a wrong-name phantom, which is worse.
"""

from pathlib import Path

from hypergumbo_core.analyze.registry import ensure_discovered, run_analyzer


def _analyze(tmp_path: Path, content: str):
    (tmp_path / "m.ts").write_text(content)
    ensure_discovered()
    return run_analyzer("javascript", tmp_path)


def _named(result, kind):
    return {s.name for s in result.symbols if s.kind == kind}


def _by_name(result, name):
    return next((s for s in result.symbols if s.name == name), None)


class TestEnumMemberSymbols:
    """A TypeScript enum's members are emitted as its members."""

    def test_initialized_and_bare_members_both_emit(self, tmp_path: Path) -> None:
        """`Red = 'red'` parses as enum_assignment, bare `Blue` does not."""
        result = _analyze(tmp_path, """
export enum Color {
    Red = 'red',
    Green = 'green',
    Blue,
}
""")
        fields = _named(result, "field")
        assert "Color.Red" in fields
        assert "Color.Green" in fields
        assert "Color.Blue" in fields

    def test_member_roots_to_the_enum_container(self, tmp_path: Path) -> None:
        """The owner segment is what the containment linker splits on."""
        result = _analyze(tmp_path, "export enum Status {\n    Active,\n}\n")
        assert "Status" in _named(result, "enum")
        member = _by_name(result, "Status.Active")
        assert member is not None
        assert member.kind == "field"

    def test_member_span_is_nested_in_the_enum_span(self, tmp_path: Path) -> None:
        """Span nesting is what the G2 parity column measures."""
        result = _analyze(tmp_path, "export enum Status {\n    Active,\n    Done,\n}\n")
        container = _by_name(result, "Status")
        member = _by_name(result, "Status.Active")
        assert container.span is not None and member.span is not None
        assert member.span.start_line >= container.span.start_line
        assert member.span.end_line <= container.span.end_line

    def test_const_enum_members_emit(self, tmp_path: Path) -> None:
        """`const enum` is the same node type with an extra modifier."""
        result = _analyze(tmp_path, "export const enum E {\n    A,\n}\n")
        assert "E.A" in _named(result, "field")

    def test_empty_enum_emits_no_member(self, tmp_path: Path) -> None:
        """No members must never mean a phantom member."""
        result = _analyze(tmp_path, "export enum Empty {}\n")
        assert "Empty" in _named(result, "enum")
        assert not [
            s for s in result.symbols
            if s.kind == "field" and s.name.startswith("Empty.")
        ]


class TestInterfaceMemberSymbols:
    """A TypeScript interface's members are emitted and owned by it."""

    def test_method_signature_emits_a_method(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """
export interface Drawable {
    draw(): string;
    area(): number;
}
""")
        methods = _named(result, "method")
        assert "Drawable.draw" in methods
        assert "Drawable.area" in methods

    def test_property_signature_emits_a_field(self, tmp_path: Path) -> None:
        """Mirrors the class `public_field_definition` branch."""
        result = _analyze(tmp_path, """
export interface Drawable {
    readonly label: string;
    size: number;
}
""")
        fields = _named(result, "field")
        assert "Drawable.label" in fields
        assert "Drawable.size" in fields

    def test_method_signature_carries_its_signature(self, tmp_path: Path) -> None:
        """An interface member's whole content is its declared shape."""
        result = _analyze(tmp_path, "export interface D {\n    draw(): string;\n}\n")
        method = _by_name(result, "D.draw")
        assert method is not None
        assert method.signature

    def test_nameless_signatures_are_skipped(self, tmp_path: Path) -> None:
        """`construct_signature` / `index_signature` carry no name to anchor."""
        result = _analyze(tmp_path, """
export interface Registry {
    new (x: number): Registry;
    [key: string]: unknown;
    lookup(k: string): unknown;
}
""")
        assert "Registry.lookup" in _named(result, "method")
        owned = {
            s.name for s in result.symbols
            if s.name.startswith("Registry.")
        }
        assert owned == {"Registry.lookup"}

    def test_empty_interface_emits_no_member(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, "export interface Marker {}\n")
        assert "Marker" in _named(result, "interface")
        assert not [
            s for s in result.symbols if s.name.startswith("Marker.")
        ]


class TestClassPathUnchanged:
    """Regression guard: the class-member branches this change mirrors."""

    def test_class_members_keep_their_owner_and_kinds(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """
export class Sq {
    side: number = 0;
    draw(): string { return 's'; }
}
""")
        assert "Sq.side" in _named(result, "field")
        assert "Sq.draw" in _named(result, "method")

    def test_interface_and_class_members_do_not_cross_owners(
        self, tmp_path: Path
    ) -> None:
        """An implementing class must not absorb the interface's members, and
        vice versa — the two containers sit in the same file with the same
        member names, which is exactly where a span-based owner lookup slips."""
        result = _analyze(tmp_path, """
export interface Drawable {
    draw(): string;
}

export class Sq implements Drawable {
    draw(): string { return 's'; }
}
""")
        methods = _named(result, "method")
        assert "Drawable.draw" in methods
        assert "Sq.draw" in methods
