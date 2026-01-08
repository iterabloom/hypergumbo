"""R language analysis pass using tree-sitter.

This analyzer parses R source files and extracts:
- Function definitions (function <- function(...) { })
- Library/require imports
- Function calls
- Source file references

If tree-sitter-r is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter with R grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .R and .r files
4. Extract functions, library imports, function calls
5. Create call edges for function invocations

Why This Design
---------------
- Optional dependency keeps base install lightweight
- R is widely used in data science and statistics
- Function definitions use assignment operators (<-, =, ->)
- library() and require() for package imports
- Useful for scientific computing and data analysis codebases
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from .base import iter_tree
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "r-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# R file extensions
R_EXTENSIONS = ["*.R", "*.r"]


def find_r_files(repo_root: Path) -> Iterator[Path]:
    """Yield all R files in the repository."""
    yield from find_files(repo_root, R_EXTENSIONS)


def is_r_tree_sitter_available() -> bool:
    """Check if tree-sitter with R grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    # Try tree_sitter_language_pack first (bundled languages)
    if importlib.util.find_spec("tree_sitter_language_pack") is not None:
        try:
            from tree_sitter_language_pack import get_language

            get_language("r")
            return True
        except Exception:  # pragma: no cover
            pass  # pragma: no cover
    # Fall back to standalone tree_sitter_r
    if importlib.util.find_spec("tree_sitter_r") is not None:  # pragma: no cover
        return True  # pragma: no cover
    return False  # pragma: no cover


@dataclass
class RAnalysisResult:
    """Result of analyzing R files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"r:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive fallback


def _extract_r_signature(func_def: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from an R function_definition node.

    Returns signature in format: (param1, param2, ...)
    R is dynamically typed so no type annotations.
    Handles default values (shown as = ...).
    """
    params_node = _find_child_by_type(func_def, "parameters")
    if params_node is None:  # pragma: no cover - function() always has params node
        return "()"

    params: list[str] = []
    for child in params_node.children:
        if child.type == "parameter":
            # Parameter may have name and default value
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                # Check for default value (has more than just identifier)
                has_default = len([c for c in child.children if c.type not in ("identifier", "=", ",")]) > 0
                if has_default:
                    params.append(f"{param_name} = ...")
                else:
                    params.append(param_name)
        elif child.type == "identifier":  # pragma: no cover - wrapped in parameter node
            # Simple parameter (no default)
            params.append(_node_text(child, source))  # pragma: no cover
        elif child.type == "dots":  # pragma: no cover - R's ... varargs
            params.append("...")

    return f"({', '.join(params)})"


def _find_enclosing_function_r(
    node: "tree_sitter.Node",
    source: bytes,
    function_registry: dict[str, str],
) -> Optional[str]:
    """Find the enclosing function ID by walking up parents."""
    current = node.parent
    while current:
        if current.type == "function_definition":
            # The function definition is the right side of an assignment
            # The parent should be binary_operator with the function name on left
            parent = current.parent
            if parent and parent.type == "binary_operator":
                for child in parent.children:
                    if child.type == "identifier":
                        func_name = source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                        if func_name in function_registry:
                            return function_registry[func_name]
                        break  # pragma: no cover - defensive
        current = current.parent
    return None  # pragma: no cover - defensive


def _process_r_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    function_registry: dict[str, str],
) -> None:
    """Process R AST tree to extract symbols and edges.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        function_registry: Registry mapping function names to symbol IDs
    """
    for node in iter_tree(root_node):
        # Function definitions: binary_operator with <- and function_definition on right
        if node.type == "binary_operator":
            # Check for function assignment
            left_node = None
            right_node = None
            is_assignment = False

            for child in node.children:
                if child.type == "identifier":
                    left_node = child
                elif child.type in ("<-", "=", "<<-"):
                    is_assignment = True
                elif child.type == "function_definition":
                    right_node = child

            if is_assignment and left_node and right_node:
                func_name = _node_text(left_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, func_name, "function")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=func_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=func_name,
                    path=rel_path,
                    language="r",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=_extract_r_signature(right_node, source),
                )
                symbols.append(sym)
                function_registry[func_name] = symbol_id

        # Function calls
        elif node.type == "call":
            func_name_node = None
            for child in node.children:
                if child.type == "identifier":
                    func_name_node = child
                    break

            if func_name_node:
                func_name = _node_text(func_name_node, source)
                start_line = node.start_point[0] + 1

                # Check for library/require imports
                if func_name in ("library", "require"):
                    # Extract package name from arguments
                    for child in node.children:
                        if child.type == "arguments":
                            for arg in child.children:
                                if arg.type == "argument":
                                    pkg_name = _node_text(arg, source).strip("\"'")
                                    # Remove "package = " prefix if present
                                    if "=" in pkg_name:  # pragma: no cover - named arg syntax
                                        pkg_name = pkg_name.split("=")[-1].strip().strip("\"'")  # pragma: no cover
                                    if pkg_name and pkg_name not in ("(", ")"):
                                        import_id = _make_symbol_id(rel_path, start_line, start_line, pkg_name, "import")
                                        imp_sym = Symbol(
                                            id=import_id,
                                            stable_id=None,
                                            shape_id=None,
                                            canonical_name=pkg_name,
                                            fingerprint=hashlib.sha256(pkg_name.encode()).hexdigest()[:16],
                                            kind="import",
                                            name=pkg_name,
                                            path=rel_path,
                                            language="r",
                                            span=Span(
                                                start_line=start_line,
                                                end_line=start_line,
                                                start_col=node.start_point[1],
                                                end_col=node.end_point[1],
                                            ),
                                            origin=PASS_ID,
                                        )
                                        symbols.append(imp_sym)
                                    break  # Only first argument is package name
                elif func_name == "source":
                    # source() imports another R file
                    for child in node.children:
                        if child.type == "arguments":
                            for arg in child.children:
                                if arg.type == "argument":
                                    for sub in arg.children:
                                        if sub.type == "string":
                                            file_path = _node_text(sub, source).strip("\"'")
                                            source_id = _make_symbol_id(rel_path, start_line, start_line, file_path, "source")
                                            src_sym = Symbol(
                                                id=source_id,
                                                stable_id=None,
                                                shape_id=None,
                                                canonical_name=file_path,
                                                fingerprint=hashlib.sha256(file_path.encode()).hexdigest()[:16],
                                                kind="source",
                                                name=file_path,
                                                path=rel_path,
                                                language="r",
                                                span=Span(
                                                    start_line=start_line,
                                                    end_line=start_line,
                                                    start_col=node.start_point[1],
                                                    end_col=node.end_point[1],
                                                ),
                                                origin=PASS_ID,
                                            )
                                            symbols.append(src_sym)
                                            break
                                    break
                else:
                    # Regular function call - create edge if inside a function
                    current_function = _find_enclosing_function_r(node, source, function_registry)
                    if current_function:
                        dst_id = function_registry.get(func_name, f"r:builtin:{func_name}")

                        edge = Edge(
                            id=_make_edge_id(current_function, dst_id, "calls"),
                            src=current_function,
                            dst=dst_id,
                            edge_type="calls",
                            line=start_line,
                            confidence=0.90 if func_name in function_registry else 0.70,
                            origin=PASS_ID,
                            evidence_type="static",
                        )
                        edges.append(edge)


def analyze_r_files(repo_root: Path) -> RAnalysisResult:
    """Analyze R files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        RAnalysisResult with symbols and edges
    """
    if not is_r_tree_sitter_available():  # pragma: no cover
        return RAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-r not installed (pip install tree-sitter-language-pack)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Function registry for cross-file resolution: name -> symbol_id
    function_registry: dict[str, str] = {}

    # Create parser - try language pack first, then standalone
    try:
        try:
            from tree_sitter_language_pack import get_language

            r_lang = get_language("r")
            parser = tree_sitter.Parser(r_lang)
        except Exception:  # pragma: no cover - language pack available
            import tree_sitter_r  # pragma: no cover

            parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_r.language()))  # pragma: no cover
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize R parser: {e}")
        return RAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    r_files = list(find_r_files(repo_root))

    for r_path in r_files:
        try:
            rel_path = str(r_path.relative_to(repo_root))
            source = r_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_r_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                function_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {r_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return RAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


# Convenience alias
analyze_r = analyze_r_files
