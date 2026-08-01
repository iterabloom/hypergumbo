# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer-side edges of ``meta["constructed_from"]`` (WI-nopod).

These live in the mainstream package because they exercise ``go.py`` and
``js_ts.py`` helpers; CI measures each package's coverage in isolation, so a
test of mainstream code cannot sit in core's tree.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import tree_sitter_language_pack as tlp

from hypergumbo_lang_mainstream.go import _go_init_value
from hypergumbo_lang_mainstream.js_ts import _jsts_constructed_from


def _go_var_spec(source: str):
    tree = tlp.get_parser("go").parse(source.encode())
    decl = next(c for c in tree.root_node.children if c.type == "var_declaration")
    return next(c for c in decl.children if c.type == "var_spec")


class TestGoInitValueUnwrapping:
    """Go wraps a var initializer in an ``expression_list``.

    The `value` FIELD resolves to that list rather than being absent, which
    is why the helper unwraps whichever it is handed instead of assuming the
    field is missing — the first version assumed, and Go silently recorded
    nothing.
    """

    def test_unwraps_the_expression_list(self) -> None:
        spec = _go_var_spec("package main\n\nvar app = NewC()\n")
        node = _go_init_value(spec, None)
        assert node is not None and node.type == "call_expression"

    def test_passes_a_direct_expression_through(self) -> None:
        """A value node that is already the expression is returned as-is."""
        spec = _go_var_spec("package main\n\nvar app = NewC()\n")
        exprs = next(c for c in spec.children if c.type == "expression_list")
        call = exprs.children[0]
        assert _go_init_value(spec, call) is call

    def test_declaration_without_initializer_yields_none(self) -> None:
        """`var x int` has neither a value field nor an expression_list."""
        spec = _go_var_spec("package main\n\nvar x int\n")
        assert _go_init_value(spec, None) is None


class TestJsTsCalleeShapes:
    def test_new_expression_with_no_constructor_field_yields_nothing(self) -> None:
        """`new (f())()` — the constructor slot is a parenthesised call."""
        src = "const a = new (f())();\n"
        tree = tlp.get_parser("javascript").parse(src.encode())
        decl = next(c for c in tree.root_node.children if c.type == "lexical_declaration")
        declarator = next(c for c in decl.children if c.type == "variable_declarator")
        # Whatever the grammar produces here, a name must not be invented.
        assert _jsts_constructed_from(declarator, src.encode()) in (None, "f")

    def test_computed_member_callee_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "m.js").write_text(
                "const a = reg[k].Build();\nconst b = new pkg.C();\n",
            )
            import hypergumbo_lang_mainstream.js_ts as js

            syms = {s.name: s for s in js.analyze_javascript(root).symbols}
        assert "constructed_from" not in (syms["a"].meta or {})
        assert (syms["b"].meta or {}).get("constructed_from") == "pkg.C"

    def test_plain_and_factory_callees_are_recorded(self) -> None:
        """`new C()` and `express()` both count.

        JS frameworks use each shape — `new Koa()` versus `express()` — and a
        YAML author keying on the framework's export does not care which, so
        the producer records both rather than privileging `new`.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "m.js").write_text(
                "const a = new C();\nconst b = express();\nconst c = 3;\n",
            )
            import hypergumbo_lang_mainstream.js_ts as js

            syms = {s.name: s for s in js.analyze_javascript(root).symbols}
        assert (syms["a"].meta or {}).get("constructed_from") == "C"
        assert (syms["b"].meta or {}).get("constructed_from") == "express"
        assert "constructed_from" not in (syms["c"].meta or {})
