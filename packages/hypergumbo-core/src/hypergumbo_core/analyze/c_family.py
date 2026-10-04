# SPDX-License-Identifier: AGPL-3.0-or-later
"""The C-family declarator walk: where a C, C++ or CUDA definition names itself.

Why this lives in ``hypergumbo-core``
-------------------------------------
Three analyzers in two packages read a ``function_definition``'s name from the
same tree-sitter shape: ``c.py`` and ``cpp.py`` in ``hypergumbo-lang-mainstream``
and ``cuda.py`` in ``hypergumbo-lang-common`` (tree-sitter-cuda extends
tree-sitter-cpp, so the declarator nodes and their ``declarator`` fields are the
same). WI-saduj wrote the walk into ``c.py`` and shared it with ``cpp.py``, but
``hypergumbo-lang-common`` cannot import ``hypergumbo-lang-mainstream``, so CUDA
kept a reader that looked for a ``function_declarator`` as a DIRECT child of the
definition: every CUDA function returning a pointer (``float *f()``) got no
symbol and every call in it was dropped (WI-fohuh). One walk in the package all
three can reach is the cure; ``c.py`` re-exports it under its old names.

How it works
------------
:func:`c_family_declarator` follows the definition's ``declarator`` field, and
the unnamed inner declarator of the wrappers that have no such field
(``parenthesized_declarator``, C++'s ``reference_declarator``), until a node
that is not a declarator: the name. The INNERMOST ``function_declarator`` on the
way is the function's own (``int (*f(void))(int)`` puts it inside the
declarator of the pointer the function returns). It counts the
``pointer_declarator`` levels of the return type and whether an outer
``function_declarator`` wraps the function's own (a function returning a
function pointer, whose return type is spelled around the name).

What it refuses
---------------
A ``function_declarator`` whose declarator is DIRECTLY another one names no
function: C has no function returning a function, so a real nested one always
has a ``parenthesized_declarator`` between. That shape is a macro tree-sitter
cannot expand -- ``int PFX(cpu_test)(void)`` (x265), ``LUALIB_API int
(luaL_loadstring) (...)`` read as a function named ``int`` -- and the walk
returns no declarator rather than let a caller mint a symbol named after the
macro. Calls inside such a definition are anchored by the caller's rule for an
unnamed definition (``hypergumbo_core.analyze.edge_source``), never on an
invented name.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Optional

if TYPE_CHECKING:
    import tree_sitter


class CFamilyDeclarator(NamedTuple):
    """What a C/C++ definition's declarator chain says about the function.

    ``function_declarator`` holds the function's OWN parameter list; ``name`` is
    the leaf naming it (``identifier``, and in C++ also ``qualified_identifier``,
    ``field_identifier``, ``operator_name``, ``destructor_name``,
    ``template_function`` or ``operator_cast``); ``returns_function`` is True when
    a second, OUTER ``function_declarator`` wraps it, i.e. the function returns a
    function pointer; ``pointer_depth`` counts the ``pointer_declarator`` levels
    of the return type. Both nodes are None for a macro-call-shaped declarator,
    which names no function (:func:`c_family_declarator`).
    """

    function_declarator: Optional["tree_sitter.Node"]
    name: Optional["tree_sitter.Node"]
    returns_function: bool
    pointer_depth: int


def _is_declarator_or_name(node_type: str) -> bool:
    """A node a declarator wrapper can hold as its inner declarator. The rest of
    its named children are modifiers (``ms_call_modifier``, attributes)."""
    return node_type.endswith(("declarator", "identifier")) or node_type in (
        "operator_name", "destructor_name", "template_function", "operator_cast",
    )


def _is_macro_call_name(function_declarator: "tree_sitter.Node") -> bool:
    """Whether a ``function_declarator`` directly wraps another (see
    :func:`c_family_declarator`): the outer one is a macro call's argument list."""
    inner = function_declarator.child_by_field_name("declarator")
    return inner is not None and inner.type == "function_declarator"


def c_family_declarator(node: "tree_sitter.Node") -> CFamilyDeclarator:
    """Walk a ``function_definition``'s declarator chain down to the name.

    WI-saduj. The name of a C or C++ function can sit at any depth: ``char **f()``
    nests two ``pointer_declarator`` s, ``int *&f()`` (C++) a pointer and a
    ``reference_declarator``, ``int (f)(int)`` a ``parenthesized_declarator``, and
    a function returning a function pointer, ``int (*f(void))(int)``, puts its own
    ``function_declarator`` INSIDE the declarator of the pointer it returns, under
    an outer one holding the returned pointer's parameters. The previous readers
    descended exactly one ``pointer_declarator``, so every deeper shape got no
    symbol and every call in it was left unemitted (crun's ``char
    **read_dir_entries``). See the module docstring for the walk and for the
    macro-call shape it refuses.
    """
    function_declarator = None
    outer_functions = 0
    pointers = 0
    pointers_at_function = 0
    current = node.child_by_field_name("declarator")
    while current is not None and current.type.endswith("_declarator"):
        if current.type == "function_declarator":
            if _is_macro_call_name(current):
                return CFamilyDeclarator(None, None, False, 0)
            if function_declarator is not None:
                outer_functions += 1
            function_declarator = current
            pointers_at_function = pointers
        elif current.type == "pointer_declarator":
            pointers += 1
        inner = current.child_by_field_name("declarator")
        if inner is None:
            inner = next(
                (c for c in current.named_children if _is_declarator_or_name(c.type)),
                None,
            )
        current = inner
    if function_declarator is None:
        return CFamilyDeclarator(None, current, False, 0)
    return CFamilyDeclarator(
        function_declarator, current, outer_functions > 0, pointers_at_function,
    )
