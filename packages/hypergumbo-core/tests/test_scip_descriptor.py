# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SCIP symbol-string descriptor parser.

The grammar being enforced is the one defined by Sourcegraph's SCIP
specification (see scip.proto ``Symbol`` comment): a symbol string is
either ``local <id>`` or ``<scheme> <manager> <package-name> <version>
<descriptor>+``, where each descriptor carries a name and a suffix
character that encodes its kind.

These tests pin down the observable output of :func:`parse_scip_symbol`
so that downstream translation passes (WI-mafut later phases) can rely
on a fixed dataclass shape. The rust-analyzer mini-trial under WI-zakub
showed that trait dispatch surfaces inside the descriptor chain rather
than in the SCIP Relationship field, which is why backtick-quoted names,
method disambiguators, and mixed descriptor chains all need explicit
coverage.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.scip.descriptor import (
    DescriptorKind,
    ScipDescriptor,
    ScipSymbol,
    parse_scip_symbol,
)


def test_local_symbol() -> None:
    sym = parse_scip_symbol("local 4")
    assert sym.is_local
    assert sym.local_id == "4"
    assert sym.scheme == ""
    assert sym.descriptors == ()


def test_local_symbol_with_alphanumeric_id() -> None:
    sym = parse_scip_symbol("local a1b2")
    assert sym.is_local
    assert sym.local_id == "a1b2"


def test_simple_namespace_and_type() -> None:
    sym = parse_scip_symbol("rust-analyzer cargo my_crate 1.0.0 foo/Bar#")
    assert not sym.is_local
    assert sym.scheme == "rust-analyzer"
    assert sym.manager == "cargo"
    assert sym.package_name == "my_crate"
    assert sym.package_version == "1.0.0"
    assert sym.descriptors == (
        ScipDescriptor(name="foo", kind=DescriptorKind.NAMESPACE),
        ScipDescriptor(name="Bar", kind=DescriptorKind.TYPE),
    )


def test_term_descriptor() -> None:
    sym = parse_scip_symbol("scip-python pypi pkg 1.0 mod/const.")
    assert sym.descriptors[-1] == ScipDescriptor(name="const", kind=DescriptorKind.TERM)


def test_meta_descriptor() -> None:
    sym = parse_scip_symbol("scheme mgr pkg 1 x:")
    assert sym.descriptors[-1] == ScipDescriptor(name="x", kind=DescriptorKind.META)


def test_macro_descriptor() -> None:
    sym = parse_scip_symbol("rust-analyzer cargo c 0.1 println!")
    assert sym.descriptors[-1] == ScipDescriptor(name="println", kind=DescriptorKind.MACRO)


def test_method_with_disambiguator() -> None:
    sym = parse_scip_symbol("scheme m p 1 T#foo(+1).")
    assert sym.descriptors == (
        ScipDescriptor(name="T", kind=DescriptorKind.TYPE),
        ScipDescriptor(
            name="foo", kind=DescriptorKind.METHOD, disambiguator="+1"
        ),
    )


def test_method_without_disambiguator() -> None:
    sym = parse_scip_symbol("scheme m p 1 T#foo().")
    assert sym.descriptors[-1] == ScipDescriptor(
        name="foo", kind=DescriptorKind.METHOD, disambiguator=""
    )


def test_type_parameter() -> None:
    sym = parse_scip_symbol("scheme m p 1 C#[T]")
    assert sym.descriptors[-1] == ScipDescriptor(name="T", kind=DescriptorKind.TYPE_PARAMETER)


def test_parameter() -> None:
    sym = parse_scip_symbol("scheme m p 1 f().(x)")
    assert sym.descriptors == (
        ScipDescriptor(name="f", kind=DescriptorKind.METHOD, disambiguator=""),
        ScipDescriptor(name="x", kind=DescriptorKind.PARAMETER),
    )


def test_backtick_quoted_name_with_spaces() -> None:
    sym = parse_scip_symbol("scheme m p 1 `Has Spaces`#")
    assert sym.descriptors == (
        ScipDescriptor(name="Has Spaces", kind=DescriptorKind.TYPE),
    )


def test_backtick_quoted_name_with_suffix_chars() -> None:
    sym = parse_scip_symbol("scheme m p 1 `foo/bar#baz`.")
    assert sym.descriptors == (
        ScipDescriptor(name="foo/bar#baz", kind=DescriptorKind.TERM),
    )


def test_backtick_escape() -> None:
    sym = parse_scip_symbol("scheme m p 1 `embed``tick`#")
    assert sym.descriptors == (
        ScipDescriptor(name="embed`tick", kind=DescriptorKind.TYPE),
    )


def test_trait_dispatch_shape_impl() -> None:
    """WI-zakub §1: rust-analyzer encodes trait dispatch in the descriptor chain."""
    sym = parse_scip_symbol(
        "rust-analyzer cargo c 0.1 impl#[T][Trait]method()."
    )
    assert sym.descriptors == (
        ScipDescriptor(name="impl", kind=DescriptorKind.TYPE),
        ScipDescriptor(name="T", kind=DescriptorKind.TYPE_PARAMETER),
        ScipDescriptor(name="Trait", kind=DescriptorKind.TYPE_PARAMETER),
        ScipDescriptor(name="method", kind=DescriptorKind.METHOD, disambiguator=""),
    )


def test_leaf_helpers() -> None:
    sym = parse_scip_symbol("scheme m p 1 foo/Bar#baz().")
    assert sym.leaf is not None
    assert sym.leaf.name == "baz"
    assert sym.leaf.kind == DescriptorKind.METHOD
    assert sym.container_names() == ("foo", "Bar")


def test_leaf_on_local_returns_none() -> None:
    sym = parse_scip_symbol("local 1")
    assert sym.leaf is None
    assert sym.container_names() == ()


def test_space_escape_in_scheme() -> None:
    """SCIP escapes single spaces inside the scheme / package fields with a double space."""
    sym = parse_scip_symbol("my  scheme cargo pkg 1.0 x#")
    assert sym.scheme == "my scheme"


def test_empty_string_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_scip_symbol("")


def test_missing_descriptor_rejected() -> None:
    with pytest.raises(ValueError, match="descriptor"):
        parse_scip_symbol("scheme mgr pkg 1.0 ")


def test_unterminated_backtick_rejected() -> None:
    with pytest.raises(ValueError, match="backtick"):
        parse_scip_symbol("scheme m p 1 `never-closes")


def test_unterminated_method_rejected() -> None:
    with pytest.raises(ValueError, match="method"):
        parse_scip_symbol("scheme m p 1 foo(+1)")


def test_unterminated_type_parameter_rejected() -> None:
    with pytest.raises(ValueError, match="type parameter"):
        parse_scip_symbol("scheme m p 1 C#[T")


def test_unterminated_parameter_rejected() -> None:
    with pytest.raises(ValueError, match="parameter"):
        parse_scip_symbol("scheme m p 1 f().(x")


def test_header_truncated_inside_field() -> None:
    with pytest.raises(ValueError, match="package"):
        parse_scip_symbol("scheme mgr pkg")


def test_header_truncated_at_field_boundary() -> None:
    """String ending right at a field separator triggers the pos==len guard."""
    with pytest.raises(ValueError, match="package"):
        parse_scip_symbol("a b c ")


def test_unknown_suffix_character_rejected() -> None:
    with pytest.raises(ValueError, match="unknown suffix"):
        parse_scip_symbol("scheme m p 1 )")


def test_missing_descriptor_suffix_rejected() -> None:
    with pytest.raises(ValueError, match="suffix"):
        parse_scip_symbol("scheme m p 1 bare_name")


def test_missing_package_fields_rejected() -> None:
    with pytest.raises(ValueError, match="package"):
        parse_scip_symbol("scheme-only")


def test_local_requires_id() -> None:
    with pytest.raises(ValueError, match="local"):
        parse_scip_symbol("local ")


def test_symbol_roundtrips_via_dataclass_equality() -> None:
    a = parse_scip_symbol("scheme m p 1 foo/Bar#")
    b = ScipSymbol(
        scheme="scheme",
        manager="m",
        package_name="p",
        package_version="1",
        descriptors=(
            ScipDescriptor(name="foo", kind=DescriptorKind.NAMESPACE),
            ScipDescriptor(name="Bar", kind=DescriptorKind.TYPE),
        ),
    )
    assert a == b
