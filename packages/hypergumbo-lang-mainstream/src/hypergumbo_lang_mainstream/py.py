# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python AST analysis pass.

This analyzer uses Python's built-in ast module to extract symbols and
relationships from Python source files, with no external dependencies.

How It Works
------------
Analysis proceeds in two passes for cross-file resolution:

**Pass 1 - Symbol Collection:**
- Parse each .py file with ast.parse()
- Extract top-level functions and classes as symbols
- Extract methods nested inside classes
- Build import mappings for cross-file resolution
- Compute stable_id (signature-based) and shape_id (structure-based)
- Extract rich metadata (decorators, base classes, parameters) per ADR-3aaa

**Pass 2 - Edge Extraction:**
- Walk AST to find function/method call sites
- Resolve callees using local symbols first, then imports
- Detect self.method() calls within classes
- Detect self.field.method() calls using field type inference from __init__
- Detect ClassName() instantiation patterns
- Track return type annotations for variable type inference
- Create import edges from files to imported symbols

Detected Patterns
-----------------
- Function calls: helper(), module.func()
- Method calls: self.method(), obj.method(), self.field.method()
- Class instantiation: ClassName()
- Module attribute reads: os.environ, sys.argv, sys.path — bare
  (non-called) ``imported_module.attribute`` accesses. Emits
  ``module_attr_ref`` edges so IO-primitive catalog ``attributes:``
  entries become reachable by ``io-boundaries`` (WI-guhok).
- Imports: from X import Y, import X
- Django URL patterns: path(), re_path(), url() calls in urls.py
- Flask URL rules: app.add_url_rule() calls

Route Detection Architecture
-----------------------------
Call-based URL routing (Django path(), Flask add_url_rule()) produces two
outputs that serve different downstream consumers:

1. **UsageContext records** — matched by YAML framework patterns (django.yaml,
   flask.yaml) to enrich *handler* symbols with ``concept: route`` metadata.
   This lets the enrichment layer tag view functions as route handlers.

2. **Route symbols** (``kind="route"``) — consumed by the ``route_handler``
   linker to create ``routes_to`` edges from route entities to handler symbols.
   These are first-class nodes in the IR representing the route itself.

Both are derived from the same extraction pass (_extract_django_usage_contexts,
_extract_flask_usage_contexts). Route symbols are created from the UsageContext
metadata at the callsite. This avoids duplicating the AST-walking logic while
preserving both outputs. Go and JS/TS analyzers follow the same dual-output
pattern.

ID Schemes
----------
- **stable_id**: sha256 of signature (param count, arity flags, decorators).
  Survives renames and moves if signature unchanged.
- **shape_id**: sha256 of AST structure (control flow, nesting).
  Detects clones with different variable names.

Rich Metadata (ADR-3aaa)
------------------------
Symbols include structured metadata in `meta` dict:
- **decorators**: List of decorator info with name, args, kwargs.
  Example: `[{"name": "app.get", "args": ["/users"], "kwargs": {"tags": ["api"]}}]`
- **base_classes**: List of base class names for classes.
  Example: `["BaseModel", "Generic[T]"]`
- **parameters**: List of parameter info for functions/methods.
  Example: `[{"name": "x", "type": "int", "default": False}]`

Why This Design
---------------
- Built-in ast module requires no dependencies and handles all Python syntax
- Two-pass approach enables cross-file call resolution via imports
- col_offset == 0 heuristic distinguishes top-level from nested functions
- Import resolution handles both absolute and relative imports
- Rich metadata feeds YAML-driven framework pattern enrichment (ADR-3aaa)
"""
import ast
import hashlib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from hypergumbo_core.dataflow import annotate_dataflow_ast, get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    assemble_stable_id,
    make_file_stable_id,
    make_route_stable_id,
    make_typed_stable_id,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_lang_mainstream._pyscope import NestedDef, Scope, ScopeStack

if TYPE_CHECKING:
    from hypergumbo_core.symbol_resolution import SymbolResolver


def find_python_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Python files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.py"], max_files=max_files)


def _python_visibility_modifiers(name: str) -> list[str]:
    """Derive visibility modifiers from Python naming convention.

    Single underscore prefix (``_name``) = private.
    Double underscore prefix without trailing double underscore (``__name``)
    = private (name-mangled).
    Dunders (``__name__``) are special methods, not private.
    No prefix = public (empty list, since Python has no explicit modifier).
    """
    # Strip qualified prefix: "Class._method" → check "_method"
    short = name.rsplit(".", 1)[-1] if "." in name else name
    if short.startswith("_") and not (short.startswith("__") and short.endswith("__")):
        return ["private"]
    return []


def _extract_module_all(tree: "ast.Module") -> frozenset[str] | None:
    """Extract the module-level ``__all__`` name set from *tree*.

    WI-gipag (WI-zimum Phase 2): the module-level ``__all__`` list
    declares the public API surface of a Python module. When present,
    only names in ``__all__`` should be flagged ``is_exported=True``;
    all other top-level symbols remain un-exported regardless of
    naming convention.

    Returns the frozenset of string names in ``__all__`` (possibly
    empty), or ``None`` if the file does not define a module-level
    ``__all__`` assignment. The ``None`` vs empty-set distinction
    matters because the callers use it to decide between
    ``__all__``-driven filtering and the fallback leading-underscore
    rule.

    Supported forms:
    - ``__all__ = ["foo", "bar"]``       (list literal)
    - ``__all__ = ("foo", "bar")``       (tuple literal)
    - ``__all__: list[str] = ["foo"]``   (annotated assignment)

    Non-literal forms (``__all__ = other_module.__all__``,
    ``__all__ += ["baz"]``, list comprehensions) are not
    interpreted — they are rare and would require evaluation. The
    caller treats "unparseable ``__all__``" the same as "no
    ``__all__``" and falls back to the leading-underscore rule.
    """
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None

        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                target_name = "__all__"
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "__all__":
                target_name = "__all__"
                value = node.value

        if target_name is None or value is None:
            continue

        if isinstance(value, (ast.List, ast.Tuple)):
            names: list[str] = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.append(elt.value)
                # Anything non-literal (e.g., a Name reference, a call)
                # makes the __all__ interpretation ambiguous. Return
                # None to fall back to the leading-underscore rule.
                else:
                    return None
            return frozenset(names)
        # __all__ set via a non-literal expression — unparseable.
        return None
    return None


def _is_python_top_level_exported(
    name: str, module_all: frozenset[str] | None,
) -> bool:
    """Return True if *name* is part of the Python module's public API.

    WI-gipag: when the module has an ``__all__`` list, membership in
    ``__all__`` is authoritative. Otherwise, the leading-underscore
    convention applies — names not starting with ``_`` are public.
    Dunders (``__name__``) are never considered exported by this rule;
    they are special methods / module hooks, not user-facing API.
    """
    if module_all is not None:
        return name in module_all
    if name.startswith("_"):
        return False
    return True


def normalize_python_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Python signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(
        signature, type_params, return_sep="->", skip_self=True,
    )


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID in format {lang}:{file}:{start}-{end}:{name}:{kind}."""
    return f"python:{path}:{line}-{end_line}:{name}:{kind}"


def _emit_module_level_assign_symbols(
    tree: "ast.Module",
    py_file: Path,
    module_all: frozenset[str] | None,
) -> list[Symbol]:
    """Emit ``Symbol(kind="variable", ...)`` for each top-level binding.

    Without this pass, ``from <mod> import NAME`` for any module-level
    constant (e.g. ``LANGUAGE_ALIASES``, ``PASS_VERSION``) misses the
    cross-file lookup and synthesises a tier-3 ``external_symbol`` —
    151 such ALL-CAPS externals on hypergumbo self-analysis (WI-gafog E2).

    Walks ``tree.body`` (top-level statements only). Handles:

    * ``ast.Assign`` with one or more ``Name`` targets, including
      tuple/list-unpacking targets like ``A, B = 1, 2``.
    * ``ast.AnnAssign`` with a ``Name`` target (``X: int = 1`` or ``X: int``).

    Does NOT emit for:

    * ``ast.AugAssign`` (``X += 1``) — mutation, not a fresh binding.
    * Subscript or attribute targets (``X[0] = 1``, ``X.y = 1``).
    * Names defined inside class or function bodies — those are not
      module-level (the walk only inspects ``tree.body``).
    """
    out: list[Symbol] = []
    for node in tree.body:
        targets: list[ast.Name] = []
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    for el in t.elts:
                        if isinstance(el, ast.Name):
                            targets.append(el)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target)
        else:
            continue
        for tgt in targets:
            line = tgt.lineno
            end_line = node.end_lineno or line
            span = Span(
                start_line=line,
                end_line=end_line,
                start_col=tgt.col_offset,
                end_col=node.end_col_offset or 0,
            )
            out.append(
                Symbol(
                    id=_make_symbol_id(str(py_file), line, end_line, tgt.id, "variable"),
                    name=tgt.id,
                    kind="variable",
                    language="python",
                    path=str(py_file),
                    span=span,
                    origin="",
                    origin_run_id="",
                    modifiers=_python_visibility_modifiers(tgt.id),
                    is_exported=_is_python_top_level_exported(tgt.id, module_all),
                )
            )
    return out


def _make_file_id(path: str) -> str:
    """Generate ID for a Python file node (used as import edge source)."""
    return f"python:{path}:1-1:file:file"


def _make_module_id(module_name: str) -> str:
    """Generate ID for an external module (used as import edge destination)."""
    return f"python:{module_name}:0-0:module:module"


def _extract_return_type_name(signature: str | None) -> str | None:
    """Extract simple return type name from a function signature string.

    Parses signatures like "(x: int) -> MyClass" and returns "MyClass".
    Only handles simple (non-generic) return types — returns None for
    complex types like "Optional[X]", "list[X]", "X | Y", etc.

    Args:
        signature: Function signature string from Symbol.signature.

    Returns:
        The simple class name if found, None otherwise.
    """
    if not signature or " -> " not in signature:
        return None
    ret_part = signature.rsplit(" -> ", 1)[1]
    # Only handle simple names (identifiers), not generics or unions
    if ret_part.isidentifier():
        return ret_part
    return None


def _resolve_return_type_class(
    type_name: str,
    func_symbol: "Symbol",
    local_symbols: dict[str, "Symbol"],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], "Symbol"],
    resolver: "SymbolResolver | None" = None,
    sym_by_path_name: dict[tuple[str, str], "Symbol"] | None = None,
) -> "Symbol | None":
    """Resolve a return type name to a class Symbol.

    Searches for the class in three places (in order):
    1. The caller's local symbols (same file as the call site)
    2. The caller's imports
    3. The function's own module (the return type is usually co-located
       with the function that returns it)

    Only returns symbols with kind == "class".

    Args:
        type_name: Simple class name (e.g., "ServiceClient").
        func_symbol: The function Symbol whose return type we're resolving.
        local_symbols: Symbols defined in the caller's file.
        imports: Import mappings from the caller's file.
        global_symbols: All symbols across the project.
        resolver: Optional SymbolResolver for efficient lookups.

    Returns:
        The class Symbol if found, None otherwise.
    """
    # Check caller's local symbols first
    sym = local_symbols.get(type_name)
    if sym and sym.kind == "class":
        return sym
    # Check caller's imports
    if type_name in imports:
        module_name, original_name = imports[type_name]
        sym = _lookup_symbol_by_module(
            global_symbols, module_name, original_name, resolver=resolver
        )
        if sym and sym.kind == "class":
            return sym
    # Check function's own module — the return type class is typically
    # defined in the same file as the function
    if sym_by_path_name is not None:
        sym = sym_by_path_name.get((func_symbol.path, type_name))
        if sym and sym.kind == "class":
            return sym
    return None


def _lookup_symbol_by_module(
    global_symbols: dict[tuple[str, str], "Symbol"],
    module_name: str,
    symbol_name: str,
    *,
    resolver: "SymbolResolver | None" = None,
) -> "Symbol | None":
    """Look up a symbol with suffix-based module matching.

    When an import says 'from app.crud import X' but the file is registered
    as 'backend.app.crud', exact lookup fails. This function handles such
    cases by trying suffix matching.

    This is a thin wrapper around the shared SymbolResolver. For repeated
    lookups, pass a pre-built resolver for better performance (cached indexes).

    Args:
        global_symbols: Map of (module, name) -> Symbol
        module_name: The module name from the import statement
        symbol_name: The symbol name being imported
        resolver: Optional pre-built SymbolResolver for cached lookups.

    Returns:
        The matching Symbol, or None if not found.
    """
    if resolver is not None:
        result = resolver.lookup(module_name, symbol_name)
        return result.symbol

    # Fallback: use the shared lookup_symbol function (creates new resolver)
    from hypergumbo_core.symbol_resolution import lookup_symbol
    return lookup_symbol(global_symbols, module_name, symbol_name)


# WI-fuvuj: stdlib I/O constructors whose return value's type we can infer
# from the constructor name alone. Key = the qualified constructor name
# (bare for builtins like ``open``; ``module.attr`` for module constructors
# like ``socket.socket``). Value = the catalog module string the inferred
# receiver's method-call dst will carry, so io-boundary's module-filter path
# disambiguates ``f.read()`` / ``s.send()`` into the right boundary bucket
# instead of the undifferentiated ``external_potential`` bucket.
#
# The file-object value MUST be exactly ``"file"`` — it is coordinated with
# the synthetic ``file`` module in the python.yaml catalog (fs_read read/
# readline/readlines, fs_write write/writelines).
EXTERNAL_CONSTRUCTOR_TYPES = {"open": "file", "socket.socket": "socket.socket"}

# Django URL pattern functions (call-based routing)
# These emit UsageContext records for YAML pattern matching (v1.1.x)
DJANGO_URL_FUNCTIONS = {"path", "re_path", "url"}

# Flask/FastAPI call-based URL routing functions.
# Flask's add_url_rule() is the call-based alternative to @app.route().
# FastAPI's add_api_route() registers routes programmatically instead of
# using @router.get() decorators.  Both take a path string as the first
# argument and a handler function as a subsequent argument.
# Flask-RESTful's add_resource() takes the resource class as the first
# argument and URL path(s) as subsequent arguments.
FLASK_URL_FUNCTIONS = {"add_url_rule", "add_api_route", "add_resource"}

# Starlette routing classes. Unlike Flask's call-based functions,
# Starlette's Route(...) and WebSocketRoute(...) are bare constructor calls,
# not method calls on an app or router. We require import-scoped matching
# (the name must be imported from starlette.routing) to avoid false positives
# from any other Route class a repo defines locally.
STARLETTE_ROUTE_FUNCTIONS = {"Route", "WebSocketRoute"}
_STARLETTE_ROUTING_MODULE = "starlette.routing"


def _ast_value_to_python(node: ast.expr) -> str | int | float | bool | list | dict | None:
    """Convert an AST expression to a Python value representation.

    For simple literals, returns the actual value.
    For complex expressions (names, calls, etc.), returns string representation.
    """
    if isinstance(node, ast.Constant):
        # Handle non-JSON-serializable constants
        if node.value is ...:
            return "..."
        if isinstance(node.value, complex):
            return str(node.value)  # "1+2j" format
        if isinstance(node.value, bytes):
            return repr(node.value)  # "b'...'" format
        return node.value
    elif isinstance(node, ast.Name):
        # Variable reference - return name as string
        return node.id
    elif isinstance(node, ast.List):
        return [_ast_value_to_python(elt) for elt in node.elts]
    elif isinstance(node, ast.Tuple):
        return [_ast_value_to_python(elt) for elt in node.elts]
    elif isinstance(node, ast.Dict):
        result = {}
        for k, v in zip(node.keys, node.values, strict=True):
            if k is not None:
                key = _ast_value_to_python(k)
                if isinstance(key, str):
                    result[key] = _ast_value_to_python(v)
        return result
    elif isinstance(node, ast.Attribute):
        # e.g., SomeClass.field -> "SomeClass.field"
        return _format_annotation(node)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        # Negative number
        val = _ast_value_to_python(node.operand)
        if isinstance(val, (int, float)):
            return -val
        return f"-{val}"  # pragma: no cover - defensive for non-numeric negation
    elif isinstance(node, ast.Call):
        # Function call as decorator arg, e.g., _add_static_prefix("/health").
        # If the first positional argument is a resolvable literal, return it.
        # This handles wrapper patterns common in Flask/FastAPI where a helper
        # function wraps a route path string.
        if node.args:
            first_arg = _ast_value_to_python(node.args[0])
            if isinstance(first_arg, str) and first_arg != "<complex>":
                return first_arg
        # Fall through to string representation
        return _format_annotation(node) or "<complex>"  # pragma: no cover
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        # Complex number literal like 1+2j or 1-2j
        left = _ast_value_to_python(node.left)
        right = _ast_value_to_python(node.right)
        # Check if this looks like a complex number (real +/- imaginary)
        if isinstance(left, (int, float)) and isinstance(right, str) and right.endswith("j"):
            op = "+" if isinstance(node.op, ast.Add) else "-"
            return f"({left}{op}{right})"
        # Fall through to string representation for other BinOps
        return _format_annotation(node) or "<binop>"  # pragma: no cover
    else:
        # Complex expression - return string representation
        return _format_annotation(node) or "<complex>"  # pragma: no cover


def _extract_decorator_info(dec: ast.expr) -> dict[str, object]:
    """Extract full decorator information including arguments.

    Returns a dict with:
        name: Decorator name (e.g., "app.get", "dataclass")
        args: List of positional arguments
        kwargs: Dict of keyword arguments
    """
    name = ""
    args: list[object] = []
    kwargs: dict[str, object] = {}

    if isinstance(dec, ast.Name):
        # @decorator
        name = dec.id
    elif isinstance(dec, ast.Attribute):
        # @module.decorator (without call)
        name = _format_annotation(dec)
    elif isinstance(dec, ast.Call):
        # @decorator(...) or @module.decorator(...)
        if isinstance(dec.func, ast.Name):
            name = dec.func.id
        elif isinstance(dec.func, ast.Attribute):
            name = _format_annotation(dec.func)
        else:
            name = "<unknown>"  # pragma: no cover - defensive for unusual decorator forms

        # Extract positional arguments
        for arg in dec.args:
            args.append(_ast_value_to_python(arg))

        # Extract keyword arguments
        for kw in dec.keywords:
            if kw.arg is not None:  # Skip **kwargs unpacking
                kwargs[kw.arg] = _ast_value_to_python(kw.value)

    return {"name": name, "args": args, "kwargs": kwargs}


def _extract_parameters_info(
    args: ast.arguments, exclude_self: bool = False
) -> list[dict[str, object]]:
    """Extract structured parameter information from function arguments.

    Args:
        args: AST arguments node
        exclude_self: If True, skip 'self' and 'cls' parameters

    Returns:
        List of dicts with name, type, and default keys
    """
    params: list[dict[str, object]] = []
    defaults_offset = len(args.args) - len(args.defaults)

    for i, arg in enumerate(args.args):
        if exclude_self and i == 0 and arg.arg in ("self", "cls"):
            continue
        has_default = i >= defaults_offset
        type_str = _format_annotation(arg.annotation) if arg.annotation else None
        params.append({
            "name": arg.arg,
            "type": type_str if type_str else None,
            "default": has_default,
        })

    # Handle *args
    if args.vararg:
        type_str = _format_annotation(args.vararg.annotation) if args.vararg.annotation else None
        params.append({
            "name": f"*{args.vararg.arg}",
            "type": type_str if type_str else None,
            "default": False,
        })

    # Handle **kwargs
    if args.kwarg:
        type_str = _format_annotation(args.kwarg.annotation) if args.kwarg.annotation else None
        params.append({
            "name": f"**{args.kwarg.arg}",
            "type": type_str if type_str else None,
            "default": False,
        })

    return params


def _format_annotation(node: ast.expr) -> str:
    """Format a type annotation node to a readable string."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Subscript):
        # e.g., List[int], Dict[str, int]
        base = _format_annotation(node.value)
        slice_val = _format_annotation(node.slice)
        return f"{base}[{slice_val}]"
    elif isinstance(node, ast.Tuple):
        # e.g., (int, str) for Dict keys
        elts = [_format_annotation(e) for e in node.elts]
        return ", ".join(elts)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # Union types: X | Y
        left = _format_annotation(node.left)
        right = _format_annotation(node.right)
        return f"{left} | {right}"
    elif isinstance(node, ast.Attribute):
        # e.g., typing.Optional
        value = _format_annotation(node.value)
        return f"{value}.{node.attr}"
    else:
        return ""  # pragma: no cover - defensive fallback for unknown AST types


def _format_arg(arg: ast.arg) -> str:
    """Format a single function argument."""
    result = arg.arg
    if arg.annotation:
        ann = _format_annotation(arg.annotation)
        if ann:
            result += f": {ann}"
    return result


def _format_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, max_len: int = 60) -> str:
    """Format a function signature from AST node.

    Args:
        node: AST FunctionDef or AsyncFunctionDef node.
        max_len: Maximum length of signature (default 60).

    Returns:
        Formatted signature string like "(x: int, y: str) -> bool".
    """
    args = node.args
    all_args: list[str] = []

    # Positional-only args (before /)
    for arg in args.posonlyargs:
        all_args.append(_format_arg(arg))

    # Regular args
    for i, arg in enumerate(args.args):
        arg_str = _format_arg(arg)
        # Check for default value
        num_defaults = len(args.defaults)
        num_args = len(args.args)
        default_idx = i - (num_args - num_defaults)
        if 0 <= default_idx < num_defaults:
            arg_str += "=…"
        all_args.append(arg_str)

    # *args
    if args.vararg:
        all_args.append(f"*{args.vararg.arg}")

    # Keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        arg_str = _format_arg(arg)
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            arg_str += "=…"
        all_args.append(arg_str)

    # **kwargs
    if args.kwarg:
        all_args.append(f"**{args.kwarg.arg}")

    sig = "(" + ", ".join(all_args) + ")"

    # Add return type annotation if present
    if node.returns:
        ret_type = _format_annotation(node.returns)
        if ret_type:
            sig += f" -> {ret_type}"

    # Truncate if too long
    if len(sig) > max_len:
        sig = sig[:max_len - 1] + "…"

    return sig


def _has_module_level_code(tree: ast.Module) -> bool:
    """Check if a module has executable code at module level.

    Returns True if the module has statements that aren't just imports,
    function/class definitions, or docstrings. These files need a <module>
    pseudo-node so module-level code has an enclosing scope for edges.

    Examples of module-level code:
    - producer.produce(topic, value)  # Function calls
    - config = load_config()          # Assignments
    - if __name__ == '__main__': ...  # Control flow
    """
    for i, node in enumerate(tree.body):
        # Skip docstrings (first constant string expression)
        if i == 0 and isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue

        # Skip imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        # Skip function/class definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        # Skip pass statements
        if isinstance(node, ast.Pass):
            continue

        # Skip type aliases and annotations
        if isinstance(node, ast.AnnAssign):
            continue

        # Any other statement is executable module-level code
        return True

    return False


def _get_file_end_line(source: str) -> int:
    """Get the last line number of a source file."""
    return len(source.splitlines())


def _has_main_guard(tree: ast.Module) -> bool:
    """Check if a module has the `if __name__ == "__main__":` pattern.

    This is a structural entry point indicator for Python scripts.
    The pattern indicates the file is designed to be run as a script.

    Handles both:
    - if __name__ == "__main__":  (standard)
    - if "__main__" == __name__:  (reversed)
    - Single and double quotes

    Returns:
        True if the main guard pattern is detected, False otherwise.
    """
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue

        test = node.test
        if not isinstance(test, ast.Compare):
            continue

        # Check for: __name__ == "__main__"
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue

        left = test.left
        comparators = test.comparators

        if len(comparators) != 1:  # pragma: no cover - defensive: len(ops) == len(comparators) in valid AST
            continue

        right = comparators[0]

        # Pattern 1: __name__ == "__main__"
        if (isinstance(left, ast.Name) and left.id == "__name__" and
                isinstance(right, ast.Constant) and right.value == "__main__"):
            return True

        # Pattern 2: "__main__" == __name__
        if (isinstance(left, ast.Constant) and left.value == "__main__" and
                isinstance(right, ast.Name) and right.id == "__name__"):
            return True

    return False


def _extract_django_usage_contexts(
    tree: ast.Module,
    file_path: str,
    symbol_by_name: dict[str, Symbol],
    local_constants: dict[str, str] | None = None,
    imports: dict[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Django URL patterns.

    Creates UsageContext records that capture how view functions are used
    in path(), re_path(), url() calls. These are matched against YAML
    patterns in the enrichment phase.

    When ``local_constants`` and ``imports`` are provided, resolves
    dynamic route paths built from string concatenation or module-level
    constants (e.g., ``path(BASE + "/users/", view)``).

    Args:
        tree: The parsed AST module
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file
        local_constants: Module-level string constant assignments
        imports: Imported names for cross-file constant resolution
        repo_root: Repository root for cross-file constant resolution

    Returns:
        List of UsageContext records for Django URL patterns.
    """
    contexts: list[UsageContext] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Check if it's a Django URL function call
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name not in DJANGO_URL_FUNCTIONS:
            continue

        # Extract the URL pattern from the first argument
        if not node.args:  # pragma: no cover
            continue

        first_arg = node.args[0]
        route_path = _resolve_string_expr(
            first_arg,
            local_constants or {},
            imports,
            repo_root,
        )
        if route_path is None:
            if isinstance(first_arg, ast.JoinedStr):  # pragma: no cover
                continue  # Skip dynamic patterns (f-strings)
            continue  # pragma: no cover - unsupported pattern type

        # Extract view reference from second argument
        view_ref = None
        view_name = None
        is_class_based = False
        if len(node.args) >= 2:
            second_arg = node.args[1]
            if isinstance(second_arg, ast.Attribute):
                # views.user_list -> check if we can resolve it
                view_name = second_arg.attr
            elif isinstance(second_arg, ast.Name):
                # user_list -> check if it's defined locally
                view_name = second_arg.id
                # Try to resolve to a local symbol
                if view_name in symbol_by_name:
                    view_ref = symbol_by_name[view_name].id
            elif isinstance(second_arg, ast.Call):
                # views.LoginView.as_view() -> "LoginView"
                # TemplateView.as_view(template_name='...') -> "TemplateView"
                call_func = second_arg.func
                if isinstance(call_func, ast.Attribute) and call_func.attr == "as_view":
                    is_class_based = True
                    # Extract the class name from the as_view() call
                    if isinstance(call_func.value, ast.Attribute):
                        # views.LoginView.as_view() -> LoginView
                        view_name = call_func.value.attr
                        # Try to resolve to a local symbol (class)
                        if view_name in symbol_by_name:
                            view_ref = symbol_by_name[view_name].id
                    elif isinstance(call_func.value, ast.Name):
                        # LoginView.as_view() -> LoginView
                        view_name = call_func.value.id
                        if view_name in symbol_by_name:
                            view_ref = symbol_by_name[view_name].id

        # Build metadata with args info
        args_values = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args_values.append(arg.value)
            elif isinstance(arg, ast.Name):
                args_values.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                # views.func -> "views.func"
                parts = []
                current: ast.expr = arg
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                args_values.append(".".join(reversed(parts)))
            else:  # pragma: no cover
                args_values.append("<expr>")

        # Normalize route path - ensure it starts with /
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        span = Span(
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            start_col=getattr(node, "col_offset", 0),
            end_col=getattr(node, "end_col_offset", 0),
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=func_name,
            position="args[1]",
            path=file_path,
            span=span,
            symbol_ref=view_ref,
            metadata={
                "args": args_values,
                "route_path": normalized_path,
                "view_name": view_name,
                "is_class_based_view": is_class_based,
            },
        )
        contexts.append(ctx)

    return contexts


def _collect_module_constants(
    tree: ast.Module,
    repo_root: Path | None = None,
    file_path: Path | None = None,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Collect module-level string constants and import mappings from an AST.

    Scans top-level assignments of the form ``NAME = "literal"`` and
    ``from mod import NAME`` to build lookup tables for constant propagation.
    Relative imports are resolved using ``file_path`` and ``repo_root``.

    Used by ``_scan_router_prefixes`` for APIRouter prefix resolution and
    by route path extraction for dynamic route resolution (e.g.,
    ``path(BASE + "/users/", view)``).

    Args:
        tree: The parsed AST module.
        repo_root: Repository root for resolving relative imports.
        file_path: Path to the source file (for relative import resolution).

    Returns:
        Tuple of (local_constants, imports) where local_constants maps
        variable names to string values and imports maps local names to
        (module_path, original_name) tuples.
    """
    local_constants: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            local_constants[node.targets[0].id] = node.value.value

    imports: dict[str, tuple[str, str]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module_path = node.module
            if node.level > 0 and file_path is not None:
                pkg_base = file_path.parent
                for _ in range(node.level - 1):
                    pkg_base = pkg_base.parent
                resolved = pkg_base / Path(*node.module.split("."))
                if repo_root is not None:
                    try:
                        rel = resolved.relative_to(repo_root)
                        module_path = ".".join(rel.parts)
                    except ValueError:  # pragma: no cover
                        pass
            for alias in node.names:
                local_name = alias.asname or alias.name
                imports[local_name] = (module_path, alias.name)

    return local_constants, imports


def _scan_router_prefixes(
    tree: ast.Module,
    repo_root: Path | None,
    file_path: Path | None = None,
) -> dict[str, str]:
    """Scan for FastAPI APIRouter(prefix=X) assignments and resolve prefixes.

    Finds assignments like ``v2_router = APIRouter(prefix="/v2")`` and builds
    a mapping from variable name to prefix string.  Handles three cases:

    1. Literal string: ``APIRouter(prefix="/v2")``
    2. Same-file constant: ``PREFIX = "/v2"; APIRouter(prefix=PREFIX)``
    3. Imported constant: ``from pkg.constants import PREFIX; APIRouter(prefix=PREFIX)``
       (requires ``repo_root`` and ``file_path`` to resolve relative imports)

    Args:
        tree: The parsed AST module.
        repo_root: Repository root for finding imported modules.
        file_path: Path to the source file (for resolving relative imports).

    Returns:
        Dict mapping variable name (e.g. "v2_router") to prefix string (e.g. "/v2").
    """
    local_constants, imports = _collect_module_constants(tree, repo_root, file_path)

    prefixes: dict[str, str] = {}

    for node in ast.walk(tree):
        # Match: var = APIRouter(prefix=X)
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        # Check the call is APIRouter(...)
        call = node.value
        func_name = None
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        if func_name != "APIRouter":
            continue

        # Extract prefix= keyword argument
        prefix_value: str | None = None
        for kw in call.keywords:
            if kw.arg != "prefix":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                # Case 1: literal string
                prefix_value = kw.value.value
            elif isinstance(kw.value, ast.Name):
                const_name = kw.value.id
                # Case 2: same-file constant
                if const_name in local_constants:
                    prefix_value = local_constants[const_name]
                # Case 3: imported constant
                elif const_name in imports and repo_root is not None:
                    prefix_value = _resolve_imported_string_constant(
                        imports[const_name], repo_root
                    )
            break

        if prefix_value is not None:
            var_name = node.targets[0].id
            prefixes[var_name] = prefix_value

    return prefixes


def _resolve_imported_string_constant(
    import_info: tuple[str, str],
    repo_root: Path,
) -> str | None:
    """Resolve a cross-file imported string constant.

    Given an import like ``from pkg.constants import V2_PREFIX``, finds the
    source file and extracts the value of ``V2_PREFIX = "/v2"``.

    Only resolves simple module-level string literal assignments to keep
    the implementation lightweight and predictable.

    Args:
        import_info: Tuple of (module_path, original_name) from the import.
        repo_root: Repository root for finding source files.

    Returns:
        The string value if found, or None.
    """
    module_path, original_name = import_info
    # Convert dotted module path to file path candidates
    parts = module_path.split(".")
    # Try both direct and src-layout paths
    candidates = [
        repo_root / Path(*parts).with_suffix(".py"),
        repo_root / "src" / Path(*parts).with_suffix(".py"),
    ]
    # Also try as package/__init__.py
    candidates.append(repo_root / Path(*parts) / "__init__.py")
    candidates.append(repo_root / "src" / Path(*parts) / "__init__.py")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            # utf-8-sig strips leading BOM; Python's own lexer does the same. INV-kitot.
            source = candidate.read_text(encoding="utf-8-sig")
            mod = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.iter_child_nodes(mod):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == original_name
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                return node.value.value
    return None


def _resolve_string_expr(
    node: ast.expr,
    local_constants: dict[str, str],
    imports: dict[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
) -> str | None:
    """Resolve a string expression from an AST node via constant propagation.

    Handles three patterns that commonly appear in route path arguments:

    1. Literal strings: ``"/users"`` → ``"/users"``
    2. Name references: ``BASE_URL`` → value from ``local_constants`` or imports
    3. String concatenation: ``BASE + "/users"`` → recursive resolution

    Recursion is bounded by Python's AST depth (no cycles possible in a
    single expression).  Only resolves module-level string literal
    assignments; dynamic or runtime-computed values return None.

    Args:
        node: The AST expression node to resolve.
        local_constants: Module-level ``NAME = "literal"`` assignments.
        imports: Imported names mapping ``local_name → (module, original)``.
        repo_root: Repository root for cross-file constant resolution.

    Returns:
        The resolved string value, or None if the expression cannot be
        statically resolved.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        name = node.id
        if name in local_constants:
            return local_constants[name]
        if imports and name in imports and repo_root is not None:
            return _resolve_imported_string_constant(imports[name], repo_root)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_string_expr(node.left, local_constants, imports, repo_root)
        right = _resolve_string_expr(node.right, local_constants, imports, repo_root)
        if left is not None and right is not None:
            return left + right
        return None
    return None


def _extract_flask_usage_contexts(
    tree: ast.Module,
    file_path: str,
    symbol_by_name: dict[str, Symbol],
    router_prefixes: dict[str, str] | None = None,
    local_constants: dict[str, str] | None = None,
    imports: dict[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Flask/FastAPI call-based route registration.

    Creates UsageContext records that capture how view functions are used
    in add_url_rule() and add_api_route() calls. These are matched against
    YAML patterns in the enrichment phase.

    Supported patterns:
    - app.add_url_rule('/users', 'user_list', user_list)
    - app.add_url_rule('/users', view_func=user_list)
    - blueprint.add_url_rule('/items', view_func=get_items, methods=['GET'])
    - router.add_api_route('/path', handler, methods=['GET'])
    - router.add_api_route('/path', handler, response_model=Model)

    When ``router_prefixes`` is provided (from ``_scan_router_prefixes``),
    routes registered on a prefixed APIRouter have the prefix composed with
    the route path.

    When ``local_constants`` and ``imports`` are provided, resolves
    dynamic route paths built from string concatenation or module-level
    constants (e.g., ``add_url_rule(PREFIX + '/users', ...)``).

    Args:
        tree: The parsed AST module
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file
        router_prefixes: Optional mapping of router variable names to their
            APIRouter prefix strings.
        local_constants: Module-level string constant assignments
        imports: Imported names for cross-file constant resolution
        repo_root: Repository root for cross-file constant resolution

    Returns:
        List of UsageContext records for Flask URL patterns.
    """
    contexts: list[UsageContext] = []
    _prefixes = router_prefixes or {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Check if it's a Flask add_url_rule call (app.add_url_rule, bp.add_url_rule)
        func_name = None
        receiver_name = None
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                receiver_name = node.func.value.id

        if func_name not in FLASK_URL_FUNCTIONS:
            continue

        # Flask-RESTful add_resource: first arg is class, second+ is path(s)
        # add_resource(TodoList, '/todos', '/todos/')
        if func_name == "add_resource":
            if len(node.args) < 2:
                continue
            # First arg is the resource class
            resource_arg = node.args[0]
            resource_name = None
            if isinstance(resource_arg, ast.Name):
                resource_name = resource_arg.id
            elif isinstance(resource_arg, ast.Attribute):
                resource_name = resource_arg.attr
            if resource_name is None:
                continue
            resource_ref = None
            if resource_name in symbol_by_name:
                resource_ref = symbol_by_name[resource_name].id
            # Second arg onwards are URL paths
            for path_arg in node.args[1:]:
                if isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str):
                    rpath = path_arg.value
                    normalized = rpath if rpath.startswith("/") else f"/{rpath}"
                    if receiver_name and receiver_name in _prefixes:
                        prefix = _prefixes[receiver_name].rstrip("/")
                        normalized = prefix + normalized
                    span = Span(
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        start_col=getattr(node, "col_offset", 0),
                        end_col=getattr(node, "end_col_offset", 0),
                    )
                    call_name = f"{receiver_name}.{func_name}" if receiver_name else func_name
                    ctx = UsageContext.create(
                        kind="call",
                        context_name=call_name,
                        position="resource_class",
                        path=file_path,
                        span=span,
                        symbol_ref=resource_ref,
                        metadata={
                            "route_path": normalized,
                            "view_name": resource_name,
                            "args": [resource_name, rpath],
                        },
                    )
                    contexts.append(ctx)
            continue

        # Extract the URL pattern from the first argument
        if not node.args:  # pragma: no cover
            continue

        first_arg = node.args[0]
        route_path = _resolve_string_expr(
            first_arg,
            local_constants or {},
            imports,
            repo_root,
        )
        if route_path is None:
            if isinstance(first_arg, ast.JoinedStr):  # pragma: no cover
                continue  # Skip dynamic patterns (f-strings)
            continue  # pragma: no cover - unsupported pattern type

        # Extract view function - can be:
        # 1. Third positional arg: add_url_rule('/path', 'name', view_func)
        # 2. Second positional arg: add_api_route('/path', handler, ...)
        # 3. view_func keyword arg: add_url_rule('/path', view_func=handler)
        view_ref = None
        view_name = None

        # Check for view_func in keyword arguments
        for kw in node.keywords:
            if kw.arg == "view_func":
                if isinstance(kw.value, ast.Name):
                    view_name = kw.value.id
                    if view_name in symbol_by_name:
                        view_ref = symbol_by_name[view_name].id
                elif isinstance(kw.value, ast.Attribute):
                    view_name = kw.value.attr
                break

        # If not found in kwargs, check positional args.
        # add_api_route: second arg is handler ('/path', handler, ...)
        # add_url_rule: third arg is handler ('/path', 'name', handler)
        handler_arg_idx = 1 if func_name == "add_api_route" else 2
        if view_name is None and len(node.args) > handler_arg_idx:
            handler_arg = node.args[handler_arg_idx]
            if isinstance(handler_arg, ast.Name):
                view_name = handler_arg.id
                if view_name in symbol_by_name:
                    view_ref = symbol_by_name[view_name].id
            elif isinstance(handler_arg, ast.Attribute):
                view_name = handler_arg.attr

        # Extract methods if specified
        methods = None
        for kw in node.keywords:
            if kw.arg == "methods":
                if isinstance(kw.value, ast.List):
                    methods = []
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            methods.append(elt.value.upper())

        # Build metadata
        args_values = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args_values.append(arg.value)
            elif isinstance(arg, ast.Name):
                args_values.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                parts = []
                current: ast.expr = arg
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                args_values.append(".".join(reversed(parts)))
            else:  # pragma: no cover
                args_values.append("<expr>")

        # Normalize route path and compose with APIRouter prefix if present
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"
        if receiver_name and receiver_name in _prefixes:
            prefix = _prefixes[receiver_name].rstrip("/")
            normalized_path = prefix + normalized_path

        span = Span(
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            start_col=getattr(node, "col_offset", 0),
            end_col=getattr(node, "end_col_offset", 0),
        )

        # Build full call name (e.g., "app.add_url_rule")
        call_name = f"{receiver_name}.{func_name}" if receiver_name else func_name

        # WI-kohav: spec §9 usage_contexts[].metadata carries a single
        # http_method STRING (matching every other language's route extractor);
        # emit one UsageContext per declared method instead of a methods list.
        for _method in (methods or ["GET"]):
            ctx = UsageContext.create(
                kind="call",
                context_name=call_name,
                position="view_func",
                path=file_path,
                span=span,
                symbol_ref=view_ref,
                metadata={
                    "args": args_values,
                    "route_path": normalized_path,
                    "view_name": view_name,
                    "http_method": _method,
                    "receiver": receiver_name,
                },
            )
            contexts.append(ctx)

    return contexts


def _extract_starlette_usage_contexts(
    tree: ast.Module,
    file_path: str,
    symbol_by_name: dict[str, Symbol],
    imports: dict[str, tuple[str, str]] | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Starlette ``Route`` / ``WebSocketRoute``.

    Starlette routes are constructor calls — ``Route("/path", handler, methods=[...])``
    and ``WebSocketRoute("/ws", handler)`` — typically passed as a list to a
    ``Starlette(routes=[...])`` constructor or to ``Mount(...)``. We treat both
    classes as route-registration points.

    The match is **import-scoped**: we only emit a UsageContext when the bare
    name (``Route`` / ``WebSocketRoute``) was imported from
    ``starlette.routing`` in this file. ``Route`` is a common class name and
    a global match would cause false positives.

    Args:
        tree: Parsed module AST.
        file_path: Path to the source file.
        symbol_by_name: Lookup table for symbols defined in this file.
        imports: ``{local_name: (module_path, original_name)}`` from
            ``_collect_module_constants``. When None, no contexts are emitted.

    Returns:
        UsageContext records with ``position="view_func"`` and metadata
        ``route_path`` / ``methods`` / ``view_name`` / ``args`` / ``receiver``
        (the imported class name, e.g., ``"Route"`` or ``"WebSocketRoute"``).
    """
    contexts: list[UsageContext] = []
    if not imports:
        return contexts

    # Build the set of locally-bound names that resolve to the Starlette
    # routing classes. Honors aliasing (``from starlette.routing import Route as R``).
    starlette_names: dict[str, str] = {}  # local_name → original_class_name
    for local_name, (module_path, original_name) in imports.items():
        if module_path != _STARLETTE_ROUTING_MODULE:
            continue
        if original_name not in STARLETTE_ROUTE_FUNCTIONS:
            continue
        starlette_names[local_name] = original_name

    if not starlette_names:
        return contexts

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        local_name = node.func.id
        if local_name not in starlette_names:
            continue
        original_name = starlette_names[local_name]

        if not node.args:  # pragma: no cover - constructor with zero args is invalid
            continue

        # First arg: route path (string literal).
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
            # Skip dynamic patterns; static analysis can't recover the path.
            continue
        route_path = first_arg.value
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # Second arg: handler function.
        view_ref = None
        view_name = None
        if len(node.args) >= 2:
            handler_arg = node.args[1]
            if isinstance(handler_arg, ast.Name):
                view_name = handler_arg.id
                if view_name in symbol_by_name:
                    view_ref = symbol_by_name[view_name].id
            elif isinstance(handler_arg, ast.Attribute):
                view_name = handler_arg.attr

        # Methods: kwarg for Route; synthetic ["WS"] for WebSocketRoute.
        methods: list[str] | None = None
        if original_name == "WebSocketRoute":
            methods = ["WS"]
        else:
            for kw in node.keywords:
                if kw.arg == "methods" and isinstance(kw.value, ast.List):
                    extracted: list[str] = []
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            extracted.append(elt.value.upper())
                    if extracted:
                        methods = extracted

        # Build args metadata mirroring Flask's shape.
        args_values: list[str | int | float | bool | None] = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args_values.append(arg.value)
            elif isinstance(arg, ast.Name):
                args_values.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                parts: list[str] = []
                current: ast.expr = arg
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                args_values.append(".".join(reversed(parts)))
            else:  # pragma: no cover - other expr forms (e.g. lambdas)
                args_values.append("<expr>")

        span = Span(
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            start_col=getattr(node, "col_offset", 0),
            end_col=getattr(node, "end_col_offset", 0),
        )
        # WI-kohav: one UsageContext per method with an http_method STRING
        # (spec §9; matches every other language's route extractor).
        for _method in (methods or ["GET"]):
            ctx = UsageContext.create(
                kind="call",
                context_name=original_name,
                position="view_func",
                path=file_path,
                span=span,
                symbol_ref=view_ref,
                metadata={
                    "args": args_values,
                    "route_path": normalized_path,
                    "view_name": view_name,
                    "http_method": _method,
                    "receiver": original_name,
                },
            )
            contexts.append(ctx)

    return contexts


def _extract_py_decorator_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str:
    """Extract sorted, comma-joined decorator names from an AST node.

    Walks the decorator list and extracts plain names (stripping module
    paths and arguments).  Returns a sorted, comma-joined string suitable
    for inclusion in stable_id formulas.  Returns empty string when no
    decorators are present.
    """
    names: list[str] = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.append(dec.func.attr)
    return ",".join(sorted(names))


def _compute_stable_id(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    containing_stable_id: str = "",
    *,
    name: str = "",
    qualified_name: str = "",
    occurrence_index: int = 0,
) -> str:
    """Compute a v6 stable_id for a Python function/class/method AST node.

    Delegates to the shared :func:`assemble_stable_id` (ADR-0035 §1), so the Python AST path
    and the tree-sitter ``BaseAnalyzer.compute_stable_id`` path emit the identical formula::

        sha256({kind}:{param_count}:{arity_flags}:{decorators}
               :{containing_stable_id}:{name}:{qualified_name}:{occurrence_index})

    The v5 divergence — this producer folded a class ``body_sig`` (sorted member names) and
    omitted ``qualified_name`` — is gone (WI-gitun / INV-tazaj):

    * ``body_sig`` is DROPPED. It churned the class id on every member add/remove (violating
      §1 "survives body edits"); structural identity is ``shape_id``'s job. With the full scope
      chain in ``qualified_name`` it disambiguated nothing on the measured corpus.
    * ``qualified_name`` carries the FULL enclosing scope chain (enclosing classes → enclosing
      functions → local name), so same-local-name symbols in distinct scopes hash distinctly.

    ``name`` is the bare local name; callers pass the scope-qualified chain as
    ``qualified_name`` (see ``_enclosing_scope_chain``). ``occurrence_index`` is the §1
    within-scope ordinal (``0`` in the carrier).
    """
    is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    kind = "function" if is_function else "class"

    if is_function:
        args = node.args
        param_count = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
        has_defaults = len(args.defaults) > 0 or len(args.kw_defaults) > 0
        has_varargs = args.vararg is not None
        has_kwargs = args.kwarg is not None
        arity_flags = f"{has_defaults},{has_varargs},{has_kwargs}"
    else:
        # Classes don't carry parameters in the same way.
        param_count = 0
        arity_flags = "False,False,False"

    decorators_str = _extract_py_decorator_names(node)
    return assemble_stable_id(
        kind,
        param_count,
        arity_flags,
        decorators_str,
        containing_stable_id,
        name,
        qualified_name,
        occurrence_index,
    )


def _ast_structure(node: ast.AST) -> str:
    """Generate structural representation of an AST node, ignoring names/literals."""
    parts = [type(node).__name__]

    for child in ast.iter_child_nodes(node):
        # Skip name nodes and constants (we want structure only)
        if isinstance(child, (ast.Name, ast.Constant, ast.arg)):
            parts.append(type(child).__name__)
        else:
            parts.append(_ast_structure(child))

    return f"({','.join(parts)})"


def _compute_shape_id(node: ast.FunctionDef | ast.ClassDef) -> str:
    """Compute shape_id based on AST structure (ignores variable names/literals).

    sha256(ast_structure) where structure is a normalized representation
    of the control flow and nesting.
    """
    # For functions, analyze the body structure
    if isinstance(node, ast.FunctionDef):
        body_parts = [_ast_structure(stmt) for stmt in node.body]
        structure = f"FunctionDef({','.join(body_parts)})"
    else:
        # For classes, analyze class body
        body_parts = [_ast_structure(stmt) for stmt in node.body]
        structure = f"ClassDef({','.join(body_parts)})"

    hash_val = hashlib.sha256(structure.encode()).hexdigest()[:16]
    return f"sha256:{hash_val}"


PASS_ID = make_pass_id("python")


def _compute_cyclomatic_complexity(node: ast.AST) -> int:
    """Compute McCabe cyclomatic complexity for a function or class.

    Cyclomatic complexity = number of decision points + 1.

    Decision points counted:
    - if (each elif counts separately)
    - for loops
    - while loops
    - except handlers
    - with statements
    - boolean operators (and, or)
    - conditional expressions (ternary)
    - match/case statements (Python 3.10+)
    - comprehensions with if clauses

    Returns 1 for straight-line code (no branches).
    """
    complexity = 1  # Base complexity

    for child in ast.walk(node):
        # Conditional statements
        if isinstance(child, ast.If):
            complexity += 1
        # Loops
        elif isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
            complexity += 1
        # Exception handlers (each except clause adds a branch)
        elif isinstance(child, ast.ExceptHandler):
            complexity += 1
        # With statements
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            complexity += 1
        # Boolean operators in conditions
        elif isinstance(child, ast.BoolOp):
            # and/or each add (n-1) where n is number of operands
            complexity += len(child.values) - 1
        # Conditional expressions (ternary: x if cond else y)
        elif isinstance(child, ast.IfExp):
            complexity += 1
        # Comprehensions with if clauses
        elif isinstance(child, ast.comprehension):
            complexity += len(child.ifs)
        # Match/case (Python 3.10+)
        elif isinstance(child, ast.Match):
            # Each case is a branch
            complexity += len(child.cases)

    return complexity


def _compute_line_span(node: ast.AST) -> int:
    """Compute lines of code for a function or class.

    Returns end_line - start_line + 1.
    """
    start = node.lineno
    end = getattr(node, "end_lineno", node.lineno)
    return end - start + 1


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Note on type inference: Variable method calls (e.g., stub.method()) are resolved
    using constructor-only type inference. This tracks types from direct constructor
    calls (stub = Client()) but NOT from function returns (stub = get_client()).
    This covers ~90% of real-world cases with minimal complexity.
    """

    symbols: list[Symbol]
    symbol_by_name: dict[str, Symbol]
    # Maps imported name -> (module_name, original_name)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Maps local alias -> module_name for 'import X' and 'import X as Y'
    module_imports: dict[str, str] = field(default_factory=dict)
    # The parsed AST tree (kept to avoid re-parsing)
    tree: ast.AST | None = None
    # Usage contexts for call-based patterns (Django URL patterns, etc.)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    # Original source text (for library_patterns regex scanning during
    # dataflow annotation — see annotate_dataflow_ast).
    source: str = ""
    # INV-mofav: per-enclosing-function inner scope. Maps the enclosing
    # function Symbol's id to {short_name -> nested Symbol}. Lets call
    # resolution see bare-name calls to inner helpers without polluting
    # the flat symbol_by_name dict.
    nested_by_parent_id: dict[str, dict[str, "Symbol"]] = field(default_factory=dict)
    # INV-mofav: maps AST FunctionDef/AsyncFunctionDef node id -> Symbol.
    # Used by edge extraction to resolve the caller Symbol for nested
    # functions (which aren't registered in the flat symbol_by_name dict).
    # AST node ids are stable within a single process; this field is only
    # consumed in the same process that produced the tree.
    func_symbol_by_node_id: dict[int, "Symbol"] = field(default_factory=dict)
    # identity:F1/F4a: maps every function/method Symbol.id to its NEAREST
    # enclosing FUNCTION Symbol.id (ClassDef ancestors are passed through).
    # Materializes the lexical scope chain for _build_scope_stack; unlike
    # nested_by_parent_id it records methods AS CHILDREN (a method's enclosing
    # function is a real scope) though never as a nested-scope VALUE.
    enclosing_func_id: dict[str, str] = field(default_factory=dict)
    # identity:F1/F4a: maps a function/method Symbol.id to the set of names it
    # binds locally (params/assignments/imports/global, minus nonlocal) — the
    # LEGB "L" shadow set consulted by ScopeStack.lookup_enclosing.
    local_names_by_func_id: dict[str, frozenset[str]] = field(default_factory=dict)
    # WI-supat (D3): AUTHORITATIVE method Symbol.id -> enclosing class Symbol.id.
    # Built where both symbols are lexically in hand, so it is immune to the
    # bare-name last-write-wins clobber a symbol_by_name lookup would suffer on
    # same-short-name / nested classes. Lets the Site-1 / Site-3 producers stamp a
    # concrete, CORRECT enclosing_class_id (which the inherited_calls linker uses
    # to resolve a namesake collision precisely instead of biasing to unresolved).
    method_to_enclosing_class_id: dict[str, str] = field(default_factory=dict)


def _detect_source_roots(repo_root: Path) -> list[Path]:
    """Detect every src/ layout source root inside ``repo_root``.

    A *source root* is a directory named ``src`` that:
    1. is not itself a Python package (no ``__init__.py`` in it), and
    2. contains at least one Python package (a child dir with ``__init__.py``).

    Supports both the traditional single-root layout (``repo/src/<pkg>/``)
    and monorepo layouts where each package owns its own src dir
    (``repo/packages/<pkg>/src/<mod>/``, ``repo/libs/<lib>/src/<mod>/``, …).
    Without this multi-root detection a file under
    ``packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`` would be
    derived as the path-shaped module qualifier
    ``packages.hypergumbo-core.src.hypergumbo_core.taxonomy`` — invalid
    Python (hyphen) and not the real importable name (WI-davan E1).

    Implementation: iterative directory walk. Skips DEFAULT_EXCLUDES
    directories and dot-prefixed dirs to avoid `.git` / `node_modules` /
    build outputs. When a ``src`` directory satisfies both conditions, it
    is collected and not descended into; nested ``src`` directories
    deeper inside another source root are not searched (they would be
    inside the package, not separate roots).

    Returns a list sorted by path (deterministic for tests and consumers).
    """
    from hypergumbo_core.discovery import DEFAULT_EXCLUDES

    skip = set(DEFAULT_EXCLUDES)
    roots: list[Path] = []
    stack: list[Path] = [repo_root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):  # pragma: no cover
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in skip or entry.name.startswith("."):
                continue
            if entry.name == "src":
                # Classify and stop descending — either a source root, a
                # package itself, or a dir whose children aren't packages.
                if (entry / "__init__.py").exists():
                    continue
                try:
                    has_pkg = any(
                        (c / "__init__.py").exists()
                        for c in entry.iterdir()
                        if c.is_dir()
                    )
                except (PermissionError, OSError):  # pragma: no cover
                    has_pkg = False
                if has_pkg:
                    roots.append(entry)
                continue
            stack.append(entry)
    return sorted(roots)


def _module_name_from_path(
    py_file: Path,
    repo_root: Path,
    source_roots: list[Path] | None = None,
) -> str:
    """Convert a file path to a Python module name.

    E.g., ``/repo/utils.py`` -> ``utils``, ``/repo/pkg/mod.py`` -> ``pkg.mod``.

    If ``source_roots`` is provided, paths under any source root are
    computed relative to the *most-specific* (longest-path) matching root.
    """
    roots = source_roots or []
    # Pick the most-specific (deepest) source root that contains the file
    matching = [r for r in roots if py_file.is_relative_to(r)]
    if matching:
        best = max(matching, key=lambda r: len(r.parts))
        try:
            rel_path = py_file.relative_to(best)
        except ValueError:  # pragma: no cover
            rel_path = py_file.relative_to(repo_root)
    else:
        try:
            rel_path = py_file.relative_to(repo_root)
        except ValueError:
            rel_path = py_file
    # Remove .py extension and convert path separators to dots
    return str(rel_path.with_suffix("")).replace("/", ".").replace("\\", ".")


def _resolve_relative_import(
    module: str | None, level: int, importing_module: str
) -> str:
    """Resolve a relative import to an absolute module name.

    Args:
        module: The module part of the import (e.g., 'utils' in 'from ..utils import X')
        level: The number of dots (0 for absolute, 1 for '.', 2 for '..', etc.)
        importing_module: The fully qualified name of the importing module

    Returns:
        The resolved absolute module name.

    Example:
        _resolve_relative_import('utils', 2, 'pkg.sub.main') -> 'pkg.utils'
    """
    if level == 0:
        # Absolute import
        return module or ""

    # Split the importing module into parts
    parts = importing_module.split(".")

    # Go up 'level' levels (level=1 means same package, level=2 means parent, etc.)
    # We go up (level) levels from the module's package (excluding the module name itself)
    # So for 'pkg.sub.main' with level=2, we go up 2 from 'pkg.sub' -> 'pkg'
    if level > len(parts):
        # Can't go up that many levels, return as-is
        return module or ""

    base_parts = parts[:-level] if level <= len(parts) else []
    if module:
        base_parts.append(module)

    return ".".join(base_parts)


def _extract_imports(
    tree: ast.AST, importing_module: str
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Extract import mappings from AST with relative import resolution.

    Args:
        tree: The parsed AST
        importing_module: The fully qualified name of the importing module

    Returns a tuple of:
        - symbol_imports: dict mapping local name -> (resolved_module_name, original_name)
          For 'from utils import helper', returns {'helper': ('utils', 'helper')}.
          For 'from ..utils import helper' in 'pkg.sub.main', returns {'helper': ('pkg.utils', 'helper')}.
        - module_imports: dict mapping local alias -> module_name
          For 'import demo_pb2_grpc', returns {'demo_pb2_grpc': 'demo_pb2_grpc'}.
          For 'import numpy as np', returns {'np': 'numpy'}.
    """
    symbol_imports: dict[str, tuple[str, str]] = {}
    module_imports: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_relative_import(
                node.module, node.level, importing_module
            )
            if resolved_module:  # Skip if we couldn't resolve
                for alias in node.names:
                    local_name = alias.asname if alias.asname else alias.name
                    symbol_imports[local_name] = (resolved_module, alias.name)

        elif isinstance(node, ast.Import):
            # Handle 'import X' and 'import X as Y'
            for alias in node.names:
                module_name = alias.name
                local_name = alias.asname if alias.asname else alias.name
                module_imports[local_name] = module_name
                # WI-zigah: for `import pkg.subpkg` with no alias, Python binds
                # only the top-level `pkg` name. Call sites see
                # `pkg.subpkg.func(...)` where `pkg` is the AST root — so the
                # full dotted `local_name` is dead data no downstream lookup
                # reaches. Record the top-level binding so the chain walker
                # in _process_call can canonicalize the qualified path.
                if alias.asname is None and "." in module_name:
                    top_level = module_name.split(".", 1)[0]
                    module_imports.setdefault(top_level, top_level)

    return symbol_imports, module_imports


def _extract_import_edges(
    tree: ast.AST,
    file_path: str,
    importing_module: str,
    global_symbols: dict[tuple[str, str], Symbol],
    resolver: "SymbolResolver | None" = None,
    *,
    module_to_file_id: dict[str, str],
    run_id: str,
) -> list[Edge]:
    """Extract import edges from AST.

    Creates edges from the importing file to the imported symbols/modules.
    For 'from X import Y', links to the resolved symbol if known, else to module.
    For 'import X', links to the module.

    supply:F4 (INV-nuzas): when an import names an in-tree MODULE rather than a
    resolvable symbol, the edge dst is the module's first-party file-anchor node
    (looked up in ``module_to_file_id``) instead of a dangling ExternalRef that
    would collapse to a phantom ``external_symbol`` boundary node. Genuine
    third-party modules are absent from the map, so they keep their ExternalRef.

    Args:
        tree: The parsed AST
        file_path: Path to the importing file
        importing_module: The fully qualified name of the importing module
        global_symbols: Map of (module, name) -> Symbol for cross-file resolution
        resolver: Optional SymbolResolver for efficient cross-file lookups
        module_to_file_id: Map of in-tree dotted module name -> file-anchor id
            (package names included for ``__init__.py``); empty when the repo has
            no analyzable in-tree modules.

    Returns list of import edges.
    """
    edges = []
    file_id = _make_file_id(file_path)

    for node in ast.walk(tree):
        # Handle 'from X import Y, Z' style imports
        if isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_relative_import(
                node.module, node.level, importing_module
            )
            if resolved_module:
                for alias in node.names:
                    # Try to find the symbol in our global table (with suffix matching)
                    symbol = _lookup_symbol_by_module(
                        global_symbols, resolved_module, alias.name, resolver=resolver
                    )
                    dst_ref: ExternalRef | None
                    if symbol:
                        dst_id = symbol.id
                        # Internal target — Symbol ID is the canonical id; no ExternalRef.
                        dst_ref = None
                    elif (
                        in_repo_fid := (
                            # supply:F4 — `from PKG import SUBMOD` where SUBMOD is
                            # an in-tree submodule (not a symbol), or `from MOD
                            # import X` where MOD is in-tree but X was not pinned
                            # as a symbol. Resolve to the in-tree file node so the
                            # edge does not dangle to a phantom external twin.
                            module_to_file_id.get(f"{resolved_module}.{alias.name}")
                            or module_to_file_id.get(resolved_module)
                        )
                    ) is not None:
                        dst_id = in_repo_fid
                        dst_ref = None
                    else:
                        # External symbol - create a reference ID
                        dst_id = f"python:{resolved_module}:0-0:{alias.name}:symbol"
                        dst_ref = ExternalRef(
                            lang="python",
                            module_path=resolved_module,
                            name=alias.name,
                        )

                    edges.append(Edge.create(
                        src=file_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=node.lineno,
                        evidence_type="ast_import",
                        dst_ref=dst_ref,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

        # Handle 'import X' and 'import X as Y' style imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
                # supply:F4 — `import pkg.sub.mod` of an in-tree module resolves
                # to its first-party file-anchor node; otherwise it stays an
                # external module reference.
                in_repo_fid = module_to_file_id.get(module_name)
                import_dst_ref: ExternalRef | None
                if in_repo_fid is not None:
                    dst_id = in_repo_fid
                    import_dst_ref = None
                else:
                    dst_id = _make_module_id(module_name)
                    import_dst_ref = ExternalRef(
                        lang="python",
                        module_path=module_name,
                        name=module_name,
                    )
                edges.append(Edge.create(
                    src=file_id,
                    dst=dst_id,
                    edge_type="imports",
                    line=node.lineno,
                    evidence_type="ast_import",
                    dst_ref=import_dst_ref,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return edges


def _resolve_base_class(
    base_name: str,
    child_sym: Symbol,
    class_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, tuple[str, str]]],
) -> Symbol | None:
    """Resolve a base class name to a specific Symbol, disambiguating collisions.

    When multiple classes share the same name (e.g., 238 classes named 'Model'
    in Django), uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Import match: child's file imports match a candidate's module path
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
            (imported_name -> (module_name, original_name))

    Returns:
        The resolved base class Symbol, or None if no match found.
    """
    candidates = class_by_name.get(base_name)
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    # Extract file path from child symbol ID for same-file check
    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base class in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Import match: check if child's file imports resolve to a candidate
    child_imports = sym_file_imports.get(child_sym.id, {})
    if base_name in child_imports:
        import_module, _original_name = child_imports[base_name]
        # Match import module against candidate file paths
        # e.g., import_module="db.models" matches candidate path "db/models.py"
        module_as_path = import_module.replace(".", "/")
        for cand in candidates:
            cand_path = cand.path or ""
            # Check if candidate path contains the module path
            # e.g., "db/models.py" contains "db/models"
            cand_no_ext = cand_path.rsplit(".py", 1)[0]
            if cand_no_ext.endswith(module_as_path):
                return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, tuple[str, str]]],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract extends edges from class inheritance.

    For each class with base_classes metadata, creates extends edges to
    base classes that exist in the analyzed codebase. This enables the
    type hierarchy linker to create dispatches_to edges for polymorphic dispatch.

    When multiple classes share the same name (common in large repos like Django
    where 238 test stubs are named 'Model'), uses import-aware disambiguation
    via ``_resolve_base_class()`` to find the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
        run: Current analysis run for provenance

    Returns:
        List of extends edges for inheritance relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Strip generics from base class name (e.g., "Generic[T]" -> "Generic")
            base_name = base_class_name.split("[")[0]

            # Resolve to the correct base class, handling name collisions
            base_sym = _resolve_base_class(
                base_name, sym, class_by_name, sym_file_imports
            )
            if base_sym is not None and base_sym.id != sym.id:
                edge = Edge.create(
                    src=sym.id,
                    dst=base_sym.id,
                    edge_type="extends",
                    line=sym.span.start_line if sym.span else 0,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_extends",
                )
                edges.append(edge)

    return edges


def _extract_file_analysis(
    py_file: Path,
    repo_root: Path | None = None,
    source_roots: list[Path] | None = None,
) -> tuple[FileAnalysis | None, str | None]:
    """Extract symbols and imports from a single file.

    Args:
        py_file: Path to the Python file
        repo_root: Repository root for resolving relative imports. If None,
                   relative imports won't be fully resolved.
        source_roots: For src/ layout projects, the source directories
                     (e.g., ``[repo/src]`` or per-package
                     ``[packages/A/src, packages/B/src]``). Used for correct
                     module name calculation.

    Returns (analysis, None) on success, or (None, reason) when the file
    cannot be parsed — the second tuple element carries the
    "<ExceptionType>: <msg>" reason so the orchestrator can route it into
    limits.failed_files (INV-buhur).
    """
    try:
        # utf-8-sig strips leading BOM; Python's own lexer does the same. INV-kitot.
        source = py_file.read_text(encoding="utf-8-sig")
        # Suppress SyntaxWarning from invalid escape sequences in analyzed code.
        # These warnings come from the target codebase, not hypergumbo.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        # OSError covers PermissionError and transient I/O failures from
        # read_text() — e.g. a chmod-000 or root-owned file encountered
        # while scanning a tree. §17 / WI-madal: fail open — skip the
        # unreadable file and record the reason in limits.failed_files
        # rather than letting the exception escape and abort the whole run.
        # ast.parse() performs no I/O, so broadening here only newly catches
        # read errors, not anything from parsing.
        return None, f"{type(e).__name__}: {e}"

    symbols = []
    symbol_by_name: dict[str, Symbol] = {}

    # INV-zudob: compute the file's stable identity once up-front so
    # top-level ClassDef and untyped-function stable_ids can fold it in
    # as their containing identity. Pre-INV-zudob the top-level call
    # sites passed no containing argument, so module identity was
    # silently erased and structurally-identical classes/functions in
    # different files collapsed to one stable_id (18.94% of class nodes
    # on self-analysis). The repo-relative path matches the convention
    # used by ``make_file_stable_id`` elsewhere — see also the
    # ``file_name`` computation for the module Symbol below.
    file_relative_path = str(py_file)
    if repo_root is not None:
        try:
            file_relative_path = str(py_file.relative_to(repo_root))
        except ValueError:  # pragma: no cover - defensive
            pass
    file_containing_id = make_file_stable_id("python", file_relative_path)

    # WI-gipag: extract the module-level __all__ (if any) once up front
    # so each top-level Symbol extraction can decide is_exported without
    # re-walking the tree. None means "no __all__ found" → fall back to
    # the leading-underscore rule.
    module_all = _extract_module_all(tree)

    # INV-hojus: emit the file pseudo-node as kind="file" with the
    # canonical file-id shape so the orchestrator file-symbol synthesizer
    # dedups against it (existing_ids check). Before this fix, every
    # Python file with module-level code got TWO Symbols: a kind="module"
    # node from here AND a kind="file" node from the synthesizer when any
    # edge targeted the file id — 332 paths affected on self-analysis.
    # File-kind is the cross-language canonical for "this file" (see
    # ``analyze.base.make_file_id``); the synthesizer's INV-vaguj fix
    # already established its identity claims (relative path, real
    # end_line). This Symbol provides an enclosing scope for module-level
    # edges so script-only files remain reachable in slice traversal.
    # WI-kazob: the file-kind node carries the module's one-line docstring
    # summary (0/902 file nodes carried one before). py.py is the only
    # producer that can read a Python module docstring — the orchestrator
    # file-symbol synthesizer is language-agnostic — so the file node is
    # emitted whenever the module has executable code OR a docstring. Per
    # the INV-hojus dedup above, broadening the condition only changes WHICH
    # producer emits the single file node (py.py vs the synthesizer); it
    # never doubles it.
    _module_docstring = ast.get_docstring(tree)
    _module_docstring_line = (
        _module_docstring.split("\n")[0].strip()[:80] if _module_docstring else None
    )
    if _has_module_level_code(tree) or _module_docstring:
        end_line = _get_file_end_line(source)
        module_span = Span(
            start_line=1,
            end_line=end_line,
            start_col=0,
            end_col=0,
        )

        # Detect structural entry point: if __name__ == "__main__"
        # This concept enables entrypoint detection for executable Python scripts
        # main_guard indicates "if __name__ == '__main__':" pattern
        module_meta: dict[str, object] | None = None
        if _has_main_guard(tree):
            module_meta = {"concepts": [{"concept": "main_guard", "framework": "python"}]}

        # Match the orchestrator synthesizer's convention: name=path (the
        # repo-relative path when possible). The orchestrator's
        # cli-level path normalize pass strips repo_root from ``.path``
        # but not ``.name``, so the analyzer is responsible for emitting
        # ``name`` already normalized (mirror of INV-vaguj for the
        # analyzer-side producer). Reuses ``file_relative_path`` computed
        # earlier for INV-zudob.
        file_name = file_relative_path

        module_symbol = Symbol(
            id=_make_file_id(str(py_file)),
            name=file_name,
            kind="file",
            language="python",
            path=str(py_file),
            span=module_span,
            origin="",
            origin_run_id="",
            docstring=_module_docstring_line,
            meta=module_meta,
        )
        symbols.append(module_symbol)
        symbol_by_name["<module>"] = module_symbol

    # WI-gafog E2: emit Symbols for module-level NAME = ... so that
    # `from <mod> import NAME` resolves cross-file rather than externalising.
    for cs in _emit_module_level_assign_symbols(tree, py_file, module_all):
        symbols.append(cs)
        symbol_by_name[cs.name] = cs

    # Track functions already processed as methods (to avoid duplicates)
    # Key: (start_line, name) tuple
    processed_functions: set[tuple[int, str]] = set()

    # INV-mofav: build a parent map so each FunctionDef can find its
    # immediate enclosing FunctionDef (if any), and emit a qualified
    # name like `outer.inner` or `outermost.middle.inner`.
    parent_map: dict[int, ast.AST] = {}
    for _p in ast.walk(tree):
        for _c in ast.iter_child_nodes(_p):
            parent_map[id(_c)] = _p
    func_symbol_by_node_id: dict[int, Symbol] = {}
    # WI-supat (D3): authoritative method Symbol.id -> enclosing class Symbol.id,
    # populated at method creation where both symbols are lexically in hand.
    method_to_enclosing_class_id: dict[str, str] = {}

    def _enclosing_function_chain(node: ast.AST) -> list[str]:
        """Return the names of enclosing FunctionDef ancestors, outermost-first.

        Stops at the first non-function parent boundary in either direction:
        class bodies and module level don't extend the chain. Used for
        qualified naming of nested functions per INV-mofav. Class methods
        keep their existing `ClassName.method` naming (computed elsewhere).
        """
        chain: list[str] = []
        current = parent_map.get(id(node))
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append(current.name)
            current = parent_map.get(id(current))
        chain.reverse()  # outermost first
        return chain

    def _enclosing_scope_chain(node: ast.AST) -> list[str]:
        """Return ALL enclosing Class/Function ancestor names, outermost-first.

        The stable_id v6 scope chain (ADR-0035 §1): unlike
        ``_enclosing_function_chain`` (functions only — used for nested-function display
        naming, INV-mofav), this also folds enclosing CLASSES, so a class/function defined
        inside a *method* of two different classes (``A.t.Mock`` vs ``B.t.Mock``) gets distinct
        ids. Function-only chains collapse those (they see only ``t``) — WI-gitun's residual.
        """
        chain: list[str] = []
        current = parent_map.get(id(node))
        while current is not None:
            if isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                chain.append(current.name)
            current = parent_map.get(id(current))
        chain.reverse()  # outermost first
        return chain

    # Scan for APIRouter prefix assignments (for route path composition)
    router_prefixes = _scan_router_prefixes(tree, repo_root, py_file)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            end_line = node.end_lineno or node.lineno
            end_col = node.end_col_offset or 0
            span = Span(
                start_line=node.lineno,
                end_line=end_line,
                start_col=node.col_offset,
                end_col=end_col,
            )

            # Build rich metadata for class (ADR-3aaa)
            class_meta: dict[str, object] = {}

            # Extract decorators with arguments
            if node.decorator_list:
                class_meta["decorators"] = [
                    _extract_decorator_info(dec) for dec in node.decorator_list
                ]

            # Extract base classes
            if node.bases:
                class_meta["base_classes"] = [
                    _format_annotation(base) for base in node.bases
                ]

            _ds = ast.get_docstring(node)
            _ds_line = _ds.split("\n")[0].strip()[:80] if _ds else None
            # WI-gipag: decide is_exported for top-level class symbols via
            # the module's __all__ (if present) or the leading-underscore
            # convention. Only top-level classes are candidates for public
            # API here — nested classes defined inside functions are not
            # externally reachable regardless of naming.
            class_is_exported = (
                node.col_offset == 0
                and _is_python_top_level_exported(node.name, module_all)
            )
            # stable_id v6 (ADR-0035 §1, WI-gitun): fold the FULL enclosing scope chain
            # (enclosing classes + functions) into the IDENTITY only, so two same-named classes
            # in distinct scopes (e.g. function-local ``class Args`` in distinct functions, or a
            # ``class Mock`` inside methods of distinct classes) no longer collapse. The
            # ``qualified_name`` FIELD is left as the bare name — v6 is a stable_id-only change;
            # the field's scope-qualification (and its call-resolution effects) is separate work.
            class_scoped_name = ".".join(_enclosing_scope_chain(node) + [node.name])
            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "class"),
                name=node.name,
                qualified_name=node.name,
                kind="class",
                language="python",
                path=str(py_file),
                span=span,
                stable_id=_compute_stable_id(
                    node, containing_stable_id=file_containing_id,
                    name=node.name, qualified_name=class_scoped_name,
                ),
                shape_id=_compute_shape_id(node),
                cyclomatic_complexity=_compute_cyclomatic_complexity(node),
                line_span=_compute_line_span(node),
                meta=class_meta if class_meta else None,
                docstring=_ds_line,
                modifiers=_python_visibility_modifiers(node.name),
                is_exported=class_is_exported,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol

            # WI-jusus (emission-parity F5): emit kind="field" Symbols for CLASS
            # ATTRIBUTES — class-body Assign / AnnAssign with Name targets (incl.
            # dataclass fields `x: int` and bare annotations). Instance
            # attributes (`self.x = ...` inside methods) are NOT class-body
            # statements and are out of scope. Identity is class-scoped via the
            # class's file-anchored stable_id (the assemble_stable_id container
            # slot), so same-named fields in different classes/files are distinct.
            for member in node.body:
                attr_names: list[str] = []
                attr_annotation: "ast.expr | None" = None
                if isinstance(member, ast.Assign):
                    for t in member.targets:
                        if isinstance(t, ast.Name):
                            attr_names.append(t.id)
                elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                    attr_names.append(member.target.id)
                    attr_annotation = member.annotation
                else:
                    continue
                attr_start = member.lineno
                attr_end = member.end_lineno or attr_start
                attr_sig = (
                    _format_annotation(attr_annotation)
                    if attr_annotation is not None else None
                )
                for attr in attr_names:
                    attr_full = f"{class_name}.{attr}"
                    attr_qualified = f"{class_scoped_name}.{attr}"
                    field_sym = Symbol(
                        id=_make_symbol_id(str(py_file), attr_start, attr_end, attr_full, "field"),
                        name=attr_full,
                        kind="field",
                        language="python",
                        path=str(py_file),
                        span=Span(
                            start_line=attr_start,
                            end_line=attr_end,
                            start_col=member.col_offset,
                            end_col=member.end_col_offset or 0,
                        ),
                        origin="",
                        origin_run_id="",
                        stable_id=assemble_stable_id(
                            "field", 0, "", "",
                            symbol.stable_id or "", attr, attr_qualified, 0,
                        ),
                        signature=attr_sig,
                        modifiers=_python_visibility_modifiers(attr),
                        is_exported=not attr.startswith("_"),
                        qualified_name=attr_qualified,
                    )
                    symbols.append(field_sym)
                    symbol_by_name[attr_full] = field_sym

            # Extract methods inside the class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_end_line = item.end_lineno or item.lineno
                    method_end_col = item.end_col_offset or 0
                    method_span = Span(
                        start_line=item.lineno,
                        end_line=method_end_line,
                        start_col=item.col_offset,
                        end_col=method_end_col,
                    )
                    method_name = f"{class_name}.{item.name}"

                    # v8 (WI-bolup): HTTP-verb-named methods of class-based views
                    # (get/post/...) flow through the SAME file-anchored identity
                    # path as every other method — make_typed_stable_id /
                    # _compute_stable_id with containing_stable_id=symbol.stable_id
                    # (the file-anchored parent class). Pre-v8 they were keyed via
                    # the LOGICAL make_route_stable_id(verb, class_scoped_name),
                    # which omits the file, so two same-named top-level CBVs' same
                    # verb method collided cross-file (Wave-2 gate, INV-tazaj). The
                    # route/endpoint signal lives on the SEPARATE kind="route" nodes
                    # (below), never on the method's own identity.
                    # INV-bazij (Phase 6 PR3): the method's qualified name threads
                    # through name/qualified_name so two same-signature test methods
                    # in one class don't collide.
                    sig = _format_function_signature(item)
                    norm_sig = normalize_python_signature(sig)
                    modifiers = _python_visibility_modifiers(method_name)
                    if norm_sig:
                        stable_id = make_typed_stable_id(
                            "method", norm_sig,
                            visibility_from_modifiers(modifiers),
                            symbol.stable_id,
                            _extract_py_decorator_names(item),
                            name=item.name, qualified_name=method_name,
                        )
                    else:
                        stable_id = _compute_stable_id(
                            item, containing_stable_id=symbol.stable_id,
                            name=item.name, qualified_name=method_name,
                        )

                    # Build rich metadata for method (ADR-3aaa)
                    method_meta: dict[str, object] = {}

                    # Extract decorators with arguments
                    if item.decorator_list:
                        method_meta["decorators"] = [
                            _extract_decorator_info(dec) for dec in item.decorator_list
                        ]
                        # Check if any decorator references a prefixed APIRouter
                        if router_prefixes:
                            for dec_info in method_meta["decorators"]:
                                dec_name = dec_info.get("name", "") if isinstance(dec_info, dict) else ""
                                dot_idx = dec_name.find(".")
                                if dot_idx > 0:
                                    receiver = dec_name[:dot_idx]
                                    if receiver in router_prefixes:
                                        method_meta["router_prefix"] = router_prefixes[receiver]
                                        break

                    # Extract structured parameters (excluding self/cls)
                    params = _extract_parameters_info(item.args, exclude_self=True)
                    if params:
                        method_meta["parameters"] = params

                    _mds = ast.get_docstring(item)
                    _mds_line = _mds.split("\n")[0].strip()[:80] if _mds else None
                    method_symbol = Symbol(
                        id=_make_symbol_id(str(py_file), item.lineno, method_end_line, method_name, "method"),
                        name=method_name,
                        qualified_name=method_name,  # WI-fagab (ADR-0032 sibling field)
                        kind="method",
                        language="python",
                        path=str(py_file),
                        span=method_span,
                        stable_id=stable_id,
                        shape_id=_compute_shape_id(item),
                        cyclomatic_complexity=_compute_cyclomatic_complexity(item),
                        line_span=_compute_line_span(item),
                        signature=_format_function_signature(item),
                        docstring=_mds_line,
                        meta=method_meta if method_meta else None,
                        modifiers=_python_visibility_modifiers(method_name),
                    )
                    symbols.append(method_symbol)
                    # Store by short name for self.method() lookups
                    symbol_by_name[item.name] = method_symbol
                    # WI-jafat CHANGE A: also register the method under its AST
                    # node id (collision-immune, mirroring the FunctionDef path
                    # below). Without this, caller resolution in _extract_edges
                    # (the `func_symbol_by_node_id.get(id(node))` lookup) misses
                    # for methods and falls through to the bare-name,
                    # last-write-wins symbol_by_name dict, so same-short-name
                    # sibling methods (to_dict, __init__, ...) own each other's
                    # calls and the overwritten sibling's calls land out-of-span
                    # (506 calls / 1194 combined edges on self-analysis). Keying
                    # on id(item) lets each method own its own call lines; the
                    # bare write above is retained for self.method() (Case 2a).
                    func_symbol_by_node_id[id(item)] = method_symbol
                    # WI-supat (D3): record the AUTHORITATIVE method->enclosing
                    # class link. ``symbol`` is the ClassDef's own class Symbol
                    # (this loop iterates that class's body), so this is exact for
                    # nested / same-short-name classes where a bare-name
                    # symbol_by_name lookup would clobber.
                    method_to_enclosing_class_id[method_symbol.id] = symbol.id
                    # Track as processed to avoid duplicate extraction
                    processed_functions.add((item.lineno, item.name))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip if already processed as a class method
            if (node.lineno, node.name) in processed_functions:
                continue

            # INV-mofav: every FunctionDef / AsyncFunctionDef is emitted as a
            # Symbol, at any nesting depth. Top-level and nested-with-decorator
            # cases are unchanged in name (`node.name`). Nested-undecorated
            # cases use a qualified name `outer.inner` (recursively
            # `outermost.middle.inner`) to disambiguate same-named nested
            # functions in different parents.
            is_top_level = node.col_offset == 0
            func_chain = _enclosing_function_chain(node)
            is_nested = bool(func_chain)
            if is_nested:
                qualified_name = ".".join(func_chain + [node.name])
                immediate_parent_name = func_chain[-1]
            else:
                qualified_name = node.name
                immediate_parent_name = None
            # stable_id v6 (ADR-0035 §1, WI-gitun): identity uses the FULL enclosing scope
            # chain (classes + functions) so a function nested in a method of distinct classes
            # (``A.t.helper`` vs ``B.t.helper``) gets distinct ids; the function-only
            # ``qualified_name`` above is left untouched (display/lookup, not identity).
            scoped_id_name = ".".join(_enclosing_scope_chain(node) + [node.name])

            if True:
                # Track as processed
                processed_functions.add((node.lineno, node.name))
                end_line = node.end_lineno or node.lineno
                end_col = node.end_col_offset or 0
                span = Span(
                    start_line=node.lineno,
                    end_line=end_line,
                    start_col=node.col_offset,
                    end_col=end_col,
                )

                # Build rich metadata for function (ADR-3aaa)
                # Route detection moved to FRAMEWORK_PATTERNS phase
                func_meta: dict[str, object] = {}

                # Extract decorators with arguments
                if node.decorator_list:
                    func_meta["decorators"] = [
                        _extract_decorator_info(dec) for dec in node.decorator_list
                    ]
                    # Check if any decorator references a prefixed APIRouter
                    if router_prefixes:
                        for dec_info in func_meta["decorators"]:
                            dec_name = dec_info.get("name", "") if isinstance(dec_info, dict) else ""
                            dot_idx = dec_name.find(".")
                            if dot_idx > 0:
                                receiver = dec_name[:dot_idx]
                                if receiver in router_prefixes:
                                    func_meta["router_prefix"] = router_prefixes[receiver]
                                    break

                # Extract structured parameters
                params = _extract_parameters_info(node.args, exclude_self=False)
                if params:
                    func_meta["parameters"] = params

                # Try typed tier first (ADR-0014 §3), fall back to untyped
                func_sig = _format_function_signature(node)
                func_modifiers = _python_visibility_modifiers(node.name)
                norm_sig = normalize_python_signature(func_sig)
                if norm_sig:
                    # INV-zudob: typed tier also threads file identity as
                    # the containing scope for top-level functions, so
                    # two same-signature functions in different modules
                    # get distinct stable_ids.
                    # INV-bazij (Phase 6 PR3): prepend qualified_name into
                    # the normalized signature so two same-signature
                    # top-level functions in the same module split.
                    func_stable_id = make_typed_stable_id(
                        "function", norm_sig,
                        visibility_from_modifiers(func_modifiers),
                        file_containing_id,
                        decorators=_extract_py_decorator_names(node),
                        name=node.name, qualified_name=scoped_id_name,
                    )
                else:
                    # INV-zudob: same threading for the untyped fallback.
                    # INV-bazij: thread qualified_name as the disambiguator
                    # so nested functions and same-named top-level functions
                    # in different modules stay distinct.
                    func_stable_id = _compute_stable_id(
                        node, containing_stable_id=file_containing_id,
                        name=node.name, qualified_name=scoped_id_name,
                    )

                _fds = ast.get_docstring(node)
                _fds_line = _fds.split("\n")[0].strip()[:80] if _fds else None
                # WI-gipag: only top-level functions are candidates for
                # the public API. Nested functions captured here (whether
                # decorated or undecorated under INV-mofav) are never
                # externally reachable via __all__, so is_exported stays
                # False for them.
                func_is_exported = (
                    is_top_level
                    and _is_python_top_level_exported(node.name, module_all)
                )
                # INV-mofav: nested functions stamp the immediate enclosing
                # function name into meta.nesting_parent so consumers can
                # branch on nesting without parsing the qualified `name`.
                if immediate_parent_name is not None:
                    func_meta["nesting_parent"] = immediate_parent_name
                symbol = Symbol(
                    id=_make_symbol_id(str(py_file), node.lineno, end_line, qualified_name, "function"),
                    name=qualified_name,
                    qualified_name=qualified_name,  # WI-fagab (ADR-0032 sibling field)
                    kind="function",
                    language="python",
                    path=str(py_file),
                    span=span,
                    stable_id=func_stable_id,
                    shape_id=_compute_shape_id(node),
                    meta=func_meta if func_meta else None,
                    cyclomatic_complexity=_compute_cyclomatic_complexity(node),
                    line_span=_compute_line_span(node),
                    signature=func_sig,
                    docstring=_fds_line,
                    modifiers=func_modifiers,
                    is_exported=func_is_exported,
                )
                symbols.append(symbol)
                # Only top-level functions get registered in the flat
                # symbol_by_name dict (which feeds call resolution for
                # bare-name calls at module-level). Nested functions resolve
                # through the per-parent inner_scope map (INV-mofav) to
                # prevent sibling collisions when two parents each define a
                # nested helper of the same short name.
                if not is_nested:
                    symbol_by_name[node.name] = symbol
                func_symbol_by_node_id[id(node)] = symbol

    # Extract usage contexts for call-based frameworks (v1.1.x).
    # UsageContext records feed into YAML-driven enrichment (concept tagging on
    # handler symbols). Route symbols are derived from the same contexts for the
    # route_handler linker, which needs kind="route" symbols to create routes_to
    # edges. See "Route Detection Architecture" in this module's docstring.
    usage_contexts: list[UsageContext] = []
    # Collect module-level string constants for route path resolution.
    # Enables constant propagation: path(BASE + "/users/", view) → "/api/v1/users/".
    route_local_constants, route_imports = _collect_module_constants(
        tree, repo_root, py_file,
    )
    django_contexts = _extract_django_usage_contexts(
        tree, str(py_file), symbol_by_name,
        local_constants=route_local_constants,
        imports=route_imports,
        repo_root=repo_root,
    )
    usage_contexts.extend(django_contexts)
    # router_prefixes already computed above (before the symbol extraction loop)
    flask_contexts = _extract_flask_usage_contexts(
        tree, str(py_file), symbol_by_name, router_prefixes,
        local_constants=route_local_constants,
        imports=route_imports,
        repo_root=repo_root,
    )
    usage_contexts.extend(flask_contexts)
    starlette_contexts = _extract_starlette_usage_contexts(
        tree, str(py_file), symbol_by_name,
        imports=route_imports,
    )
    usage_contexts.extend(starlette_contexts)

    # Create route symbols from Django usage contexts.
    #
    # WI-lojoh: class-based views (registered via Cls.as_view()) get
    # http_method="ANY" so the post-pass `expand_class_based_view_routes`
    # can introspect the view class's get/post/put/patch/delete/head/options
    # methods and emit one route variant per declared method. When the view
    # class lives outside the analyzed repo (e.g. django.contrib.auth.views),
    # the route stays at "ANY" — better than fabricating a wrong "GET".
    # Function-based views keep "GET" (Django dispatches them for any HTTP
    # verb, but GET is the conventional default for static-analysis output).
    for ctx in django_contexts:
        route_path = ctx.metadata.get("route_path", "")
        view_name = ctx.metadata.get("view_name")
        is_cbv = bool(ctx.metadata.get("is_class_based_view"))
        http_method = "ANY" if is_cbv else "GET"
        meta = {
            "route_path": route_path,
            "http_method": http_method,
            "view_name": view_name,
            "framework_role": "route",
        }
        if is_cbv:
            meta["is_class_based_view"] = True
        symbol = Symbol(
            # ADR-0036 Ruling 2: id kind-slot == Symbol.kind ("function"); the
            # route role lives on meta.framework_role, not the id-slot.
            id=_make_symbol_id(str(py_file), ctx.span.start_line, ctx.span.end_line, route_path, "function"),
            name=f"django:{view_name or 'unknown'}",
            kind="function",
            language="python",
            path=str(py_file),
            span=ctx.span,
            stable_id=make_route_stable_id(http_method, route_path),
            meta=meta,
        )
        symbols.append(symbol)

    # Create route symbols from Starlette Route/WebSocketRoute usage contexts.
    # Starlette routes are constructor calls, not method calls on app/router,
    # so emitting kind="route" here mirrors the Django path rather than the
    # YAML-only path used for Flask add_url_rule / FastAPI add_api_route.
    for ctx in starlette_contexts:
        route_path = ctx.metadata.get("route_path", "")
        view_name = ctx.metadata.get("view_name")
        # WI-kohav: each usage_context now carries a single http_method string
        # (one ctx per method emitted by the producer); wrap in a 1-elem list so
        # the per-method minting below is unchanged.
        methods = [ctx.metadata.get("http_method") or "GET"]
        receiver = ctx.metadata.get("receiver", "Route")
        for method in methods:
            # ADR-0034 / Phase 6 PR6: canonical IDs forbid ``:`` in the
            # name segment (the same character is the segment separator).
            # The method-disambiguated route name embeds the method and
            # path via ``" "`` so the canonical 5-segment shape holds.
            symbol = Symbol(
                id=_make_symbol_id(
                    str(py_file), ctx.span.start_line, ctx.span.end_line,
                    # ADR-0036 Ruling 2: kind-slot "function" (role on meta).
                    f"{method} {route_path}", "function",
                ),
                name=f"starlette:{view_name or 'unknown'}",
                kind="function",
                language="python",
                path=str(py_file),
                span=ctx.span,
                stable_id=make_route_stable_id(method, route_path),
                meta={
                    "route_path": route_path,
                    "http_method": method,
                    "view_name": view_name,
                    "handler_ref": ctx.symbol_ref,
                    "framework": "starlette",
                    "route_class": receiver,
                    "framework_role": "route",
                },
            )
            symbols.append(symbol)

    # Create route symbols from Flask-RESTful add_resource usage contexts.
    # add_resource registers all HTTP methods the Resource class defines,
    # but we don't know which methods at static analysis time, so we use
    # ANY as the method.
    for ctx in flask_contexts:
        if ctx.position != "resource_class":
            continue
        route_path = ctx.metadata.get("route_path", "")
        view_name = ctx.metadata.get("view_name")
        symbol = Symbol(
            # ADR-0036 Ruling 2: id kind-slot == Symbol.kind ("function"); the
            # route role lives on meta.framework_role, not the id-slot.
            id=_make_symbol_id(str(py_file), ctx.span.start_line, ctx.span.end_line, route_path, "function"),
            name=f"{view_name or 'unknown'}",
            kind="function",
            language="python",
            path=str(py_file),
            span=ctx.span,
            stable_id=make_route_stable_id("ANY", route_path),
            meta={
                "route_path": route_path,
                "http_method": "ANY",
                "view_name": view_name,
                "handler_ref": ctx.symbol_ref,
                "framework_role": "route",
            },
        )
        symbols.append(symbol)

    # Compute module name for import resolution
    if repo_root is not None:
        importing_module = _module_name_from_path(py_file, repo_root, source_roots)
    else:
        importing_module = py_file.stem  # Fallback to just filename
    symbol_imports, module_imports = _extract_imports(tree, importing_module)

    # INV-mofav: build the per-parent inner scope map. For each emitted
    # function Symbol whose AST node has an enclosing FunctionDef ancestor,
    # register it under its short name in the parent function's scope.
    nested_by_parent_id: dict[str, dict[str, Symbol]] = {}
    # identity:F1/F4a: enclosing_func_id maps every func/method Symbol.id to its
    # nearest enclosing FUNCTION Symbol.id — the SAME parent_map ancestry walk as
    # nested_by_parent_id, but NOT gated on kind (a method's enclosing function
    # IS a real lexical scope). Computed here so _build_scope_stack can
    # materialize the LEGB frame chain without a second walk.
    enclosing_func_id: dict[str, str] = {}
    for _node_id, _sym in func_symbol_by_node_id.items():
        _parent = parent_map.get(_node_id)
        while _parent is not None:
            if isinstance(_parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _parent_sym = func_symbol_by_node_id.get(id(_parent))
                if _parent_sym is not None:
                    enclosing_func_id[_sym.id] = _parent_sym.id
                    # WI-jafat CHANGE B: a method must NOT be registered as a
                    # VALUE in any enclosing function's inner scope — otherwise a
                    # method inside a class inside a function would shadow that
                    # function's own nested helper of the same short name at
                    # callee resolution. (A method can still be a PARENT: a
                    # function nested inside a method registers, keyed by the
                    # method's id — the resolution-improving intent of decision
                    # #8, not a regression.) So the nested-scope VALUE record is
                    # gated on kind, but the enclosing_func_id CHILD record above
                    # is not.
                    if _sym.kind != "method":
                        short_name = _sym.name.rsplit(".", 1)[-1]
                        nested_by_parent_id.setdefault(_parent_sym.id, {})[short_name] = _sym
                break
            _parent = parent_map.get(id(_parent))

    # identity:F1/F4a: per-function LEGB "L" shadow sets (needs the AST nodes, so
    # a dedicated walk — func_symbol_by_node_id is keyed by node id only).
    local_names_by_func_id: dict[str, frozenset[str]] = {}
    for _fn_node in ast.walk(tree):
        if isinstance(_fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _fn_sym = func_symbol_by_node_id.get(id(_fn_node))
            if _fn_sym is not None:
                local_names_by_func_id[_fn_sym.id] = _collect_scope_local_names(_fn_node)

    return FileAnalysis(
        symbols=symbols,
        symbol_by_name=symbol_by_name,
        imports=symbol_imports,
        module_imports=module_imports,
        tree=tree,
        usage_contexts=usage_contexts,
        source=source,
        nested_by_parent_id=nested_by_parent_id,
        func_symbol_by_node_id=func_symbol_by_node_id,
        enclosing_func_id=enclosing_func_id,
        local_names_by_func_id=local_names_by_func_id,
        method_to_enclosing_class_id=method_to_enclosing_class_id,
    ), None


def _collect_call_func_attr_ids(block_nodes: list[ast.AST]) -> set[int]:
    """Return the ``id()``s of Attribute nodes that are the direct callee of a Call.

    An attribute that is a call's ``func`` (``os.getenv(...)``, ``obj.method()``)
    is handled by the calls pipeline; attribute-READ emitters (``module_attr_ref``
    and the WI-gubar ``@property``-read producer) must skip these so they never
    double-emit a read edge for what is really a call callee.
    """
    ids: set[int] = set()
    for root in block_nodes:
        for sub in ast.walk(root):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                ids.add(id(sub.func))
    return ids


def _resolve_property_getter(
    class_symbol: Symbol,
    attr_name: str,
    local_symbols: dict[str, Symbol],
    sym_by_path_name: dict[tuple[str, str], Symbol] | None,
) -> Symbol | None:
    """Return the class's ``@property`` getter Symbol for ``attr_name``, else None.

    Resolution mirrors the Case 2c method lookup surfaces: the full pipeline's
    ``(path, qualified)`` cross-file index first, then — for single-file
    ``extract_nodes`` where that index is absent and methods are keyed by SHORT
    name — a short-name hit accepted only when its qualified name matches this
    class (guarding the same-short-name-across-classes collision the
    ``(path, qualified)`` index would otherwise disambiguate). Gates on the
    resolved symbol being a *method* carrying the bare ``@property`` decorator
    (``kind == "method"`` and a ``meta['decorators']`` entry whose ``name`` is
    exactly ``"property"`` — a getter, not a ``@x.setter`` whose recorded name
    is the dotted ``"x.setter"``). A plain data field, a non-property method, or
    a missing member returns None, so only a genuine getter invocation (which IS
    a call) emits a ``calls`` edge.
    """
    qualified_name = f"{class_symbol.name}.{attr_name}"
    getter: Symbol | None = None
    if sym_by_path_name is not None:
        getter = sym_by_path_name.get((class_symbol.path, qualified_name))
    if getter is None:
        cand = local_symbols.get(attr_name)
        if cand is not None and cand.name == qualified_name:
            getter = cand
    if getter is None or getter.kind != "method":
        return None
    for dec in (getter.meta or {}).get("decorators", []):
        if isinstance(dec, dict) and dec.get("name") == "property":
            return getter
    return None


def _extract_edges(
    tree: ast.AST,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str] | None = None,
    resolver: "SymbolResolver | None" = None,
    _sym_by_path_name: dict[tuple[str, str], Symbol] | None = None,
    *,
    run_id: str,
    nested_by_parent_id: dict[str, dict[str, Symbol]] | None = None,
    func_symbol_by_node_id: dict[int, Symbol] | None = None,
    enclosing_func_id: dict[str, str] | None = None,
    local_names_by_func_id: dict[str, frozenset[str]] | None = None,
    method_to_enclosing_class_id: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and instantiation edges from an AST.

    Resolves both local and cross-file calls/instantiations.

    Handles:
    - Direct calls: helper(), ClassName()
    - Self method calls: self.method()
    - Self field method calls: self.field.method() (using field type inference from __init__)
    - Module-qualified calls: module.ClassName(), module.func()
    - Variable method calls: variable.method() (with constructor-only type inference)

    Type inference sources:
    1. Direct constructor calls: stub = Client() → var_types['stub'] = Client
    2. Return type annotations: stub = get_client() where get_client() -> Client
       → var_types['stub'] = Client (requires annotation on the function)
    3. Parameter type annotations: def f(session: Session) → param maps to Session

    Field type inference tracks self.field assignments in __init__ from typed params
    and constructor calls.

    Args:
        tree: The parsed AST
        local_symbols: Symbols defined in this file
        imports: Symbol imports (from X import Y)
        global_symbols: All symbols across the project
        module_imports: Module imports (import X, import X as Y)
        resolver: Optional SymbolResolver for efficient cross-file lookups
    """
    if module_imports is None:  # pragma: no cover
        module_imports = {}
    if nested_by_parent_id is None:  # pragma: no cover
        nested_by_parent_id = {}
    if func_symbol_by_node_id is None:  # pragma: no cover
        func_symbol_by_node_id = {}
    if enclosing_func_id is None:  # pragma: no cover
        enclosing_func_id = {}
    if local_names_by_func_id is None:  # pragma: no cover
        local_names_by_func_id = {}
    if method_to_enclosing_class_id is None:  # pragma: no cover
        method_to_enclosing_class_id = {}

    # WI-supat (D3): per-file class SHORT-NAME multiplicity. A receiver-type id
    # is only trustworthy when its short name resolves to a SINGLE in-file class:
    # with >=2 same-short-name classes the bare-name inference (symbol_by_name is
    # last-write-wins) that produced the receiver's type Symbol could have hit
    # the wrong twin, so the id is omitted and the linker falls back to the safe
    # name+guard path. Counts ClassDef nodes (nested included) so a nested
    # namesake also trips the gate. The ENCLOSING id needs no such gate — it comes
    # from the authoritative method->class map, not a name lookup.
    class_name_counts: dict[str, int] = {}
    for _cnode in ast.walk(tree):
        if isinstance(_cnode, ast.ClassDef):
            class_name_counts[_cnode.name] = class_name_counts.get(_cnode.name, 0) + 1

    edges: list[Edge] = []

    def _emit_function_ref(name_node: ast.Name, caller: Symbol, stack: ScopeStack | None = None) -> None:
        """Emit a 'references' edge if *name_node* resolves to a function/method.

        Used for function references in non-call contexts: call arguments,
        dict values, variable assignments, and collection literals.
        """
        name = name_node.id
        # INV-mofav: enclosing-function scope wins over module scope, mirroring
        # Python's LEGB rule for bare names (step 1-2). Without this, a bare-name
        # reference to a nested helper resolves to a same-named top-level Symbol.
        symbol = stack.lookup_immediate(name) if stack else None
        if symbol is None:
            symbol = local_symbols.get(name)
        if not symbol and name in imports:
            mod_name, original_name = imports[name]
            symbol = _lookup_symbol_by_module(
                global_symbols, mod_name, original_name, resolver=resolver
            )
        # identity:F1/F4a step-4: last-resort enclosing-scope lookup for a bare
        # reference to a helper defined in a grandparent enclosing function.
        # Additive — fires only when unresolved above; returns only functions.
        if symbol is None and stack is not None:
            symbol = stack.lookup_enclosing(name)
        if symbol and symbol.kind in ("function", "method"):
            edges.append(Edge.create(
                src=caller.id,
                dst=symbol.id,
                edge_type="references",
                line=name_node.lineno,
                evidence_type="function_reference",
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

    module_level_vars: dict[str, Symbol] = {
        name: sym for name, sym in local_symbols.items()
        if sym.kind == "variable"
    }

    def _collect_local_bindings(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> frozenset[str]:
        """Return names bound locally in *func_node* (params + body assignments).

        Used to detect shadows that suppress variable-reference edges.
        Walks the immediate scope only — nested function/class bodies are
        excluded so their locals don't mask the enclosing function's view.
        """
        names: set[str] = set()
        for arg in func_node.args.args:
            names.add(arg.arg)
        for arg in func_node.args.posonlyargs:
            names.add(arg.arg)
        for arg in func_node.args.kwonlyargs:
            names.add(arg.arg)
        if func_node.args.vararg:
            names.add(func_node.args.vararg.arg)
        if func_node.args.kwarg:
            names.add(func_node.args.kwarg.arg)

        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _walk_scope(nodes: list[ast.AST]) -> None:
            for node in nodes:
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        names.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        names.add(alias.asname or alias.name)
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, scope_boundary):
                        _walk_scope([child])

        _walk_scope(list(ast.iter_child_nodes(func_node)))
        return frozenset(names)

    def _emit_variable_refs(
        body_nodes: list[ast.AST],
        caller_symbol: Symbol,
        local_bindings: frozenset[str] = frozenset(),
    ) -> None:
        """Emit ``references`` edges for bare-name reads of module-level variables.

        Walks *body_nodes* (skipping nested function/class scopes) and emits
        an edge for each ``ast.Name`` in Load context that resolves to a
        module-level variable Symbol and is not shadowed by a local binding.
        """
        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _walk(nodes: list[ast.AST]) -> None:
            for node in nodes:
                if (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id in module_level_vars
                    and node.id not in local_bindings
                ):
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=module_level_vars[node.id].id,
                        edge_type="references",
                        line=node.lineno,
                        evidence_type="ast_name_read",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, scope_boundary):
                        _walk([child])

        _walk(body_nodes)

    def _emit_closure_factory_dispatch(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        caller_symbol: Symbol,
        inner_scope: dict[str, Symbol] | None,
    ) -> None:
        """Emit a ``dispatches_to`` edge for a returned directly-nested closure.

        A *closure factory* is a function ``F`` whose body contains
        ``return <bare-name>`` where ``<bare-name>`` resolves to one of F's own
        directly-nested ``FunctionDef`` / ``AsyncFunctionDef`` definitions (the
        canonical ``register_analyzer``-style decorator factory:
        ``def register(...): def decorator(func): ...; return decorator``).

        The returned inner closure is reachable whenever ``F`` is reached at its
        own call / decoration sites, but the reachability BFS in
        ``cli._REACHABILITY_EDGE_TYPES`` only traverses
        ``{calls, dispatches_to, wraps}``. Without this edge the nested closure
        has zero reachability in-edges and ``dead-code-maybe`` falsely flags it
        dead. We emit ``F -> nested`` of type ``dispatches_to`` with
        ``meta["dispatch_kind"] == "closure_factory"`` so the closure inherits
        F's reachability (dispatch:F8 PR-A).

        Scope is narrow on purpose to avoid edge proliferation:

        * Only a *bare* ``ast.Name`` return target counts. A returned call
          (``return f()``), attribute (``return self.x``), parameter, or
          non-nested name emits NO edge — those are not "this function returns
          its own inner closure".
        * Resolution is keyed on ``inner_scope`` (``nested_by_parent_id`` for
          F's symbol id), so it can ONLY match F's directly-nested defs. A
          sibling top-level function of the same name is never matched because
          it lives in ``local_symbols``, not ``inner_scope``.
        * Returns are collected from F's direct body plus the bodies of simple
          ``if`` / ``try`` blocks nested directly inside it (the common
          early-return / try-fallthrough factory shapes), but NOT from nested
          function / class scopes (whose returns belong to a different ``F``).

        Per-target de-duplication is handled by ``Edge.edge_key`` (which keys on
        ``(src, dst, type)`` and excludes the line), so two return statements
        pointing at the same nested closure collapse to one logical edge
        downstream; we still avoid emitting duplicate ``Edge`` objects here by
        tracking the nested symbol ids already linked.
        """
        if not inner_scope:
            return
        scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def _direct_returns(nodes: list[ast.AST]) -> list[ast.Return]:
            """Collect ``Return`` nodes in F's own scope (descending if/try only)."""
            found: list[ast.Return] = []
            for node in nodes:
                if isinstance(node, ast.Return):
                    found.append(node)
                elif isinstance(node, (ast.If, ast.Try)):
                    # Descend into the control-flow block's own statement lists
                    # (body / orelse / handlers / finalbody) — these returns are
                    # still F's. ``ast.iter_child_nodes`` would also surface the
                    # condition expression, which never contains a top-level
                    # Return, so the scope_boundary guard below is sufficient.
                    for child in ast.iter_child_nodes(node):
                        if not isinstance(child, scope_boundary):
                            found.extend(_direct_returns([child]))
            return found

        linked: set[str] = set()
        for ret in _direct_returns(list(func_node.body)):
            value = ret.value
            if not isinstance(value, ast.Name):
                continue
            # ``inner_scope`` (``nested_by_parent_id[F]``) is populated only
            # from ``func_symbol_by_node_id`` values that are NOT methods (the
            # construction at ~py.py:2784 skips ``kind == "method"`` as a
            # value), so every entry is a ``kind == "function"`` nested def. We
            # therefore only need the presence check — a returned name that is
            # not a nested def (parameter, import, sibling top-level function,
            # attribute, call) is absent from ``inner_scope`` and yields None.
            nested = inner_scope.get(value.id)
            if nested is None:
                continue
            if nested.id in linked:
                continue
            linked.add(nested.id)
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=nested.id,
                edge_type="dispatches_to",
                line=ret.lineno,
                # The return is a bare function *reference* (not a call); the
                # dispatch SHAPE rides on ``meta['dispatch_kind']`` per the
                # axis-registry division of labor (evidence_type = inference
                # pathway; dispatch_kind = dispatch shape). Reusing the
                # registered ``function_reference`` evidence type avoids minting
                # a one-producer heavyweight ADR-0028 axis value.
                evidence_type="function_reference",
                origin=PASS_ID,
                origin_run_id=run_id,
                meta={"dispatch_kind": "closure_factory"},
            ))

    def _emit_module_attr_refs(
        block_nodes: list[ast.AST],
        caller_symbol: Symbol,
    ) -> None:
        """Emit ``module_attr_ref`` edges for attribute reads on imported modules.

        Targets patterns like ``os.environ[...]``, ``sys.argv``, ``sys.path``:
        an imported module name followed by an attribute access that is NOT
        itself the callable of a function call (those are handled by the
        calls pipeline and matched against the YAML ``functions:``/``methods:``
        entries).  This emission pairs with ``attributes:`` entries in the
        io_primitives YAML catalog, which were previously dead metadata —
        without an edge to match, ``io-boundaries`` silently under-reported
        env_read / ipc_send chains (WI-guhok).
        """
        # Pre-collect Attribute-node ids that are the direct callee of a Call
        # so we can skip them below — `os.getenv("X")` already produces a
        # `calls` edge and doesn't need a redundant `module_attr_ref`.
        call_func_attr_ids = _collect_call_func_attr_ids(block_nodes)

        for root in block_nodes:
            for sub in ast.walk(root):
                if not isinstance(sub, ast.Attribute):
                    continue
                if id(sub) in call_func_attr_ids:
                    continue
                if not isinstance(sub.value, ast.Name):
                    continue
                local_name = sub.value.id
                if local_name not in module_imports:
                    continue
                real_module = module_imports[local_name]
                qname = f"{real_module}.{sub.attr}"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=f"python:{real_module}:0-0:{qname}:attribute",
                    edge_type="module_attr_ref",
                    line=sub.lineno,
                    evidence_type="module_attribute_reference",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    # Helper to extract edges from a code block (function body, module level, etc.)
    def _external_constructor_module(call: ast.Call) -> str | None:
        """WI-fuvuj: if ``call`` is a recognized I/O constructor, return the
        catalog module string for the object it constructs; else ``None``.

        - ``func`` is ``ast.Name`` (e.g. ``open``) → bare constructor name.
        - ``func`` is ``ast.Attribute`` with an ``ast.Name`` base that is a
          known module import (e.g. ``socket.socket``) → ``module.attr``.
        """
        func = call.func
        if isinstance(func, ast.Name):
            name = func.id
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in module_imports
        ):
            name = f"{module_imports[func.value.id]}.{func.attr}"
        else:
            return None
        return EXTERNAL_CONSTRUCTOR_TYPES.get(name)

    def process_code_block(
        block_nodes: list[ast.AST],
        caller_symbol: Symbol,
        var_types: dict[str, Symbol] | None = None,
        stack: ScopeStack | None = None,
        external_var_types: dict[str, str] | None = None,
    ) -> None:
        """Process AST nodes within a code block, tracking variable types.

        ``stack`` is the caller's materialized LEGB frame chain (identity:F1/F4a;
        ``None`` at module level). Its immediate frame is the enclosing-function
        inner scope (INV-mofav — nested helpers of ``caller_symbol``, consulted
        before ``local_symbols``); its outer frames add the last-resort
        enclosing-scope lookup for bare calls to grandparent helpers. The type
        inference input (``_resolve_call_target``) sees only the immediate frame.

        ``external_var_types`` (WI-fuvuj) maps a local variable name to the
        catalog module string of the I/O object it was assigned from a known
        constructor (``f = open(p)`` → ``{"f": "file"}``). It parallels
        ``var_types`` (which tracks in-repo class types) and lets
        ``_process_call`` emit a module-qualified unresolved dst for method
        calls on those variables.
        """
        if var_types is None:
            var_types = {}
        if external_var_types is None:
            external_var_types = {}

        # WI-hiziz PR-3 (review): the caller method's OWN __init__ field names
        # (from the closure-visible ``class_own_field_names``). The Site-3 emit
        # excludes these so an own field re-declared by the caller's class never
        # resolves against a same-named PARENT field of a different type.
        _own_field_names = (
            class_own_field_names.get(
                caller_symbol.qualified_name.split(".")[-2], frozenset()
            )
            if caller_symbol.kind == "method"
            and "." in (caller_symbol.qualified_name or "")
            else frozenset()
        )

        for node in block_nodes:
            # Track variable assignments for type inference
            # e.g., stub = EmailServiceStub(channel) -> var_types['stub'] = EmailServiceStub
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                        assigned_class = _resolve_call_target(
                            node.value, local_symbols, imports, global_symbols,
                            module_imports, resolver,
                            inner_scope=stack.immediate_symbols() if stack else None,
                        )
                        if assigned_class and assigned_class.kind == "class":
                            var_types[target.id] = assigned_class
                        elif assigned_class and assigned_class.kind in ("function", "method"):
                            # Return type inference: if the function has a
                            # return type annotation pointing to a class,
                            # track the variable's type from that annotation.
                            ret_name = _extract_return_type_name(
                                assigned_class.signature
                            )
                            if ret_name:
                                ret_class = _resolve_return_type_class(
                                    ret_name, assigned_class, local_symbols,
                                    imports, global_symbols, resolver,
                                    _sym_by_path_name,
                                )
                                if ret_class:
                                    var_types[target.id] = ret_class
                        elif assigned_class is None:
                            # WI-fuvuj: in-repo resolution found no class. If
                            # the RHS is a known I/O constructor (open(...),
                            # socket.socket()), record the inferred receiver
                            # type so method calls on this variable emit a
                            # module-qualified unresolved dst.
                            ext_module = _external_constructor_module(node.value)
                            if ext_module is not None:
                                external_var_types[target.id] = ext_module

            # Function reference in assignment RHS: callback = my_func
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                _emit_function_ref(node.value, caller_symbol, stack=stack)

            # WI-fuvuj: ``with open(p) as f:`` / ``with socket.socket() as s:``
            # — the dominant I/O constructor idiom. Type the bound name from
            # the context-manager constructor so method calls inside the body
            # emit a module-qualified unresolved dst. The body is still
            # recursed below (the generic ast.iter_child_nodes traversal).
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if (
                        isinstance(item.optional_vars, ast.Name)
                        and isinstance(item.context_expr, ast.Call)
                    ):
                        ext_module = _external_constructor_module(item.context_expr)
                        if ext_module is not None:
                            external_var_types[item.optional_vars.id] = ext_module

            # Process calls
            if isinstance(node, ast.Call):
                _process_call(
                    node, caller_symbol, local_symbols, imports, global_symbols,
                    module_imports, var_types, edges, resolver,
                    sym_by_path_name=_sym_by_path_name,
                    run_id=run_id,
                    stack=stack,
                    external_var_types=external_var_types,
                    function_aliases=function_aliases,
                    own_field_names=_own_field_names,
                    method_to_enclosing_class_id=method_to_enclosing_class_id,
                    class_name_counts=class_name_counts,
                )
                # Function references in call arguments: map(transform, items)
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        _emit_function_ref(arg, caller_symbol, stack=stack)
                for kw in node.keywords:
                    if isinstance(kw.value, ast.Name):
                        _emit_function_ref(kw.value, caller_symbol, stack=stack)

            # WI-gubar (D2): a @property attribute READ (obj.prop) is an
            # ast.Attribute in Load context, NOT an ast.Call, so the calls
            # pipeline never sees it and the getter (Symbol.end_line,
            # ValidationResult.ok) looks dead. Emit an unresolved `calls` edge
            # carrying receiver_type_hint — mirroring the WI-noham Part A method
            # producer — and let the inherited_calls linker mint the resolved
            # edge (strict Site-2 Step-1 finds the getter, kind 'method', on the
            # concrete type). Gated on a var_types-typed INSTANCE receiver whose
            # target attribute is a @property getter; a bare-CLASS receiver
            # (ClassName.prop yields the descriptor, not the value) is
            # deliberately excluded, so no INV-fahub scope guard is needed
            # (var_types is per-function scope-local, unlike the file-global
            # local_symbols the Part A bare-class branch consults).
            # call_construct='method' keeps the unresolved edge taint-safe.
            # Restricted to function/method callers: at MODULE scope
            # caller_symbol is the <module> pseudo-node (kind='file'), and a
            # file-kind src emitting a `calls` edge would introduce a NEW
            # runtime_coherence offender in the (file, python, external_symbol,
            # python) partition — which already carries `imports` — breaking the
            # ADR-0023 §3 shrink-only ratchet (module-level property reads are
            # rare and none of the flagship reads are module-scope).
            if (
                caller_symbol.kind in ("function", "method")
                and isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id in var_types
                and id(node) not in _call_func_attr_ids
            ):
                _getter = _resolve_property_getter(
                    var_types[node.value.id], node.attr,
                    local_symbols, _sym_by_path_name,
                )
                if _getter is not None:
                    _prop_recv = var_types[node.value.id]
                    _prop_meta: dict[str, str] = {
                        "call_construct": "method",
                        "resolution_quality": "type_inferred",
                        "receiver_type_hint": _prop_recv.name,
                    }
                    # WI-supat (D3): stamp the concrete receiver-type id only when
                    # trustworthy (file-unique short name AND not import-shadowed),
                    # as in the method-call Site-2 producer. class_name_counts /
                    # imports / module_imports are closure-visible from
                    # _extract_edges.
                    if _receiver_type_id_trustworthy(
                        _prop_recv, class_name_counts, imports, module_imports,
                        local_symbols,
                    ):
                        _prop_meta["receiver_type_id"] = _prop_recv.id
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=f"python:external:0-0:{node.attr}:unresolved",
                        edge_type="calls",
                        line=node.lineno,
                        evidence_type="ast_call",
                        is_resolved=False,
                        meta=_prop_meta,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

            # Function references in dict values: {"GET": handle_get}
            if isinstance(node, ast.Dict):
                for val in node.values:
                    if isinstance(val, ast.Name):
                        _emit_function_ref(val, caller_symbol, stack=stack)

            # Function references in list/tuple: [func_a, func_b]
            if isinstance(node, (ast.List, ast.Tuple)):
                for elt in node.elts:
                    if isinstance(elt, ast.Name):
                        _emit_function_ref(elt, caller_symbol, stack=stack)

            # Recurse into child nodes (but not into nested function defs —
            # those get their own caller_symbol in the outer FunctionDef loop).
            for child in ast.iter_child_nodes(node):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    process_code_block(
                        [child], caller_symbol, var_types,
                        stack=stack,
                        external_var_types=external_var_types,
                    )

    def _extract_param_types(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, Symbol]:
        """Extract type information from function parameter annotations.

        Handles simple annotations like:
        - def f(session: Session) -> session maps to Session class
        - def f(item: Item) -> item maps to Item class

        Does not currently handle:
        - Generic types: Optional[T], List[T], etc.
        - String annotations: "Session"
        """
        param_types: dict[str, Symbol] = {}

        for arg in func_node.args.args + func_node.args.kwonlyargs:
            if arg.annotation is None:
                continue

            param_name = arg.arg
            annotation = arg.annotation

            # Handle simple name annotations: param: ClassName
            if isinstance(annotation, ast.Name):
                type_name = annotation.id

                # Check local symbols first
                class_symbol = local_symbols.get(type_name)
                if class_symbol and class_symbol.kind == "class":
                    param_types[param_name] = class_symbol
                    continue

                # Check imports (with suffix matching)
                if type_name in imports:
                    module_name, original_name = imports[type_name]
                    class_symbol = _lookup_symbol_by_module(
                        global_symbols, module_name, original_name, resolver=resolver
                    )
                    if class_symbol and class_symbol.kind == "class":
                        param_types[param_name] = class_symbol

            # Handle attribute annotations: param: module.ClassName
            elif isinstance(annotation, ast.Attribute) and isinstance(
                annotation.value, ast.Name
            ):
                receiver_name = annotation.value.id
                attr_name = annotation.attr
                if receiver_name in module_imports:
                    module_name = module_imports[receiver_name]
                    class_symbol = _lookup_symbol_by_module(
                        global_symbols, module_name, attr_name, resolver=resolver
                    )
                    if class_symbol and class_symbol.kind == "class":
                        param_types[param_name] = class_symbol

        return param_types

    def _resolve_decorator_target(
        decorator: ast.expr,
    ) -> Symbol | None:
        """Resolve the target of a decorator expression to a Symbol.

        Handles:
        - @decorator -> decorator function
        - @decorator(args) -> decorator function
        - @module.decorator -> module.decorator function
        - @app.get("/path") -> app.get method
        """
        # For Call decorators, extract the actual function being called
        # @decorator(args) or @app.get("/path")
        if isinstance(decorator, ast.Call):
            decorator = decorator.func

        # Simple name: @decorator or @dataclass
        if isinstance(decorator, ast.Name):
            name = decorator.id
            # Check local symbols
            symbol = local_symbols.get(name)
            if symbol:
                return symbol
            # Check imports (with suffix matching)
            if name in imports:
                module_name, original_name = imports[name]
                return _lookup_symbol_by_module(
                    global_symbols, module_name, original_name, resolver=resolver
                )

        # Attribute: @module.decorator or @app.get
        elif isinstance(decorator, ast.Attribute):
            if isinstance(decorator.value, ast.Name):
                receiver_name = decorator.value.id
                attr_name = decorator.attr

                # Check if receiver is a local class (e.g., @Registry.register)
                # Methods are stored with short name as key, so look up attr_name
                # and verify it's a method of the receiver class
                symbol = local_symbols.get(attr_name)
                if symbol and symbol.name == f"{receiver_name}.{attr_name}":
                    return symbol

                # Check if receiver is an imported module (with suffix matching)
                if receiver_name in module_imports:
                    module_name = module_imports[receiver_name]
                    return _lookup_symbol_by_module(
                        global_symbols, module_name, attr_name, resolver=resolver
                    )

        return None

    def _process_decorators(
        decorated_symbol: Symbol,
        decorator_list: list[ast.expr],
    ) -> None:
        """Create decorated_by edges for each decorator on a symbol."""
        for decorator in decorator_list:
            decorator_symbol = _resolve_decorator_target(decorator)

            # Get the line number from the decorator itself
            line = getattr(decorator, "lineno", 0)

            if decorator_symbol:
                edges.append(Edge.create(
                    src=decorated_symbol.id,
                    dst=decorator_symbol.id,
                    edge_type="decorated_by",
                    line=line,
                    evidence_type="ast_decorator",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            else:
                # Emit unresolved edge for decorators we can't resolve
                # This helps track framework decorators like @app.get
                if isinstance(decorator, ast.Call):
                    dec_func = decorator.func
                else:
                    dec_func = decorator

                if isinstance(dec_func, ast.Attribute) and isinstance(
                    dec_func.value, ast.Name
                ):
                    receiver_name = dec_func.value.id
                    attr_name = dec_func.attr
                    dst_id = f"python:unresolved:0-0:{receiver_name}.{attr_name}:unresolved"
                    edges.append(Edge.create(
                        src=decorated_symbol.id,
                        dst=dst_id,
                        edge_type="decorated_by",
                        line=line,
                        evidence_type="ast_decorator",
                        is_resolved=False,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

            # Check for Django signal receiver decorator: @receiver(signal, ...)
            # Creates signal_receiver edges from signal to handler
            _process_signal_receiver(decorated_symbol, decorator, line)

    def _process_signal_receiver(
        decorated_symbol: Symbol,
        decorator: ast.expr,
        line: int,
    ) -> None:
        """Create signal_receiver edges for Django @receiver decorators.

        When a function is decorated with @receiver(signal) or @receiver([sig1, sig2]),
        create signal_receiver edges from each signal to the decorated function.
        """
        # Must be a call: @receiver(signal, ...)
        if not isinstance(decorator, ast.Call):
            return

        # Check if decorator is "receiver"
        dec_func = decorator.func
        decorator_name = None
        if isinstance(dec_func, ast.Name):
            decorator_name = dec_func.id
        elif isinstance(dec_func, ast.Attribute):
            decorator_name = dec_func.attr

        if decorator_name != "receiver":
            return

        # Extract signals from first argument
        if not decorator.args:
            return

        first_arg = decorator.args[0]
        signal_nodes: list[ast.expr] = []

        # Handle @receiver([signal1, signal2])
        if isinstance(first_arg, ast.List):
            signal_nodes = first_arg.elts
        else:
            # Single signal: @receiver(post_save)
            signal_nodes = [first_arg]

        # Create signal_receiver edges for each signal
        for signal_node in signal_nodes:
            signal_symbol = None

            if isinstance(signal_node, ast.Name):
                signal_name = signal_node.id
                signal_symbol = local_symbols.get(signal_name)
                if not signal_symbol and signal_name in imports:
                    module_name, original_name = imports[signal_name]
                    signal_symbol = _lookup_symbol_by_module(
                        global_symbols, module_name, original_name, resolver=resolver
                    )

            if signal_symbol:
                edges.append(Edge.create(
                    src=signal_symbol.id,
                    dst=decorated_symbol.id,
                    edge_type="signal_receiver",
                    line=line,
                    evidence_type="ast_decorator",
                    meta={"framework_dispatch": "django_signal"},
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            elif isinstance(signal_node, ast.Name):
                # Unresolved signal - emit edge anyway for visibility
                dst_id = f"python:unresolved:0-0:{signal_node.id}:signal"
                edges.append(Edge.create(
                    src=dst_id,
                    dst=decorated_symbol.id,
                    edge_type="signal_receiver",
                    line=line,
                    evidence_type="ast_decorator",
                    is_resolved=False,
                    meta={"framework_dispatch": "django_signal"},
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    # Pre-collect class field types for self.field.method() resolution (INV-014).
    # Scans __init__ methods for self.field = param (typed) and self.field = Class()
    # assignments, building a per-class map of field name -> type Symbol.
    class_field_types: dict[str, dict[str, Symbol]] = {}
    # WI-hiziz PR-3 (review): the NAMES of ALL __init__ ``self.X`` targets per
    # class (typed or not), so the Site-3 emit can exclude an OWN field the
    # child assigns from a factory / untyped param (``self.f = make_conn()``) —
    # which ``class_field_types`` (typed-only) misses. An own field is never
    # inherited, so excluding it prevents a confidently-wrong Site-3 resolution
    # to a same-named PARENT field of a different type.
    class_own_field_names: dict[str, frozenset[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        init_method = None
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
                init_method = item
                break
        if init_method is None:
            continue
        init_param_types = _extract_param_types(init_method)
        field_types: dict[str, Symbol] = {}
        own_field_names: set[str] = set()
        for stmt in ast.walk(init_method):
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    field_name = target.attr
                    own_field_names.add(field_name)
                    # self.field = param where param has type annotation
                    if isinstance(stmt.value, ast.Name) and stmt.value.id in init_param_types:
                        field_types[field_name] = init_param_types[stmt.value.id]
                    # self.field = ClassName()
                    elif isinstance(stmt.value, ast.Call):
                        assigned_class = _resolve_call_target(
                            stmt.value, local_symbols, imports, global_symbols,
                            module_imports, resolver
                        )
                        if assigned_class and assigned_class.kind == "class":
                            field_types[field_name] = assigned_class
        if own_field_names:
            class_own_field_names[node.name] = frozenset(own_field_names)
        if field_types:
            class_field_types[node.name] = field_types
            # WI-hiziz PR-3 (Site 3): mirror java.py — attach
            # {field: type_short_name} to the class symbol's meta["fields"] so
            # inherited_calls._walk_parents_for_field can resolve a
            # self.field.method() where ``field`` is declared on a PARENT.
            # ``local_symbols`` IS the file's ``symbol_by_name``, so this mutates
            # the same Symbol object emitted in the node list (shared reference).
            # Only ADDS the "fields" key — a class's existing base_classes /
            # decorators meta survives. ``_ft.name`` is the type's full name,
            # matching the linker's ``class_ids_by_name`` keys.
            _cls_sym = local_symbols.get(node.name)
            # Only attach to a genuine class symbol: a same-name method/function
            # that shadows the class in the last-write-wins ``local_symbols`` must
            # not receive a spurious (inert) fields key (review finding). The
            # same-name-CLASS clobber (two classes, one short name → recall loss,
            # not a wrong edge) is a deferred id-keyed follow-up.
            if _cls_sym is None or _cls_sym.kind != "class":  # pragma: no cover
                continue
            if _cls_sym.meta is None:
                _cls_sym.meta = {}
            _cls_sym.meta["fields"] = {
                _fn: _ft.name for _fn, _ft in field_types.items()
            }

    # WI-gulot: resolve module-level function aliases (`f = g` where g is a
    # function/method, incl. an imported g). The LHS is extracted as a
    # kind=variable node, so a call through the alias otherwise dead-ends at a
    # 0-out-degree variable and the target appears uncalled (a dispatch:F3 /
    # INV-pohik instance). This name->target map is consumed by _process_call to
    # resolve an alias call straight to the real body (so callers reach it and
    # the target is genuinely `calls`-reachable — the filed repro's expectation).
    # Scan MODULE-LEVEL statements only (``tree.body``), NOT ``ast.walk`` — a
    # function-local ``f = g`` must not pollute this module-scope map, else a
    # module variable of the same name would wrongly resolve to g (the LHS name
    # alone can't distinguish the two scopes). The call resolver's own
    # ``kind == "variable"`` guard means non-variable entries here are inert.
    function_aliases: dict[str, Symbol] = {}
    for _al_node in tree.body:
        if not (isinstance(_al_node, ast.Assign) and isinstance(_al_node.value, ast.Name)):
            continue
        _rhs_name = _al_node.value.id
        _alias_target = local_symbols.get(_rhs_name)
        if _alias_target is None and _rhs_name in imports:
            _mod, _orig = imports[_rhs_name]
            _alias_target = _lookup_symbol_by_module(
                global_symbols, _mod, _orig, resolver=resolver
            )
        if _alias_target is None or _alias_target.kind not in ("function", "method"):
            continue
        for _al_tgt in _al_node.targets:
            if isinstance(_al_tgt, ast.Name):
                function_aliases[_al_tgt.id] = _alias_target

    # WI-gubar (D2): whole-tree set of Attribute-node ids that are a call
    # callee, so the @property-read producer inside process_code_block skips
    # them (a method-call callee is handled by _process_call, not a read). ids
    # are unique per-parse, so one whole-tree set covers every function body
    # AND the module-level block, despite process_code_block recursing per-node.
    _call_func_attr_ids = _collect_call_func_attr_ids([tree])

    # Process functions (including async functions)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # INV-mofav + WI-jafat: every FunctionDef / AsyncFunctionDef walked
            # here is registered in func_symbol_by_node_id — plain functions
            # (top-level and nested) at the elif branch above, and methods via
            # CHANGE A — so the node-id lookup is authoritative. The bare-name
            # fallback below was the path methods took pre-CHANGE-A; it is now a
            # defensive backstop for any future node reaching this loop
            # unregistered, unreachable on the current producer (verified
            # 0/24152 funcdefs on self-analysis), mirroring the
            # func_symbol_by_node_id-None backstop above.
            caller_symbol = func_symbol_by_node_id.get(id(node))
            if caller_symbol is None:  # pragma: no cover - WI-jafat: all FunctionDefs are node-id-registered
                caller_symbol = local_symbols.get(node.name)
            if caller_symbol:
                # Process decorators on the function
                _process_decorators(caller_symbol, node.decorator_list)
                # Extract types from parameter annotations
                param_types = _extract_param_types(node)
                # Merge class field types for self.field.method() resolution
                if caller_symbol.kind == "method":
                    class_name = caller_symbol.name.split(".")[0]
                    if class_name in class_field_types:
                        for fname, fsym in class_field_types[class_name].items():
                            if fname not in param_types:
                                param_types[fname] = fsym
                # INV-mofav: each function's inner_scope contains its nested
                # function helpers, keyed by short name. The RAW dict is kept for
                # closure-factory dispatch (which must see only the caller's OWN
                # inner closures, never a grandparent's); call/reference
                # resolution uses the materialized LEGB stack (identity:F1/F4a).
                inner_scope = nested_by_parent_id.get(caller_symbol.id)
                stack = _build_scope_stack(
                    caller_symbol.id, enclosing_func_id, nested_by_parent_id,
                    local_names_by_func_id,
                )
                _emit_closure_factory_dispatch(node, caller_symbol, inner_scope)
                _emit_module_attr_refs(node.body, caller_symbol)
                process_code_block(node.body, caller_symbol, param_types, stack=stack)
                _emit_variable_refs(
                    node.body, caller_symbol,
                    local_bindings=_collect_local_bindings(node),
                )

        # Process class decorators
        elif isinstance(node, ast.ClassDef):
            class_symbol = local_symbols.get(node.name)
            if class_symbol:
                _process_decorators(class_symbol, node.decorator_list)

    # Process module-level code for <module> pseudo-nodes
    module_symbol = local_symbols.get("<module>")
    if module_symbol:
        # Get top-level statements (excluding function/class defs)
        module_level_nodes = [
            node for node in tree.body
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        _emit_module_attr_refs(module_level_nodes, module_symbol)
        # A single module frame with EMPTY bindings: behaviorally identical to
        # stack=None for every resolution surface (immediate/enclosing lookups
        # return None), but its local_names carry the module-scope rebound names
        # so the WI-noham receiver_type_hint local-class guard also fires at
        # module scope (a module-level `X = f(); X.m()` shadowing class `X`).
        module_stack = ScopeStack(frames=[Scope(
            owner_id=module_symbol.id,
            bindings={},
            local_names=_collect_module_local_names(tree),
        )])
        process_code_block(module_level_nodes, module_symbol, stack=module_stack)
        _emit_variable_refs(module_level_nodes, module_symbol)

    return edges


def _collect_scope_local_names(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    """Names bound as a param / assignment / import / ``global`` in *func_node*'s
    OWN body (nested function/class bodies excluded), minus ``nonlocal`` names.

    Feeds ``Scope.local_names`` so the scope-stack enclosing lookup honors LEGB
    local shadowing (identity:F1/F4a): a name in this set shadows any same-named
    def in a further-out scope (Python calls the local/global, not the enclosing
    def). ``def``/``class`` statement names are excluded — those are the
    ``NestedDef`` bindings the lookup resolves to directly. Distinct from the
    nested ``_collect_local_bindings`` (variable-reference shadow suppression,
    which does not treat ``global``/``nonlocal``).
    """
    names: set[str] = set()
    for arg in (
        func_node.args.args
        + func_node.args.posonlyargs
        + func_node.args.kwonlyargs
    ):
        names.add(arg.arg)
    if func_node.args.vararg:
        names.add(func_node.args.vararg.arg)
    if func_node.args.kwarg:
        names.add(func_node.args.kwarg.arg)
    bound, nonlocals = _collect_bound_names(list(ast.iter_child_nodes(func_node)))
    names |= bound
    return frozenset(names - nonlocals)


def _collect_bound_names(
    child_nodes: list[ast.AST],
) -> tuple[set[str], set[str]]:
    """Walk a scope's direct children, collecting names bound by assignment /
    import / ``global`` and, separately, ``nonlocal`` declarations, skipping
    nested function/class subtrees (their own scopes). Shared by
    ``_collect_scope_local_names`` (function scope, which additionally adds
    params) and ``_collect_module_local_names`` (module scope). Returns
    ``(bound, nonlocal)`` so each caller applies its own params/subtraction.
    """
    names: set[str] = set()
    nonlocals: set[str] = set()
    scope_boundary = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def _walk(nodes: list[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, scope_boundary):
                # A nested function/class is its OWN scope — its locals (and its
                # def/class name) are not this scope's locals. Skip its subtree.
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Global):
                names.update(node.names)
            elif isinstance(node, ast.Nonlocal):
                nonlocals.update(node.names)
            for child in ast.iter_child_nodes(node):
                _walk([child])

    _walk(child_nodes)
    return names, nonlocals


def _collect_module_local_names(tree: ast.Module) -> frozenset[str]:
    """Module-scope analog of ``_collect_scope_local_names``: names REBOUND at
    module level (assignment targets / imports). Feeds the module frame's
    ``Scope.local_names`` so the WI-noham receiver_type_hint guard suppresses a
    hint when a module-level variable shadows a same-named class (the
    module-scope twin of the per-function local-shadow guard). ``global`` at
    module scope is a no-op and ``nonlocal`` is illegal there, so the nonlocal
    set is empty; ``def``/``class`` statement names are excluded (they are the
    genuine class/function symbols a hint legitimately points at).
    """
    bound, nonlocals = _collect_bound_names(list(ast.iter_child_nodes(tree)))
    return frozenset(bound - nonlocals)


def _build_scope_stack(
    caller_id: str,
    enclosing_func_id: dict[str, str],
    nested_by_parent_id: dict[str, dict[str, Symbol]],
    local_names_by_func_id: dict[str, frozenset[str]],
) -> ScopeStack:
    """Materialize the caller's LEGB frame chain (identity:F1/F4a).

    Walks ``enclosing_func_id`` from the caller outward to the outermost
    enclosing function, then builds one :class:`Scope` frame per link
    (outermost-first, caller last). Each frame's bindings are the enclosing
    function's nested helpers (``nested_by_parent_id``) wrapped as
    :class:`NestedDef` — the only Binding variant produced in PR-0 — and its
    ``local_names`` carry that function's locally-bound names (for LEGB
    shadowing). A top-level caller yields a single-frame stack, so
    ``lookup_enclosing`` returns ``None`` for every name and resolution stays
    byte-identical to the pre-rewrite path.
    """
    chain = [caller_id]
    cur = caller_id
    while True:
        nxt = enclosing_func_id.get(cur)
        if nxt is None:
            break
        chain.append(nxt)
        cur = nxt
    chain.reverse()  # outermost-first, caller last
    frames = [
        Scope(
            owner_id=fid,
            bindings={
                name: NestedDef(sym)
                for name, sym in nested_by_parent_id.get(fid, {}).items()
            },
            local_names=local_names_by_func_id.get(fid, frozenset()),
        )
        for fid in chain
    ]
    return ScopeStack(frames=frames)


def _unwind_attribute_chain(
    node: ast.Attribute,
) -> tuple[ast.Name, list[str]] | None:
    """Walk an ``ast.Attribute`` chain back to its root ``ast.Name``.

    Given ``a.b.c.d`` — parsed as
    ``Attribute(Attribute(Attribute(Name('a'), 'b'), 'c'), 'd')`` — returns
    ``(Name('a'), ['b', 'c', 'd'])``. Attributes are returned root-to-leaf.

    Returns ``None`` when the chain's root is not an ``ast.Name`` (e.g.,
    ``f().x.y`` roots at a ``Call``, ``(a+b).c`` at a ``BinOp``). Those
    receivers don't participate in import-qualified call resolution and
    would be misresolved if we pretended they did.
    """
    attrs: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    attrs.reverse()
    return current, attrs


def _resolve_call_target(
    call_node: ast.Call,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str],
    resolver: "SymbolResolver | None" = None,
    inner_scope: dict[str, Symbol] | None = None,
) -> Symbol | None:
    """Resolve the target of a call expression to a Symbol.

    Handles:
    - ClassName() -> class symbol
    - module.ClassName() -> class symbol in module
    - imported_name() -> resolved symbol

    ``inner_scope`` is the enclosing-function scope (INV-mofav): when the
    bare name resolves to a nested function in the caller's body, it wins
    over a same-named top-level Symbol (Python LEGB rule).
    """
    func = call_node.func

    # Simple name: ClassName() or func()
    if isinstance(func, ast.Name):
        name = func.id
        # INV-mofav: enclosing-function scope wins over module scope.
        if inner_scope is not None:
            symbol = inner_scope.get(name)
            if symbol:
                return symbol
        # Check local symbols
        symbol = local_symbols.get(name)
        if symbol:
            return symbol
        # Check imports (with suffix matching)
        if name in imports:
            module_name, original_name = imports[name]
            return _lookup_symbol_by_module(
                global_symbols, module_name, original_name, resolver=resolver
            )

    # Attribute: module.ClassName() or obj.method()
    elif isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            receiver_name = func.value.id
            attr_name = func.attr

            # Check if receiver is an imported module (with suffix matching)
            if receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                return _lookup_symbol_by_module(
                    global_symbols, module_name, attr_name, resolver=resolver
                )

    return None


def _receiver_type_id_trustworthy(
    recv_sym: Symbol,
    class_name_counts: dict[str, int],
    imports: dict[str, tuple[str, str]],
    module_imports: dict[str, str],
    local_symbols: dict[str, Symbol],
) -> bool:
    """WI-supat (D3): whether a concrete receiver-type id is safe to stamp.

    The Site-2 receiver-type inference is bare-name-based (last-write-wins
    ``symbol_by_name`` / local-first annotation resolution), so a concrete id is
    only trustworthy under BOTH conditions:

    1. **File-unique short name** — with >=2 same-short-name ``ClassDef``s in the
       file the inference could have hit the wrong twin (``class_name_counts``).
    2. **Not import-shadowed** — when the resolved type IS the in-file class of
       that name (``local_symbols.get(name) is recv_sym``) but a same-name import
       exists (``name in imports``/``module_imports``), a later
       ``from x import Name`` rebinds the name at runtime (Python last-binding-
       wins), so the local-first-resolved type is the WRONG class. This is
       *precise*, not blanket: a correctly cross-file-resolved imported type
       (``recv_sym`` is NOT the local symbol) keeps its id, preserving the
       cross-file collision-recovery this feature exists for.

    When it returns ``False`` the producer omits the id and the linker falls back
    to the safe name+ambiguity-guard path (biases to unresolved on a collision).
    """
    name = recv_sym.name
    if class_name_counts.get(name, 0) > 1:
        return False
    if local_symbols.get(name) is recv_sym and (
        name in imports or name in module_imports
    ):
        return False
    return True


def _process_call(
    call_node: ast.Call,
    caller_symbol: Symbol,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str],
    var_types: dict[str, Symbol],
    edges: list[Edge],
    resolver: "SymbolResolver | None" = None,
    sym_by_path_name: dict[tuple[str, str], Symbol] | None = None,
    *,
    run_id: str,
    stack: ScopeStack | None = None,
    external_var_types: dict[str, str] | None = None,
    function_aliases: dict[str, Symbol] | None = None,
    own_field_names: frozenset[str] = frozenset(),
    method_to_enclosing_class_id: dict[str, str] | None = None,
    class_name_counts: dict[str, int] | None = None,
) -> None:
    """Process a single call expression and emit appropriate edges.

    Handles:
    - Direct calls: helper(), ClassName()
    - Self method calls: self.method()
    - Self field method calls: self.field.method() (using field type inference)
    - Module-qualified calls: module.ClassName(), module.func()
    - Variable method calls: stub.method() (using var_types for type inference)

    ``stack`` is the caller's materialized LEGB frame chain (identity:F1/F4a):
    bare-name calls resolve through its immediate frame (INV-mofav) before
    ``local_symbols``/imports, then via a last-resort enclosing-scope lookup.

    ``external_var_types`` (WI-fuvuj) maps a local variable name to the
    catalog module string of the I/O object it was constructed from
    (``f = open(p)`` → ``{"f": "file"}``). When a bare ``receiver.method()``
    call's receiver is in this map, the unresolved-edge emit uses a
    module-qualified dst (carrying the inferred module in both the dst id's
    module slot and a structured ``dst_ref``) so io-boundary can classify it.
    """
    if external_var_types is None:  # pragma: no cover - defensive default
        external_var_types = {}
    if method_to_enclosing_class_id is None:  # pragma: no cover - defensive default
        method_to_enclosing_class_id = {}
    if class_name_counts is None:  # pragma: no cover - defensive default
        class_name_counts = {}
    func = call_node.func
    callee_symbol = None
    is_instantiation = False
    evidence_type = "ast_call_direct"
    call_meta: dict[str, str] | None = None

    # Case 1: Simple name calls - helper() or ClassName()
    if isinstance(func, ast.Name):
        callee_name = func.id
        # INV-mofav: enclosing-function scope wins over module scope (step 1-2).
        callee_symbol = stack.lookup_immediate(callee_name) if stack else None
        if callee_symbol is None:
            callee_symbol = local_symbols.get(callee_name)
        # WI-gulot: a module-level `f = g` function alias resolves as a variable;
        # chase it to the aliased function so the call reaches the real body
        # (else it dead-ends at the 0-out-degree variable node).
        if (
            callee_symbol is not None
            and callee_symbol.kind == "variable"
            and function_aliases
            and callee_name in function_aliases
        ):
            callee_symbol = function_aliases[callee_name]

        if callee_symbol and callee_symbol.kind == "class":
            is_instantiation = True
        elif not callee_symbol and callee_name in imports:
            module_name, original_name = imports[callee_name]
            callee_symbol = _lookup_symbol_by_module(
                global_symbols, module_name, original_name, resolver=resolver
            )
            if callee_symbol and callee_symbol.kind == "class":
                is_instantiation = True

        # identity:F1/F4a step-4: last-resort enclosing-scope lookup — resolves a
        # bare call to a helper defined in a GRANDPARENT (or higher) enclosing
        # function that the flat immediate frame missed. Additive: fires only
        # when nothing above resolved, and returns only nested FUNCTIONS (never a
        # class), so is_instantiation stays False and no existing edge changes.
        if callee_symbol is None and stack is not None:
            callee_symbol = stack.lookup_enclosing(callee_name)

    # Case 2: Attribute calls - self.method(), module.ClassName(), variable.method()
    elif isinstance(func, ast.Attribute):
        attr_name = func.attr
        # Cluster 28D (audit-findings 0012): a method call folds to the
        # ``ast_call`` apex + ``meta['call_construct']='method'`` (WI-nibis),
        # not the parked peer ``ast_call_method``.
        evidence_type = "ast_call"
        call_meta = {"call_construct": "method"}

        if isinstance(func.value, ast.Name):
            receiver_name = func.value.id

            # Case 2a: self.method()
            if receiver_name == "self":
                callee_symbol = local_symbols.get(attr_name)

            # Case 2b: module.ClassName() or module.func()
            elif receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                callee_symbol = _lookup_symbol_by_module(
                    global_symbols, module_name, attr_name, resolver=resolver
                )
                if callee_symbol and callee_symbol.kind == "class":
                    is_instantiation = True

            # Case 2c: variable.method() - use type inference
            elif receiver_name in var_types:
                class_symbol = var_types[receiver_name]
                # Look for ClassName.method in local symbols
                qualified_name = f"{class_symbol.name}.{attr_name}"
                callee_symbol = local_symbols.get(qualified_name)
                # If not found locally, try global symbols via index
                if not callee_symbol and sym_by_path_name is not None:
                    callee_symbol = sym_by_path_name.get(
                        (class_symbol.path, qualified_name)
                    )

            # Case 2d: Imported class method calls - Item.model_validate()
            # When Item is imported via "from app.models import Item"
            elif receiver_name in imports:
                module_name, original_name = imports[receiver_name]
                class_symbol = _lookup_symbol_by_module(
                    global_symbols, module_name, original_name, resolver=resolver
                )
                if class_symbol and class_symbol.kind == "class":
                    # Look for ClassName.method (class method/static method)
                    qualified_name = f"{original_name}.{attr_name}"
                    if sym_by_path_name is not None:
                        callee_symbol = sym_by_path_name.get(
                            (class_symbol.path, qualified_name)
                        )

                # Case 2e: Imported submodule calls - crud.create_user()
                # When crud is imported via "from app import crud" (crud is a module)
                # and we call crud.create_user(), we need to look up (app.crud, create_user)
                if not callee_symbol:
                    submodule_name = f"{module_name}.{original_name}"
                    callee_symbol = _lookup_symbol_by_module(
                        global_symbols, submodule_name, attr_name, resolver=resolver
                    )

        # Case 2f: self.field.method() - call on injected dependency (INV-014)
        # Pattern: self.svc.process() where self.svc was assigned from a typed param
        # or constructor call in __init__. Field types are pre-loaded into var_types.
        elif (
            isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        ):
            field_name = func.value.attr
            if field_name in var_types:
                class_symbol = var_types[field_name]
                qualified_name = f"{class_symbol.name}.{attr_name}"
                callee_symbol = local_symbols.get(qualified_name)
                if not callee_symbol and sym_by_path_name is not None:
                    callee_symbol = sym_by_path_name.get(
                        (class_symbol.path, qualified_name)
                    )

    # Emit edge if we resolved the callee
    if callee_symbol:
        if is_instantiation:
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=callee_symbol.id,
                edge_type="instantiates",
                line=call_node.lineno,
                evidence_type="ast_new",
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
        else:
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=callee_symbol.id,
                edge_type="calls",
                line=call_node.lineno,
                evidence_type=evidence_type,
                meta=call_meta,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
    else:
        # Emit unresolved edge for attribute calls with known module context
        # This enables cross-language linking and makes the graph more complete
        func = call_node.func
        # Hoisted (WI-hiziz PR-3): the caller's scope-local names + decorator
        # names are the shared INV-fahub guard inputs for BOTH the Site-1
        # self.method() branch and the Site-3 self.field.method() branch below.
        _caller_locals = (
            stack.frames[-1].local_names
            if stack is not None and stack.frames
            else frozenset()
        )
        _caller_decos = {
            d.get("name")
            for d in (caller_symbol.meta or {}).get("decorators", [])
            if isinstance(d, dict)
        }
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            receiver_name = func.value.id
            attr_name = func.attr

            # Case: module.func() where module is imported but func not found
            if receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                dst_id = f"python:{module_name}:0-0:{attr_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta={"call_construct": "method"},
                    dst_ref=ExternalRef(
                        lang="python", module_path=module_name, name=attr_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            # Case: imported_name.method() where imported_name not resolved
            elif receiver_name in imports:
                module_name, original_name = imports[receiver_name]
                dst_id = f"python:{module_name}:0-0:{original_name}.{attr_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta={"call_construct": "method"},
                    dst_ref=ExternalRef(
                        lang="python",
                        module_path=module_name,
                        name=f"{original_name}.{attr_name}",
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            # WI-fuvuj: local_var.method() where the receiver was typed by a
            # known I/O constructor (``f = open(p)`` / ``s = socket.socket()``,
            # incl. the ``with ... as`` form). Emit a MODULE-QUALIFIED dst so
            # io-boundary's catalog can disambiguate the method (e.g. file
            # .read() → fs_read, socket.socket.send() → net_send) via the
            # module-filter path — bypassing the ambiguous_names suppression
            # that protects UNtyped receivers. The module is carried in BOTH
            # the dst id's module slot AND the dst_ref because the io-boundary
            # CLI consumer drops dst_ref on serialize/reload and falls back to
            # parsing the dst id.
            elif receiver_name in external_var_types:
                ext_module = external_var_types[receiver_name]
                dst_id = f"python:{ext_module}:0-0:{attr_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta={"call_construct": "method", "resolution_quality": "type_inferred"},
                    dst_ref=ExternalRef(
                        lang="python", module_path=ext_module, name=attr_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            # Case: local_var.method() where type cannot be inferred.
            # Emit unresolved edge using the attribute name so that IO
            # boundary analysis and taint-flow analysis can detect it.
            # Lower confidence since we don't know the receiver type.
            else:
                dst_id = f"python:external:0-0:{attr_name}:unresolved"
                unresolved_meta = {
                    "call_construct": "method",
                    "resolution_quality": "type_inferred",
                }
                # WI-noham Part A: when the receiver's type is GENUINELY
                # inferred, stamp a receiver_type_hint so the inherited_calls
                # linker's strict INV-fahub Site-2 mode can resolve the method
                # on the concrete type. Two inferred sources: a var_types-tracked
                # variable (param annotation / constructor / return-type), whose
                # method Case 2c could not resolve directly (inherited, or a
                # cross-file miss in single-file analysis); or a bare LOCAL class
                # name used as a receiver (``Foo.bar()`` — a static/classmethod
                # call py.py has no direct case for). The edge STAYS
                # is_resolved=False with an unchanged dst — the linker is the
                # sole minter of the resolved edge (taint-safe by construction).
                # An untyped / duck receiver gets NO hint: INV-fahub mandates
                # biasing to unresolved rather than binding to an arbitrary
                # same-named internal def.
                #
                # SCOPE GUARD (INV-fahub): the local-class branch reads the
                # FILE-GLOBAL ``local_symbols`` (symbol_by_name), so a bare-name
                # receiver that is a function-LOCAL binding (param or assignment)
                # shadowing a module-level class would otherwise resolve to that
                # class — an under-determined receiver. We suppress the hint when
                # ``receiver_name`` is bound in the caller's own scope (the same
                # shadow signal ``_emit_variable_refs`` consults), using the
                # already-materialized LEGB frame's ``local_names``. The
                # var_types branch needs no such guard: var_types is built
                # per-function, so it is already scope-local.
                # WI-hiziz PR-2 (Site 1): a bare ``self.method()`` call that
                # Case 2a could not resolve in-file is a cross-file INHERITED
                # method (or an absent one). Stamp the DIRECT enclosing class
                # short name so the inherited_calls linker's Site-1 resolver
                # walks the method up the class's C3 MRO (Python walker landed in
                # PR-1). This is the LEADING branch of a mutually-exclusive elif
                # chain — it dispatches to Site-1 (enclosing_class), never Site-2
                # (receiver_type_hint, which _try_resolve checks first). Guards,
                # each load-bearing:
                #   * ``kind == "method"`` — crash guard: guarantees a dotted
                #     qualified_name so ``split(".")[-2]`` cannot IndexError (a
                #     module-level fn named-param ``self``, or a nested function,
                #     is kind "function", where the class is unrecoverable).
                #   * ``receiver_name not in var_types`` — an EXPLICIT ``self: T``
                #     annotation is a deliberate static-type declaration whose
                #     methods may live OFF the enclosing class's MRO (the mixin/
                #     host idiom). It must route to Site-2 on ``T`` (the demoted
                #     elif), not Site-1 on the enclosing class — else a legit edge
                #     is lost, or (namesake case) a confidently-wrong 0.90 edge is
                #     minted. Only an UNANNOTATED ``self`` (not in var_types) is
                #     lexically the enclosing class.
                #   * ``"self" in _caller_locals`` — ``self`` must be a bound
                #     local (param), excluding a classmethod that references
                #     ``self`` (``self`` undefined; its param is ``cls``) and a
                #     @staticmethod with no ``self`` param.
                #   * ``"staticmethod" not in _caller_decos`` — a @staticmethod
                #     that DOES declare a ``self`` param (anti-pattern) passes the
                #     locals gate, but its ``self`` is an arbitrary argument, not
                #     an instance of the enclosing class — an under-determined
                #     receiver that INV-fahub requires biasing to unresolved.
                # enclosing_class ONLY — taint-safe (is_resolved stays False, dst
                # unchanged, the linker is the sole minter).
                if (
                    receiver_name == "self"
                    and caller_symbol.kind == "method"
                    and receiver_name not in var_types
                    and "self" in _caller_locals
                    and "staticmethod" not in _caller_decos
                ):
                    unresolved_meta["enclosing_class"] = (
                        caller_symbol.qualified_name.split(".")[-2]
                    )
                    # WI-supat (D3): stamp the AUTHORITATIVE enclosing class id
                    # (the lexical method->class map, clobber-immune) so the
                    # linker resolves a same-short-name / cross-language namesake
                    # precisely instead of biasing to unresolved.
                    _encl_id = method_to_enclosing_class_id.get(caller_symbol.id)
                    if _encl_id is not None:
                        unresolved_meta["enclosing_class_id"] = _encl_id
                elif receiver_name in var_types:
                    _rt = var_types[receiver_name]
                    unresolved_meta["receiver_type_hint"] = _rt.name
                    # WI-supat (D3): stamp the concrete receiver-type id only when
                    # it is trustworthy (file-unique short name AND not shadowed by
                    # a same-name import); else omit and fall back to name+guard.
                    if _receiver_type_id_trustworthy(
                        _rt, class_name_counts, imports, module_imports,
                        local_symbols,
                    ):
                        unresolved_meta["receiver_type_id"] = _rt.id
                elif receiver_name not in _caller_locals:
                    _recv_sym = local_symbols.get(receiver_name)
                    if _recv_sym is not None and _recv_sym.kind == "class":
                        unresolved_meta["receiver_type_hint"] = receiver_name
                        if _receiver_type_id_trustworthy(
                            _recv_sym, class_name_counts, imports,
                            module_imports, local_symbols,
                        ):
                            unresolved_meta["receiver_type_id"] = _recv_sym.id
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta=unresolved_meta,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        ):
            # WI-hiziz PR-3 (Site 3): self.field.method() that Case 2f could not
            # resolve. An INHERITED field lives in class_field_types[parent] and
            # is never merged into this method's var_types (only the class's OWN
            # fields are), so it misses Case 2f and lands here. Stamp
            # inherited_field_receiver + enclosing_class so the inherited_calls
            # Site-3 resolver walks the enclosing class's PARENTS for the field's
            # type and resolves the method there (ast_call_inherited_field @0.80).
            # Taint-safe: is_resolved stays False, dst is an unchanged external
            # unresolved id, the linker is the sole minter. Guards mirror the
            # Site-1 branch (each load-bearing): kind=="method" (dotted
            # qualified_name for split('.')[-2]); "self" not in var_types (an
            # annotated ``def m(self: T)`` binds self to T, whose fields differ
            # from the LEXICAL enclosing class — route away); "self" in
            # _caller_locals (excludes a classmethod referencing self);
            # "staticmethod" not in _caller_decos (a staticmethod's declared self
            # is under-determined); and the OWN-field exclusion the linker's
            # parent-only walk assumes — ``field_name not in var_types`` (typed
            # own fields) AND ``field_name not in own_field_names`` (EVERY
            # __init__ self.X target, incl. an untyped/factory ``self.f =
            # make_conn()`` that var_types misses). An own field is never
            # inherited, so this blocks the shadow FP where the child re-declares
            # a parent field name with a different type.
            field_name = func.value.attr
            method_name = func.attr
            if (
                caller_symbol.kind == "method"
                and "self" not in var_types
                and "self" in _caller_locals
                and "staticmethod" not in _caller_decos
                and field_name not in var_types
                and field_name not in own_field_names
            ):
                _site3_meta: dict[str, str] = {
                    "call_construct": "method",
                    "inherited_field_receiver": field_name,
                    "enclosing_class": (
                        caller_symbol.qualified_name.split(".")[-2]
                    ),
                }
                # WI-supat (D3): the authoritative enclosing-class id (same
                # contract as Site-1) lets the linker start the parent-field walk
                # from exactly the caller's lexical class, skipping the enclosing
                # ambiguity guard on a same-short-name collision.
                _encl_id = method_to_enclosing_class_id.get(caller_symbol.id)
                if _encl_id is not None:
                    _site3_meta["enclosing_class_id"] = _encl_id
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=f"python:external:0-0:{method_name}:unresolved",
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call",
                    is_resolved=False,
                    meta=_site3_meta,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
        elif isinstance(func, ast.Attribute):
            # WI-zigah: multi-segment chain like `urllib.request.urlopen(x)`
            # where func.value is itself an Attribute. Walk back to the root
            # Name, look it up in module_imports, and emit a qualified
            # unresolved edge so io-boundaries and taint-flow can match
            # dotted-submodule stdlib primitives.
            chain = _unwind_attribute_chain(func)
            if chain is not None:
                root_name_node, chain_attrs = chain
                root_name = root_name_node.id
                if root_name in module_imports and len(chain_attrs) >= 2:
                    real_root = module_imports[root_name]
                    submodule = real_root + "." + ".".join(chain_attrs[:-1])
                    callee = chain_attrs[-1]
                    dst_id = f"python:{submodule}:0-0:{callee}:unresolved"
                    edges.append(Edge.create(
                        src=caller_symbol.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=call_node.lineno,
                        evidence_type="ast_call_direct",
                        is_resolved=False,
                        dst_ref=ExternalRef(
                            lang="python", module_path=submodule, name=callee
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))
        elif isinstance(func, ast.Name):
            # WI-zigah: bare call like `urlopen(x)` after
            # `from urllib.request import urlopen`, where Case 1 looked the
            # name up in `imports` but _lookup_symbol_by_module returned None
            # (stdlib/external target). Emit an unresolved edge keyed by the
            # recorded (module, original_name) pair.
            callee_name = func.id
            if callee_name in imports:
                module_name, original_name = imports[callee_name]
                dst_id = f"python:{module_name}:0-0:{original_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call_direct",
                    is_resolved=False,
                    dst_ref=ExternalRef(
                        lang="python", module_path=module_name, name=original_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))
            elif callee_name in EXTERNAL_CONSTRUCTOR_TYPES:
                # WI-mitul: a bare builtin I/O constructor (open) — Case 1 found
                # no import so nothing was emitted, leaving the io_primitives/
                # python.yaml `builtins` rows (fs_read/fs_write functions=[open])
                # dead. Emit a calls edge to builtins so open() itself is a
                # visible I/O call in every syntactic form. (For a bare ast.Name
                # only the bare EXTERNAL_CONSTRUCTOR_TYPES key `open` can match;
                # the dotted `socket.socket` entry is an ast.Attribute reached
                # by a different branch.) The receiver's .read()/.write() edges
                # (WI-fuvuj, module=file) are orthogonal to this open()-call edge.
                dst_id = f"python:builtins:0-0:{callee_name}:unresolved"
                edges.append(Edge.create(
                    src=caller_symbol.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=call_node.lineno,
                    evidence_type="ast_call_direct",
                    is_resolved=False,
                    dst_ref=ExternalRef(
                        lang="python", module_path="builtins", name=callee_name
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))


def extract_nodes(py_file: Path, global_symbols: dict[str, Symbol] | None = None) -> AnalysisResult:
    """
    Extract function/class definitions and call edges from a Python file.

    Returns an AnalysisResult with symbols and edges.
    Gracefully handles syntax errors and encoding issues.

    Note: For cross-file call detection, use analyze_python() instead.
    This function only detects intra-file calls for backwards compatibility.
    """
    file_analysis, _ = _extract_file_analysis(py_file)
    if file_analysis is None:
        return AnalysisResult(symbols=[], edges=[], usage_contexts=[])

    # For single-file analysis, only detect local calls.
    # WI-higap: create a run so Edge constructions have a valid origin_run_id.
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    edges = _extract_edges(
        file_analysis.tree, file_analysis.symbol_by_name, {}, {},
        file_analysis.module_imports,
        run_id=run.execution_id,
        nested_by_parent_id=file_analysis.nested_by_parent_id,
        func_symbol_by_node_id=file_analysis.func_symbol_by_node_id,
        enclosing_func_id=file_analysis.enclosing_func_id,
        local_names_by_func_id=file_analysis.local_names_by_func_id,
        method_to_enclosing_class_id=file_analysis.method_to_enclosing_class_id,
    )
    return AnalysisResult(
        symbols=file_analysis.symbols,
        edges=edges,
        usage_contexts=file_analysis.usage_contexts,
    )


@register_analyzer("python", supports_max_files=True)
def analyze_python(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """
    Analyze all Python files in a repository.

    Returns an AnalysisResult with all detected symbols, edges, and provenance.
    Supports cross-file call detection via import resolution.

    Args:
        repo_root: Root directory of the repository
        max_files: Optional limit on number of files to analyze
    """
    import time

    start_time = time.time()

    # Create analysis run for provenance tracking
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Detect src/ layout source roots (PEP 517/518 + monorepo
    # packages/<pkg>/src/<mod>/ layouts — WI-davan E1).
    source_roots = _detect_source_roots(repo_root)

    # First pass: collect all symbols and imports from all files
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    for py_file in find_python_files(repo_root, max_files=max_files):
        analysis, fail_reason = _extract_file_analysis(py_file, repo_root, source_roots)
        if analysis is not None:
            file_analyses[py_file] = analysis
        else:
            files_skipped += 1
            # INV-buhur: record the dropped file so consumers can detect
            # partially-analyzed repos. Path is repo-relative when possible.
            try:
                rel = str(py_file.relative_to(repo_root))
            except ValueError:  # pragma: no cover  # defensive: should always be under repo_root
                rel = str(py_file)
            run.record_failed_file(rel, fail_reason or "parse failure")

    # Build global symbol table: (module_name, symbol_name) -> Symbol
    global_symbols: dict[tuple[str, str], Symbol] = {}
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root, source_roots)
        # INV-nuzas: a package's __init__.py *is* the package module. A symbol
        # DEFINED in ``pkg/__init__.py`` is importable/callable as ``pkg.<name>``,
        # but _module_name_from_path keys it under ``pkg.__init__`` — so every
        # cross-module call/import to such a symbol missed the (module, name)
        # lookup and leaked to an external_symbol twin (342 first-party ``calls``
        # edges on the self-corpus). Also register __init__-defined symbols under
        # the importable package name. (The re-export aliasing below covers names
        # IMPORTED into __init__; this covers names DEFINED there. The
        # ``pkg.__init__`` key is retained for back-compat — nothing references
        # it, but keeping it makes this strictly additive / zero-regression.)
        keys = [module_name]
        if py_file.name == "__init__.py":
            package_name = module_name.rsplit(".__init__", 1)[0]
            if package_name != module_name:
                keys.append(package_name)
        for symbol in analysis.symbols:
            for key in keys:
                global_symbols[(key, symbol.name)] = symbol

    # Process re-exports from __init__.py files
    # When __init__.py does "from .submodule import helper", add an alias
    # so that "from package import helper" resolves to the real symbol
    for py_file, analysis in file_analyses.items():
        if py_file.name != "__init__.py":
            continue

        module_name = _module_name_from_path(py_file, repo_root, source_roots)
        # Package name is module name without .__init__ suffix
        package_name = module_name.rsplit(".__init__", 1)[0]

        for local_name, (resolved_module, original_name) in analysis.imports.items():
            # Check if this import points to a known symbol (with suffix matching)
            source_symbol = _lookup_symbol_by_module(
                global_symbols, resolved_module, original_name
            )
            if source_symbol:
                # Add alias: (package, local_name) -> source_symbol
                global_symbols[(package_name, local_name)] = source_symbol
                # Mark the source symbol as re-exported from __init__.py
                # so library-export patterns can detect it
                if "re_exported" not in source_symbol.modifiers:
                    source_symbol.modifiers.append("re_exported")

    # supply:F4 (INV-nuzas) — map every in-tree dotted module name to its
    # first-party file-anchor id, so imports of workspace-sibling modules resolve
    # to real in-repo nodes instead of dangling to phantom external_symbol
    # boundary nodes. Built from the absolute py_file paths (the same form
    # _make_file_id uses for the import-edge SOURCE); the orchestrator
    # relativizes every id uniformly afterward, so dst and src stay consistent.
    module_to_file_id: dict[str, str] = {}
    for py_file in file_analyses:
        module_name = _module_name_from_path(py_file, repo_root, source_roots)
        file_id = _make_file_id(str(py_file))
        module_to_file_id[module_name] = file_id
        if py_file.name == "__init__.py":
            # A package is importable by its package name (module sans .__init__).
            package_name = module_name.rsplit(".__init__", 1)[0]
            module_to_file_id[package_name] = file_id

    # Create resolver for efficient lookups in Pass 2 (with cached indexes)
    from hypergumbo_core.symbol_resolution import SymbolResolver
    resolver = SymbolResolver(global_symbols)

    # Build (path, name) -> symbol index for O(1) lookups in typed method
    # resolution. Replaces O(n) scans of global_symbols.items() that check
    # sym.path == target_path and sym_name == target_name.
    _sym_by_path_name: dict[tuple[str, str], Symbol] = {}
    for (_mod, sym_name), sym in global_symbols.items():
        key = (sym.path, sym_name)
        # First entry wins (same as the break in the old linear scan)
        if key not in _sym_by_path_name:
            _sym_by_path_name[key] = sym

    # Second pass: extract edges with cross-file resolution
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []
    # Load python.yaml dataflow config once for library_patterns fallback
    # in annotate_dataflow_ast (per-file is wasteful — it's a static config).
    py_dataflow_config = get_dataflow_config("python")
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root, source_roots)

        # Set origin on symbols
        for symbol in analysis.symbols:
            symbol.origin = [PASS_ID]
            symbol.origin_run_id = run.execution_id
        all_symbols.extend(analysis.symbols)

        # Extract call edges (WI-higap: run_id plumbed at construction so
        # Edge.__post_init__ enforcement passes without orchestrator backfill).
        call_edges = _extract_edges(
            analysis.tree, analysis.symbol_by_name, analysis.imports, global_symbols,
            analysis.module_imports, resolver, _sym_by_path_name,
            run_id=run.execution_id,
            nested_by_parent_id=analysis.nested_by_parent_id,
            func_symbol_by_node_id=analysis.func_symbol_by_node_id,
            enclosing_func_id=analysis.enclosing_func_id,
            local_names_by_func_id=analysis.local_names_by_func_id,
            method_to_enclosing_class_id=analysis.method_to_enclosing_class_id,
        )
        # ADR-0015: annotate edges with access_mode from Python AST context.
        # Pass source + python.yaml config so library_patterns (e.g. .append,
        # .write, .send → write) can fall back when the AST positional walk
        # leaves a call edge unclassified.  Without this, PR #2733's
        # library_patterns wiring is dead code for Python.
        call_edges = annotate_dataflow_ast(
            call_edges, analysis.tree,
            source=analysis.source,
            config=py_dataflow_config,
        )
        all_edges.extend(call_edges)

        # Extract import edges
        import_edges = _extract_import_edges(
            analysis.tree, str(py_file), module_name, global_symbols, resolver,
            module_to_file_id=module_to_file_id,
            run_id=run.execution_id,
        )
        all_edges.extend(import_edges)

        # Collect usage contexts (v1.1.x)
        all_usage_contexts.extend(analysis.usage_contexts)

    # Extract inheritance edges (META-001: base_classes metadata -> extends edges)
    # Build multi-value class lookup: name -> list of candidates
    # (single-value dict had last-writer-wins bug: 238 Django 'Model' classes
    # all resolved to a single test stub instead of django.db.models.base.Model)
    class_by_name: dict[str, list[Symbol]] = {}
    for sym in all_symbols:
        if sym.kind == "class":
            class_by_name.setdefault(sym.name, []).append(sym)

    # Build symbol ID -> file-level imports mapping for disambiguation
    sym_file_imports: dict[str, dict[str, tuple[str, str]]] = {}
    for _py_file, analysis in file_analyses.items():
        for sym in analysis.symbols:
            sym_file_imports[sym.id] = analysis.imports

    # Create extends edges with import-aware disambiguation
    inheritance_edges = _extract_inheritance_edges(
        all_symbols, class_by_name, sym_file_imports, run
    )
    all_edges.extend(inheritance_edges)

    # Update run metadata
    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    # Parse pyproject.toml deps for tier-2 classification of boundary
    # nodes (WI-nunuj). Empty manifest → no entries → boundary nodes
    # stay tier 3 (no regression vs. previous behaviour).
    from hypergumbo_lang_mainstream.py_deps import parse_python_dependencies
    py_manifest = parse_python_dependencies(repo_root)

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
        dependency_manifest=py_manifest if py_manifest.entries else None,
    )
