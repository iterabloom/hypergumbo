# SPDX-License-Identifier: AGPL-3.0-or-later
"""C def/use extractor for intraprocedural dataflow (ADR-0017 §1c, WI-himob).

Extracts variable definitions (writes) and uses (reads) from tree-sitter-c
statement nodes, so ``solve_reaching_defs`` can chain a value from where it is
bound to where it is consumed. Until this module c had a catalogue (54 sinks,
40 sources) and no CFG mapping, def/use extractor or DDG spec, so every c taint
finding was labelled ``structural`` with the walk ``unavailable``: a secret
that reaches ``send`` and a secret read and thrown away in the same function
produced byte-identical verdicts.

How It Works
------------
The Java extractor's shape (one walker for every statement type), because C's
assignment is also an EXPRESSION and turns up inside conditions and arguments
-- ``while ((n = read(fd, b, 8)) > 0)`` defines ``n`` although the CFG hands
the walker only the loop condition. Binding forms:

- ``char *k = e, buf[8];``        declaration / init_declarator; the name is
  found through the declarator chain (pointer, array, parenthesized)
- ``x = e`` / ``x += e``          assignment_expression, anywhere
- ``s->f = e`` / ``s.f = e``      mutation of the root ``s``
- ``*p = e``                      mutation of the root ``p``
- ``a[i] = e``                    mutation of the root ``a``
- ``i++`` / ``--i``               update_expression

What is not a use: the member of a field access (``s->len`` uses ``s``), type
positions (casts, ``sizeof(T)``, declaration types), literals, and the callee
NAME of a call -- ``send(fd, k)`` uses ``fd`` and ``k``. A C callee is a plain
identifier, and counting it a use would link a local that shadows a function
name to every call of it.

Scope, stated rather than implied
---------------------------------
Intraprocedural and statement-level, like the other extractors. A ``for``
initializer and update are not handed to the walker by the CFG hooks, so their
bindings are not recorded and ``uncovered_semantic_lines`` forfeits refutation
for that function rather than claim to have seen them. Aliasing through a
pointer (``q = p; *q = secret; send(p)``) is not modelled: a write through
``*q`` defines ``q``, not ``p``. Macros are whatever tree-sitter parses them as,
which for an unexpanded function-like macro is a call.
"""
from __future__ import annotations

from typing import Any, Optional

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor
from hypergumbo_core.ddg_build import LanguageDdgSpec, register_ddg_language

#: Fields whose child is never a variable read.
_NON_USE_FIELDS = frozenset({"field", "type", "function"})

#: Node types that carry no variable reference.
_NON_USE_TYPES = frozenset({
    "primitive_type", "type_identifier", "sized_type_specifier", "type_descriptor",
    "struct_specifier", "union_specifier", "enum_specifier", "storage_class_specifier",
    "type_qualifier", "number_literal", "string_literal", "concatenated_string",
    "char_literal", "true", "false", "null", "comment", "field_identifier",
})


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _declared_name(node: Any, source: bytes) -> Optional[str]:
    """The identifier a declarator declares: ``k`` for ``*k``, ``buf[8]``, ``(*k)``."""
    while node is not None:
        if node.type == "identifier":
            return _node_text(node, source)
        node = node.child_by_field_name("declarator")
    return None


def _root_name(node: Any, source: bytes) -> Optional[str]:
    """The variable a write through ``node`` mutates: ``s`` for ``s->a.b``,
    ``p`` for ``*p``, ``a`` for ``a[i][j]``; None when rooted in a call."""
    while True:
        if node.type == "identifier":
            return _node_text(node, source)
        if node.type in ("field_expression", "pointer_expression", "subscript_expression"):
            node = node.child_by_field_name("argument")
        elif node.type == "parenthesized_expression":
            node = node.named_children[0]
        else:
            return None


class _Collector:
    """Accumulates one statement's defines and uses, in source order."""

    def __init__(self, source: bytes) -> None:
        self.source = source
        self.defines: list[str] = []
        self.uses: list[str] = []

    def walk(self, node: Any) -> None:
        """Collect uses from ``node`` and the definitions its binding forms make."""
        kind = node.type
        if kind in _NON_USE_TYPES:
            return
        if kind == "identifier":
            self.uses.append(_node_text(node, self.source))
            return
        if kind == "assignment_expression":
            self._assignment(node)
            return
        if kind == "update_expression":
            target = node.child_by_field_name("argument")
            self.walk(target)
            name = _root_name(target, self.source)
            if name is not None:
                self.defines.append(name)
            return
        if kind == "call_expression":
            # The callee NAME is not a read; a callee EXPRESSION (``ops->send``,
            # ``(*fp)``) reads its root.
            function = node.child_by_field_name("function")
            if function is not None and function.type != "identifier":
                self.walk(function)
            arguments = node.child_by_field_name("arguments")
            if arguments is not None:
                self.walk(arguments)
            return
        if kind == "init_declarator":
            value = node.child_by_field_name("value")
            if value is not None:
                self.walk(value)
            name = _declared_name(node.child_by_field_name("declarator"), self.source)
            if name is not None:
                self.defines.append(name)
            return
        if kind == "declaration":
            for i, child in enumerate(node.children):
                if node.field_name_for_child(i) != "declarator":
                    continue
                if child.type == "init_declarator":
                    self.walk(child)
                else:
                    # ``int n;`` / ``char buf[n];`` declare without a value the
                    # walk could chain: no define, and the name is not a read.
                    self._declarator_reads(child)
            return
        for i, child in enumerate(node.children):
            if child.is_named and node.field_name_for_child(i) not in _NON_USE_FIELDS:
                self.walk(child)

    def _declarator_reads(self, node: Any) -> None:
        """The reads inside a bare declarator: only an array size (``buf[n]``)."""
        while node is not None and node.type != "identifier":
            size = node.child_by_field_name("size")
            if size is not None:
                self.walk(size)
            node = node.child_by_field_name("declarator")

    def _assignment(self, node: Any) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        operator = node.child_by_field_name("operator")
        target = _root_name(left, self.source)
        if left.type == "identifier":
            # A compound `x += e` reads x before writing it; `x = e` does not.
            if operator is not None and operator.type != "=":
                self.uses.append(_node_text(left, self.source))
        else:
            # `s->f = v`, `*p = v`, `a[i] = v` read the root (and `i`) to find the slot.
            self.walk(left)
        self.walk(right)
        if target is not None:
            self.defines.append(target)


@register_def_use_extractor("c")
class CDefUseExtractor:
    """Extracts variable definitions and uses from C tree-sitter AST nodes."""

    language = "c"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        collector = _Collector(source)
        collector.walk(node)
        return DefUseResult(defines=collector.defines, uses=collector.uses)


# --------------------------------------------------------------------------
# Repo-level DDG spec (ADR-0017 §1c). Named through c.py's own rule, so the ids
# minted here are the ones the analyzer emitted for the same definitions.
# --------------------------------------------------------------------------


def _c_function_name(node: Any, source: bytes) -> Optional[str]:
    from .c import _get_function_name

    return _get_function_name(node, source)


register_ddg_language(LanguageDdgSpec(
    language="c",
    file_glob="*.c",
    function_node_types=frozenset({"function_definition"}),
    name_for=_c_function_name,
))
