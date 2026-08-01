# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edge cases of the shared ``constructed_from_callee`` helper (WI-nopod).

Every branch here answers the same question — *when should the key be
absent?* — and the answer is always "whenever the callee is not a static
name". A framework YAML keying on ``constructed_from`` matches by regex, so
a partial or invented value would match a fiction and fail **silently**.
These are the shapes where silence is the correct output, exercised through
real source rather than by constructing AST nodes by hand.
"""
from __future__ import annotations

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.base import constructed_from_callee


def _value_node(language: str, source: str, path: tuple[str, ...]):
    """Parse *source* and walk *path* (node types) to the value expression."""
    tree = tlp.get_parser(language).parse(source.encode())
    node = tree.root_node
    for wanted in path:
        node = next(c for c in node.children if c.type == wanted)
    return node


def test_non_call_initializer_yields_nothing() -> None:
    """`var n = 3` is not a construction. Guards the key's usefulness as a
    filter — a key present on every variable filters nothing."""
    literal = _value_node(
        "go", "package main\n\nvar n = 3\n",
        ("var_declaration", "var_spec", "expression_list", "int_literal"),
    )
    assert constructed_from_callee(literal, b"") is None


def test_none_value_node_yields_nothing() -> None:
    """A declaration with no initializer at all (`var x int`)."""
    assert constructed_from_callee(None, b"") is None


def test_computed_callee_yields_nothing() -> None:
    """`registry[k]()` has no static name — record nothing, not a guess."""
    src = "package main\n\nvar app = registry[key]()\n"
    call = _value_node(
        "go", src,
        ("var_declaration", "var_spec", "expression_list", "call_expression"),
    )
    assert constructed_from_callee(call, src.encode()) is None


def test_qualified_callee_is_kept_whole() -> None:
    """`pkg.New()` records `pkg.New`, never the bare `New`.

    Stripping the qualifier would make a namespaced framework callee
    indistinguishable from a same-named local, which is unrecoverable
    downstream rather than merely lossy.
    """
    src = "package main\n\nvar app = pkg.New()\n"
    call = _value_node(
        "go", src,
        ("var_declaration", "var_spec", "expression_list", "call_expression"),
    )
    assert constructed_from_callee(call, src.encode()) == "pkg.New"


def test_unnamed_callee_field_falls_back_to_a_name_shaped_child() -> None:
    """Swift's `call_expression` names no fields at all.

    Its children are `(simple_identifier, call_suffix)`, so the field lookup
    finds nothing and the helper falls back to a leading name-shaped child.
    This is the branch that made Swift work without special-casing it.
    """
    src = "let app = C()\n"
    call = _value_node(
        "swift", src, ("property_declaration", "call_expression"),
    )
    assert constructed_from_callee(call, src.encode()) == "C"


def test_scoped_rust_path_is_normalised_but_kept() -> None:
    """`C::new()` records `C::new` — the callee as written.

    Rust's construction idiom is an associated function, so the recorded
    value legitimately differs from Go's or Python's. The parity column
    carries a per-language expectation for exactly this reason.
    """
    src = "pub static A: C = C::new();\n"
    call = _value_node("rust", src, ("static_item", "call_expression"))
    assert constructed_from_callee(call, src.encode()) == "C::new"


def test_non_name_shaped_first_child_yields_nothing() -> None:
    """Swift's `(f)()` — the fallback's first child is a tuple, not a name.

    The fallback exists because Swift's grammar names no callee field; this
    pins that it cannot therefore mistake an arbitrary leading node for a
    callee. `(f)()` parses with a `tuple_expression` first child.
    """
    src = "let x = (f)()\n"
    call = _value_node("swift", src, ("property_declaration", "call_expression"))
    assert call.children[0].type == "tuple_expression"
    assert constructed_from_callee(call, src.encode()) is None
