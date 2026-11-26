# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lua analysis pass using tree-sitter-lua.

This analyzer uses tree-sitter to parse Lua files and extract:
- Function declarations (global, local, dot-index `Table.func`)
- Method-style function definitions (`Table:method`)
- Function call relationships (direct, dot-index, method). The caller is the
  declaration enclosing the call, found by its POSITION (INV-mozas); a call
  outside every function (module-level setup code, or a function literal
  bound to a module-level local or table field) takes the file symbol
- require statements (imports), and per file a table for the shared call
  resolution that binds no name (``_import_scope`` says why)
- One ``file`` symbol per module, plus per-symbol identity (``stable_id``;
  a function nested in another hashes the enclosing functions, INV-fabih,
  ``shape_id``) and metrics (cyclomatic complexity, line span)
- ADR-0015 dataflow annotation on the emitted edges
- File-scope bindings a framework row keys on (``_file_scope_bindings``):
  ``T = R:extend(...)`` / ``T = R:subclass(...)`` makes a ``class`` ``T``
  with ``meta["base_classes"] = [R]`` (Lapis ``lapis.Application:extend``,
  rxi/classic and 30log ``Object:extend``, middleclass ``Base:subclass``);
  any other ``x = C(...)`` except ``require`` makes a ``variable`` ``x`` with
  ``meta["constructed_from"] = C`` (``local app = lapis.Application()``).
  A name's head is read through the file's requires, so
  ``local l = require "lapis"; l.Application`` records ``lapis.Application``
  and a local table that is not the module records no dotted name at all.
  These symbols are no call targets: before them Lua had
  no symbol for a class or an application object, so no ``base_class:`` or
  ``constructed_from`` row (openresty.yaml's Lapis ``application`` rows)
  could fire. Not read: ``class("X", Base)`` (the base's argument position
  differs between middleclass, penlight and 30log), ``setmetatable(T,
  {__index = Base})`` (a class and an instance are the same statement),
  ``return R:extend()`` (no name), bindings inside functions, and MoonScript
  ``class extends`` (``.moon`` files are not parsed).

If tree-sitter with Lua support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-lua is available; if not, return a skipped result.
2. Pass 1: parse every file, extract its symbols.
3. Pass 2: per file, the ``imports`` edges, the call edges, and the file's
   ``ImportScope``.

Call resolution (ADR-0064)
--------------------------
The analyzer binds a call only where Lua's own rules name its callee, and
looks no name up among the repository's declarations (``_LuaCalls``):

- a ``local function`` the called name is bound to by lexical scoping
  (``_Scopes``; manual §3.5), or a ``local`` that a ``function f()``
  statement assigns: ``lexical_scope``;
- a field of a module: the owner or the name is bound to ``require
  "<literal>"`` of a file of the tree (``_module_names``: two roots, the tree
  and its parent), and the value that file returns holds the field:
  ``import``;
- a field of a local table of the caller's file, declared on the SAME local:
  ``lexical_scope``;
- ``self:f()`` in ``function T:m()``: ``T``'s own member, declared in the
  caller's file: ``own_type``;
- a local constructed as ``R.new(..)``, ``R:new(..)`` or ``setmetatable(o,
  R)``: ``R``'s field: ``receiver_type``.

Every other call goes out unresolved with the facts its site has
(``_report``): ``qualifier`` for a module of the tree, ``enclosing_class``
for ``self``, ``receiver_type_hint`` for a constructed receiver,
``call_construct="method"`` for a call on a receiver, ``stdlib_member_name``
for a name the screens of the standard library (``LUA_BASIC_FUNCTIONS``,
``LUA_LIBRARY_TABLES``, ``LUA_VALUE_METHODS``) or of LuaJIT / OpenResty
(``LUAJIT_OPENRESTY_TABLES``, ``OPENRESTY_VALUE_METHODS``) hold. The shared
linkers decide those; the file's table binds no name (``_import_scope``), so
the one that can act on a Lua stub is the name step (``single_holder_name``),
for a call on a receiver: it runs for Lua files because the table says the
names are screened (``LIBRARY_NAMES_SCREENED``).

Not bound, and why: a call of a GLOBAL function or a field of a global table
from another file (Lua's globals are one namespace for every file loaded in
a state; the table has no form for it, and a symbol does not record whether
a function is local); a bare ``f()`` inside ``function T:m()`` is never
``T.f`` (Lua has no implicit receiver); inheritance through ``__index``
(no type symbol for a table, no hierarchy walker for Lua).

Lua-Specific Considerations
---------------------------
- Lua has both global functions (`function foo()`) and local functions
  (`local function foo()`)
- Method-style definitions (`Table:method`) are common for OOP patterns
- Dot-index definitions (`function _M.process()`) are the dominant
  export pattern in OpenResty/Kong Lua modules, returned by ``return _M``
- require() is the standard import mechanism
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.import_scope import MODULES_ARE_FILES, ImportScope
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core import resolved_by
from hypergumbo_core.analyze.base import (
    record_partial_parse,
    AnalysisResult,
    SymbolsAt,
    TreeSitterAnalyzer,
    ancestor_labels,
    enclosing_declared_symbol,
    find_child_by_type,
    iter_tree,
    file_anchor_symbol,
    make_file_id,
    make_symbol_id,
    make_unresolved_edge,
    node_text,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import nested_qualified_name
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.dataflow import annotate_dataflow, get_dataflow_config
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("lua")

#: What a name or expression denotes (``_LuaCalls.denote_value``): a tag and
#: its operands.
_Meant = tuple[Any, ...]

# ADR-0015: Dataflow config for Lua
_df_config = get_dataflow_config("lua")


def find_lua_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Lua files in the repository."""
    yield from find_files(repo_root, ["*.lua"])


def _is_lua_tree_sitter_available_legacy() -> bool:  # pragma: no cover - replaced by TreeSitterAnalyzer
    """Legacy availability check -- replaced by TreeSitterAnalyzer._check_grammar_available."""
    pass


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution:
    ``declarations`` finds a declaration's symbol by its position
    (``symbols_at``), ``scopes`` what a name means where it is used.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]
    declarations: SymbolsAt = field(default_factory=dict)
    scopes: "_Scopes" = field(default=None)  # type: ignore[assignment]


def _extract_lua_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_declaration.

    Returns signature in format: (param1, param2, ...)
    Lua is dynamically typed, so no type annotations.
    """
    params_node = find_child_by_type(node, "parameters")
    if params_node is None:  # pragma: no cover - defensive
        return "()"

    params: list[str] = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(node_text(child, source))
        elif child.type == "spread":  # pragma: no cover - rare varargs
            params.append("...")

    return f"({', '.join(params)})"


def _get_function_name(
    node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, str]:
    """Extract function name and kind from function_declaration.

    Returns:
        Tuple of (name, kind) where kind is "function" or "method".
        Handles three patterns:
        - ``function foo()`` → ("foo", "function")
        - ``function Table:method()`` → ("Table.method", "method")
        - ``function Table.func()`` → ("Table.func", "function")
    """
    # Look for direct identifier (global/local function)
    name_node = find_child_by_type(node, "identifier")
    if name_node:
        return node_text(name_node, source), "function"

    # Look for method_index_expression (Table:method)
    method_expr = find_child_by_type(node, "method_index_expression")
    if method_expr:
        # Extract Table and method name
        table_id = None
        method_id = None
        for child in method_expr.children:
            if child.type == "identifier":
                if table_id is None:
                    table_id = node_text(child, source)
                else:
                    method_id = node_text(child, source)
        if table_id and method_id:
            return f"{table_id}.{method_id}", "method"

    # Look for dot_index_expression (Table.func)
    dot_expr = find_child_by_type(node, "dot_index_expression")
    if dot_expr:
        table_id = None
        func_id = None
        for child in dot_expr.children:
            if child.type == "identifier":
                if table_id is None:
                    table_id = node_text(child, source)
                else:
                    func_id = node_text(child, source)
        if table_id and func_id:
            return f"{table_id}.{func_id}", "function"

    return "", "function"  # pragma: no cover - fallback for unparseable functions


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Lua file.

    Detects:
    - function_declaration (both global and local)
    - Method-style functions (Table:method)
    """
    symbols: list[Symbol] = []

    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_path`` is
    # the repo-relative path (``analyze()`` passes ``rel_path = lua_file.relative_to
    # (repo_root)``). Folded into compute_stable_id's containing slot so same-name
    # functions/methods in different files hash distinctly. Computed once per file
    # and reused across every symbol in this file scope.
    file_stable_id = _analyzer._file_anchor(file_path)

    for node in iter_tree(tree.root_node):
        if node.type == "function_declaration":
            name, kind = _get_function_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("lua", file_path, start_line, end_line, name, kind)
                symbols.append(Symbol(
                    id=sym_id,
                    name=name,
                    kind=kind,
                    language="lua",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_lua_signature(node, source),
                    # INV-fabih: a function nested in another hashes the
                    # enclosing functions ("" at top level, unchanged).
                    stable_id=_analyzer.compute_stable_id(
                        node, kind=kind, name=name,
                        qualified_name=nested_qualified_name(ancestor_labels(
                            node, lambda n: _get_function_name(n, source)[0]
                            if n.type == "function_declaration" else None,
                        ), name),
                        file_stable_id=file_stable_id,
                    ),
                    shape_id=_analyzer.compute_shape_id(node),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "lua"),
                    line_span=end_line - start_line + 1,
                ))

    return symbols


@dataclass(frozen=True)
class _Bound:
    """What a name, used at one place of a file, is bound to by Lua's scoping.

    ``form``: ``"function"`` (``local function f``), ``"variable"`` (``local
    x [= value]``, a ``for`` variable), ``"parameter"``, or ``"self"`` (the
    receiver of ``function T:m()``, or the first parameter, named ``self``, of
    ``function T.m(self)``). ``node`` is the declaring statement (the function
    for a parameter or ``self``); ``value`` the expression a ``local``
    declaration pairs with the name by position, if any. Two uses of a name
    with equal ``key`` read the same variable.
    """

    form: str  # axis: bounded-enum
    name: str  # axis: identity
    node: "tree_sitter.Node"
    value: "tree_sitter.Node | None" = None

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.node.start_byte, self.node.end_byte, self.name)


class _Scopes:
    """Lua's lexical scoping over one parsed file (Lua 5.4 manual §3.5).

    A name used at a node is the innermost ``local`` that is visible there: a
    ``local`` statement of an enclosing block that ENDS before the use (``local
    x = x`` reads the outer ``x``), a ``local function`` from its own
    statement on (it sees itself), a parameter or ``for`` variable of an
    enclosing function or loop body. No ``local`` -> a global (``None``). An
    assignment to a local after its declaration is not followed: the value a
    name denotes is the one its ``local`` statement gives it.
    """

    def __init__(self, source: bytes) -> None:
        self.source = source
        self._entries: dict[int, list[_Bound]] = {}

    def declared_in(self, block: "tree_sitter.Node") -> list[_Bound]:
        """The locals the statements of ``block`` (a ``chunk`` or ``block``)
        declare, in statement order."""
        cached = self._entries.get(block.id)
        if cached is not None:
            return cached
        entries: list[_Bound] = []
        for stmt in block.children:
            if stmt.type == "function_declaration" and _is_local(stmt):
                name = stmt.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    entries.append(_Bound("function", node_text(name, self.source), stmt))
            elif stmt.type == "variable_declaration":
                assign = find_child_by_type(stmt, "assignment_statement")
                var_list = find_child_by_type(assign or stmt, "variable_list")
                values = (
                    find_child_by_type(assign, "expression_list") if assign is not None else None
                )
                paired = values.children_by_field_name("value") if values is not None else []
                for i, target in enumerate(
                    var_list.children_by_field_name("name") if var_list is not None else ()
                ):
                    if target.type == "identifier":
                        entries.append(_Bound(
                            "variable", node_text(target, self.source), stmt,
                            paired[i] if i < len(paired) else None,
                        ))
        self._entries[block.id] = entries
        return entries

    def declared_by(self, stmt: "tree_sitter.Node") -> list[_Bound]:
        """The locals one statement declares (see ``declared_in``)."""
        return [e for e in self.declared_in(stmt.parent) if e.node == stmt] if stmt.parent else []

    def bound(self, name: str, node: "tree_sitter.Node") -> _Bound | None:
        """The local ``name`` means at ``node``, or ``None`` for a global."""
        child, current = node, node.parent
        while current is not None:
            if current.type in ("chunk", "block"):
                for entry in reversed(self.declared_in(current)):
                    if entry.name != name:
                        continue
                    if entry.form == "function":
                        if entry.node.start_byte <= child.start_byte:
                            return entry
                    elif entry.node.end_byte <= child.start_byte:
                        return entry
            elif current.type in ("function_declaration", "function_definition"):
                found = self._parameter(name, current, child)
                if found is not None:
                    return found
            elif current.type == "for_statement" and child == current.child_by_field_name("body"):
                clause = current.child_by_field_name("clause")
                names = list(clause.children_by_field_name("name")) if clause is not None else []
                lists = [c for c in clause.children if c.type == "variable_list"] if clause else []
                for var_list in lists:
                    names += var_list.children_by_field_name("name")
                if any(node_text(n, self.source) == name for n in names):
                    return _Bound("variable", name, current)
            child, current = current, current.parent
        return None

    def _parameter(
        self, name: str, function: "tree_sitter.Node", child: "tree_sitter.Node",
    ) -> _Bound | None:
        """``name`` as a parameter (or the implicit ``self``) of ``function``,
        when the use is in its body."""
        if child != function.child_by_field_name("body"):
            return None
        params = function.child_by_field_name("parameters")
        names = [
            node_text(p, self.source)
            for p in (params.children_by_field_name("name") if params is not None else ())
        ]
        declared = function.child_by_field_name("name")
        shape = declared.type if declared is not None else None
        if name == "self" and (
            shape == "method_index_expression"
            or (shape == "dot_index_expression" and names[:1] == ["self"])
        ):
            return _Bound("self", name, function)
        return _Bound("parameter", name, function) if name in names else None


def _global_key(name: str) -> tuple[int, int, str]:
    """The key a global table ``name`` stores its declared fields under in one
    file (``_LuaCalls._declared_fields``); no local's key is negative."""
    return (-1, -1, name)


def _is_local(stmt: "tree_sitter.Node") -> bool:
    """``local function f`` (the grammar keeps ``local`` as the first child)."""
    return bool(stmt.children) and stmt.children[0].type == "local"


def _owner_of(declared: "tree_sitter.Node") -> "tree_sitter.Node | None":
    """The table a declared or called name is a field of: ``T`` in ``T.f`` /
    ``T:f``, when it is a plain identifier."""
    table = declared.child_by_field_name("table")
    return table if table is not None and table.type == "identifier" else None


def _field_of(declared: "tree_sitter.Node") -> "tree_sitter.Node | None":
    """``f`` in ``T.f`` (``field``) or ``T:f`` (``method``)."""
    return declared.child_by_field_name("field") or declared.child_by_field_name("method")


# Methods whose result is a NEW table inheriting from the receiver: the
# subclassing convention of Lapis (``lapis.Application:extend("App")``),
# rxi/classic and 30log (``Object:extend()``) and middleclass
# (``Base:subclass("X")``). The receiver is the base; it is the only argument
# position these libraries agree on, which is why ``class("X", Base)`` (base
# second in middleclass, first in penlight, absent in 30log) is not read.
_SUBCLASS_METHODS = frozenset({"extend", "subclass"})


def _static_path(node: "tree_sitter.Node", source: bytes) -> list[str] | None:
    """``a`` -> ``["a"]``, ``a.b.c`` -> ``["a", "b", "c"]``; else None.

    A computed index (``a[k]``), a call or a parenthesised expression has no
    static name, and a framework row matching a partial one would match a
    fiction, so it yields None.
    """
    if node.type == "identifier":
        return [node_text(node, source)]
    if node.type == "dot_index_expression":
        table = node.child_by_field_name("table")
        field = node.child_by_field_name("field")
        if table is None or field is None:  # pragma: no cover - grammar invariant
            return None
        head = _static_path(table, source)
        return head + [node_text(field, source)] if head else None
    return None


def _is_require_call(node: "tree_sitter.Node", source: bytes) -> bool:
    """A call of ``require``, with a literal module name or a computed one."""
    if node.type != "function_call":
        return False
    callee = node.child_by_field_name("name")
    return (
        callee is not None and callee.type == "identifier"
        and node_text(callee, source) == "require"
    )


def _require_module(node: "tree_sitter.Node", source: bytes) -> str | None:
    """``require("m")`` / ``require "m"`` -> ``"m"``; anything else -> None."""
    if not _is_require_call(node, source):
        return None
    args = node.child_by_field_name("arguments")
    for arg in args.children if args is not None else ():
        if arg.type == "string":
            content = arg.child_by_field_name("content")
            return node_text(content, source) if content is not None else None
    return None


def _qualify(path: list[str], env: dict[str, str | None]) -> str | None:
    """Render a static path with its head read through the file's bindings.

    ``env`` maps each name bound at file scope SO FAR to what it denotes: a
    module path (``"lapis"`` for ``local l = require "lapis"``, ``"lapis.
    Application"`` for ``local A = require("lapis").Application``) or None
    for any other value (a table literal, a call's result). Then:

    * head bound to a module -> the module path replaces it
      (``l.Application`` -> ``lapis.Application``), so an aliased require is
      the same name as the plain one;
    * head unbound -> a global, rendered as written (``Object``, ``ngx``);
    * head bound to another value -> the bare name alone is kept (``Base``,
      a class this file made), but a dotted path through it yields None: it
      is a field of a file-local table, and rendering it as written would
      let ``local lapis = {}`` pass for the ``lapis`` module.
    """
    head, rest = path[0], path[1:]
    if head not in env:
        return ".".join(path)
    module = env[head]
    if module is None:
        return None if rest else head
    return ".".join([module, *rest])


def _denotation(
    node: "tree_sitter.Node", source: bytes, env: dict[str, str | None],
) -> str | None:
    """What a binding's value denotes for :func:`_qualify`: a module path, a
    global's name (``local ngx = ngx``), or None for any other value."""
    module = _require_module(node, source)
    if module is not None:
        return module
    if node.type == "dot_index_expression":
        table = node.child_by_field_name("table")
        field = node.child_by_field_name("field")
        if table is None or field is None:  # pragma: no cover - grammar invariant
            return None
        base = _denotation(table, source, env)
        return f"{base}.{node_text(field, source)}" if base else None
    if node.type == "identifier":
        name = node_text(node, source)
        return env.get(name) if name in env else name
    return None


@dataclass
class _Binding:
    """One file-scope binding the analyzer records as a symbol."""

    stmt: "tree_sitter.Node"
    name: str
    kind: str  # "class" or "variable"
    meta: dict[str, object] | None


def _file_scope_bindings(
    tree: "tree_sitter.Tree", source: bytes,
) -> list[_Binding]:
    """The file-scope bindings that make a subclass or construct a value.

    Statements are read in order, each against the bindings made BEFORE it
    (``local x = x`` reads the outer ``x``), so a later ``local lapis = {}``
    shadows an earlier require and the reverse. Per target ``T`` paired with
    value ``V`` (``local a, b = f(), g()`` pairs by position):

    * ``V = R:extend(...)`` / ``R:subclass(...)`` -> a ``class`` ``T`` with
      ``base_classes = [R]`` (``T`` may be dotted: ``M.App``). The base is
      left out when ``R`` has no name (:func:`_qualify`);
    * any other call ``V = C(...)`` / ``R:m(...)`` except ``require`` -> a
      ``variable`` ``T`` (an identifier other than the discard ``_``; a
      table field is not a binding) with
      ``constructed_from = C`` / ``"R:m"``, left out when ``C`` has no name.

    Only file scope is read: a table made inside a function is a value of one
    call, not a module-level entity a framework or a reader addresses.
    """
    out: list[_Binding] = []
    env: dict[str, str | None] = {}
    for stmt in tree.root_node.children:
        if stmt.type == "function_declaration":
            name_node = stmt.child_by_field_name("name")
            if name_node is not None and name_node.type == "identifier":
                env[node_text(name_node, source)] = None
            continue
        is_local = stmt.type == "variable_declaration"
        assign = find_child_by_type(stmt, "assignment_statement") if is_local else (
            stmt if stmt.type == "assignment_statement" else None
        )
        if assign is None:
            if is_local:  # ``local x, y`` -- bound, to no module
                var_list = find_child_by_type(stmt, "variable_list")
                for target in var_list.children_by_field_name("name") if var_list else ():
                    env[node_text(target, source)] = None
            continue
        var_list = find_child_by_type(assign, "variable_list")
        expr_list = find_child_by_type(assign, "expression_list")
        if var_list is None or expr_list is None:  # pragma: no cover - grammar invariant
            continue
        targets = var_list.children_by_field_name("name")
        values = expr_list.children_by_field_name("value")
        updates: dict[str, str | None] = {}
        for target, value in zip(targets, values, strict=False):
            target_path = _static_path(target, source)
            if target_path is None:
                continue
            target_name = ".".join(target_path)
            if len(target_path) == 1:
                updates[target_name] = _denotation(value, source, env)
            binding = _classify_binding(stmt, target_name, len(target_path) == 1, value, source, env)
            if binding is not None:
                out.append(binding)
        for target in targets[len(values):]:  # ``local a, b = f()``: b bound too
            if target.type == "identifier":
                updates[node_text(target, source)] = None
        env.update(updates)
    return out


def _classify_binding(
    stmt: "tree_sitter.Node",
    target_name: str,
    target_is_name: bool,
    value: "tree_sitter.Node",
    source: bytes,
    env: dict[str, str | None],
) -> _Binding | None:
    """The symbol one target/value pair makes, if any (see the caller)."""
    if value.type != "function_call" or _is_require_call(value, source):
        return None
    callee = value.child_by_field_name("name")
    if callee is None:  # pragma: no cover - grammar invariant
        return None
    receiver_name: str | None = None
    if callee.type == "method_index_expression":
        receiver = callee.child_by_field_name("table")
        method = callee.child_by_field_name("method")
        if receiver is None or method is None:  # pragma: no cover - grammar invariant
            return None
        receiver_path = _static_path(receiver, source)
        receiver_name = _qualify(receiver_path, env) if receiver_path else None
        method_name = node_text(method, source)
        if method_name in _SUBCLASS_METHODS:
            return _Binding(
                stmt, target_name, "class",
                {"base_classes": [receiver_name]} if receiver_name else None,
            )
        constructed = f"{receiver_name}:{method_name}" if receiver_name else None
    else:
        callee_path = _static_path(callee, source)
        constructed = _qualify(callee_path, env) if callee_path else None
    if not target_is_name or target_name == "_":  # ``_``: the discard idiom
        return None
    return _Binding(
        stmt, target_name, "variable",
        {"constructed_from": constructed} if constructed else None,
    )


def _binding_symbols(
    tree: "tree_sitter.Tree", source: bytes, file_path: str, run_id: str,
) -> list[Symbol]:
    """Symbols for :func:`_file_scope_bindings`, identified like functions."""
    file_stable_id = _analyzer._file_anchor(file_path)
    symbols: list[Symbol] = []
    for b in _file_scope_bindings(tree, source):
        start_line = b.stmt.start_point[0] + 1
        end_line = b.stmt.end_point[0] + 1
        symbols.append(Symbol(
            id=make_symbol_id("lua", file_path, start_line, end_line, b.name, b.kind),
            name=b.name,
            kind=b.kind,
            language="lua",
            path=file_path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=b.stmt.start_point[1],
                end_col=b.stmt.end_point[1],
            ),
            origin=PASS_ID,
            origin_run_id=run_id,
            stable_id=_analyzer.compute_stable_id(
                b.stmt, kind=b.kind, name=b.name, file_stable_id=file_stable_id,
            ),
            shape_id=_analyzer.compute_shape_id(b.stmt),
            line_span=end_line - start_line + 1,
            meta=b.meta,
        ))
    return symbols


def _module_names(file_path: str, root_name: str) -> list[str]:
    """The names ``require`` finds ``file_path`` by, from either load-path root.

    Lua's searcher turns the dots of a module name into directory separators
    and tries ``?.lua`` then ``?/init.lua`` under each root of ``package.path``
    (manual §6.3, ``package.searchpath``). The roots are an environment fact the
    source does not hold, so two are assumed, both exact (never a suffix):

    * the tree's root: ``foo/bar.lua`` is ``foo.bar``, ``foo/init.lua`` ``foo``;
    * the tree root's parent, i.e. the tree is itself a package named after
      its directory: in a tree ``kong/``, ``db/init.lua`` is ``kong.db``. Kong
      and its plugins run from the directory above it (``./?.lua``), and every
      module of the analysed ``kong/`` tree is required as ``kong.*``.
    ``foo/init.lua`` is also ``foo.init`` (the searcher tries ``?.lua`` first).
    """
    parts = file_path.replace("\\", "/").split("/")
    parts[-1] = parts[-1][: -len(".lua")] if parts[-1].endswith(".lua") else parts[-1]
    stems = [parts] + ([parts[:-1]] if parts[-1] == "init" and len(parts) > 1 else [])
    names = [".".join(stem) for stem in stems]
    return names + [".".join([root_name, *stem]) for stem in stems if root_name]


def _module_index(paths: list[str], root_name: str) -> dict[str, list[str]]:
    """Module name -> the files of the tree ``require`` of that name finds
    (``_module_names``). Two files under one name: ``require`` of it is not
    recorded (``_LuaCalls.module_file``)."""
    index: dict[str, list[str]] = {}
    for path in paths:
        for name in _module_names(path, root_name):
            files = index.setdefault(name, [])
            if path not in files:
                files.append(path)
    return index


#: The Lua standard library, from the Lua 5.4 reference manual §6 ("Standard
#: Libraries"), with the Lua 5.1 names LuaJIT (and so OpenResty, Kong) still
#: provides (5.1 manual §5). Not taken from any analysed tree.
#:
#: Basic functions, §6.1 (5.1: §5.1, which adds getfenv, setfenv, loadstring,
#: unpack, module, gcinfo, newproxy).
LUA_BASIC_FUNCTIONS: frozenset[str] = frozenset({
    "assert", "collectgarbage", "dofile", "error", "getmetatable", "ipairs",
    "load", "loadfile", "next", "pairs", "pcall", "print", "rawequal", "rawget",
    "rawlen", "rawset", "require", "select", "setmetatable", "tonumber",
    "tostring", "type", "warn", "xpcall",
    "getfenv", "setfenv", "loadstring", "unpack", "module", "gcinfo", "newproxy",
})

#: The library tables and their functions: §6.2 coroutine, §6.3 package, §6.4
#: string, §6.5 utf8, §6.6 table, §6.7 math, §6.8 io, §6.9 os, §6.10 debug (and
#: the 5.1 manual's §5.2 to §5.9 for the names 5.4 dropped: table.getn / maxn /
#: foreach / foreachi / setn, math.pow / atan2 / cosh / sinh / tanh / log10 /
#: frexp / ldexp / mod, string.gfind, package.loaders / seeall).
LUA_LIBRARY_TABLES: dict[str, frozenset[str]] = {
    "coroutine": frozenset({
        "close", "create", "isyieldable", "resume", "running", "status", "wrap", "yield",
    }),
    "package": frozenset({
        "config", "cpath", "loaded", "loadlib", "path", "preload", "searchers",
        "searchpath", "loaders", "seeall",
    }),
    "string": frozenset({
        "byte", "char", "dump", "find", "format", "gmatch", "gsub", "len", "lower",
        "match", "pack", "packsize", "rep", "reverse", "sub", "unpack", "upper", "gfind",
    }),
    "utf8": frozenset({"char", "charpattern", "codes", "codepoint", "len", "offset"}),
    "table": frozenset({
        "concat", "insert", "move", "pack", "remove", "sort", "unpack",
        "getn", "maxn", "foreach", "foreachi", "setn",
    }),
    "math": frozenset({
        "abs", "acos", "asin", "atan", "ceil", "cos", "deg", "exp", "floor", "fmod",
        "huge", "log", "max", "maxinteger", "min", "mininteger", "modf", "pi", "rad",
        "random", "randomseed", "sin", "sqrt", "tan", "tointeger", "type", "ult",
        "pow", "atan2", "cosh", "sinh", "tanh", "log10", "frexp", "ldexp", "mod",
    }),
    "io": frozenset({
        "close", "flush", "input", "lines", "open", "output", "popen", "read",
        "stderr", "stdin", "stdout", "tmpfile", "type", "write",
    }),
    "os": frozenset({
        "clock", "date", "difftime", "execute", "exit", "getenv", "remove", "rename",
        "setlocale", "time", "tmpname",
    }),
    "debug": frozenset({
        "debug", "gethook", "getinfo", "getlocal", "getmetatable", "getregistry",
        "getupvalue", "getuservalue", "sethook", "setlocal", "setmetatable",
        "setupvalue", "setuservalue", "traceback", "upvalueid", "upvaluejoin",
        "setcstacklimit", "getfenv", "setfenv",
    }),
}

#: LuaJIT's own libraries (luajit.org, "Extensions" and "FFI Library" pages:
#: the ``bit`` module's API functions, the ``ffi.*`` API functions, the ``jit.*``
#: functions, and the ``table.new`` / ``table.clear`` extension modules), and
#: the ``ngx`` API of OpenResty's lua-nginx-module (its README, "Nginx API for
#: Lua": the functions of ``ngx`` and of its sub-tables ``ngx.req``,
#: ``ngx.resp``, ``ngx.location``, ``ngx.re``, ``ngx.timer``, ``ngx.thread``,
#: ``ngx.socket``, ``ngx.worker``, ``ngx.config``). Separate from the manual's:
#: they hold only where the code runs on LuaJIT or OpenResty (Kong does).
LUAJIT_OPENRESTY_TABLES: dict[str, frozenset[str]] = {
    "bit": frozenset({
        "tobit", "tohex", "bnot", "band", "bor", "bxor", "lshift", "rshift",
        "arshift", "rol", "ror", "bswap",
    }),
    "ffi": frozenset({
        "cdef", "load", "new", "typeof", "cast", "metatype", "gc", "sizeof",
        "alignof", "offsetof", "istype", "errno", "string", "copy", "fill", "abi",
        "os", "arch", "C",
    }),
    "jit": frozenset({
        "on", "off", "flush", "status", "version", "version_num", "os", "arch", "opt",
    }),
    "table": frozenset({"new", "clear"}),
    "ngx": frozenset({
        "say", "print", "log", "exit", "eof", "flush", "redirect", "exec", "sleep",
        "now", "time", "today", "update_time", "localtime", "utctime", "http_time",
        "parse_http_time", "cookie_time", "escape_uri", "unescape_uri",
        "encode_args", "decode_args", "encode_base64", "decode_base64", "md5",
        "md5_bin", "sha1_bin", "hmac_sha1", "crc32_short", "crc32_long",
        "quote_sql_str", "on_abort", "get_phase", "send_headers",
        "headers_sent", "is_subrequest", "status", "var", "ctx", "arg", "header",
        "shared", "null", "config", "worker", "req", "resp", "location", "re",
        "timer", "thread", "socket", "ocsp", "ssl", "process", "errlog", "balancer",
    }),
    "ngx.req": frozenset({
        "get_headers", "set_header", "clear_header", "get_uri_args", "set_uri_args",
        "get_post_args", "read_body", "discard_body", "get_body_data",
        "get_body_file", "set_body_data", "set_body_file", "init_body",
        "append_body", "finish_body", "get_method", "set_method", "set_uri",
        "raw_header", "http_version", "socket", "start_time", "is_internal",
    }),
    "ngx.resp": frozenset({"get_headers"}),
    "ngx.location": frozenset({"capture", "capture_multi"}),
    "ngx.re": frozenset({"match", "find", "gmatch", "sub", "gsub"}),
    "ngx.timer": frozenset({"at", "every", "running_count", "pending_count"}),
    "ngx.thread": frozenset({"spawn", "wait", "kill"}),
    "ngx.socket": frozenset({"tcp", "udp", "stream", "connect"}),
    "ngx.worker": frozenset({"pid", "count", "id", "exiting", "pids"}),
    "ngx.config": frozenset({
        "subsystem", "debug", "prefix", "nginx_version", "nginx_configure",
        "ngx_lua_version",
    }),
}

#: Method names the standard library defines on its VALUES, called with
#: ``v:m()``: a string's (its metatable's ``__index`` is the ``string`` table,
#: manual §6.4), a file handle's (§6.8: ``file:close`` ... ``file:write``), and
#: the metamethods (§2.4, plus 5.1's ``__mode``, 5.4's ``__close``, ``__name``).
#: A call ``x:m()`` on a receiver of unknown type with one of these names goes
#: out marked ``stdlib_member_name``: ``s:format(..)`` is not the repository's
#: one ``format`` method.
LUA_VALUE_METHODS: frozenset[str] = LUA_LIBRARY_TABLES["string"] | frozenset({
    "close", "flush", "lines", "read", "seek", "setvbuf", "write",
    "__index", "__newindex", "__call", "__add", "__sub", "__mul", "__div",
    "__mod", "__pow", "__unm", "__idiv", "__band", "__bor", "__bxor", "__shl",
    "__shr", "__bnot", "__concat", "__len", "__eq", "__lt", "__le", "__gc",
    "__close", "__mode", "__name", "__tostring", "__metatable", "__pairs",
})

#: Method names of OpenResty's values (lua-nginx-module README): a cosocket's
#: (``ngx.socket.tcp`` / ``udp`` / ``stream``, ``ngx.req.socket``: connect ...
#: setpeername) and a shared dictionary's (``ngx.shared.DICT``: get ...
#: free_space). Separate from the manual's, as above.
OPENRESTY_VALUE_METHODS: frozenset[str] = frozenset({
    "connect", "sslhandshake", "send", "receive", "receiveany", "receiveuntil",
    "close", "settimeout", "settimeouts", "setoption", "setkeepalive",
    "getreusedtimes", "setpeername", "bind",
    "get", "get_stale", "set", "safe_set", "add", "safe_add", "replace", "delete",
    "incr", "lpush", "rpush", "lpop", "rpop", "llen", "ttl", "expire",
    "flush_all", "flush_expired", "get_keys", "capacity", "free_space",
})

_SCREENED_METHODS = LUA_VALUE_METHODS | OPENRESTY_VALUE_METHODS
_LIBRARY_PATHS: dict[str, frozenset[str]] = {
    **LUA_LIBRARY_TABLES,
    "table": LUA_LIBRARY_TABLES["table"] | LUAJIT_OPENRESTY_TABLES["table"],
    **{k: v for k, v in LUAJIT_OPENRESTY_TABLES.items() if k != "table"},
}


def _library_member(path: str) -> bool:
    """``path`` (``string.format``, ``ngx.req.get_headers``, ``print``) is a
    function the screen above names: a basic function, or a member of a
    library table."""
    owner, _, member = path.rpartition(".")
    if not owner:
        return path in LUA_BASIC_FUNCTIONS
    return member in _LIBRARY_PATHS.get(owner, ())


#: ``ImportScope.library_names_screened`` for every Lua file's table: a stub
#: whose name the screens above hold goes out marked ``stdlib_member_name``.
LIBRARY_NAMES_SCREENED = True


#: The constructors a local's value is read as an instance of a table by:
#: ``R.new(..)`` / ``R:new(..)`` (the convention of the manual's §16 examples
#: and of the class libraries) and ``setmetatable(o, R)`` (manual §6.1: ``o``'s
#: fields then come from ``R`` through ``R.__index``).
_CONSTRUCTOR = "new"


class _LuaCalls:
    """Which declaration a call means, by Lua's own rules (ADR-0064).

    Nothing here looks a name up among the repository's declarations. A call
    is bound when the language makes one declaration its callee:

    * a ``local function`` the call's name is bound to (``_Scopes``):
      ``lexical_scope``;
    * a field of a module: the owner (``M.f()``, ``M:f()``,
      ``require("a").f()``) or the name (``local f = require("a").f``; ``local
      f = M.f``) is bound to ``require "<literal>"`` of a file of the tree
      (``_module_names``), and that file's returned value (its last chunk-level
      ``return``: a local table ``return M`` or ``return setmetatable(M, ..)``,
      or a table constructor ``return {f = f}``) holds ``f``: the declaration
      ``function M.f()`` / ``M:f()`` on the same local, or a field whose value is
      a local function or another module's field. A called module
      (``local m = require("a"); m()``) is the local function it returns:
      ``import``;
    * a field of a local table of the caller's file (``local _M = {}``;
      ``function _M.f()``; ``_M.f()``), the declaration on the SAME local
      (``_Bound.key``): ``lexical_scope``;
    * ``self.f()`` / ``self:f()`` in ``function T:m()`` (or ``T.m(self)``): the
      declaration of ``f`` on the local ``T``: ``own_type``;
    * a local constructed as ``R.new(..)``, ``R:new(..)`` or
      ``setmetatable(o, R)``, where ``R`` is a local table or a module: ``R``'s
      field: ``receiver_type``.
    Two declarations of one field on one local: the later (a redefinition).
    Everything else is reported (``_extract_edges_from_file``).
    """

    _DEPTH = 8

    def __init__(self, files: dict[str, "FileAnalysis"], root_name: str) -> None:
        self.files = files
        self.modules = _module_index(list(files), root_name)
        self._fields: dict[str, dict[tuple[tuple[int, int, str], str], list[object]]] = {}

    def module_file(self, literal: str) -> str | None:
        """The file of the tree ``require(literal)`` loads, or ``None`` (a
        library; or two files answer the name, counted as ambiguous)."""
        found = self.modules.get(literal.replace("/", "."), [])
        return found[0] if len(found) == 1 else None

    # ------------------------------------------------------------ denotation
    def denote(self, fa: "FileAnalysis", name: str, at: "tree_sitter.Node", depth: int = 0) -> _Meant:
        """What ``name`` used at ``at`` denotes (see ``denote_value``)."""
        bound = fa.scopes.bound(name, at)
        if bound is None:
            return ("global", name)
        if bound.form == "function":
            return ("function", fa, bound.node)
        if bound.form == "self":
            return ("self", fa, bound.node)
        if bound.form == "variable" and bound.value is not None and depth < self._DEPTH:
            value = self.denote_value(fa, bound.value, depth + 1)
            if value is not None:
                return value
        return ("local", fa, bound.key)

    def denote_value(self, fa: "FileAnalysis", expr: "tree_sitter.Node", depth: int = 0) -> _Meant | None:
        """What an expression denotes, when Lua's rules say statically:
        ``("function", fa, decl)`` a local function; ``("module", file,
        literal)`` a ``require`` (``file`` ``None`` for a library);
        ``("member", owner, field)`` a field of ``owner`` (a module, a local);
        ``("instance", owner, written)`` a table constructed from ``owner``;
        ``("local", fa, key)`` a local with no such value; ``("self", fa, fn)``;
        ``("global", "a.b")`` a global and its fields. ``None``: unknown."""
        if expr.type == "identifier":
            return self.denote(fa, node_text(expr, fa.source), expr, depth)
        literal = _require_module(expr, fa.source)
        if literal is not None:
            return ("module", self.module_file(literal), literal)
        if expr.type == "dot_index_expression":
            table, field = expr.child_by_field_name("table"), expr.child_by_field_name("field")
            owner = self.denote_value(fa, table, depth) if table is not None else None
            if owner is None or field is None:
                return None
            if owner[0] == "global":
                return ("global", f"{owner[1]}.{node_text(field, fa.source)}")
            return ("member", owner, node_text(field, fa.source))
        if expr.type == "function_call":
            return self._constructed(fa, expr, depth)
        return None

    def _constructed(self, fa: "FileAnalysis", call: "tree_sitter.Node", depth: int) -> _Meant | None:
        """``R.new(..)`` / ``R:new(..)`` / ``setmetatable(o, R)``: an instance of ``R``."""
        callee = call.child_by_field_name("name")
        if callee is None:  # pragma: no cover - grammar invariant
            return None
        owner = None
        if callee.type in ("dot_index_expression", "method_index_expression"):
            field = _field_of(callee)
            if field is not None and node_text(field, fa.source) == _CONSTRUCTOR:
                owner = _owner_of(callee)
        elif callee.type == "identifier" and node_text(callee, fa.source) == "setmetatable":
            args = call.child_by_field_name("arguments")
            named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
            owner = named[1] if len(named) == 2 and named[1].type == "identifier" else None
        if owner is None:
            return None
        meant = self.denote(fa, node_text(owner, fa.source), owner, depth + 1)
        return ("instance", meant, node_text(owner, fa.source))

    def own_local(self, fa: "FileAnalysis", owner: "tree_sitter.Node") -> _Meant | None:
        """``("local", fa, key)`` when ``owner`` is a name bound to a ``local``
        variable (whatever its value denotes), else ``None``."""
        if owner.type != "identifier":
            return None
        bound = fa.scopes.bound(node_text(owner, fa.source), owner)
        return ("local", fa, bound.key) if bound is not None and bound.form == "variable" else None

    # --------------------------------------------------------------- lookup
    def _declared_fields(self, fa: "FileAnalysis") -> dict[tuple[tuple[int, int, str], str], list[object]]:
        """Per local table of the file and field name, what is stored there, in
        file order: the symbol of ``function T.f()`` / ``T:f()``, or the value
        expression of a chunk-level ``T.f = <value>``."""
        cached = self._fields.get(fa.path)
        if cached is not None:
            return cached
        fields: dict[tuple[tuple[int, int, str], str], list[tuple[int, object]]] = {}

        def store(target: "tree_sitter.Node", what: object, at: int) -> None:
            owner, field = _owner_of(target), _field_of(target)
            if owner is None or field is None:
                return
            bound = fa.scopes.bound(node_text(owner, fa.source), owner)
            table = bound.key if bound is not None else _global_key(node_text(owner, fa.source))
            fields.setdefault((table, node_text(field, fa.source)), []).append((at, what))

        for node in iter_tree(fa.tree.root_node):  # type: ignore[attr-defined]
            if node.type == "variable_declaration":
                # ``local M = { f = f }``: the fields the local starts with.
                for entry in fa.scopes.declared_by(node):
                    if entry.value is not None and entry.value.type == "table_constructor":
                        for name, value in _constructor_fields(entry.value, fa.source).items():
                            fields.setdefault((entry.key, name), []).append((node.start_byte, value))
            elif node.type == "function_declaration":
                declared = node.child_by_field_name("name")
                symbol = symbol_declared_by(node, fa.declarations)
                if declared is None or symbol is None:
                    continue
                if declared.type != "identifier":
                    store(declared, symbol, node.start_byte)
                elif not _is_local(node):
                    # ``local f`` ... ``function f() end``: the statement
                    # assigns the local in scope (manual §3.4.11), stored
                    # under the field name ``""`` (the local itself).
                    bound = fa.scopes.bound(node_text(declared, fa.source), declared)
                    if bound is not None:
                        fields.setdefault((bound.key, ""), []).append((node.start_byte, symbol))
        for stmt in fa.tree.root_node.children:  # type: ignore[attr-defined]
            if stmt.type != "assignment_statement":
                continue
            targets = find_child_by_type(stmt, "variable_list")
            values = find_child_by_type(stmt, "expression_list")
            if targets is None or values is None:  # pragma: no cover - grammar invariant
                continue
            for target, value in zip(
                targets.children_by_field_name("name"), values.children_by_field_name("value"),
                strict=False,
            ):
                if target.type == "dot_index_expression":
                    store(target, value, stmt.start_byte)
        ordered = {key: [what for _, what in sorted(found, key=lambda x: x[0])] for key, found in fields.items()}
        self._fields[fa.path] = ordered
        return ordered

    def target(self, meant: _Meant | None, depth: int = 0) -> Symbol | None:
        """The declaration a denotation is, when it is a function of the tree."""
        if meant is None or depth > self._DEPTH:
            return None
        if meant[0] == "function":
            return symbol_declared_by(meant[2], meant[1].declarations)
        if meant[0] == "local":
            assigned = self._declared_fields(meant[1]).get((meant[2], ""), [])
            return assigned[-1] if assigned and isinstance(assigned[-1], Symbol) else None
        if meant[0] == "module" and meant[1] is not None:
            returned = self.returned(meant[1])
            return self.target(returned, depth + 1) if returned is not None else None
        if meant[0] == "member":
            return self.field(meant[1], meant[2], depth + 1)
        return None

    def field(self, owner: _Meant, name: str, depth: int = 0) -> Symbol | None:
        """The function stored in field ``name`` of what ``owner`` denotes."""
        if depth > self._DEPTH:
            return None
        if owner[0] == "module" and owner[1] is not None:
            returned = self.returned(owner[1])
            return self.field(returned, name, depth + 1) if returned is not None else None
        if owner[0] in ("local", "self", "instance"):
            if owner[0] == "instance":
                return self.field(owner[1], name, depth + 1)
            fa = owner[1]
            key = owner[2] if owner[0] == "local" else self._own_table(fa, owner[2])
            stored = self._declared_fields(fa).get((key, name), []) if key else []
            if not stored:
                return None
            last = stored[-1]
            if isinstance(last, Symbol):
                return last
            return self.target(self.denote_value(fa, last, depth + 1), depth + 1)  # type: ignore[arg-type]
        if owner[0] == "fields":
            value = owner[2].get(name)
            meant = self.denote_value(owner[1], value, depth + 1) if value is not None else None
            return self.target(meant, depth + 1)
        return None

    def _own_table(self, fa: "FileAnalysis", function: "tree_sitter.Node") -> tuple[int, int, str] | None:
        """The table ``T`` of ``function T:m()``, as a key: its local, or, for a
        global ``T``, the global as this file declares members on it (the
        enclosing type's own members are the ones of the caller's file)."""
        declared = function.child_by_field_name("name")
        owner = _owner_of(declared) if declared is not None else None
        if owner is None:
            return None
        bound = fa.scopes.bound(node_text(owner, fa.source), owner)
        return bound.key if bound is not None else _global_key(node_text(owner, fa.source))

    def returned(self, path: str) -> _Meant | None:
        """What the module file ``path`` returns: its last chunk-level
        ``return``'s first value. A table constructor is its fields
        (``("fields", fa, {name: value})``); ``setmetatable(T, ..)`` is ``T``."""
        fa = self.files[path]
        returns = [s for s in fa.tree.root_node.children if s.type == "return_statement"]  # type: ignore[attr-defined]
        values = find_child_by_type(returns[-1], "expression_list") if returns else None
        value = values.named_children[0] if values is not None and values.named_children else None
        if value is None:
            return None
        if value.type == "table_constructor":
            return ("fields", fa, _constructor_fields(value, fa.source))
        callee = value.child_by_field_name("name") if value.type == "function_call" else None
        if callee is not None and callee.type == "identifier" and node_text(callee, fa.source) == "setmetatable":
            args = value.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            value = first if first is not None and first.type == "identifier" else value
        return self.denote_value(fa, value)


def _constructor_fields(table: "tree_sitter.Node", source: bytes) -> dict[str, "tree_sitter.Node"]:
    """``{ f = value, ... }``: each field written ``name = value``, by name
    (a positional or ``[expr] =`` field has no static name)."""
    fields: dict[str, "tree_sitter.Node"] = {}
    for entry in table.named_children:
        name, value = entry.child_by_field_name("name"), entry.child_by_field_name("value")
        if entry.type == "field" and name is not None and value is not None and name.type == "identifier":
            fields[node_text(name, source)] = value
    return fields


def _call_shape(node: "tree_sitter.Node", source: bytes) -> tuple[str | None, "tree_sitter.Node | None", str | None, bool]:
    """``(written, owner, field, on_receiver)`` of a ``function_call``.

    ``written`` is the callee name as the ``require`` check and the stub of a
    call with no static owner use it: the identifier of a bare call, else the
    last identifier among the callee's direct children (``x.f`` -> ``f``,
    ``a.b.require`` -> ``require``); ``None`` when there is none (``t[k]()``,
    ``f()()``). ``owner`` is the expression the callee is a field of;
    ``on_receiver`` is ``x:m()``."""
    callee = node.children[0] if node.children else None
    if callee is None:  # pragma: no cover - grammar invariant
        return None, None, None, False
    if callee.type == "identifier":
        return node_text(callee, source), None, None, False
    if callee.type in ("method_index_expression", "dot_index_expression"):
        names = [node_text(c, source) for c in callee.children if c.type == "identifier"]
        field = _field_of(callee)
        return (
            names[1] if len(names) >= 2 else (names[0] if names else None),
            callee.child_by_field_name("table"),
            node_text(field, source) if field is not None else None,
            callee.type == "method_index_expression",
        )
    return None, None, None, False


def _extract_edges_from_file(
    fa: "FileAnalysis",
    calls: _LuaCalls,
    run_id: str,
) -> list[Edge]:
    """Call and import edges of one parsed Lua file (ADR-0064).

    A call is bound when ``_LuaCalls`` finds the declaration Lua's own rules
    make its callee; otherwise it goes out unresolved with what the call
    site says (``_report``). The caller is the declaration enclosing the call,
    found by its POSITION (``enclosing_declared_symbol``), else the file.
    """
    edges: list[Edge] = []
    source, file_path = fa.source, fa.path
    file_id = make_file_id("lua", file_path)
    # The file symbol pass 1 emits under this same id: the anchor of last resort.
    file_anchor = file_anchor_symbol("lua", str(file_path), PASS_ID, run_id)

    for node in iter_tree(fa.tree.root_node):  # type: ignore[attr-defined]
        if node.type != "function_call":
            continue
        written, owner, field, on_receiver = _call_shape(node, source)
        line = node.start_point[0] + 1
        if written == "require":
            # A ``require`` (whatever the call's shape) is an import, not a call.
            args_node = find_child_by_type(node, "arguments")
            for child in args_node.children if args_node else ():
                content_node = find_child_by_type(child, "string_content") if child.type == "string" else None
                if content_node:
                    edges.append(Edge.create(
                        src=file_id,
                        dst=f"lua:{node_text(content_node, source)}:0-0:module:module",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="require",
                    ))
            continue
        # INV-bamij: a call outside every function is anchored on the FILE.
        caller = enclosing_declared_symbol(node, fa.declarations, _DECLARATIONS) or file_anchor
        bound = _bind(fa, calls, node, written, owner, field)
        if bound is not None:
            target, by = bound
            on_a_value = on_receiver or by in (resolved_by.OWN_TYPE, resolved_by.RECEIVER_TYPE)
            # ADR-0039 caller-valued: a bind through a require keeps the 0.85 the
            # require-alias route gave it (a constant, not a match grade); the
            # other binds pass a resolution_score and no confidence.
            edges.append(Edge.create(
                src=caller.id,
                dst=target.id,
                edge_type="calls",
                line=line,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="require_alias_call" if by == resolved_by.IMPORT else "ast_call",
                confidence=CONFIDENCE_REQUIRE_ALIAS if by == resolved_by.IMPORT else None,
                resolution_score=(
                    None if by == resolved_by.IMPORT
                    else TYPED_METHOD_GRADE if by == resolved_by.RECEIVER_TYPE else 1.0
                ),
                meta={
                    "call_construct": "method" if on_a_value else "function",
                    # Read by receiver_blind_magnets: the receiver was identified.
                    **({"resolution_quality": "typed"} if by == resolved_by.RECEIVER_TYPE else {}),
                },
                resolved_by=by,
            ))
        else:
            edges.append(_report(fa, calls, node, caller.id, written, owner, field, on_receiver, run_id))
    return edges


#: The node types whose symbol encloses a call: a ``function_declaration``
#: (a function literal, ``function_definition``, has no symbol and is walked
#: past, as is a declaration of a nested name ``a.b.c``).
_DECLARATIONS = frozenset({"function_declaration"})

#: Confidence of a call bound through a ``require``, and the grade of one bound
#: on a constructed receiver's table (ADR-0039 ruling 6: interpolated in the
#: ``ast_call`` band); the values the routes these replace gave them.
CONFIDENCE_REQUIRE_ALIAS = 0.85
TYPED_METHOD_GRADE = 0.90


def _bind(
    fa: "FileAnalysis", calls: _LuaCalls, node: "tree_sitter.Node",
    written: str | None, owner: "tree_sitter.Node | None", field: str | None,
) -> tuple[Symbol, str] | None:
    """The declaration Lua makes this call's callee, and the evidence
    (``resolved_by``), or ``None``. A field the owner's own local holds
    (``function M.extra()`` beside ``local M = require "m"``, a field set on
    an instance) is looked at before its module's or its constructor's."""
    if owner is None:
        if written is None:
            return None
        meant = calls.denote(fa, written, node)
        target = calls.target(meant)
        if target is None:
            return None
        return target, resolved_by.LEXICAL_SCOPE if meant[0] in ("function", "local") else resolved_by.IMPORT
    owner_meant = calls.denote_value(fa, owner) if field is not None else None
    if owner_meant is None or owner_meant[0] in ("global", "function"):
        return None
    own = calls.own_local(fa, owner)
    if own is not None and owner_meant[0] != "local":
        target = calls.field(own, field)  # type: ignore[arg-type]
        if target is not None:
            return target, resolved_by.LEXICAL_SCOPE
    target = calls.field(owner_meant, field)  # type: ignore[arg-type]
    if target is None:
        return None
    by = {
        "self": resolved_by.OWN_TYPE, "instance": resolved_by.RECEIVER_TYPE,
        "local": resolved_by.LEXICAL_SCOPE,
    }.get(owner_meant[0], resolved_by.IMPORT)
    return target, by


def _report(
    fa: "FileAnalysis", calls: _LuaCalls, node: "tree_sitter.Node", caller_id: str,
    written: str | None, owner: "tree_sitter.Node | None", field: str | None,
    on_receiver: bool, run_id: str,
) -> Edge:
    """The unresolved edge of a call nothing bound, with the facts it has.

    * bare ``f()``: the name as written; ``stdlib_member_name`` when it is a
      basic function (or a local alias of a library function, ``local insert
      = table.insert``); a module the name comes from as ``module_hint``.
    * ``M.f()`` / ``M:f()`` on a ``require`` binding: ``f``, the module as
      ``module_hint`` (and ``dst_ref``), and, for a module of the tree,
      ``qualifier=M`` (a fact of the call; the file's table binds no ``M``).
    * ``self:f()`` the own table had no ``f``: ``enclosing_class=T``.
    * a constructed receiver: ``receiver_type_hint=R`` (as written).
    * ``x:m()`` on any other receiver: ``m``, ``call_construct="method"``,
      ``stdlib_member_name`` for a name of the value-method screens.
    * ``X.f()`` / ``a.b.f()`` with no binding to a module (a global table, a
      library, a parameter): the path as written (``ngx.say``), as before,
      ``stdlib_member_name`` for a member of a screened library table.
    * no static name (``t[k]()``, ``f()()``): the callee's text.
    """
    source, line = fa.source, node.start_point[0] + 1
    if owner is None:
        if written is None:
            callee = node.children[0]
            return make_unresolved_edge(
                "lua", caller_id, " ".join(node_text(callee, source).split())[:80],
                line, PASS_ID, run_id,
            )
        meant = calls.denote(fa, written, node)
        module = _module_of(meant)
        return make_unresolved_edge(
            "lua", caller_id, written, line, PASS_ID, run_id,
            module_hint=module or "external",
            stdlib_member_name=meant[0] == "global" and _library_member(meant[1]),
        )
    owner_meant = calls.denote_value(fa, owner)
    name = field or written or ""
    if owner_meant is not None and owner_meant[0] == "self":
        own = owner_meant[2].child_by_field_name("name")
        table = _owner_of(own) if own is not None else None
        return make_unresolved_edge(
            "lua", caller_id, name, line, PASS_ID, run_id,
            enclosing_class=node_text(table, source) if table is not None else None,
            call_construct="method",
            stdlib_member_name=on_receiver and name in _SCREENED_METHODS,
        )
    if owner_meant is not None and owner_meant[0] == "instance":
        return make_unresolved_edge(
            "lua", caller_id, name, line, PASS_ID, run_id,
            receiver_type_hint=owner_meant[2], call_construct="method",
        )
    module = _module_of(owner_meant)
    if module is not None:
        in_tree = owner_meant is not None and owner_meant[0] == "module" and owner_meant[1] is not None
        return make_unresolved_edge(
            "lua", caller_id, name, line, PASS_ID, run_id,
            module_hint=module,
            qualifier=node_text(owner, source) if in_tree and owner.type == "identifier" else None,
            call_construct="function",
        )
    if on_receiver or owner_meant is None or field is None:
        return make_unresolved_edge(
            "lua", caller_id, name, line, PASS_ID, run_id,
            call_construct="method",
            stdlib_member_name=name in _SCREENED_METHODS,
        )
    path = _static_path(owner, source)
    written_path = ".".join([*(path or []), field]) if path else field
    return make_unresolved_edge(
        "lua", caller_id, written_path, line, PASS_ID, run_id,
        stdlib_member_name=owner_meant[0] == "global" and _library_member(f"{owner_meant[1]}.{field}"),
    )


def _module_of(meant: _Meant | None) -> str | None:
    """The ``require`` literal a denotation comes from: a module, or a field of
    one (``local f = require("a").f``)."""
    while meant is not None and meant[0] == "member":
        meant = meant[1]
    return meant[2] if meant is not None and meant[0] == "module" else None


def _import_scope() -> ImportScope:
    """A file's table for the shared call resolution (ADR-0064): files as its
    kind of module, the library's names screened, and NO binding.

    Every file gets one: its presence says this analyzer hands its unbound
    calls over, and that the name step may run for them. It binds no name
    because Lua's export rule is what a module file RETURNS (``return M``,
    ``return {f = f}``), and the analyzer already binds a call through a
    ``require`` on that rule (``_LuaCalls``). The linkers do not model it:
    they take "the file declares a function of that name", which on kong bound
    ``reports.log(ctx)`` to the module's ``local function log`` while the
    module exports ``log = function(ctx) ... end``, and ``endpoints.not_found()``
    to a local function the module does not export; and where the module
    declares no such name, the name step bound the one function of that name
    anywhere in the tree (9 wrong of 10 read: ``rand.random_string()``, a field
    the module fills by assignment). With no binding, a call through a
    ``require`` that the analyzer could not bind stays unresolved: unresolved
    beats a guess. The stub keeps its ``qualifier``, a fact of the call.
    """
    return ImportScope(
        module_kind=MODULES_ARE_FILES, library_names_screened=LIBRARY_NAMES_SCREENED,
    )


class LuaAnalyzer(TreeSitterAnalyzer):
    """Lua analyzer using tree-sitter-lua.

    Overrides ``analyze()`` because Lua uses a custom two-pass structure with
    per-file FileAnalysis, require-alias resolution, module-path mapping, and
    type-based method call resolution.
    """

    lang = "lua"
    file_patterns: ClassVar[list[str]] = ["*.lua"]
    grammar_module = "tree_sitter_lua"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Lua analysis with two-pass symbol/edge extraction."""
        start_time = time.time()

        # Create analysis run for provenance
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            skip_reason = (
                "Lua analysis skipped: requires tree-sitter-lua "
                "(pip install tree-sitter-lua)"
            )
            warnings.warn(skip_reason)
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=skip_reason,
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        import tree_sitter
        import tree_sitter_lua

        LUA_LANGUAGE = tree_sitter.Language(tree_sitter_lua.language())
        parser = tree_sitter.Parser(LUA_LANGUAGE)
        run_id = run.execution_id

        # Pass 1: Parse all files and extract symbols
        file_analyses: list[FileAnalysis] = []
        all_symbols: list[Symbol] = []
        files_analyzed = 0

        for lua_file in find_lua_files(repo_root):
            try:
                source = lua_file.read_bytes()
            except OSError:  # pragma: no cover
                continue

            tree = parser.parse(source)
            if tree.root_node is None:  # pragma: no cover - parser always returns root
                continue

            rel_path = str(lua_file.relative_to(repo_root))
            record_partial_parse(run, rel_path, tree)  # WI-bulaz

            # Create file symbol
            file_symbol = Symbol(
                id=make_file_id("lua", rel_path),
                name="file",
                kind="file",
                language="lua",
                path=rel_path,
                span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
                origin=PASS_ID,
                origin_run_id=run_id,
            )
            all_symbols.append(file_symbol)

            # Extract symbols
            file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
            all_symbols.extend(file_symbols)
            # File-scope subclass tables and constructed values. Kept OUT of
            # ``file_symbols`` and call resolution: they carry what framework
            # rows key on (``base_classes`` / ``constructed_from``) and are
            # not call targets, so call resolution is unchanged by them.
            all_symbols.extend(_binding_symbols(tree, source, rel_path, run_id))

            file_analyses.append(FileAnalysis(
                path=rel_path,
                source=source,
                tree=tree,
                symbols=file_symbols,
                declarations=symbols_at(file_symbols),
                scopes=_Scopes(source),
            ))
            files_analyzed += 1

        # Pass 2: edges. A call is bound only where Lua's own rules name its
        # callee (``_LuaCalls``); every other call goes to the shared call
        # resolution with its facts and the file's table (ADR-0064).
        calls = _LuaCalls({fa.path: fa for fa in file_analyses}, repo_root.resolve().name)
        all_edges: list[Edge] = []
        import_scopes: dict[str, ImportScope] = {}
        for fa in file_analyses:
            edges = _extract_edges_from_file(fa, calls, run_id)
            # ADR-0015 Tier 1: automatic dataflow annotation
            if _df_config is not None:
                annotate_dataflow(edges, fa.tree, fa.source, _df_config)
            all_edges.extend(edges)
            import_scopes[fa.path] = _import_scope()

        run.files_analyzed = files_analyzed

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
            import_scopes=import_scopes,
        )


_analyzer = LuaAnalyzer()


def is_lua_tree_sitter_available() -> bool:
    """Check if tree-sitter with Lua grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("lua")
def analyze_lua(repo_root: Path) -> AnalysisResult:
    """Analyze Lua files in a repository."""
    return _analyzer.analyze(repo_root)
