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
- Extract rich metadata (decorators, base classes, parameters) per ADR-0003

**Pass 2 - Edge Extraction:**
- Walk AST to find function/method call sites
- Resolve callees using local symbols first, then imports
- Detect self.method() calls within classes
- Detect ClassName() instantiation patterns
- Create import edges from files to imported symbols

Detected Patterns
-----------------
- Function calls: helper(), module.func()
- Method calls: self.method(), obj.method()
- Class instantiation: ClassName()
- Imports: from X import Y, import X
- Django URL patterns: path(), re_path(), url() calls in urls.py (deprecated)

ID Schemes
----------
- **stable_id**: sha256 of signature (param count, arity flags, decorators).
  Survives renames and moves if signature unchanged.
- **shape_id**: sha256 of AST structure (control flow, nesting).
  Detects clones with different variable names.

Rich Metadata (ADR-0003)
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
- Rich metadata enables future FRAMEWORK_PATTERNS phase for semantic detection
"""
import ast
import hashlib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol


def find_python_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Python files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.py"], max_files=max_files)


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID in format {lang}:{file}:{start}-{end}:{name}:{kind}."""
    return f"python:{path}:{line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Python file node (used as import edge source)."""
    return f"python:{path}:1-1:file:file"


def _make_module_id(module_name: str) -> str:
    """Generate ID for an external module (used as import edge destination)."""
    return f"python:{module_name}:0-0:module:module"


# HTTP methods recognized as route decorators (FastAPI, Flask 2.0+)
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route"}

# Django URL pattern functions (deprecated - use django.yaml patterns)
DJANGO_URL_FUNCTIONS = {"path", "re_path", "url"}

# Deprecation tracking for analyzer-level route detection (ADR-0003 v1.0.x)
# Framework-specific route detection is deprecated in favor of YAML patterns
_deprecated_route_warnings_emitted: set[str] = set()


def _emit_route_deprecation_warning(framework: str) -> None:
    """Emit deprecation warning for analyzer-level route detection.

    This is deprecated in ADR-0003 v1.0.x. Use YAML patterns instead.
    Warning emitted once per framework per session.
    """
    if framework in _deprecated_route_warnings_emitted:
        return
    _deprecated_route_warnings_emitted.add(framework)
    warnings.warn(
        f"{framework} analyzer-level route detection is deprecated. "
        f"Use framework YAML patterns (--frameworks) for semantic detection. "
        f"See ADR-0003 for migration guidance.",
        DeprecationWarning,
        stacklevel=4,
    )


def _ast_value_to_python(node: ast.expr) -> str | int | float | bool | list | dict | None:
    """Convert an AST expression to a Python value representation.

    For simple literals, returns the actual value.
    For complex expressions (names, calls, etc.), returns string representation.
    """
    if isinstance(node, ast.Constant):
        # Handle Ellipsis (...) which is not JSON-serializable
        if node.value is ...:
            return "..."
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


def _extract_django_url_patterns(tree: ast.Module) -> list[tuple[int, int, str, str | None]]:
    """Extract Django URL patterns from path(), re_path(), url() calls.

    Returns list of (start_line, end_line, route_path, view_name).
    """
    patterns: list[tuple[int, int, str, str | None]] = []

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
        route_path = None
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            route_path = first_arg.value
        elif isinstance(first_arg, ast.JoinedStr):  # pragma: no cover
            continue  # Skip dynamic patterns (f-strings)

        if not route_path:  # pragma: no cover
            continue

        # Extract view name from second argument
        view_name = None
        if len(node.args) >= 2:
            second_arg = node.args[1]
            if isinstance(second_arg, ast.Attribute):
                # views.user_list -> "user_list"
                view_name = second_arg.attr
            elif isinstance(second_arg, ast.Name):
                # user_list -> "user_list"
                view_name = second_arg.id

        patterns.append((
            node.lineno,
            getattr(node, "end_lineno", node.lineno),
            route_path,
            view_name,
        ))

    return patterns


def _compute_stable_id(node: ast.FunctionDef | ast.ClassDef) -> str:
    """Compute stable_id based on signature (survives renames/moves).

    Returns:
    sha256({kind}:{param_count}:{arity_flags}:{decorators})

    arity_flags: has_defaults, has_varargs, has_kwargs
    decorators: sorted list of decorator names
    """
    kind = "function" if isinstance(node, ast.FunctionDef) else "class"

    # Extract signature info for functions
    if isinstance(node, ast.FunctionDef):
        args = node.args
        param_count = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
        has_defaults = len(args.defaults) > 0 or len(args.kw_defaults) > 0
        has_varargs = args.vararg is not None
        has_kwargs = args.kwarg is not None
        arity_flags = f"{has_defaults},{has_varargs},{has_kwargs}"
    else:
        # Classes don't have parameters in the same way
        param_count = 0
        arity_flags = "False,False,False"

    # Extract decorator names
    decorators = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            decorators.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            decorators.append(dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                decorators.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                decorators.append(dec.func.attr)
    decorators_str = ",".join(sorted(decorators))

    # Build signature string and hash
    sig = f"{kind}:{param_count}:{arity_flags}:{decorators_str}"
    hash_val = hashlib.sha256(sig.encode()).hexdigest()[:16]
    return f"sha256:{hash_val}"


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


PASS_ID = "python-ast-v1"
PASS_VERSION = "hypergumbo-0.1.0"


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


def _compute_lines_of_code(node: ast.AST) -> int:
    """Compute lines of code for a function or class.

    Returns end_line - start_line + 1.
    """
    start = node.lineno
    end = getattr(node, "end_lineno", node.lineno)
    return end - start + 1


@dataclass
class AnalysisResult:
    """Result of analyzing Python files."""

    symbols: list[Symbol]
    edges: list[Edge]
    run: AnalysisRun | None = None


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


def _detect_src_layout(repo_root: Path) -> Path | None:
    """Detect if repo uses src/ layout (PEP 517/518 style).

    Returns the source root (e.g., repo_root/src) if detected, else None.

    A src/ layout is detected when:
    1. repo_root/src/ directory exists
    2. src/ contains at least one Python package (dir with __init__.py)
    3. There's no __init__.py directly in src/ (it's not itself a package)
    """
    src_dir = repo_root / "src"
    if not src_dir.is_dir():
        return None

    # Check src/ is not itself a package
    if (src_dir / "__init__.py").exists():
        return None

    # Check if src/ contains at least one package
    for child in src_dir.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            return src_dir

    return None


def _module_name_from_path(
    py_file: Path, repo_root: Path, source_root: Path | None = None
) -> str:
    """Convert a file path to a module name.

    E.g., /repo/utils.py -> 'utils', /repo/pkg/mod.py -> 'pkg.mod'

    If source_root is provided (e.g., repo_root/src for src/ layout),
    paths are computed relative to source_root instead of repo_root
    when the file is under source_root.
    """
    # For src/ layout, use source_root as base for files under it
    if source_root and py_file.is_relative_to(source_root):
        try:
            rel_path = py_file.relative_to(source_root)
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

    return symbol_imports, module_imports


def _extract_import_edges(
    tree: ast.AST,
    file_path: str,
    importing_module: str,
    global_symbols: dict[tuple[str, str], Symbol],
) -> list[Edge]:
    """Extract import edges from AST.

    Creates edges from the importing file to the imported symbols/modules.
    For 'from X import Y', links to the resolved symbol if known, else to module.
    For 'import X', links to the module.

    Args:
        tree: The parsed AST
        file_path: Path to the importing file
        importing_module: The fully qualified name of the importing module
        global_symbols: Map of (module, name) -> Symbol for cross-file resolution

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
                    # Try to find the symbol in our global table
                    symbol = global_symbols.get((resolved_module, alias.name))
                    if symbol:
                        dst_id = symbol.id
                    else:
                        # External symbol - create a reference ID
                        dst_id = f"python:{resolved_module}:0-0:{alias.name}:symbol"

                    edges.append(Edge.create(
                        src=file_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=node.lineno,
                        evidence_type="ast_import",
                        confidence=0.95,
                    ))

        # Handle 'import X' and 'import X as Y' style imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
                dst_id = _make_module_id(module_name)
                edges.append(Edge.create(
                    src=file_id,
                    dst=dst_id,
                    edge_type="imports",
                    line=node.lineno,
                    evidence_type="ast_import",
                    confidence=0.95,
                ))

    return edges


def _extract_file_analysis(
    py_file: Path,
    repo_root: Path | None = None,
    source_root: Path | None = None,
) -> FileAnalysis | None:
    """Extract symbols and imports from a single file.

    Args:
        py_file: Path to the Python file
        repo_root: Repository root for resolving relative imports. If None,
                   relative imports won't be fully resolved.
        source_root: For src/ layout projects, the source directory (e.g., repo/src).
                     Used for correct module name calculation.

    Returns None if the file cannot be parsed.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return None

    symbols = []
    symbol_by_name: dict[str, Symbol] = {}

    # Create <module> pseudo-node for files with module-level executable code.
    # This provides an enclosing scope for linker synthetic nodes at module level,
    # enabling slice traversal for script-only files (no functions/classes).
    if _has_module_level_code(tree):
        end_line = _get_file_end_line(source)
        module_name = py_file.name  # e.g., "producer_ccsr.py"
        module_span = Span(
            start_line=1,
            end_line=end_line,
            start_col=0,
            end_col=0,
        )
        module_symbol = Symbol(
            id=_make_symbol_id(str(py_file), 1, end_line, f"<module:{module_name}>", "module"),
            name=f"<module:{module_name}>",
            kind="module",
            language="python",
            path=str(py_file),
            span=module_span,
            origin="",
            origin_run_id="",
        )
        symbols.append(module_symbol)
        symbol_by_name["<module>"] = module_symbol

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

            # Build rich metadata for class (ADR-0003)
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

            symbol = Symbol(
                id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "class"),
                name=node.name,
                kind="class",
                language="python",
                path=str(py_file),
                span=span,
                stable_id=_compute_stable_id(node),
                shape_id=_compute_shape_id(node),
                cyclomatic_complexity=_compute_cyclomatic_complexity(node),
                lines_of_code=_compute_lines_of_code(node),
                meta=class_meta if class_meta else None,
            )
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol

            # Extract methods inside the class
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    method_end_line = item.end_lineno or item.lineno
                    method_end_col = item.end_col_offset or 0
                    method_span = Span(
                        start_line=item.lineno,
                        end_line=method_end_line,
                        start_col=item.col_offset,
                        end_col=method_end_col,
                    )
                    method_name = f"{class_name}.{item.name}"

                    # For Django/DRF class-based views, methods named get/post/etc.
                    # should have stable_id set to the HTTP method (uppercase for consistency)
                    stable_id = _compute_stable_id(item)
                    if item.name.lower() in HTTP_METHODS:
                        stable_id = item.name.upper()

                    # Build rich metadata for method (ADR-0003)
                    method_meta: dict[str, object] = {}

                    # Extract decorators with arguments
                    if item.decorator_list:
                        method_meta["decorators"] = [
                            _extract_decorator_info(dec) for dec in item.decorator_list
                        ]

                    # Extract structured parameters (excluding self/cls)
                    params = _extract_parameters_info(item.args, exclude_self=True)
                    if params:
                        method_meta["parameters"] = params

                    method_symbol = Symbol(
                        id=_make_symbol_id(str(py_file), item.lineno, method_end_line, method_name, "method"),
                        name=method_name,
                        kind="method",
                        language="python",
                        path=str(py_file),
                        span=method_span,
                        stable_id=stable_id,
                        shape_id=_compute_shape_id(item),
                        cyclomatic_complexity=_compute_cyclomatic_complexity(item),
                        lines_of_code=_compute_lines_of_code(item),
                        signature=_format_function_signature(item),
                        meta=method_meta if method_meta else None,
                    )
                    symbols.append(method_symbol)
                    # Store by short name for self.method() lookups
                    symbol_by_name[item.name] = method_symbol

        elif isinstance(node, ast.FunctionDef):
            # Check if this is a top-level function (not inside a class)
            # We do this by checking if the parent is the module
            # ast.walk doesn't give parent info, so we need to handle this differently
            # For now, we skip functions that were already processed as methods
            # by checking if the function is at module level (column 0)
            if node.col_offset == 0:
                end_line = node.end_lineno or node.lineno
                end_col = node.end_col_offset or 0
                span = Span(
                    start_line=node.lineno,
                    end_line=end_line,
                    start_col=node.col_offset,
                    end_col=end_col,
                )

                # Build rich metadata for function (ADR-0003)
                # Route detection moved to FRAMEWORK_PATTERNS phase
                func_meta: dict[str, object] = {}

                # Extract decorators with arguments
                if node.decorator_list:
                    func_meta["decorators"] = [
                        _extract_decorator_info(dec) for dec in node.decorator_list
                    ]

                # Extract structured parameters
                params = _extract_parameters_info(node.args, exclude_self=False)
                if params:
                    func_meta["parameters"] = params

                symbol = Symbol(
                    id=_make_symbol_id(str(py_file), node.lineno, end_line, node.name, "function"),
                    name=node.name,
                    kind="function",
                    language="python",
                    path=str(py_file),
                    span=span,
                    stable_id=_compute_stable_id(node),
                    shape_id=_compute_shape_id(node),
                    meta=func_meta if func_meta else None,
                    cyclomatic_complexity=_compute_cyclomatic_complexity(node),
                    lines_of_code=_compute_lines_of_code(node),
                    signature=_format_function_signature(node),
                )
                symbols.append(symbol)
                symbol_by_name[node.name] = symbol

    # Detect Django URL patterns (path, re_path, url calls) - deprecated
    django_patterns = _extract_django_url_patterns(tree)
    if django_patterns:
        _emit_route_deprecation_warning("Django")
    for start_line, end_line, route_path, view_name in django_patterns:
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=0,
            end_col=0,
        )
        # Normalize route path - ensure it starts with /
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"
        route_name = f"django:{view_name or 'unknown'}"
        symbol = Symbol(
            id=_make_symbol_id(str(py_file), start_line, end_line, normalized_path, "route"),
            name=route_name,
            kind="route",
            language="python",
            path=str(py_file),
            span=span,
            stable_id="GET",  # Django defaults to GET, all methods allowed
            meta={
                "route_path": normalized_path,
                "http_method": "GET",
                "view_name": view_name,
            },
        )
        symbols.append(symbol)

    # Compute module name for import resolution
    if repo_root is not None:
        importing_module = _module_name_from_path(py_file, repo_root, source_root)
    else:
        importing_module = py_file.stem  # Fallback to just filename
    symbol_imports, module_imports = _extract_imports(tree, importing_module)
    return FileAnalysis(
        symbols=symbols,
        symbol_by_name=symbol_by_name,
        imports=symbol_imports,
        module_imports=module_imports,
        tree=tree,
    )


def _extract_edges(
    tree: ast.AST,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and instantiation edges from an AST.

    Resolves both local and cross-file calls/instantiations.

    Handles:
    - Direct calls: helper(), ClassName()
    - Self method calls: self.method()
    - Module-qualified calls: module.ClassName(), module.func()
    - Variable method calls: variable.method() (with constructor-only type inference)

    Note: Type inference only tracks types from direct constructor calls
    (stub = Client()), not from function returns (stub = get_client()).

    Args:
        tree: The parsed AST
        local_symbols: Symbols defined in this file
        imports: Symbol imports (from X import Y)
        global_symbols: All symbols across the project
        module_imports: Module imports (import X, import X as Y)
    """
    if module_imports is None:  # pragma: no cover
        module_imports = {}

    edges: list[Edge] = []

    # Helper to extract edges from a code block (function body, module level, etc.)
    def process_code_block(
        block_nodes: list[ast.AST],
        caller_symbol: Symbol,
        var_types: dict[str, Symbol] | None = None,
    ) -> None:
        """Process AST nodes within a code block, tracking variable types."""
        if var_types is None:
            var_types = {}

        for node in block_nodes:
            # Track variable assignments for type inference
            # e.g., stub = EmailServiceStub(channel) -> var_types['stub'] = EmailServiceStub
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                        assigned_class = _resolve_call_target(
                            node.value, local_symbols, imports, global_symbols, module_imports
                        )
                        if assigned_class and assigned_class.kind == "class":
                            var_types[target.id] = assigned_class

            # Process calls
            if isinstance(node, ast.Call):
                _process_call(
                    node, caller_symbol, local_symbols, imports, global_symbols,
                    module_imports, var_types, edges
                )

            # Recurse into child nodes (but not into nested function defs)
            for child in ast.iter_child_nodes(node):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    process_code_block([child], caller_symbol, var_types)

    # Process functions (including async functions)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            caller_symbol = local_symbols.get(node.name)
            if caller_symbol:
                process_code_block(node.body, caller_symbol)

    # Process module-level code for <module> pseudo-nodes
    module_symbol = local_symbols.get("<module>")
    if module_symbol:
        # Get top-level statements (excluding function/class defs)
        module_level_nodes = [
            node for node in tree.body
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        process_code_block(module_level_nodes, module_symbol)

    return edges


def _resolve_call_target(
    call_node: ast.Call,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str],
) -> Symbol | None:
    """Resolve the target of a call expression to a Symbol.

    Handles:
    - ClassName() -> class symbol
    - module.ClassName() -> class symbol in module
    - imported_name() -> resolved symbol
    """
    func = call_node.func

    # Simple name: ClassName() or func()
    if isinstance(func, ast.Name):
        name = func.id
        # Check local symbols
        symbol = local_symbols.get(name)
        if symbol:
            return symbol
        # Check imports
        if name in imports:
            module_name, original_name = imports[name]
            return global_symbols.get((module_name, original_name))

    # Attribute: module.ClassName() or obj.method()
    elif isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            receiver_name = func.value.id
            attr_name = func.attr

            # Check if receiver is an imported module
            if receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                return global_symbols.get((module_name, attr_name))

    return None


def _process_call(
    call_node: ast.Call,
    caller_symbol: Symbol,
    local_symbols: dict[str, Symbol],
    imports: dict[str, tuple[str, str]],
    global_symbols: dict[tuple[str, str], Symbol],
    module_imports: dict[str, str],
    var_types: dict[str, Symbol],
    edges: list[Edge],
) -> None:
    """Process a single call expression and emit appropriate edges.

    Handles:
    - Direct calls: helper(), ClassName()
    - Self method calls: self.method()
    - Module-qualified calls: module.ClassName(), module.func()
    - Variable method calls: stub.method() (using var_types for type inference)
    """
    func = call_node.func
    callee_symbol = None
    is_instantiation = False
    evidence_type = "ast_call_direct"

    # Case 1: Simple name calls - helper() or ClassName()
    if isinstance(func, ast.Name):
        callee_name = func.id
        callee_symbol = local_symbols.get(callee_name)

        if callee_symbol and callee_symbol.kind == "class":
            is_instantiation = True
        elif not callee_symbol and callee_name in imports:
            module_name, original_name = imports[callee_name]
            callee_symbol = global_symbols.get((module_name, original_name))
            if callee_symbol and callee_symbol.kind == "class":
                is_instantiation = True

    # Case 2: Attribute calls - self.method(), module.ClassName(), variable.method()
    elif isinstance(func, ast.Attribute):
        attr_name = func.attr
        evidence_type = "ast_call_method"

        if isinstance(func.value, ast.Name):
            receiver_name = func.value.id

            # Case 2a: self.method()
            if receiver_name == "self":
                callee_symbol = local_symbols.get(attr_name)

            # Case 2b: module.ClassName() or module.func()
            elif receiver_name in module_imports:
                module_name = module_imports[receiver_name]
                callee_symbol = global_symbols.get((module_name, attr_name))
                if callee_symbol and callee_symbol.kind == "class":
                    is_instantiation = True

            # Case 2c: variable.method() - use type inference
            elif receiver_name in var_types:
                class_symbol = var_types[receiver_name]
                # Look for ClassName.method in local symbols
                qualified_name = f"{class_symbol.name}.{attr_name}"
                callee_symbol = local_symbols.get(qualified_name)
                # If not found locally, try global symbols
                if not callee_symbol:
                    # Find methods in the file where the class is defined
                    class_path = class_symbol.path
                    for (_mod, sym_name), sym in global_symbols.items():
                        if sym.path == class_path and sym_name == qualified_name:
                            callee_symbol = sym
                            break

    # Emit edge if we resolved the callee
    if callee_symbol:
        if is_instantiation:
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=callee_symbol.id,
                edge_type="instantiates",
                line=call_node.lineno,
                evidence_type="ast_new",
                confidence=0.95,
            ))
        else:
            edges.append(Edge.create(
                src=caller_symbol.id,
                dst=callee_symbol.id,
                edge_type="calls",
                line=call_node.lineno,
                evidence_type=evidence_type,
            ))


def extract_nodes(py_file: Path, global_symbols: dict[str, Symbol] | None = None) -> AnalysisResult:
    """
    Extract function/class definitions and call edges from a Python file.

    Returns an AnalysisResult with symbols and edges.
    Gracefully handles syntax errors and encoding issues.

    Note: For cross-file call detection, use analyze_python() instead.
    This function only detects intra-file calls for backwards compatibility.
    """
    file_analysis = _extract_file_analysis(py_file)
    if file_analysis is None:
        return AnalysisResult(symbols=[], edges=[])

    # For single-file analysis, only detect local calls
    edges = _extract_edges(
        file_analysis.tree, file_analysis.symbol_by_name, {}, {},
        file_analysis.module_imports
    )
    return AnalysisResult(symbols=file_analysis.symbols, edges=edges)


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

    # Detect src/ layout (PEP 517/518 style)
    # If detected, use src/ as the base for module names
    source_root = _detect_src_layout(repo_root)

    # First pass: collect all symbols and imports from all files
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    for py_file in find_python_files(repo_root, max_files=max_files):
        analysis = _extract_file_analysis(py_file, repo_root, source_root)
        if analysis is not None:
            file_analyses[py_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol table: (module_name, symbol_name) -> Symbol
    global_symbols: dict[tuple[str, str], Symbol] = {}
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root, source_root)
        for symbol in analysis.symbols:
            global_symbols[(module_name, symbol.name)] = symbol

    # Process re-exports from __init__.py files
    # When __init__.py does "from .submodule import helper", add an alias
    # so that "from package import helper" resolves to the real symbol
    for py_file, analysis in file_analyses.items():
        if py_file.name != "__init__.py":
            continue

        module_name = _module_name_from_path(py_file, repo_root, source_root)
        # Package name is module name without .__init__ suffix
        package_name = module_name.rsplit(".__init__", 1)[0]

        for local_name, (resolved_module, original_name) in analysis.imports.items():
            # Check if this import points to a known symbol
            source_symbol = global_symbols.get((resolved_module, original_name))
            if source_symbol:
                # Add alias: (package, local_name) -> source_symbol
                global_symbols[(package_name, local_name)] = source_symbol

    # Second pass: extract edges with cross-file resolution
    all_symbols = []
    all_edges = []
    for py_file, analysis in file_analyses.items():
        module_name = _module_name_from_path(py_file, repo_root, source_root)

        # Set origin on symbols
        for symbol in analysis.symbols:
            symbol.origin = PASS_ID
            symbol.origin_run_id = run.execution_id
        all_symbols.extend(analysis.symbols)

        # Extract call edges
        call_edges = _extract_edges(
            analysis.tree, analysis.symbol_by_name, analysis.imports, global_symbols,
            analysis.module_imports
        )
        for edge in call_edges:
            edge.origin = PASS_ID
            edge.origin_run_id = run.execution_id
        all_edges.extend(call_edges)

        # Extract import edges
        import_edges = _extract_import_edges(
            analysis.tree, str(py_file), module_name, global_symbols
        )
        for edge in import_edges:
            edge.origin = PASS_ID
            edge.origin_run_id = run.execution_id
        all_edges.extend(import_edges)

    # Update run metadata
    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return AnalysisResult(symbols=all_symbols, edges=all_edges, run=run)
