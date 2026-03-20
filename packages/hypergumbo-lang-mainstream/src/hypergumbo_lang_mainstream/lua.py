# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lua analysis pass using tree-sitter-lua.

This analyzer uses tree-sitter to parse Lua files and extract:
- Function declarations (global, local, dot-index `Table.func`)
- Method-style function definitions (`Table:method`)
- Function call relationships (direct, dot-index, method)
- require statements (imports) with alias tracking
- Cross-file require-alias call resolution

If tree-sitter with Lua support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-lua is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract symbols, build module path mapping
   - Pass 2: Detect calls and resolve via require-alias, type-based, or
     name-based strategies (in priority order)
4. Module path mapping: each file's relative path is converted to Lua
   module paths (e.g., `foo/bar.lua` -> `foo.bar`, `foo/init.lua` -> `foo`).
   This enables require-alias resolution.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-lua package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Lua-Specific Considerations
---------------------------
- Lua has both global functions (`function foo()`) and local functions
  (`local function foo()`)
- Method-style definitions (`Table:method`) are common for OOP patterns
- Dot-index definitions (`function _M.process()`) are the dominant
  export pattern in OpenResty/Kong Lua modules
- require() is the standard import mechanism
- Lua is dynamically typed, so call resolution is based on name matching.
  Method calls (obj:method()) get lower confidence (0.40x) than direct
  calls (func(), 0.85x) because common method names like connect, send,
  close collide across unrelated receiver objects
- Single-assignment type tracking: when a local variable is assigned from
  a known table/class (e.g., `local sock = MyClass` or `local sock = mod.tcp()`),
  the receiver type is tracked. Method calls on typed receivers resolve to
  `ReceiverType.method` with higher confidence (0.80x). This eliminates
  false positive edges from name collisions (e.g., sock:send vs pdk.response:send)
- Require-alias resolution: when `local X = require("foo.bar")` is followed
  by `X.method()` or `X:method()`, the call resolves to symbols defined in
  `foo/bar.lua` with 0.85 confidence. This is the dominant calling convention
  in Kong/OpenResty and eliminates the largest class of unresolved call edges.
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.dataflow import annotate_dataflow, get_dataflow_config

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("lua")

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

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]


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
                    stable_id=_analyzer.compute_stable_id(node, kind=kind),
                    shape_id=_analyzer.compute_shape_id(node),
                ))

    return symbols


def _find_enclosing_lua_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the function that contains this node by walking up the parent chain."""
    current = node.parent
    while current:
        if current.type == "function_declaration":
            name, _ = _get_function_name(current, source)
            if name in local_symbols:
                return local_symbols[name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_var_types(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract variable type assignments from local variable declarations.

    Tracks single-assignment patterns like:
    - ``local obj = MyClass`` → obj has type MyClass
    - ``local sock = mod.tcp()`` → sock has type mod.tcp (dot-index receiver)
    - ``local x = MyClass:new()`` → x has type MyClass (constructor pattern)

    Only tracks the first assignment to each variable within a file scope.
    Does not handle reassignment or conditional assignment — Lua's dynamic
    nature makes flow-sensitive analysis infeasible without a type system.

    Returns:
        Dict mapping variable name to inferred type string. The type string
        matches the naming convention used in symbol registration (e.g.,
        "MyClass" for ``function MyClass:method()`` definitions).
    """
    var_types: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "variable_declaration":
            continue

        # Find the assignment_statement child
        assign = find_child_by_type(node, "assignment_statement")
        if assign is None:  # pragma: no cover - tree-sitter always wraps in assignment
            continue

        # Extract variable name from variable_list
        var_list = find_child_by_type(assign, "variable_list")
        if var_list is None:  # pragma: no cover - assignment always has variable_list
            continue
        var_id = find_child_by_type(var_list, "identifier")
        if var_id is None:  # pragma: no cover - destructuring not tracked
            continue
        var_name = node_text(var_id, source)

        # Extract expression from expression_list
        expr_list = find_child_by_type(assign, "expression_list")
        if expr_list is None:  # pragma: no cover - assignment always has expression_list
            continue

        # Get first expression (the right-hand side value)
        expr = None
        for child in expr_list.children:
            if child.type not in (",",):
                expr = child
                break

        if expr is None:  # pragma: no cover - expression_list always has children
            continue

        # Infer type from expression
        inferred = _infer_type_from_expr(expr, source, var_types)
        if inferred and var_name not in var_types:
            var_types[var_name] = inferred

    return var_types


def _extract_require_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract require-alias mappings from local variable declarations.

    Detects the pattern ``local X = require("foo.bar")`` and maps variable
    name ``X`` to module path ``foo.bar``. Only tracks the first assignment
    to each variable.

    Returns:
        Dict mapping variable name to the require'd module path string
        (e.g., ``{"bar": "foo.bar", "utils": "kong.tools.utils"}``).
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "variable_declaration":
            continue

        assign = find_child_by_type(node, "assignment_statement")
        if assign is None:  # pragma: no cover
            continue

        var_list = find_child_by_type(assign, "variable_list")
        if var_list is None:  # pragma: no cover
            continue
        var_id = find_child_by_type(var_list, "identifier")
        if var_id is None:  # pragma: no cover
            continue
        var_name = node_text(var_id, source)

        expr_list = find_child_by_type(assign, "expression_list")
        if expr_list is None:  # pragma: no cover
            continue

        # Look for require("...") call in expression list
        for child in expr_list.children:
            if child.type == "function_call":
                func_id = find_child_by_type(child, "identifier")
                if func_id and node_text(func_id, source) == "require":
                    args = find_child_by_type(child, "arguments")
                    if args:
                        for arg in args.children:
                            if arg.type == "string":
                                content = find_child_by_type(arg, "string_content")
                                if content and var_name not in aliases:
                                    aliases[var_name] = node_text(content, source)
                break  # Only check first expression

    return aliases


def _build_module_path(file_path: str) -> list[str]:
    """Compute possible Lua module paths for a file.

    Maps file paths to the module names that ``require()`` would use:
    - ``foo/bar.lua`` → ``foo.bar``
    - ``foo/bar/init.lua`` → ``foo.bar`` (directory module)

    Returns a list because a file might match multiple module paths
    (e.g., ``foo/init.lua`` matches both ``foo.init`` and ``foo``).
    """
    parts = file_path.replace("\\", "/").split("/")
    # Strip .lua extension from last part
    if parts and parts[-1].endswith(".lua"):
        parts[-1] = parts[-1][:-4]

    paths = [".".join(parts)]

    # init.lua → directory module (foo/init.lua → foo)
    if parts and parts[-1] == "init":
        paths.append(".".join(parts[:-1]))

    return paths


def _infer_type_from_expr(
    expr: "tree_sitter.Node",
    source: bytes,
    var_types: dict[str, str],
) -> str | None:
    """Infer the type of a variable from its assigned expression.

    Handles three core Lua assignment patterns:

    1. Identifier: ``local obj = MyClass`` → type is "MyClass"
       Also follows transitive aliases: if MyClass was previously assigned
       from SomeTable, resolves to SomeTable.

    2. Function call with dot-index receiver: ``local sock = mod.tcp()``
       → type is "mod" (the table that owns tcp). This covers the common
       OpenResty pattern ``local sock = ngx.socket.tcp()``.

    3. Method call (constructor): ``local obj = MyClass:new()``
       → type is "MyClass" (Lua OOP convention: :new() returns self).

    Returns None for unrecognizable expressions (table literals, complex
    expressions, multi-return calls).
    """
    # Case 1: simple identifier (local obj = MyClass)
    if expr.type == "identifier":
        name = node_text(expr, source)
        # Follow transitive aliases
        return var_types.get(name, name)

    # Case 2: function call
    if expr.type == "function_call":
        func_part = expr.children[0] if expr.children else None
        if func_part is None:  # pragma: no cover - function_call always has children
            return None

        # Case 2a: method call (MyClass:new()) → type is MyClass
        if func_part.type == "method_index_expression":
            receiver_id = find_child_by_type(func_part, "identifier")
            if receiver_id:
                receiver_name = node_text(receiver_id, source)
                return var_types.get(receiver_name, receiver_name)

        # Case 2b: dot-index call (mod.tcp()) → type is mod
        if func_part.type == "dot_index_expression":
            # Get the receiver (first identifier or nested dot expression)
            first_child = func_part.children[0] if func_part.children else None
            if first_child and first_child.type == "identifier":
                receiver_name = node_text(first_child, source)
                return var_types.get(receiver_name, receiver_name)

    return None


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    module_symbols: dict[str, list[Symbol]] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Lua file.

    Detects:
    - function_call: Direct function calls
    - Method calls (obj:method()) with optional type-based resolution
    - Dot-index calls (X.method()) with require-alias resolution
    - require statements

    Type-based resolution: when a method call's receiver has a known type
    (from single-assignment tracking via ``_extract_var_types``), the call
    is resolved to ``ReceiverType.method`` with 0.80 confidence instead of
    falling back to name-only lookup at 0.40 confidence.

    Require-alias resolution: when ``local X = require("foo.bar")`` is
    followed by ``X.method()`` or ``X:method()``, the call is resolved
    to symbols defined in the module file (foo/bar.lua) with 0.85 confidence
    and evidence_type ``require_alias_call``.

    Args:
        module_symbols: mapping from module path (e.g., "foo.bar") to
            symbols defined in that module's file. Built during Pass 1
            from the file→module path mapping.
    """
    edges: list[Edge] = []
    _caller_path = str(file_path)
    file_id = make_file_id("lua", file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    # Confidence multipliers for call resolution. Direct calls (func())
    # use a higher base because function names tend to be unique. Method
    # calls (obj:method()) use a lower base because Lua lacks static types
    # and common method names (connect, send, close) collide across
    # unrelated receiver objects, producing false positive edges.
    CONFIDENCE_DIRECT_CALL = 0.85
    CONFIDENCE_METHOD_CALL = 0.40
    CONFIDENCE_TYPED_METHOD_CALL = 0.80
    CONFIDENCE_REQUIRE_ALIAS = 0.85

    # Extract variable type assignments for receiver-based method resolution
    var_types = _extract_var_types(tree, source)

    # Extract require aliases for module-scoped call resolution
    require_aliases = _extract_require_aliases(tree, source)
    if module_symbols is None:  # pragma: no cover - backward compat guard
        module_symbols = {}

    for node in iter_tree(tree.root_node):
        if node.type == "function_call":
            # Extract function name being called
            callee_name = None
            is_method_call = False
            is_dot_call = False
            receiver_name = None

            # Direct call: identifier(args)
            first_child = node.children[0] if node.children else None
            if first_child:
                if first_child.type == "identifier":
                    callee_name = node_text(first_child, source)
                elif first_child.type == "method_index_expression":
                    # Method call: obj:method(args)
                    # Extract both receiver and method name
                    is_method_call = True
                    identifiers = []
                    for child in first_child.children:
                        if child.type == "identifier":
                            identifiers.append(node_text(child, source))
                    if len(identifiers) >= 2:
                        receiver_name = identifiers[0]
                        callee_name = identifiers[1]
                    elif len(identifiers) == 1:  # pragma: no cover - method_index always has 2 ids
                        callee_name = identifiers[0]
                elif first_child.type == "dot_index_expression":
                    # Dot call: X.method(args)
                    is_dot_call = True
                    identifiers = []
                    for child in first_child.children:
                        if child.type == "identifier":
                            identifiers.append(node_text(child, source))
                    if len(identifiers) >= 2:
                        receiver_name = identifiers[0]
                        callee_name = identifiers[1]
                    elif len(identifiers) == 1:  # pragma: no cover
                        callee_name = identifiers[0]

            # Check for require() call - special handling for imports
            if callee_name == "require":
                # Find the argument (module name)
                args_node = find_child_by_type(node, "arguments")
                if args_node:
                    for child in args_node.children:
                        if child.type == "string":
                            # Extract string content
                            content_node = find_child_by_type(child, "string_content")
                            if content_node:
                                module_name = node_text(content_node, source)
                                # Create import edge
                                module_id = f"lua:{module_name}:0-0:module:module"
                                edge = Edge.create(
                                    src=file_id,
                                    dst=module_id,
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="require",
                                    confidence=0.95,
                                )
                                edges.append(edge)
            elif callee_name:
                # Regular function call
                # Find the caller (enclosing function)
                caller = _find_enclosing_lua_function(node, source, local_symbols)
                if caller:
                    # Try require-alias resolution first (highest signal)
                    require_resolved = False
                    recv = receiver_name
                    if recv and recv in require_aliases and (is_dot_call or is_method_call):
                        mod_path = require_aliases[recv]
                        mod_syms = module_symbols.get(mod_path, [])
                        # Search for a symbol whose name ends with .callee_name
                        target = _find_module_symbol(mod_syms, callee_name)
                        if target:
                            edge = Edge.create(
                                src=caller.id,
                                dst=target.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="require_alias_call",
                                confidence=CONFIDENCE_REQUIRE_ALIAS,
                            )
                            edges.append(edge)
                            require_resolved = True

                    if require_resolved:
                        pass  # Already resolved via require alias
                    elif is_method_call:
                        # Try type-based resolution for method calls
                        typed_resolved = False
                        if receiver_name and receiver_name in var_types:
                            receiver_type = var_types[receiver_name]
                            qualified_name = f"{receiver_type}.{callee_name}"
                            typed_lookup = resolver.lookup(qualified_name, caller_path=_caller_path)
                            if typed_lookup.found:
                                confidence = CONFIDENCE_TYPED_METHOD_CALL * typed_lookup.confidence
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=typed_lookup.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="method_call_typed",
                                    confidence=confidence,
                                )
                                edges.append(edge)
                                typed_resolved = True

                        if not typed_resolved:
                            # Fallback: name-only resolution
                            lookup_result = resolver.lookup(callee_name, caller_path=_caller_path)
                            callee = lookup_result.symbol if lookup_result.found else None
                            base = CONFIDENCE_METHOD_CALL
                            confidence = base * lookup_result.confidence if lookup_result.found else 0.50
                            if callee:
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=confidence,
                                )
                                edges.append(edge)
                            else:
                                # Unresolved call
                                unresolved_id = f"lua:?:0-0:{callee_name}:function"
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=unresolved_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=0.50,
                                )
                                edges.append(edge)
                    elif is_dot_call:
                        # Dot call without require alias — try qualified name
                        if receiver_name:
                            qualified = f"{receiver_name}.{callee_name}"
                            lookup_result = resolver.lookup(qualified, caller_path=_caller_path)
                            if lookup_result.found:
                                confidence = CONFIDENCE_DIRECT_CALL * lookup_result.confidence
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=confidence,
                                )
                                edges.append(edge)
                            else:
                                unresolved_id = f"lua:?:0-0:{qualified}:function"
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=unresolved_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=0.50,
                                )
                                edges.append(edge)
                    else:
                        # Direct call: func(args)
                        lookup_result = resolver.lookup(callee_name, caller_path=_caller_path)
                        callee = lookup_result.symbol if lookup_result.found else None
                        confidence = CONFIDENCE_DIRECT_CALL * lookup_result.confidence if lookup_result.found else 0.50
                        if callee:
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=confidence,
                            )
                            edges.append(edge)
                        else:
                            unresolved_id = f"lua:?:0-0:{callee_name}:function"
                            edge = Edge.create(
                                src=caller.id,
                                dst=unresolved_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=0.50,
                            )
                            edges.append(edge)

    return edges


def _find_module_symbol(symbols: list[Symbol], method_name: str) -> Symbol | None:
    """Find a symbol in a module's exports that matches the given method name.

    Searches for symbols whose name ends with ``.method_name`` (e.g.,
    ``_M.process`` matches method_name ``process``). This handles the
    common Lua pattern where modules export via a local table:

    .. code-block:: lua

        local _M = {}
        function _M.process(data) ... end
        return _M

    Returns the first matching symbol, or None if no match found.
    """
    suffix = f".{method_name}"
    for sym in symbols:
        if sym.name.endswith(suffix):
            return sym
    return None


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
            )

        import tree_sitter
        import tree_sitter_lua

        LUA_LANGUAGE = tree_sitter.Language(tree_sitter_lua.language())
        parser = tree_sitter.Parser(LUA_LANGUAGE)
        run_id = run.execution_id

        # Pass 1: Parse all files and extract symbols
        file_analyses: list[FileAnalysis] = []
        all_symbols: list[Symbol] = []
        global_symbol_registry: dict[str, Symbol] = {}
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

            # Register symbols globally (for cross-file resolution)
            for sym in file_symbols:
                global_symbol_registry[sym.name] = sym

            file_analyses.append(FileAnalysis(
                path=rel_path,
                source=source,
                tree=tree,
                symbols=file_symbols,
            ))
            files_analyzed += 1

        # Build module path -> symbols mapping for require-alias resolution.
        # Maps each possible module path (e.g., "foo.bar") to the symbols
        # defined in its file (e.g., foo/bar.lua).
        module_symbols: dict[str, list[Symbol]] = {}
        for fa in file_analyses:
            for mod_path in _build_module_path(fa.path):
                module_symbols[mod_path] = fa.symbols

        # Pass 2: Extract edges with cross-file resolution
        all_edges: list[Edge] = []
        resolver = NameResolver(global_symbol_registry)

        for fa in file_analyses:
            edges = _extract_edges_from_file(
                fa.tree,  # type: ignore
                fa.source,
                fa.path,
                fa.symbols,
                resolver,
                run_id,
                module_symbols=module_symbols,
            )
            # ADR-0015 Tier 1: automatic dataflow annotation
            if _df_config is not None:
                annotate_dataflow(edges, fa.tree, fa.source, _df_config)
            all_edges.extend(edges)

        run.files_analyzed = files_analyzed

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = LuaAnalyzer()


def is_lua_tree_sitter_available() -> bool:
    """Check if tree-sitter with Lua grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("lua")
def analyze_lua(repo_root: Path) -> AnalysisResult:
    """Analyze Lua files in a repository."""
    return _analyzer.analyze(repo_root)
