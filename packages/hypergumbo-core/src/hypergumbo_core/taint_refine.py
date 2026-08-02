# SPDX-License-Identifier: AGPL-3.0-or-later
"""Post-DDG IR refinement pass: resolve unresolved-external method-call edges.

WI-dilih addresses a documented overapproximation in the structural and
DDG-aware taint passes: when the Python analyzer can't pin a method
call's receiver type, the resulting edge's ``dst`` is
``python:external:0-0:NAME:unresolved`` — a placeholder that collides
with any sink declared on the bare name ``NAME`` (``dict.get`` looks
the same as ``os.environ.get`` looks the same as
``multiprocessing.Queue.get``). The sink matcher
(``taint._lookup_named_entry``, which filters with
``io_boundary._module_matches``) exempts BOTH placeholder spellings —
``external`` and ``<external>``, held together in
``taint._UNRESOLVED_MODULE_PLACEHOLDERS`` — from the short-name-collision
check, because rejecting them outright would suppress legitimate findings.

This module's job is to *replace* the ``external`` placeholder with a
real module path whenever the DDG can prove what the receiver was bound
to.

Pipeline placement (ADR-0017)
-----------------------------
Per the ADR's per-language §1c accretion model, this pass slots between
:func:`hypergumbo_core.cfg.solve_reaching_defs` and
:func:`hypergumbo_core.taint.propagate_taint_ddg` /
:func:`propagate_taint_structural`. It consumes the DDG (variable
reaching-defs) plus the function's AST (for receiver chain inspection)
and produces a rewritten edge list where unresolved externals that
*can* be resolved have been. Edges whose receivers are parameters,
closure captures, or call-returns (anything outside the §1c
accretion's reach) keep the ``external`` placeholder and fall back to
the documented overapproximation behaviour — consistent with the
ADR's "per-language precision" framing.

Scope
-----
The pass is structurally a consumer of a §1c def/use extractor's DDG;
it is applicable to any language with a §1c extractor (Python first,
Rust / TypeScript when those extractors land). The current
implementation is Python-only because the AST traversal helpers
(:func:`extract_python_imports`, :func:`extract_python_receiver_hints`)
are written against tree-sitter Python node types. A sibling
``extract_rust_*`` / ``extract_typescript_*`` would extend coverage
without changing the shape of :func:`refine_external_edges`.

Out of scope
------------
* **Call-RHS bindings** (``x = requests.Session(); x.get(...)``). These
  require return-type inference, which is outside ADR-0017's
  intraprocedural design. Such edges remain unresolved.
* **Module-attribute receivers walked across files**. The pass is
  per-file: it only resolves receivers bound by imports visible at the
  file's top level plus assignments in the calling function.
* **Languages without a §1c extractor**. By construction — there is no
  DDG to walk backwards through.

WI-dozon parameter-annotation pinning (in scope)
------------------------------------------------
Parameter-receiver type pinning is supported when the parameter carries
a type annotation. ``def f(name: str): name.replace(...)`` is resolved
by reading the ``str`` annotation: the receiver hint at the call site
becomes ``builtins.str``, which makes the sink matcher's
``_module_matches`` filter reject the short-name match against
``pathlib.Path.replace``. Supported
annotation shapes: bare builtin names (``str``, ``bytes``, ``list``,
``dict``, ``int``, ``float``, ``bool``, ``tuple``, ``set``,
``frozenset``, ``bytearray``, ``memoryview``, ``complex``, ``range``,
``object``); bare imported class names (``Path`` when
``from pathlib import Path`` is in scope); dotted annotations
(``pathlib.Path``); generic types with a supported outer
(``list[int]`` → ``builtins.list``). Explicit non-coverage: ``Optional``,
``Union``, forward-reference strings — these can't be pinned to a single
module hint and are conservatively skipped.
"""
from __future__ import annotations

from typing import Any


def extract_python_imports(
    tree_root: Any, source: bytes,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Extract top-level imports from a Python tree-sitter module node.

    Returns ``(module_imports, imports)``:

    * ``module_imports`` maps a local-bound name to a canonical module
      path. Covers ``import M``, ``import M as A``, ``import M.S``
      (Python binds the root name ``M``), ``import M.S as A``.
    * ``imports`` maps a local-bound name to ``(module, original_name)``
      for ``from M import N`` and ``from M import N as A`` (including
      dotted-module forms ``from M.S import N``).

    Mirrors the shape py.py builds during its own analysis pass; the
    pass re-derives the maps here because the verify-claims pipeline
    consumes the behaviour-map edges by-value and doesn't share py.py's
    per-file scratchpad.
    """
    module_imports: dict[str, str] = {}
    imports: dict[str, tuple[str, str]] = {}

    for child in tree_root.children:
        if child.type == "import_statement":
            _parse_import_statement(child, source, module_imports)
        elif child.type == "import_from_statement":
            _parse_import_from_statement(child, source, imports)

    return module_imports, imports


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _dotted_name_text(node: Any, source: bytes) -> str:
    """Reassemble a ``dotted_name`` tree-sitter node into ``a.b.c`` text."""
    parts = [
        _node_text(child, source)
        for child in node.children
        if child.type == "identifier"
    ]
    return ".".join(parts)


def _dotted_name_root(node: Any, source: bytes) -> str:
    """Return the leftmost identifier in a ``dotted_name`` (``a`` in ``a.b.c``)."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return ""  # pragma: no cover - dotted_name without identifiers is malformed


def _parse_import_statement(
    node: Any, source: bytes, module_imports: dict[str, str],
) -> None:
    """Handle ``import M`` / ``import M as A`` / ``import M.S`` / ``import M.S as A``."""
    for child in node.children:
        if child.type == "dotted_name":
            # `import M` or `import M.S` — binds the root name ``M``.
            root = _dotted_name_root(child, source)
            full = _dotted_name_text(child, source)
            if root:
                module_imports[root] = full
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node is None or alias_node is None:
                # Defensive: tree-sitter normally fills both fields.
                continue  # pragma: no cover
            full = _dotted_name_text(name_node, source)
            alias = _node_text(alias_node, source)
            module_imports[alias] = full


def _parse_import_from_statement(
    node: Any, source: bytes, imports: dict[str, tuple[str, str]],
) -> None:
    """Handle ``from M import N`` and ``from M.S import N as A`` shapes."""
    module_path: str | None = None
    # The first ``dotted_name`` child is the module; subsequent
    # ``dotted_name`` / ``aliased_import`` children are imported names.
    seen_module = False
    for child in node.children:
        if child.type == "dotted_name":
            if not seen_module:
                module_path = _dotted_name_text(child, source)
                seen_module = True
                continue
            if module_path is not None:
                name = _dotted_name_text(child, source)
                imports[name] = (module_path, name)
        elif child.type == "aliased_import" and module_path is not None:
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node is None or alias_node is None:
                continue  # pragma: no cover
            original = _dotted_name_text(name_node, source)
            alias = _node_text(alias_node, source)
            imports[alias] = (module_path, original)


_BUILTIN_TYPE_NAMES: frozenset[str] = frozenset({
    "str", "bytes", "int", "float", "bool", "list", "tuple", "dict",
    "set", "frozenset", "bytearray", "memoryview", "complex", "range",
    "object",
})

# Generic-type outer names that don't pin a single concrete type and
# therefore can't be used as a module hint. WI-dozon conservatively
# skips them rather than guessing a default.
_UNPINNABLE_GENERIC_OUTERS: frozenset[str] = frozenset({
    "Optional", "Union", "Callable", "Any", "Iterable", "Iterator",
    "Sequence", "Mapping", "MutableMapping", "Awaitable", "AsyncIterable",
})


def _resolve_annotation_name(
    name: str,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> str | None:
    """Resolve a bare annotation name to a ``module.name`` hint or None.

    Rules:
    - Builtin type → ``builtins.<name>`` (handles ``str``, ``bytes``,
      ``list``, ``dict``, etc.).
    - ``from X import Name`` → ``X.Name``.
    - ``import X.Y`` aliased to ``Name`` (less common for types but
      supported via ``module_imports``).
    - Otherwise: None (conservative — don't invent a hint).
    """
    if name in _BUILTIN_TYPE_NAMES:
        return f"builtins.{name}"
    if name in imports:
        module_path, original = imports[name]
        return f"{module_path}.{original}"
    if name in module_imports:
        # The annotation is itself a module path (rare for types but
        # not impossible: ``import collections; def f(x: collections): ...``).
        return module_imports[name]
    return None


def _resolve_annotation_node(
    type_node: Any,
    source: bytes,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> str | None:
    """Resolve a ``type``-node annotation to a module hint or None.

    Handles the tree-sitter shapes ``identifier`` (``str``),
    ``attribute`` (``pathlib.Path``), and ``generic_type`` (``list[int]``,
    ``Optional[str]``). For generic_type, the outer name is what
    matters; ``Optional`` / ``Union`` / etc. return None.
    """
    # The ``type`` node wraps a single annotation expression.
    inner: Any = None
    for child in type_node.children:
        if child.type in ("identifier", "attribute", "generic_type"):
            inner = child
            break
    if inner is None:
        return None
    if inner.type == "identifier":
        return _resolve_annotation_name(
            _node_text(inner, source), module_imports, imports,
        )
    if inner.type == "attribute":
        # Reassemble ``pathlib.Path`` from the attribute chain.
        return _node_text(inner, source)
    # generic_type: ``list[int]``, ``Optional[str]``, etc.
    outer = None
    for child in inner.children:
        if child.type == "identifier":
            outer = _node_text(child, source)
            break
    if outer is None or outer in _UNPINNABLE_GENERIC_OUTERS:
        return None
    return _resolve_annotation_name(outer, module_imports, imports)


def extract_python_param_annotations(
    func_def_node: Any,
    source: bytes,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> dict[str, str]:
    """Map each annotated parameter to a module hint (WI-dozon).

    Walks ``func_def_node.child_by_field_name("parameters")`` and
    extracts ``typed_parameter`` / ``typed_default_parameter`` shapes.
    Unannotated parameters and parameters whose annotation can't be
    pinned to a single type are silently absent from the result.

    Used by :func:`extract_python_receiver_hints` to resolve receivers
    that the DDG can't bind (parameter receivers). Closes the
    ``def f(name: str): name.replace(...)`` FP class that previously
    collided with ``pathlib.Path.replace`` under the ``external``
    exemption.
    """
    annotations: dict[str, str] = {}
    params_node = func_def_node.child_by_field_name("parameters")
    if params_node is None:  # pragma: no cover - tree-sitter always provides this
        return annotations
    for param in params_node.children:
        if param.type not in ("typed_parameter", "typed_default_parameter"):
            continue
        # First identifier child is the parameter name; the type child
        # carries the annotation expression.
        param_name = None
        type_node = None
        for child in param.children:
            if child.type == "identifier" and param_name is None:
                param_name = _node_text(child, source)
            elif child.type == "type":
                type_node = child
        if param_name is None or type_node is None:  # pragma: no cover - defensive
            continue
        hint = _resolve_annotation_node(
            type_node, source, module_imports, imports,
        )
        if hint is not None:
            annotations[param_name] = hint
    return annotations


def extract_python_receiver_hints(
    body_node: Any,
    source: bytes,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
    ddg_edges: list,
    param_annotations: dict[str, str] | None = None,
) -> dict[tuple[int, str], str]:
    """For each ``recv.method()`` site in a function body, derive a module hint.

    Walks the AST to find method-call sites whose receiver is a simple
    ``identifier`` (``x.method()`` shape; multi-segment chains like
    ``urllib.request.urlopen`` are already resolved by py.py and do not
    reach this pass). For each such call:

    1. Find the DDG edge with ``variable == receiver``, ``use_line ==
       call_line``. Take ``def_line``.
    2. Walk the AST to the assignment statement at ``def_line``.
    3. If the RHS is an attribute chain rooted at a known import (or a
       single identifier that names a ``from``-import), derive the
       canonical module path.

    Returns ``{(call_line, attr_name) → module_hint}``. Empty when no
    receiver resolves cleanly.

    ``param_annotations`` (WI-dozon) provides the
    ``{parameter_name → module_hint}`` fallback used when the DDG has no
    def visible at the call site (i.e., the receiver is a parameter
    whose annotation pins its type).
    """
    if param_annotations is None:
        param_annotations = {}

    # Map ``def_line → assignment AST node`` for fast lookup. Only
    # statements at the function-body level are considered (nested
    # control flow uses the same line number for the assignment, so
    # this lookup remains correct for typical patterns).
    assignments_by_line: dict[int, Any] = {}
    _collect_assignments(body_node, assignments_by_line)

    # Map ``(variable, use_line) → def_line`` from the DDG.
    def_line_for_use: dict[tuple[str, int], int] = {}
    for edge in ddg_edges:
        def_line_for_use[(edge.variable, edge.use_line)] = edge.def_line

    hints: dict[tuple[int, str], str] = {}

    def visit(node: Any) -> None:
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func is not None and func.type == "attribute":
                obj = func.child_by_field_name("object")
                attr = func.child_by_field_name("attribute")
                if (
                    obj is not None
                    and obj.type == "identifier"
                    and attr is not None
                    and attr.type == "identifier"
                ):
                    receiver = _node_text(obj, source)
                    attr_name = _node_text(attr, source)
                    call_line = node.start_point[0] + 1
                    hint = _resolve_receiver_hint(
                        receiver,
                        call_line,
                        def_line_for_use,
                        assignments_by_line,
                        source,
                        module_imports,
                        imports,
                    )
                    # WI-dozon: when no DDG-visible binding pins the
                    # receiver, fall back to the parameter annotation
                    # (if any). DDG wins when both fire — local rebinds
                    # are more specific than the signature annotation.
                    if hint is None and receiver in param_annotations:
                        hint = param_annotations[receiver]
                    if hint is not None:
                        hints[(call_line, attr_name)] = hint
        for child in node.children:
            visit(child)

    visit(body_node)
    return hints


def _collect_assignments(node: Any, out: dict[int, Any]) -> None:
    """Recursively index assignment nodes by their starting line."""
    if node.type == "assignment":
        line = node.start_point[0] + 1
        out[line] = node
    for child in node.children:
        _collect_assignments(child, out)


def _resolve_receiver_hint(
    receiver: str,
    call_line: int,
    def_line_for_use: dict[tuple[str, int], int],
    assignments_by_line: dict[int, Any],
    source: bytes,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> str | None:
    """Look up ``receiver``'s def via the DDG and derive a module hint.

    Returns the canonical module path the receiver was bound to, or
    ``None`` when:
    * the receiver has no in-function definition (parameter / closure
      capture / global);
    * the binding RHS is a call expression (no return-type info);
    * the binding RHS is rooted at an identifier we don't recognise as
      an import.
    """
    def_line = def_line_for_use.get((receiver, call_line))
    if def_line is None:
        return None
    assignment = assignments_by_line.get(def_line)
    if assignment is None:
        return None  # pragma: no cover - DDG cited a non-assignment line
    rhs = assignment.child_by_field_name("right")
    if rhs is None:
        return None  # pragma: no cover - malformed assignment
    return _module_hint_from_rhs(rhs, source, module_imports, imports)


def _module_hint_from_rhs(
    rhs: Any,
    source: bytes,
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> str | None:
    """Inspect an assignment RHS for a recoverable module-of-origin.

    Handles two shapes:

    * ``identifier`` — the binding aliases a single name. If that name
      was introduced by ``from M import N``, the hint is ``M.N``.
    * ``attribute`` chain — the binding is ``a.b.c…``. The root must be
      a known ``import M``-style binding; the hint is the full module
      path concatenated with the trailing attribute segments
      (e.g., root ``os`` + tail ``environ`` → ``os.environ``).

    Returns ``None`` for any other RHS shape (calls, subscripts,
    literals).
    """
    if rhs.type == "identifier":
        name = _node_text(rhs, source)
        if name in imports:
            module, original = imports[name]
            return f"{module}.{original}"
        # `x = os` where `os` is an import — hint is the module itself.
        if name in module_imports:
            return module_imports[name]
        return None
    if rhs.type == "attribute":
        chain = _unwind_attribute_chain(rhs, source)
        if chain is None:
            return None
        root, tail = chain
        if root in module_imports:
            base = module_imports[root]
            return ".".join([base] + tail)
        if root in imports:
            # `from M import N`; `x = N.S` → hint M.N.S
            module, original = imports[root]
            return ".".join([f"{module}.{original}"] + tail)
        return None
    return None


def _unwind_attribute_chain(
    node: Any, source: bytes,
) -> tuple[str, list[str]] | None:
    """Unwind ``a.b.c`` into ``("a", ["b", "c"])``.

    Returns ``None`` if the chain root is not a simple identifier (e.g.,
    ``foo().bar`` — call result as receiver — has no recoverable root).
    """
    tail: list[str] = []
    current = node
    while current.type == "attribute":
        attr = current.child_by_field_name("attribute")
        obj = current.child_by_field_name("object")
        if attr is None or obj is None:
            return None  # pragma: no cover - malformed attribute
        tail.append(_node_text(attr, source))
        current = obj
    if current.type != "identifier":
        return None
    tail.reverse()
    return _node_text(current, source), tail


# ---------------------------------------------------------------------------
# Edge rewriting
# ---------------------------------------------------------------------------


def refine_external_edges(
    edges: list[dict],
    hints_by_caller: dict[str, dict[tuple[int, str], str]],
) -> list[dict]:
    """Rewrite unresolved-external method-call edges using receiver hints.

    For each edge whose ``dst`` matches the unresolved-external shape
    ``{lang}:external:0-0:{name}:unresolved``, look the caller up in
    ``hints_by_caller``. If the caller has a hint at
    ``(edge.line, name)``, rewrite ``dst``'s module segment to the
    hinted module path:

        python:external:0-0:get:unresolved
            → python:os.environ:0-0:get:unresolved

    Edges without a matching hint are passed through unchanged; edges
    whose dst module segment is already specific (not ``external``) are
    also passed through. The returned list is a new list of new edge
    dicts — the input ``edges`` list and its dict contents are not
    mutated.

    The pass is idempotent: re-running it on a refined edge list is a
    no-op because each rewritten edge's module segment is no longer
    ``external``.
    """
    refined: list[dict] = []
    for edge in edges:
        new_edge = _maybe_rewrite_edge(edge, hints_by_caller)
        refined.append(new_edge)
    return refined


def _maybe_rewrite_edge(
    edge: dict, hints_by_caller: dict[str, dict[tuple[int, str], str]],
) -> dict:
    """Return a copy of ``edge`` with the dst rewritten if a hint applies."""
    dst = edge.get("dst", "")
    parts = dst.split(":")
    if len(parts) < 5:
        return dict(edge)
    module = parts[1]
    name = parts[3]
    if module != "external":
        return dict(edge)
    # ADR-0037 ruling 4: read the resolution verdict from ``Edge.is_resolved``,
    # not the dst kind-slot — WI-pubiv's boundary-id remap rewrites the
    # ``:unresolved`` suffix to ``:external_symbol`` on the final graph, so the
    # old ``kind != "unresolved"`` check skipped every external edge and made
    # this refinement pass a silent no-op post-remap.
    if edge.get("is_resolved", False):
        return dict(edge)  # pragma: no cover - external-module edges are unresolved by construction
    caller = edge.get("src", "")
    caller_hints = hints_by_caller.get(caller)
    if not caller_hints:
        return dict(edge)
    line = edge.get("line", 0)
    hint = caller_hints.get((line, name))
    if hint is None:
        return dict(edge)
    # Reassemble with hinted module path. ``name`` may itself contain
    # colons (ObjC selector format); use the original parts list and
    # only replace index 1.
    new_parts = list(parts)
    new_parts[1] = hint
    new_dst = ":".join(new_parts)
    new_edge = dict(edge)
    new_edge["dst"] = new_dst
    if "meta" in new_edge and isinstance(new_edge["meta"], dict):
        new_edge["meta"] = dict(new_edge["meta"])
    return new_edge
