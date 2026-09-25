# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared AST helpers for the "don't hand-roll a registry subset" linters.

Why this module exists
----------------------
Two registries ship a linter that answers the same shape of question —
"does any module in this tree enumerate PART of a family by hand?":

* :func:`hypergumbo_core.symbol_kinds.find_partial_abstract_family_literals`
  for the ``Symbol.kind`` abstract-type family (audit-findings 0018), and
* :func:`hypergumbo_core.edge_types.find_partial_inheritance_family_literals`
  for the ``Edge.edge_type`` inheritance family (INV-nosoz).

Both need the same three primitives: read the string members of a literal
collection, find the boolean expressions that also test a ``language`` (a
per-language predicate is allowed to be partial), and read the languages a
module declares via ``@register_linker(depends_on=...)``.

The helpers live here rather than in either registry because the alternative
is to copy them into the second one — and a family fact kept in two homes
that drift apart is the exact defect both linters exist to catch. Putting
the second copy in the module whose job is preventing second copies would
have been a fine joke and a bad decision.

These are deliberately *syntactic*. They read source text, never import it,
so a linter can scan a module that would not import cleanly.
"""

from __future__ import annotations

import ast

__all__ = [
    "declared_linker_languages",
    "language_guarded_line_spans",
    "string_literal_members",
]


def string_literal_members(node: ast.AST) -> frozenset[str] | None:
    """Return the string members of a literal collection, or ``None``.

    Recognizes a bare ``set`` / ``tuple`` / ``list`` display and the
    ``frozenset({...})`` / ``set({...})`` call wrapper around one. Returns
    ``None`` — rather than a partial set — when any element is not a string
    constant, so a computed or mixed collection is never mistaken for a
    hand-rolled vocabulary.
    """
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        elts = node.elts
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("frozenset", "set")
        and node.args
        and isinstance(node.args[0], (ast.Set, ast.Tuple, ast.List))
    ):
        elts = node.args[0].elts
    else:
        return None
    values = [
        e.value for e in elts
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    ]
    if len(values) != len(elts) or not values:
        return None
    return frozenset(values)


def language_guarded_line_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """Line spans of boolean expressions that also test a ``language``.

    ``s.language == "graphql" and s.kind in ("type", "field", "interface")``
    is a per-language predicate. Detecting the sibling comparison is what
    lets a linter stay strict everywhere else instead of needing an
    allow-list with a justification field.
    """
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp):
            continue
        mentions_language = any(
            isinstance(sub, ast.Attribute) and sub.attr == "language"
            for sub in ast.walk(node)
        ) or any(
            isinstance(sub, ast.Name) and sub.id == "language"
            for sub in ast.walk(node)
        )
        if mentions_language:
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def declared_linker_languages(tree: ast.AST) -> frozenset[str] | None:
    """Languages named in a module's ``@register_linker(depends_on=...)``.

    Returns ``None`` when the module declares none, which means "treat as
    language-agnostic" — the conservative direction, since an undeclared
    module may see any language.
    """
    langs: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "depends_on":
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                langs.add(sub.value)
                found = True
    return frozenset(langs) if found else None
