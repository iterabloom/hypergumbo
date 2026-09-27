# SPDX-License-Identifier: AGPL-3.0-or-later
"""Java def/use extractor for intraprocedural dataflow (ADR-0017 §1c, WI-gotun).

Extracts variable definitions (writes) and uses (reads) from tree-sitter-java
statement nodes, so ``solve_reaching_defs`` can chain a value from where it is
bound to where it is consumed. Until this module, java had a CFG mapping and
69 catalogued sinks but no extractor, so ``dataflow_coverage`` reported java
not data-flow capable and every java taint finding was labelled ``structural``
with the walk ``unavailable``: a value that reaches the sink and a source that
merely shares a method with it read the same. They now read ``ddg`` /
``confirmed`` and ``ddg_mixed`` respectively.

What this does NOT unlock (measured, WI-gotun): the same-function sanitizer
credit for java's one plaintext sanitizer. ``javax.crypto.Cipher.doFinal`` is
both java's plaintext SOURCE and its plaintext -> ciphertext SANITIZER, since
the cipher's mode is set elsewhere. So the output of an encrypting ``doFinal``
is itself re-sourced as plaintext, and ``decrypt -> encrypt -> write`` stays a
violation with or without a DDG. That is a catalogue question (WI-buvob).

How It Works
------------
One walker, ``_Collector.walk``, serves every statement type. It collects uses from
every expression it passes through and definitions from the binding forms it
meets on the way:

- ``T a = e, b = f;`` / ``var x = e;``   local_variable_declaration
- ``x = e`` / ``x += e``                  assignment_expression, anywhere in
  the statement, so ``while ((line = r.readLine()) != null)`` defines
  ``line`` even though the CFG hands the walker only the loop condition
- ``obj.f = e`` / ``this.f = e``           mutation of the root receiver
- ``arr[i] = e``                           mutation of the root container
- ``i++`` / ``--i``                        update_expression
- ``try (T r = e)``                        resource
- ``catch (E ex)``                         catch_formal_parameter

Why one walker and not a handler per statement type (the Go and TypeScript
shape): a Java assignment is an EXPRESSION and turns up inside conditions,
arguments and other assignments, so a handler keyed on the statement type
would miss every nested one.

What is not a use. The member name of a call or field access
(``c.doFinal(x)`` uses ``c`` and ``x``, not ``doFinal``; ``this.f`` uses
``this``, not ``f``), type positions, literals, and the parameters of a lambda.
A class-name receiver (``Files.write(p, d)``) does count as a use of ``Files``.
Nothing defines that name, so the extra use links nothing.

Closures. A lambda or an anonymous class body is walked for USES (a tainted
value captured by a lambda is still read by the statement that creates it) but
not for DEFINITIONS: Java lets a lambda assign only fields and its own locals,
never an enclosing local, so a binding inside one is not a binding of this
function.

Scope, stated rather than implied
---------------------------------
Intraprocedural and statement-level, like the other four extractors. The CFG
hooks name only a loop's condition and body, so these bindings are never
handed to the walker, and ``uncovered_semantic_lines`` reports their lines
(which forfeits refutation for the function rather than letting the walk
claim to have seen them):

- the enhanced-for variable (``for (T s : xs)``)
- a classic for's ``init`` and ``update``

``if (o instanceof String s)`` binds ``s`` inside a condition. That is read
as a use of ``o`` only; ``s`` stays undefined. A declaration whose initializer
contains control flow (a ``switch`` expression, or a lambda whose block holds
an ``if``) is decomposed by the builder, as it is for TypeScript and Rust, so
its variable is never defined either.
"""
from __future__ import annotations

from typing import Any, Optional

from hypergumbo_core.cfg import DefUseResult, register_def_use_extractor
from hypergumbo_core.ddg_build import LanguageDdgSpec, register_ddg_language

#: Fields whose child is never a variable read: the member name of a call or
#: field access, the declared name of a binding (collected separately where
#: it defines), type positions, and a lambda's own parameters.
_NON_USE_FIELDS = frozenset({
    "name", "field", "type", "type_arguments", "type_parameters", "parameters",
})

#: Node types that carry no variable reference.
_NON_USE_TYPES = frozenset({
    "type_identifier", "scoped_type_identifier", "generic_type", "array_type",
    "integral_type", "floating_point_type", "boolean_type", "void_type",
    "type_arguments", "dimensions", "class_literal",
    "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
    "binary_integer_literal", "decimal_floating_point_literal",
    "hex_floating_point_literal", "string_literal", "character_literal",
    "text_block", "null_literal", "true", "false", "super",
    "line_comment", "block_comment", "marker_annotation", "annotation",
})

#: Bodies whose bindings belong to another function (see "Closures" above).
_CLOSURE_TYPES = frozenset({"lambda_expression", "class_body"})


def _node_text(node: Any, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _root_name(node: Any, source: bytes) -> Optional[str]:
    """The variable a write through ``node`` mutates: ``a`` for ``a.b.c`` and
    ``a[i][j]``, ``this`` for ``this.f``, None for anything rooted elsewhere
    (``f().x = v``)."""
    while True:
        if node.type == "identifier":
            return _node_text(node, source)
        if node.type == "this":
            return "this"
        if node.type == "field_access":
            node = node.child_by_field_name("object")
        elif node.type == "array_access":
            node = node.child_by_field_name("array")
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

    def define(self, name: Optional[str], in_closure: bool) -> None:
        if name is not None and not in_closure:
            self.defines.append(name)

    def walk(self, node: Any, in_closure: bool = False) -> None:
        """Collect uses from ``node`` and the definitions its binding forms make."""
        kind = node.type
        if kind in _NON_USE_TYPES:
            return
        if kind == "identifier":
            self.uses.append(_node_text(node, self.source))
            return
        if kind == "this":
            self.uses.append("this")
            return
        if kind == "assignment_expression":
            self._assignment(node, in_closure)
            return
        if kind == "update_expression":
            target = node.named_children[0]
            self.walk(target, in_closure)
            self.define(_root_name(target, self.source), in_closure)
            return
        if kind in ("variable_declarator", "resource", "catch_formal_parameter"):
            name = node.child_by_field_name("name")
            if name is not None:
                self.define(_node_text(name, self.source), in_closure)
        if kind == "method_reference":
            # `obj::m` reads `obj`; the method name is the last child.
            self.walk(node.named_children[0], in_closure)
            return
        inner = in_closure or kind in _CLOSURE_TYPES
        for i, child in enumerate(node.children):
            if child.is_named and node.field_name_for_child(i) not in _NON_USE_FIELDS:
                self.walk(child, inner)

    def _assignment(self, node: Any, in_closure: bool) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        operator = node.child_by_field_name("operator")
        target = _root_name(left, self.source)
        if left.type == "identifier":
            # A compound `x += e` reads x before writing it; a plain `x = e`
            # does not.
            if operator.type != "=":
                self.uses.append(_node_text(left, self.source))
        else:
            # `a.f = v` and `a[i] = v` read `a` (and `i`) to find the slot.
            self.walk(left, in_closure)
        self.walk(right, in_closure)
        self.define(target, in_closure)


@register_def_use_extractor("java")
class JavaDefUseExtractor:
    """Extracts variable definitions and uses from Java tree-sitter AST nodes."""

    language = "java"

    def extract(self, node: Any, source: bytes) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        collector = _Collector(source)
        collector.walk(node)
        return DefUseResult(defines=collector.defines, uses=collector.uses)


# --------------------------------------------------------------------------
# Repo-level DDG spec (ADR-0017 §1c)
#
# Named through java.py's own rule, so the ids minted here are the ones the
# analyzer emitted for the same declarations and `ddg_symbols` lines up with
# the structural BFS's node keys (the reason the TypeScript spec borrows
# js_ts.py's namer rather than re-deriving it).
# --------------------------------------------------------------------------


def _java_callable_name(node: Any, source: bytes) -> Optional[str]:
    from .java import java_callable_name

    return java_callable_name(node, source)


def _java_callable_kind(node: Any) -> str:
    return "constructor" if node.type == "constructor_declaration" else "method"


register_ddg_language(LanguageDdgSpec(
    language="java",
    file_glob="*.java",
    function_node_types=frozenset({"method_declaration", "constructor_declaration"}),
    name_for=_java_callable_name,
    kind_for=_java_callable_kind,
))
