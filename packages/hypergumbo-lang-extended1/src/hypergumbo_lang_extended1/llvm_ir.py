"""LLVM IR analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse LLVM IR files (.ll) and extract:
- Function definitions (define)
- Function declarations (declare)
- Global variable definitions
- Function call relationships

If tree-sitter with LLVM IR support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-llvm is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and resolve targets

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-llvm for grammar
- Two-pass allows cross-file call resolution (for multi-file IR projects)
- Same pattern as other tree-sitter analyzers for consistency

LLVM IR-Specific Considerations
-------------------------------
- LLVM IR is the intermediate representation used by LLVM-based compilers
- Functions are defined with `define` and declared with `declare`
- Global variables are defined with `@name = global/constant`
- Local variables use `%` prefix, global identifiers use `@` prefix
- Functions can have attributes like `dso_local`, `noinline`, etc.
- Call instructions reference functions by their `@name`
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "llvm-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_llvm_ir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all LLVM IR files in the repository."""
    yield from find_files(repo_root, ["*.ll"])


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID for a symbol."""
    return f"llvm_ir:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:  # pragma: no cover - reserved for future file-level symbols
    """Generate ID for a LLVM IR file node."""
    return f"llvm_ir:{path}:1-1:file:file"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def is_llvm_tree_sitter_available() -> bool:
    """Check if tree-sitter with LLVM IR grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_llvm") is None:
        return False  # pragma: no cover - llvm grammar not installed
    try:
        import tree_sitter
        import tree_sitter_llvm

        tree_sitter.Language(tree_sitter_llvm.language())
        return True
    except Exception:  # pragma: no cover - grammar loading failed
        return False


@dataclass
class LLVMIRAnalysisResult:
    """Result of analyzing LLVM IR files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: Path
    symbols: list[Symbol] = field(default_factory=list)
    calls: list[tuple[str, str, int, int]] = field(default_factory=list)
    # calls = [(caller_name, callee_name, line, col), ...]


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text content from a node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _get_function_name(header_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract function name from function_header node.

    The function name is stored in a global_var node (e.g., @add).
    """
    for child in iter_tree(header_node):
        if child.type == "global_var":
            name = _node_text(child, source)
            # Remove @ prefix for internal use
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _get_global_var_name(global_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract global variable name from global_global node."""
    for child in global_node.children:
        if child.type == "global_var":
            name = _node_text(child, source)
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _get_call_target(call_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the called function name from an instruction_call node.

    The target is typically in value > var > global_var.
    """
    for child in iter_tree(call_node):
        if child.type == "global_var":
            name = _node_text(child, source)
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _find_enclosing_function(
    node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Find the name of the function containing this node."""
    current = node.parent
    while current is not None:
        if current.type == "fn_define":
            # Find function_header child
            for child in current.children:
                if child.type == "function_header":
                    return _get_function_name(child, source)
        current = current.parent
    return None  # pragma: no cover - defensive fallback


def _extract_arguments(header_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature from function_header."""
    for child in iter_tree(header_node):
        if child.type == "argument_list":
            return _node_text(child, source)
    return "()"  # pragma: no cover - defensive fallback


def _get_return_type(header_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract return type from function_header."""
    for child in header_node.children:
        if child.type == "type":
            return _node_text(child, source)
    return None  # pragma: no cover - defensive fallback


def _analyze_file(
    file_path: Path, source: bytes, tree: "tree_sitter.Tree", run_id: str
) -> FileAnalysis:
    """Analyze a single LLVM IR file.

    Pass 1: Extract symbols and record call sites for later resolution.
    """
    result = FileAnalysis(path=file_path)
    rel_path = str(file_path)

    for node in iter_tree(tree.root_node):
        if node.type == "fn_define":
            # Function definition
            header = None
            for child in node.children:
                if child.type == "function_header":
                    header = child
                    break

            if header is not None:
                name = _get_function_name(header, source)
                if name:
                    ret_type = _get_return_type(header, source)
                    args = _extract_arguments(header, source)
                    signature = f"{ret_type or 'void'} @{name}{args}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym_id = _make_symbol_id(rel_path, start_line, end_line, name, "function")

                    symbol = Symbol(
                        id=sym_id,
                        name=name,
                        kind="function",
                        language="llvm_ir",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        canonical_name=f"@{name}",
                        signature=signature,
                    )
                    result.symbols.append(symbol)

        elif node.type == "declare":
            # Function declaration (external function)
            header = None
            for child in node.children:
                if child.type == "function_header":
                    header = child
                    break

            if header is not None:
                name = _get_function_name(header, source)
                if name:
                    ret_type = _get_return_type(header, source)
                    args = _extract_arguments(header, source)
                    signature = f"{ret_type or 'void'} @{name}{args}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym_id = _make_symbol_id(rel_path, start_line, end_line, name, "declaration")

                    symbol = Symbol(
                        id=sym_id,
                        name=name,
                        kind="declaration",
                        language="llvm_ir",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        canonical_name=f"@{name}",
                        signature=signature,
                    )
                    result.symbols.append(symbol)

        elif node.type == "global_global":
            # Global variable definition
            name = _get_global_var_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                sym_id = _make_symbol_id(rel_path, start_line, end_line, name, "variable")

                symbol = Symbol(
                    id=sym_id,
                    name=name,
                    kind="variable",
                    language="llvm_ir",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    canonical_name=f"@{name}",
                )
                result.symbols.append(symbol)

        elif node.type == "instruction_call":
            # Function call
            caller = _find_enclosing_function(node, source)
            callee = _get_call_target(node, source)
            if caller and callee:
                result.calls.append((
                    caller,
                    callee,
                    node.start_point[0] + 1,
                    node.start_point[1],
                ))

    return result


def _resolve_calls(
    file_analyses: list[FileAnalysis],
    resolver: NameResolver,
    run_id: str,
) -> list[Edge]:
    """Resolve call sites to known symbols (pass 2).

    Creates edges for calls where both caller and callee are in the registry.
    Uses NameResolver for flexible symbol lookup with confidence tracking.
    """
    edges: list[Edge] = []
    base_confidence = 0.90

    for file_analysis in file_analyses:
        for caller_name, callee_name, line, _col in file_analysis.calls:
            caller_result = resolver.lookup(caller_name)
            callee_result = resolver.lookup(callee_name)

            if caller_result.found and callee_result.found:
                caller_sym = caller_result.symbol
                callee_sym = callee_result.symbol
                assert caller_sym is not None
                assert callee_sym is not None
                # Combine base confidence with resolver confidence
                confidence = base_confidence * min(
                    caller_result.confidence, callee_result.confidence
                )
                edge = Edge(
                    id=_make_edge_id(caller_sym.id, callee_sym.id, "calls"),
                    src=caller_sym.id,
                    dst=callee_sym.id,
                    edge_type="calls",
                    line=line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="ast_call_direct",
                )
                edges.append(edge)

    return edges


def analyze_llvm_ir(repo_root: Path) -> LLVMIRAnalysisResult:
    """Analyze LLVM IR files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        LLVMIRAnalysisResult containing symbols, edges, and analysis metadata
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_llvm_tree_sitter_available():
        skip_reason = (
            "LLVM IR analysis skipped: requires tree-sitter-llvm "
            "(pip install tree-sitter-llvm)"
        )
        warnings.warn(skip_reason, UserWarning, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LLVMIRAnalysisResult(
            run=run, skipped=True, skip_reason="tree-sitter-llvm not available"
        )

    import tree_sitter
    import tree_sitter_llvm

    language = tree_sitter.Language(tree_sitter_llvm.language())
    parser = tree_sitter.Parser(language)
    run_id = run.execution_id

    file_analyses: list[FileAnalysis] = []
    files_analyzed = 0
    symbol_registry: dict[str, Symbol] = {}

    # Pass 1: Parse all files and extract symbols
    for file_path in find_llvm_ir_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)
            file_analysis = _analyze_file(file_path, source, tree, run_id)
            file_analyses.append(file_analysis)
            files_analyzed += 1

            # Build symbol registry for cross-file resolution
            for sym in file_analysis.symbols:
                # Use name without @ prefix as key
                symbol_registry[sym.name] = sym

        except Exception:  # nosec B112 # noqa: S112 # pragma: no cover - file read error
            continue

    # Pass 2: Resolve calls to symbols
    resolver = NameResolver(symbol_registry)
    edges = _resolve_calls(file_analyses, resolver, run_id)

    # Collect all symbols
    all_symbols: list[Symbol] = []
    for file_analysis in file_analyses:
        all_symbols.extend(file_analysis.symbols)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return LLVMIRAnalysisResult(
        symbols=all_symbols,
        edges=edges,
        run=run,
    )
