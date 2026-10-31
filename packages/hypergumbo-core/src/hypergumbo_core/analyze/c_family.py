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
three can reach is the cure; ``c.py``, ``cpp.py`` and ``cuda.py`` now import it
from here.

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

The parameters, too
-------------------
The same declarator holds the function's ``parameter_list``, and the
C-family analyzers that have overloads to tell apart (``cpp.py``, CUDA, GLSL,
HLSL) need it twice: as ``meta["parameters"]``, the declared home of the
arity fact that overload selection reads (WI-hilum), and as the identity
input that tells same-name overloads apart (WI-tusad:
``overload_arity.declared_arity_stable_id``, whose typed tier hashes the
parameter types, since ``f(int)`` / ``f(float)`` tie on the count alone).
``c.py`` reads neither, since C has no overloading.
``compute_stable_id`` looks for the list as a DIRECT child of the definition
and never finds it here, so it hashed ``param_count 0`` for every C-family
overload. :func:`c_family_parameter_list` finds it through
:func:`c_family_declarator`, and :func:`c_family_parameters` turns it into
``parameter_entry`` records. They began in ``cpp.py``; CUDA, GLSL and HLSL
share the grammar shape (tree-sitter-cuda and tree-sitter-hlsl extend
tree-sitter-cpp, tree-sitter-glsl extends tree-sitter-c) and cannot import
``hypergumbo-lang-mainstream``, so they live here, with
:func:`c_family_scope_chain`, the enclosing namespaces and classes an
inline member's identity needs beside its bare name (and, on request, the
enclosing functions a block-scope declaration's identity needs).

Function values
---------------
A function named as a VALUE (META-mino) -- a pointer initialiser
(``int (*g)(void) = f;``), an assignment (``h = f;``), a return operand, a
designated or positional aggregate element (``{ .run = f }``, ``{ "n", f }``)
-- is how C-family code builds callbacks and dispatch tables.
:func:`c_family_is_value_operand` says whether an identifier stands in one of
those places; a call's callee, a call ARGUMENT (each analyzer's own
``function_pointer_arg`` arm), an assignment's target and an operand of an
operator are not. :func:`c_family_array_declarator` finds the array an
``init_declarator`` declares through the wrappers an array of function
POINTERS adds (``int (*t[])(void)`` is function_declarator ->
parenthesized_declarator -> pointer_declarator -> array_declarator), so a
dispatch-table pass sees ``{ f }`` there as it sees a struct array's.
:func:`c_family_declares_local` says whether a name in one of those places is
instead a VARIABLE of the translation unit -- a parameter, local, enumerator,
template parameter or range-for variable of the enclosing function, or a
file-scope variable of the file (:func:`c_family_file_scope_variables`, which
a caller computes once per file). The bare-value arms of ``c.py`` and
``cpp.py`` ask it before they emit a ``references`` edge; without it, a use
of zstd's file-scope ``static const size_t ZSTD_frameHeaderSize`` would be
taken for the ``ZSTD_frameHeaderSize()`` defined in another file.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, Optional

from ..overload_arity import parameter_entry
from .base import iter_tree

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


#: Declarator wrappers between an ``init_declarator`` and the array it declares.
_ARRAY_WRAPPERS: frozenset[str] = frozenset({
    "function_declarator", "parenthesized_declarator", "pointer_declarator",
})

#: Parents under which an expression is a VALUE, and the field that holds it
#: (``None``: any named child).
_VALUE_PARENTS: dict[str, Optional[str]] = {
    "init_declarator": "value",
    "initializer_pair": "value",
    "assignment_expression": "right",
    "return_statement": None,
    "initializer_list": None,
}


def c_family_array_declarator(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The ``array_declarator`` a declarator declares, through function,
    parenthesized and pointer wrappers; None for anything else."""
    current: Optional["tree_sitter.Node"] = node
    while current is not None and current.type in _ARRAY_WRAPPERS:
        current = current.child_by_field_name("declarator") or next(
            (c for c in current.named_children
             if c.type == "array_declarator" or c.type in _ARRAY_WRAPPERS),
            None)
    return current if current is not None and current.type == "array_declarator" else None


def c_family_is_value_operand(node: "tree_sitter.Node") -> bool:
    """Whether ``node`` (an identifier or qualified identifier) is an expression
    whose VALUE is taken: an initialiser, an assignment's right side, a return
    operand or an aggregate element -- not a callee, an argument, a target or
    an operator's operand."""
    parent = node.parent
    if parent is None or parent.type not in _VALUE_PARENTS:
        return False
    field = _VALUE_PARENTS[parent.type]
    if field is None:
        return node.is_named
    if parent.type == "assignment_expression":
        # ``x += f`` is arithmetic on ``f``, not storing it (zstd's
        # ``ip += ZSTD_frameHeaderSize``); only plain ``=`` stores a value.
        operator = parent.child_by_field_name("operator")
        if operator is None or operator.type != "=":
            return False
    value = parent.child_by_field_name(field)
    return value is not None and value.id == node.id


def _declared_identifier(declarator: Optional["tree_sitter.Node"]) -> Optional["tree_sitter.Node"]:
    """The identifier node a (possibly wrapped) declarator declares, else None."""
    current = declarator
    while current is not None and current.type != "identifier":
        nxt = current.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in current.named_children if c.type.endswith("declarator")
                        or c.type == "identifier"), None)
        current = nxt
    return current


def _declared_name(declarator: Optional["tree_sitter.Node"], source: bytes) -> Optional[str]:
    """The identifier a (possibly wrapped) declarator declares, else None."""
    ident = _declared_identifier(declarator)
    return source[ident.start_byte:ident.end_byte].decode("utf-8", "replace") if ident else None


def c_family_declares_local(
    node: "tree_sitter.Node",
    name: str,
    source: bytes,
    file_scope_vars: Optional[frozenset[str]] = None,
) -> bool:
    """Whether ``name`` at ``node`` is a VARIABLE of this translation unit, not
    the function of that name elsewhere: a parameter, local or enumerator of
    the enclosing function, or a file-scope variable of this file (zstd's
    ``static const size_t ZSTD_frameHeaderSize = 4;`` in zstd_v01.c beside a
    ``ZSTD_frameHeaderSize()`` in zstd_decompress.c). ``file_scope_vars`` is
    :func:`c_family_file_scope_variables` of ``node``'s tree, when the caller
    has it; otherwise it is computed here."""
    if file_scope_vars is None:
        root = node
        while root.parent is not None:
            root = root.parent
        file_scope_vars = c_family_file_scope_variables(root, source)
    if name in file_scope_vars:
        return True
    function = node.parent
    while function is not None and function.type != "function_definition":
        function = function.parent
    if function is None:
        return False
    stack = [function]
    # A C++ template's parameters sit outside the definition it declares
    # (``template<int size> void f(...) { dst += size; }``, x265).
    template = function.parent
    while template is not None and template.type == "template_declaration":
        params = template.child_by_field_name("parameters")
        if params is not None:
            stack.append(params)
        template = template.parent
    while stack:
        current = stack.pop()
        if current.type in (
            "parameter_declaration", "declaration",
            "optional_parameter_declaration",
            # C++ ``for (auto const& x : xs)`` declares ``x`` in the loop.
            "for_range_loop",
        ):
            for declarator in current.children_by_field_name("declarator"):
                if _declared_name(declarator, source) == name:
                    return True
        elif current.type == "enumerator":
            # ``enum { empty, full } state = empty;`` in the function (apt).
            enum_name = current.child_by_field_name("name")
            if enum_name is not None and enum_name.text == name.encode():
                return True
        stack.extend(current.named_children)
    return False


def c_family_file_scope_variables(root: "tree_sitter.Node", source: bytes) -> frozenset[str]:
    """The names declared as VARIABLES (not function prototypes) outside every
    function and class in a translation unit. Computed once per file by a
    caller that asks :func:`c_family_declares_local` about many names."""
    names: set[str] = set()
    defined: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current.type == "function_definition":
            # A name this file DEFINES is a function whatever its other
            # declarations look like: zstd's ``void *myalloc _Z_OF((...));``
            # (a macro-wrapped prototype) reads as a variable.
            leaf = c_family_declarator(current).name
            if leaf is not None:
                defined.add(source[leaf.start_byte:leaf.end_byte].decode("utf-8", "replace"))
            continue  # its locals are not file scope
        if current.type in ("class_specifier", "struct_specifier"):
            continue  # a member: not file scope
        if current.type == "declaration":
            for declarator in current.children_by_field_name("declarator"):
                # A FUNCTION is declared when the name's own parent is a
                # function_declarator: ``void *f(void)`` (pointer return) is
                # one, ``void *(*f)(void)`` (a function POINTER) is a variable.
                ident = _declared_identifier(declarator)
                if ident is not None and (
                    ident.parent is None or ident.parent.type != "function_declarator"
                ):
                    names.add(source[ident.start_byte:ident.end_byte].decode("utf-8", "replace"))
            continue
        stack.extend(current.named_children)
    return frozenset(names - defined)


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


def c_family_parameter_list(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The ``parameter_list`` of a function definition or prototype, or None.

    The function's own declarator, at any depth (:func:`c_family_declarator`).
    A C++ conversion operator (``operator bool() const``) has none: its
    parameter list sits in the ``operator_cast``'s abstract declarator.
    """
    shape = c_family_declarator(node)
    declarator = shape.function_declarator
    if declarator is None and shape.name is not None:
        declarator = next(
            (n for n in iter_tree(shape.name) if n.type == "abstract_function_declarator"),
            None,
        )
    if declarator is None:
        return None
    return next((c for c in declarator.children if c.type == "parameter_list"), None)


def c_family_parameter_name(param: "tree_sitter.Node", source: bytes) -> str:
    """The declared name of one parameter (``*&x`` -> ``x``), "" if unnamed."""
    current = param.child_by_field_name("declarator")
    while current is not None and current.type not in ("identifier", "field_identifier"):
        inner = current.child_by_field_name("declarator")
        if inner is None:
            inner = next(
                (c for c in current.named_children
                 if c.type.endswith("declarator") or c.type == "identifier"),
                None,
            )
        current = inner
    if current is None:
        return ""
    return source[current.start_byte:current.end_byte].decode("utf-8", errors="replace")


def c_family_parameters(
    param_list: "tree_sitter.Node", source: bytes,
) -> list[dict[str, Any]]:
    """``meta["parameters"]`` for a C-family parameter list (WI-hilum, ADR-0058).

    ``f(void)`` declares no parameter. ``int y = 2`` is an
    ``optional_parameter_declaration`` (``default``); C's ``...`` token and a
    pack ``Args&&... args`` (``variadic_parameter_declaration``) take any number
    of arguments (``variadic``). The type is the text before the declarator,
    qualifiers included (``const char``, GLSL ``in vec2``, HLSL ``uniform
    float``); the ``*`` / ``&`` / ``[2]`` belong to the declarator and are not in
    it. A default written only on a separate C++ declaration is merged in by
    ``cpp.py`` at registration, never here.
    """
    entries: list[dict[str, Any]] = []
    for child in param_list.children:
        if child.type == "...":
            entries.append(parameter_entry("...", variadic=True))
            continue
        if child.type not in (
            "parameter_declaration", "optional_parameter_declaration",
            "variadic_parameter_declaration",
        ):
            continue
        declarator = child.child_by_field_name("declarator")
        end = declarator.start_byte if declarator is not None else (
            child.child_by_field_name("type") or child
        ).end_byte
        type_text = " ".join(
            source[child.start_byte:end].decode("utf-8", errors="replace").split()
        ) or None
        name = c_family_parameter_name(child, source)
        if child.type == "parameter_declaration" and not name and type_text == "void":
            continue  # ``f(void)``: the C spelling of an empty list
        entries.append(parameter_entry(
            name, type_text,
            default=child.type == "optional_parameter_declaration",
            variadic=child.type == "variadic_parameter_declaration",
        ))
    return entries


#: The C-family nodes that scope a definition, and the child naming each.
_SCOPE_NAMES = {
    "namespace_definition": (
        "namespace_identifier", "nested_namespace_specifier", "identifier",
    ),
    "class_specifier": ("type_identifier",),
    "struct_specifier": ("type_identifier",),
    "union_specifier": ("type_identifier",),
}


def c_family_scope_chain(
    node: "tree_sitter.Node", source: bytes, *, types: bool = True, functions: bool = False,
) -> list[str]:
    """The names of the scopes enclosing ``node``, outermost first.

    By default the namespaces and classes: an inline member function is named
    by its bare ``field_identifier`` (``int size() { ... }`` in ``struct A``), so
    two classes' ``size`` share a name; the chain is what tells them apart in a
    stable_id's qualified name. ``functions=True`` adds the enclosing function
    definitions, by the name their declarator declares, for a producer that
    emits declarations at block scope (an HLSL local, a C ``struct`` declared
    inside a function; INV-punig). ``types=False`` leaves the namespaces and
    classes out: C scopes a tag declared inside a ``struct`` to the file
    (C11 6.2.1), so its chain is the functions alone. An anonymous namespace
    or class, or a macro-named function, contributes nothing.
    """
    chain: list[str] = []
    current = node.parent
    while current is not None:
        name_types = _SCOPE_NAMES.get(current.type) if types else None
        if name_types is not None:
            name = next((c for c in current.children if c.type in name_types), None)
            if name is not None:
                chain.append(
                    source[name.start_byte:name.end_byte].decode("utf-8", errors="replace")
                )
        elif functions and current.type == "function_definition":
            declared = c_family_declarator(current).name
            if declared is not None:
                chain.append(
                    source[declared.start_byte:declared.end_byte].decode(
                        "utf-8", errors="replace",
                    )
                )
        current = current.parent
    chain.reverse()
    return chain
