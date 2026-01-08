"""Starlark (Bazel/Buck) analysis pass using tree-sitter.

Detects:
- Function definitions (def)
- Build targets (py_binary, cc_library, etc.)
- Load statements as imports
- Variable assignments
- Dependency edges between targets

Starlark is a Python-like language used for Bazel, Buck, and other build systems.
The tree-sitter-starlark parser handles BUILD, BUILD.bazel, BUCK, and .bzl files.

How It Works
------------
1. Check if tree-sitter with Starlark grammar is available
2. If not available, return skipped result (not an error)
3. Parse all BUILD, BUILD.bazel, BUCK, and .bzl files
4. Extract function definitions and signatures
5. Extract build targets with rule types
6. Track load statements as import edges
7. Track target dependencies as depends_on edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Starlark grammar
- Starlark is essential for Bazel/Buck build systems
- Enables analysis of build configurations for understanding dependencies
"""
from __future__ import annotations

import importlib.util
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from .base import iter_tree
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "starlark-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Starlark file patterns
STARLARK_PATTERNS = ["BUILD", "BUILD.bazel", "BUCK", "*.bzl"]


def find_starlark_files(repo_root: Path) -> Iterator[Path]:
    """Find all Starlark files in the repository."""
    for pattern in STARLARK_PATTERNS:
        yield from find_files(repo_root, [pattern])


@dataclass
class StarlarkAnalysisResult:
    """Result of analyzing Starlark files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def is_starlark_tree_sitter_available() -> bool:
    """Check if tree-sitter-starlark is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("starlark")
        return True
    except Exception:  # pragma: no cover - starlark grammar not available
        return False


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", child_type: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover - defensive


def _extract_string_content(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract string content from a string node."""
    content = _find_child_by_type(node, "string_content")
    if content:
        return _node_text(content, source)
    return None  # pragma: no cover - defensive


def _extract_function_signature(
    params_node: "tree_sitter.Node", source: bytes
) -> str:
    """Extract function signature from parameters node."""
    params = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(_node_text(child, source))
        elif child.type == "default_parameter":
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                params.append(f"{_node_text(name_node, source)} = ...")
        elif child.type == "typed_default_parameter":  # pragma: no cover - defensive
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                params.append(f"{_node_text(name_node, source)} = ...")
        elif child.type == "list_splat_pattern":  # pragma: no cover - defensive
            params.append("*args")
        elif child.type == "dictionary_splat_pattern":  # pragma: no cover - defensive
            params.append("**kwargs")
    return f"({', '.join(params)})"


@dataclass
class _FileContext:
    """Context for processing a single file."""

    source: bytes
    rel_path: str
    file_stable_id: str
    run_id: str
    symbols: list[Symbol]
    edges: list[Edge]
    target_ids: dict[str, str]


def _make_symbol(ctx: _FileContext, node: "tree_sitter.Node", name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"starlark:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="starlark",
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"starlark:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _process_function(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function definition."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover

    name = _node_text(name_node, ctx.source)
    params_node = _find_child_by_type(node, "parameters")
    signature = (
        _extract_function_signature(params_node, ctx.source) if params_node else "()"
    )

    ctx.symbols.append(_make_symbol(ctx, node, name, "function", signature=signature))


def _process_assignment(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a variable assignment."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover

    name = _node_text(name_node, ctx.source)

    # Skip lowercase names (likely local variables in functions)
    # Only capture uppercase constants at top level
    if not name.isupper() and not name[0].isupper():
        return  # pragma: no cover - filtering lowercase

    ctx.symbols.append(_make_symbol(ctx, node, name, "variable"))


def _process_load(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a load statement."""
    arg_list = _find_child_by_type(node, "argument_list")
    if not arg_list:
        return  # pragma: no cover

    # First argument is the source file
    source_file = None
    loaded_symbols = []

    for child in arg_list.children:
        if child.type == "string":
            content = _extract_string_content(child, ctx.source)
            if content:
                if source_file is None:
                    source_file = content
                else:
                    loaded_symbols.append(content)

    if source_file and loaded_symbols:
        for sym in loaded_symbols:
            ctx.edges.append(
                Edge(
                    id=f"edge:starlark:{uuid.uuid4().hex[:12]}",
                    src=ctx.file_stable_id,
                    dst=f"starlark:{source_file}:{sym}",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=ctx.run_id,
                )
            )


def _process_target(ctx: _FileContext, node: "tree_sitter.Node", rule_type: str) -> None:
    """Process a build target invocation."""
    arg_list = _find_child_by_type(node, "argument_list")
    if not arg_list:
        return  # pragma: no cover

    # Find 'name' keyword argument
    target_name = None
    deps_list: list[str] = []

    for child in arg_list.children:
        if child.type == "keyword_argument":
            key_node = _find_child_by_type(child, "identifier")
            if not key_node:
                continue  # pragma: no cover - defensive

            key = _node_text(key_node, ctx.source)

            if key == "name":
                # Get the value
                for value in child.children:
                    if value.type == "string":
                        target_name = _extract_string_content(value, ctx.source)
                        break
            elif key == "deps":
                # Get dependencies list
                for value in child.children:
                    if value.type == "list":
                        for item in value.children:
                            if item.type == "string":
                                dep = _extract_string_content(item, ctx.source)
                                if dep:
                                    deps_list.append(dep)

    if target_name:
        stable_id = f"starlark:{ctx.rel_path}:{target_name}"
        ctx.target_ids[target_name] = stable_id

        ctx.symbols.append(
            _make_symbol(ctx, node, target_name, "target", meta={"rule_type": rule_type})
        )

        # Create dependency edges
        for dep in deps_list:
            # Deps can be ":name" (same package), "//pkg:name", "@repo//pkg:name"
            ctx.edges.append(
                Edge(
                    id=f"edge:starlark:{uuid.uuid4().hex[:12]}",
                    src=stable_id,
                    dst=f"starlark:{ctx.rel_path}:{dep}",
                    edge_type="depends_on",
                    line=node.start_point[0] + 1,
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=ctx.run_id,
                )
            )


def _process_call(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a call expression (load statement or rule invocation)."""
    func_node = _find_child_by_type(node, "identifier")
    if not func_node:
        return  # pragma: no cover

    func_name = _node_text(func_node, ctx.source)

    if func_name == "load":
        _process_load(ctx, node)
    else:
        _process_target(ctx, node, func_name)


def _process_node(ctx: _FileContext, root_node: "tree_sitter.Node") -> None:
    """Process a tree-sitter node and its children."""
    for node in iter_tree(root_node):
        if node.type == "function_definition":
            _process_function(ctx, node)
        elif node.type == "expression_statement":
            # Check for assignment or call
            for child in node.children:
                if child.type == "assignment":
                    _process_assignment(ctx, child)
                elif child.type == "call":
                    _process_call(ctx, child)


def analyze_starlark(repo_root: Path) -> StarlarkAnalysisResult:
    """Analyze Starlark files in a repository.

    Returns a StarlarkAnalysisResult with symbols for functions, targets, and variables,
    plus edges for load statements and target dependencies.
    """
    if not is_starlark_tree_sitter_available():
        warnings.warn("Starlark analysis skipped: tree-sitter-starlark unavailable")
        return StarlarkAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-starlark unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("starlark")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    # Track targets by name for dependency resolution
    target_ids: dict[str, str] = {}

    for file_path in find_starlark_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"starlark:{rel_path}:file:"

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=symbols,
            edges=edges,
            target_ids=target_ids,
        )

        _process_node(ctx, tree.root_node)

    duration_ms = int((time.time() - start_time) * 1000)
    return StarlarkAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=AnalysisRun(
            execution_id=run_id,
            pass_id=PASS_ID,
            version=PASS_VERSION,
            files_analyzed=files_analyzed,
            duration_ms=duration_ms,
        ),
    )
