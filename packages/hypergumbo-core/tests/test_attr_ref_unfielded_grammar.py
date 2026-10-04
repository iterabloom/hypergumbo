# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rabum: ``emit_module_attribute_refs`` on a grammar with no fields.

tree-sitter-kotlin declares no fields on ``navigation_expression`` or
``call_expression``, and puts each call argument in a ``value_argument``
wrapper, so the helper could not read ``System.err`` there at all: every part
was looked up by field name. Three generic additions let kotlin use the SHARED
emitter rather than a private copy:

* a field name beginning ``@`` selects a NAMED child -- ``@first``, ``@last``
  or ``@<node type>`` (``_child_by_fields``);
* ``carrier_argument_wrapper_kinds`` steps over an argument wrapper when asking
  which call a use is handed to (INV-hopib's ``attr_carrier``);
* ``attribute_names`` keeps only the named attributes (scala calls a
  parameterless method without parentheses, so ``System.currentTimeMillis`` is
  not a field read).

And a backtick-quoted attribute names the member it quotes (kotlin's
``System.`in```).

These run on tree-sitter-javascript, the grammar core's tests already use, so
the pseudo-field forms are exercised against nodes that DO have fields: the
assertions compare each form with the field-named one where both apply.
"""
from __future__ import annotations

from hypergumbo_core.analyze.base import emit_module_attribute_refs
from hypergumbo_core.ir import Edge, Span, Symbol


def _root(source: str):
    import tree_sitter
    import tree_sitter_javascript

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_javascript.language()))
    return parser.parse(source.encode("utf-8")).root_node


_CALLER = Symbol(
    id="javascript:app.js:1-5:handler:function", name="handler", kind="function",
    language="javascript", path="app.js",
    span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
)


def _emit(source: str, **kwargs) -> list[Edge]:
    grammar = {
        "node_kinds": ("member_expression",),
        "object_field_names": ("object",),
        "property_field_names": ("property",),
    }
    grammar.update(kwargs)
    edges: list[Edge] = []
    emit_module_attribute_refs(
        _root(source), source.encode("utf-8"), {"process": "process"}, _CALLER,
        "javascript", edges, pass_id="p", run_id="r", **grammar,
    )
    return edges


def _dsts(edges: list[Edge]) -> list[str]:
    return [e.dst for e in edges]


class TestAPartNamedByPosition:
    def test_first_and_last_named_children_read_as_the_fields_do(self) -> None:
        source = "const p = process.env;\n"
        by_field = _emit(source)
        by_position = _emit(source, object_field_names=("@first",),
                            property_field_names=("@last",))
        assert _dsts(by_field) == ["javascript:process:0-0:process.env:attribute"]
        assert _dsts(by_position) == _dsts(by_field)

    def test_a_part_named_by_node_type(self) -> None:
        source = "const p = process.env;\n"
        assert _dsts(_emit(source, object_field_names=("@identifier",),
                           property_field_names=("@property_identifier",))) == [
            "javascript:process:0-0:process.env:attribute"]

    def test_a_type_no_child_has_selects_nothing(self) -> None:
        assert _emit("const p = process.env;\n",
                     property_field_names=("@no_such_node",)) == []

    def test_a_node_with_no_named_children_selects_nothing(self) -> None:
        """``@first`` / ``@last`` on a childless node is None, not an IndexError.

        The object of ``process.env`` is the leaf ``process``: asked for ITS
        first named child, there is none, so no read is recorded.
        """
        edges: list[Edge] = []
        root = _root("const p = process.env;\n")
        emit_module_attribute_refs(
            root, b"const p = process.env;\n", {"process": "process"}, _CALLER,
            "javascript", edges, pass_id="p", run_id="r",
            node_kinds=("identifier",), object_field_names=("@first",),
            property_field_names=("@last",),
        )
        assert edges == []

    def test_the_carrier_reads_the_call_by_position_too(self) -> None:
        """``f(process.env)``: the arguments node found by type, the callee of
        a receiver-form use found by position."""
        handed = _emit("f(process.env);\n", carrier_call_kinds=("call_expression",),
                       carrier_arguments_field="@arguments")
        assert [(e.meta or {}).get("attr_carrier") for e in handed] == ["f@1"]
        receiver = _emit("process.env.trim();\n",
                         call_function_field_names=("@first",),
                         carrier_call_kinds=("call_expression",),
                         carrier_arguments_field="@arguments")
        assert [(e.meta or {}).get("attr_carrier") for e in receiver] == [
            "process.env.trim@1"]


class TestAnArgumentWrapper:
    """``f(...process.argv)`` puts the use in a ``spread_element``: the stand-in
    for kotlin's ``value_argument``."""

    _SOURCE = "f(...process.argv);\n"

    def test_without_the_wrapper_the_use_has_no_carrier(self) -> None:
        (edge,) = _emit(self._SOURCE, carrier_call_kinds=("call_expression",))
        assert edge.meta is None

    def test_with_the_wrapper_the_call_is_named(self) -> None:
        (edge,) = _emit(self._SOURCE, carrier_call_kinds=("call_expression",),
                        carrier_argument_wrapper_kinds=("spread_element",))
        assert (edge.meta or {}).get("attr_carrier") == "f@1"

    def test_a_wrapper_outside_any_call_names_nothing(self) -> None:
        (edge,) = _emit("const a = [...process.argv];\n",
                        carrier_call_kinds=("call_expression",),
                        carrier_argument_wrapper_kinds=("spread_element", "array"))
        assert edge.meta is None


class TestTheAttributesAreNamed:
    def test_only_the_named_attributes_are_reads(self) -> None:
        source = "const a = process.env;\nconst b = process.argv;\n"
        assert len(_emit(source)) == 2  # reach: both are reads without the list
        assert _dsts(_emit(source, attribute_names=frozenset({"env"}))) == [
            "javascript:process:0-0:process.env:attribute"]


def test_a_backtick_quoted_attribute_names_the_member_it_quotes() -> None:
    """kotlin writes ``System.`in``` (``in`` is a keyword). JS has no quoted
    identifier, so an array of a name and a template string stands in for the
    access: the quoted part is the attribute, unquoted."""
    edges = _emit("const a = [process, `env`];\n", node_kinds=("array",),
                  object_field_names=("@identifier",),
                  property_field_names=("@template_string",),
                  attribute_names=frozenset({"env"}))
    assert _dsts(edges) == ["javascript:process:0-0:process.env:attribute"]
