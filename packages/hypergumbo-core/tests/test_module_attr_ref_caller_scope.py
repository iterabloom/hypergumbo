# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two caller-supplied knobs on ``emit_module_attribute_refs``.

Both default to the historical behaviour, so every language that passes
neither is unchanged; go passes both (WI-labik, WI-kugap).

``owner_kinds``: the symbol kinds a read may be anchored on. A read inside
go's ``var upper = strings.ToUpper`` belongs to the variable ``upper``, as the
calls in that initializer already do (INV-nopoh); with callables only, it fell
to the file pseudo-symbol, so a walk reaching ``upper`` stopped there.

``is_local_value``: a predicate on the attribute's base node. When a LOCAL
binding of the import's name is in scope (``func f(os T) { _ = os.Args }``),
the read is a field of the local, not of the package, and emitting
``os.Args`` there would report an argv read that does not happen.
"""

from __future__ import annotations

from hypergumbo_core.analyze.base import (
    _innermost_callable_at,
    emit_module_attribute_refs,
    node_text,
)
from hypergumbo_core.ir import Edge, Span, Symbol


def _parse_javascript(source: bytes):
    import tree_sitter
    import tree_sitter_javascript

    lang = tree_sitter.Language(tree_sitter_javascript.language())
    return tree_sitter.Parser(lang).parse(source).root_node


def _sym(name: str, kind: str, lo: int, hi: int) -> Symbol:
    return Symbol(
        id=f"javascript:app.js:{lo}-{hi}:{name}:{kind}",
        name=name, kind=kind, language="javascript", path="app.js",
        span=Span(start_line=lo, end_line=hi, start_col=0, end_col=0),
    )


_FILE = _sym("app.js", "module", 0, 0)


def _emit(source: bytes, **kwargs) -> list[Edge]:
    edges: list[Edge] = []
    emit_module_attribute_refs(
        _parse_javascript(source), source, {"process": "process"}, _FILE,
        "javascript", edges,
        node_kinds=("member_expression",), object_field_names=("object",),
        property_field_names=("property",), pass_id="t", run_id="r",
        **kwargs,
    )
    return edges


class TestOwnerKinds:
    def test_a_variable_owns_a_read_only_when_its_kind_is_admitted(self) -> None:
        var = _sym("v", "variable", 1, 1)
        default = _emit(b"const v = process.env;\n", enclosing_symbols=[var])
        assert [e.src for e in default] == [_FILE.id]
        widened = _emit(
            b"const v = process.env;\n", enclosing_symbols=[var],
            owner_kinds=frozenset({"function", "variable"}),
        )
        assert [e.src for e in widened] == [var.id]

    def test_the_narrowest_admitted_owner_still_wins(self) -> None:
        syms = [_sym("v", "variable", 1, 9), _sym("f", "function", 3, 5)]
        kinds = frozenset({"function", "variable"})
        assert _innermost_callable_at(4, syms, kinds).name == "f"
        assert _innermost_callable_at(2, syms, kinds).name == "v"
        assert _innermost_callable_at(2, syms) is None


class TestIsLocalValue:
    _SRC = b"function f(process) { return process.env; }\nconst g = process.argv;\n"

    def test_a_read_on_a_local_of_the_imports_name_is_skipped(self) -> None:
        seen: list[str] = []

        def is_param(base) -> bool:
            seen.append(node_text(base, self._SRC))
            return base.start_point[0] == 0  # only the read inside f

        edges = _emit(self._SRC, is_local_value=is_param)
        assert seen == ["process", "process"]  # reach: asked about both reads
        assert [e.dst for e in edges] == [
            "javascript:process:0-0:process.argv:attribute",
        ]

    def test_no_predicate_keeps_every_read(self) -> None:
        edges = _emit(self._SRC)
        assert sorted(e.dst for e in edges) == [
            "javascript:process:0-0:process.argv:attribute",
            "javascript:process:0-0:process.env:attribute",
        ]
