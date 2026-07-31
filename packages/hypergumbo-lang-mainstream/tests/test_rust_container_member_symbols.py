# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-duguk: Rust enum-variant and trait-member symbols.

The Rust analyzer emitted the ``enum`` and ``trait`` CONTAINERS but nothing
inside them, so the containment linker had nothing to root and a reverse slice
from either returned the container alone — which a consumer reads as "this enum
is dead" / "this trait has no users". The defect was a missing NODE, not a
missing edge: the linker has handled trait/enum containers since 2026-02-08 and
roots every container whose members exist.

Three distinct gaps, measured rather than assumed:

* an ``enum_variant`` produced no symbol at all;
* a ``function_signature_item`` (a trait method with no default body) produced
  no symbol at all;
* a ``function_item`` inside a ``trait_item`` (a method WITH a default body) DID
  produce a symbol, but as ``kind="function"`` with a BARE name — ``area``, not
  ``Drawable::area`` — indistinguishable from a module-level free function, and
  inconsistent with the ``impl`` path that has always emitted ``Service::run``.

The naming follows what the file already does: ``rust.py`` builds
``f"{impl_target}::{func_name}"`` for impl methods and already keys enum-variant
field types as ``f"{enum_name}::{variant_name}"``. ``::`` is one of the
containment linker's separators, so a member named ``Owner::member`` roots to
its container for free. Variants are ``kind="field"`` — the precedent set by the
D and Nim analyzers, which emit enum members as dotted ``kind="field"`` anchors
for exactly this reason.
"""

from pathlib import Path

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_lang_mainstream.rust import _get_trait_owner, analyze_rust
from hypergumbo_lang_mainstream.rust_scip import _parse_rust_source


def _write(tmp_path: Path, content: str) -> None:
    (tmp_path / "m.rs").write_text(content)


def _named(result, kind):
    return {s.name for s in result.symbols if s.kind == kind}


def _by_name(result, name):
    return next((s for s in result.symbols if s.name == name), None)


class TestEnumVariantSymbols:
    """An enum's variants are emitted as its members."""

    def test_all_three_variant_shapes_emit_a_field(self, tmp_path: Path) -> None:
        """Unit, tuple and struct variants each anchor under the enum."""
        _write(tmp_path, """
pub enum Color {
    Red,
    Green(i32),
    Blue { hue: u8 },
}
""")
        fields = _named(analyze_rust(tmp_path), "field")
        assert "Color::Red" in fields
        assert "Color::Green" in fields
        assert "Color::Blue" in fields

    def test_variant_name_roots_to_the_enum_container(self, tmp_path: Path) -> None:
        """The name's owner segment matches the emitted enum, which is what the
        containment linker splits on (`::` is one of its separators)."""
        _write(tmp_path, "enum Status {\n    Active,\n}\n")
        result = analyze_rust(tmp_path)
        assert "Status" in _named(result, "enum")
        variant = _by_name(result, "Status::Active")
        assert variant is not None
        assert variant.kind == "field"
        assert variant.name.rsplit("::", 1)[0] == "Status"

    def test_variant_span_is_nested_in_the_enum_span(self, tmp_path: Path) -> None:
        """Span nesting is what the G2 parity matrix measures, so pin it here."""
        _write(tmp_path, "enum Status {\n    Active,\n    Done,\n}\n")
        result = analyze_rust(tmp_path)
        enum_sym = _by_name(result, "Status")
        variant = _by_name(result, "Status::Active")
        assert enum_sym.span is not None and variant.span is not None
        assert variant.span.start_line >= enum_sym.span.start_line
        assert variant.span.end_line <= enum_sym.span.end_line

    def test_variant_qualified_name_carries_the_mod_path(self, tmp_path: Path) -> None:
        """A variant inside a `mod` is scoped like every other Rust symbol."""
        _write(tmp_path, """
pub mod shapes {
    pub enum Color {
        Red,
    }
}
""")
        variant = _by_name(analyze_rust(tmp_path), "Color::Red")
        assert variant is not None
        assert variant.qualified_name == "shapes::Color::Red"

    def test_enum_with_no_variants_emits_only_the_container(
        self, tmp_path: Path
    ) -> None:
        """No members must never mean a phantom member."""
        _write(tmp_path, "pub enum Never {}\n")
        result = analyze_rust(tmp_path)
        assert "Never" in _named(result, "enum")
        assert not [s for s in result.symbols if s.kind == "field"]


class TestTraitMemberSymbols:
    """A trait's method members are emitted, and owned by the trait."""

    def test_signature_only_method_is_emitted(self, tmp_path: Path) -> None:
        """`fn draw(&self) -> String;` produced no symbol at all before."""
        _write(tmp_path, """
pub trait Drawable {
    fn draw(&self) -> String;
    fn area(&self) -> f64;
}
""")
        methods = _named(analyze_rust(tmp_path), "method")
        assert "Drawable::draw" in methods
        assert "Drawable::area" in methods

    def test_default_bodied_method_is_a_qualified_method(
        self, tmp_path: Path
    ) -> None:
        """The regression that made a trait method look like a free function.

        This one WAS emitted before — as `kind="function"` named `area` — so a
        test asserting only "a symbol exists inside the trait span" would have
        passed against the bug.
        """
        _write(tmp_path, """
pub trait Drawable {
    fn area(&self) -> f64 { 0.0 }
}
""")
        result = analyze_rust(tmp_path)
        assert "Drawable::area" in _named(result, "method")
        assert "area" not in _named(result, "function")

    def test_signature_method_carries_its_signature(self, tmp_path: Path) -> None:
        """A trait contract's whole content is its signature; dropping it would
        make the new symbol strictly less useful than the impl-side one."""
        _write(tmp_path, "pub trait D {\n    fn draw(&self) -> String;\n}\n")
        method = _by_name(analyze_rust(tmp_path), "D::draw")
        assert method is not None
        assert method.signature

    def test_trait_method_qualified_name_carries_the_mod_path(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, """
pub mod shapes {
    pub trait Drawable {
        fn draw(&self) -> String;
    }
}
""")
        method = _by_name(analyze_rust(tmp_path), "Drawable::draw")
        assert method is not None
        assert method.qualified_name == "shapes::Drawable::draw"

    def test_associated_const_and_type_do_not_become_methods(
        self, tmp_path: Path
    ) -> None:
        """Only callables are members here; a bad walk would sweep these in."""
        _write(tmp_path, """
pub trait Drawable {
    const MAX: i32;
    type Out;
    fn draw(&self) -> String;
}
""")
        methods = _named(analyze_rust(tmp_path), "method")
        assert methods == {"Drawable::draw"}

    def test_trait_with_no_methods_emits_only_the_container(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "pub trait Marker {}\n")
        result = analyze_rust(tmp_path)
        assert "Marker" in _named(result, "trait")
        assert not _named(result, "method")


class TestImplPathUnchanged:
    """Regression guard: the impl-side naming this change deliberately mirrors."""

    def test_inherent_and_trait_impl_methods_keep_the_type_owner(
        self, tmp_path: Path
    ) -> None:
        """An `impl Trait for Type` method is owned by the TYPE, not the trait —
        the distinction the new trait-owner lookup must not blur."""
        _write(tmp_path, """
pub struct Service;

impl Service {
    pub fn run(&self) -> String { String::from("x") }
}

pub trait Drawable {
    fn draw(&self) -> String;
}

impl Drawable for Service {
    fn draw(&self) -> String { String::from("s") }
}
""")
        methods = _named(analyze_rust(tmp_path), "method")
        assert "Service::run" in methods
        assert "Service::draw" in methods
        assert "Drawable::draw" in methods
        assert "Drawable::run" not in methods

    def test_free_function_stays_an_unqualified_function(
        self, tmp_path: Path
    ) -> None:
        """A module-level `fn` must not acquire an owner."""
        _write(tmp_path, "pub fn helper(v: i32) -> i32 { v }\n")
        result = analyze_rust(tmp_path)
        assert "helper" in _named(result, "function")
        assert not _named(result, "method")


class TestTraitOwnerLookup:
    """`_get_trait_owner` directly — its impl-stops-the-walk contract.

    Exercised white-box because the production caller short-circuits it:
    ``owner = impl_target or _get_trait_owner(...)`` never consults the trait
    lookup when an impl target was already found. The guard is still
    load-bearing rather than decorative — it is what keeps a trait from
    claiming the methods of every type that implements it — so it is pinned
    here rather than marked defensive and left unmeasured.
    """

    @staticmethod
    def _owner_of(source: str, node_type: str) -> object:
        """Resolve the trait owner of the first `node_type` node in `source`."""
        raw = source.encode("utf-8")
        tree = _parse_rust_source(raw)
        assert tree is not None, "tree-sitter-rust unavailable"
        node = next(
            (n for n in iter_tree(tree.root_node) if n.type == node_type), None
        )
        assert node is not None, f"no {node_type} in sample"
        return _get_trait_owner(node, raw)

    def test_impl_block_stops_the_walk(self) -> None:
        """A method inside `impl Trait for Type` has NO trait owner.

        The impl-side method is the only `function_item` here (the trait's own
        member is a `function_signature_item`), so this pins the impl branch.
        """
        source = (
            "pub trait Drawable {\n"
            "    fn draw(&self) -> String;\n"
            "}\n"
            "pub struct Service;\n"
            "impl Drawable for Service {\n"
            '    fn draw(&self) -> String { String::from("s") }\n'
            "}\n"
        )
        assert self._owner_of(source, "function_item") is None

    def test_trait_body_yields_the_trait_name(self) -> None:
        """The positive control for the same helper."""
        source = "pub trait Drawable {\n    fn draw(&self) -> String;\n}\n"
        assert self._owner_of(source, "function_signature_item") == "Drawable"

    def test_free_function_has_no_trait_owner(self) -> None:
        """Walking to the root without a container yields None."""
        assert self._owner_of("pub fn helper() -> i32 { 1 }\n", "function_item") is None
