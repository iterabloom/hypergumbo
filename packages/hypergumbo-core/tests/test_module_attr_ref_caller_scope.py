# SPDX-License-Identifier: AGPL-3.0-or-later
"""A caller-supplied knob on ``emit_module_attribute_refs``.

It defaults to the historical behaviour, so every language that does not pass
it is unchanged; go passes it (WI-kugap).

``is_local_value``: a predicate on the attribute's base node. When a LOCAL
binding of the import's name is in scope (``func f(os T) { _ = os.Args }``),
the read is a field of the local, not of the package, and emitting
``os.Args`` there would report an argv read that does not happen.
"""

from __future__ import annotations

from hypergumbo_core.analyze.base import (
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
