"""PowerShell analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse PowerShell files and extract:
- Function definitions
- Filter definitions
- Workflow definitions (PowerShell 5.1)
- Parameter declarations with types
- Command/function calls
- Module imports (Import-Module, using module)

If tree-sitter with PowerShell support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter with PowerShell grammar is available
2. If not available, return skipped result (not an error)
3. Parse all PowerShell files and extract symbols
4. Detect Import-Module and using statements for import edges
5. Detect command invocations for call edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for PowerShell grammar
- PowerShell is essential for Windows/Azure automation
- Enables analysis of DevOps and infrastructure scripts

PowerShell-Specific Considerations
----------------------------------
- Functions use verb-noun naming (Get-User, Set-Config)
- Parameters can have types, defaults, and attributes
- Filters are special functions for pipeline processing
- Workflows (deprecated) may appear in legacy code
- Import-Module and using module for dependencies
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

PASS_ID = "powershell-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_powershell_files(repo_root: Path) -> Iterator[Path]:
    """Yield all PowerShell files in the repository."""
    yield from find_files(repo_root, ["*.ps1", "*.psm1", "*.psd1"])


def is_powershell_tree_sitter_available() -> bool:
    """Check if tree-sitter with PowerShell grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language
        get_language("powershell")
        return True
    except Exception:  # pragma: no cover - powershell grammar not available
        return False


@dataclass
class PowerShellAnalysisResult:
    """Result of analyzing PowerShell files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"powershell:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a PowerShell file node (used as import edge source)."""
    return f"powershell:{path}:1-1:file:file"


def _make_edge_id() -> str:
    """Generate a unique edge ID."""
    return f"edge:powershell:{uuid.uuid4().hex[:12]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_function_signature(func_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature showing parameters and types.

    PowerShell function syntax:
        function Verb-Noun {
            param(
                [Type]$ParamName,
                [Type]$ParamName = Default
            )
        }

    Returns signature like "([string]$UserId, [int]$Age)".
    """
    # Find the script_block inside the function
    script_block = _find_child_by_type(func_node, "script_block")
    if not script_block:
        return "()"

    # Find param_block inside script_block
    param_block = _find_child_by_type(script_block, "param_block")
    if not param_block:
        return "()"

    # Find parameter_list inside param_block
    param_list = _find_child_by_type(param_block, "parameter_list")
    if not param_list:
        return "()"  # pragma: no cover - defensive

    # Extract parameters
    params: list[str] = []
    for child in param_list.children:
        if child.type == "script_parameter":
            param_str = _extract_parameter(child, source)
            if param_str:
                params.append(param_str)

    return f"({', '.join(params)})"


def _extract_parameter(param_node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract a single parameter with its type annotation."""
    type_str: Optional[str] = None
    var_name: Optional[str] = None

    for child in param_node.children:
        if child.type == "attribute_list":
            # Look for type_literal in attribute
            for attr in child.children:
                if attr.type == "attribute":
                    type_lit = _find_child_by_type(attr, "type_literal")
                    if type_lit:
                        type_str = _node_text(type_lit, source).strip()
        elif child.type == "variable":
            var_name = _node_text(child, source).strip()

    if var_name:
        if type_str:
            return f"{type_str}{var_name}"
        return var_name
    return None  # pragma: no cover - defensive


def _make_powershell_symbol(
    file_path: str,
    run_id: str,
    node: "tree_sitter.Node",
    name: str,
    kind: str,
    signature: Optional[str] = None,
) -> Symbol:
    """Create a Symbol from a tree-sitter node."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    start_col = node.start_point[1]
    end_col = node.end_point[1]

    span = Span(
        start_line=start_line,
        end_line=end_line,
        start_col=start_col,
        end_col=end_col,
    )
    sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="powershell",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=signature,
    )


def _find_enclosing_function_powershell(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find the enclosing function name by walking up parents."""
    current = node.parent
    while current:
        if current.type == "function_statement":
            name_node = _find_child_by_type(current, "function_name")
            if name_node:
                return _node_text(name_node, source).strip()
        current = current.parent
    return None  # pragma: no cover - defensive


def _process_import_module(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    edges: list[Edge],
) -> None:
    """Process Import-Module command to extract module name."""
    elements_node = _find_child_by_type(node, "command_elements")
    if elements_node:
        for child in elements_node.children:
            if child.type == "generic_token":
                module_text = _node_text(child, source).strip()
                if module_text and not module_text.startswith("-"):
                    edges.append(Edge(
                        id=_make_edge_id(),
                        src=_make_file_id(file_path),
                        dst=f"powershell:?:?:{module_text}:module",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                    ))
                    return


def _process_using_command(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    edges: list[Edge],
) -> None:
    """Process 'using' command to extract module imports."""
    elements_node = _find_child_by_type(node, "command_elements")
    if elements_node:
        tokens = [
            _node_text(child, source).strip()
            for child in elements_node.children
            if child.type == "generic_token"
        ]
        if len(tokens) >= 2 and tokens[0].lower() == "module":
            module_name = tokens[1]
            edges.append(Edge(
                id=_make_edge_id(),
                src=_make_file_id(file_path),
                dst=f"powershell:?:?:{module_name}:module",
                edge_type="imports",
                line=node.start_point[0] + 1,
            ))


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract all symbols and edges from a parsed PowerShell file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    function_names: set[str] = set()  # Track defined functions

    for node in iter_tree(tree.root_node):
        if node.type == "function_statement":
            # Process a function, filter, or workflow definition
            kind = "function"
            for child in node.children:
                if child.type == "filter":
                    kind = "filter"
                    break
                elif child.type == "workflow":
                    kind = "workflow"
                    break
                elif child.type == "function":
                    kind = "function"
                    break

            name_node = _find_child_by_type(node, "function_name")
            if name_node:
                func_name = _node_text(name_node, source).strip()
                function_names.add(func_name)
                sig = _extract_function_signature(node, source)
                symbols.append(_make_powershell_symbol(
                    file_path, run_id, node, func_name, kind, signature=sig
                ))

        elif node.type == "command":
            command_name_node = _find_child_by_type(node, "command_name")
            if command_name_node:
                command_name = _node_text(command_name_node, source).strip()

                if command_name.lower() == "import-module":
                    _process_import_module(node, source, file_path, edges)
                elif command_name.lower() == "using":
                    _process_using_command(node, source, file_path, edges)
                else:
                    # Check if inside a function
                    caller = _find_enclosing_function_powershell(node, source)
                    if caller:
                        edges.append(Edge(
                            id=_make_edge_id(),
                            src=_make_file_id(file_path),
                            dst=f"powershell:?:?:{command_name}:function",
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                        ))

    return symbols, edges


def analyze_powershell(repo_root: Path) -> PowerShellAnalysisResult:
    """Analyze all PowerShell files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        PowerShellAnalysisResult with symbols and edges found.
    """
    if not is_powershell_tree_sitter_available():
        warnings.warn("PowerShell analysis skipped: tree-sitter-language-pack not available")
        return PowerShellAnalysisResult(skipped=True, skip_reason="tree-sitter-language-pack not available")

    from tree_sitter_language_pack import get_parser

    parser = get_parser("powershell")
    run_id = f"uuid:{uuid.uuid4()}"
    start_time = time.time()
    files_analyzed = 0

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for file_path in find_powershell_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)

            rel_path = str(file_path.relative_to(repo_root))
            symbols, edges = _extract_symbols_and_edges(tree, source, rel_path, run_id)

            all_symbols.extend(symbols)
            all_edges.extend(edges)
            files_analyzed += 1

        except (OSError, IOError):  # pragma: no cover - defensive
            continue  # Skip files we can't read

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun(
        execution_id=run_id,
        pass_id=PASS_ID,
        version=PASS_VERSION,
        files_analyzed=files_analyzed,
        duration_ms=duration_ms,
    )

    return PowerShellAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
