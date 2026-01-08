"""Perl analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Perl files and extract:
- Package declarations (modules)
- Subroutine declarations (sub)
- Function call relationships
- use/require statements (imports)

If tree-sitter with Perl support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-language-pack with Perl is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and use/require statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Perl-Specific Considerations
----------------------------
- Perl packages define namespaces (package MyModule;)
- Subroutines are defined with `sub name { ... }`
- `use Module` imports at compile time
- `require 'file.pl'` imports at runtime
- Method calls can be `$obj->method()` or `ClassName->method()`
- Perl has complex calling conventions that can be hard to statically analyze
"""
from __future__ import annotations

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

PASS_ID = "perl-v1"
PASS_VERSION = "hypergumbo-0.6.0"


def find_perl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Perl files in the repository."""
    yield from find_files(repo_root, ["*.pl", "*.pm", "*.t"])


def is_perl_tree_sitter_available() -> bool:
    """Check if tree-sitter with Perl grammar is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("perl")
        return True
    except Exception:  # pragma: no cover
        return False


@dataclass
class PerlAnalysisResult:
    """Result of analyzing Perl files."""

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

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]
    package_name: str  # The package name for this file


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"perl:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Perl file node (used as import edge source)."""
    return f"perl:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_children_by_type(
    node: "tree_sitter.Node", type_name: str
) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]


def _extract_perl_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract subroutine signature from a subroutine_declaration_statement.

    Perl 5.20+ supports subroutine signatures:
        sub foo($x, $y) { ... }

    Traditional Perl uses @_ unpacking which we can't easily extract.

    Returns signature in format: ($param1, $param2) or () if no params found.
    """
    # Look for signature_params or param_list child (Perl 5.20+ signatures)
    for child in node.children:
        if child.type in ("signature", "signature_params"):  # pragma: no cover - rare
            # Extract the full signature text
            sig_text = _node_text(child, source).strip()
            return sig_text

    # Check for prototype (old style) - e.g., sub foo($) { }
    # This is after bareword and before block
    for child in node.children:
        if child.type == "prototype":  # pragma: no cover - rare syntax
            proto_text = _node_text(child, source).strip()
            return proto_text

    # No explicit signature found
    return "()"


def _get_current_package(node: "tree_sitter.Node", source: bytes) -> str:
    """Walk up the tree to find the most recent package_statement before this node.

    Returns "main" if no package statement is found.
    """
    # Walk backwards through siblings in parent to find package_statement
    current = node.parent
    while current:
        # Check siblings that appear before this node
        found_self = False
        for sibling in reversed(current.children):
            if sibling is node or (sibling.end_byte <= node.start_byte):
                found_self = True
            if found_self and sibling.type == "package_statement":
                package_nodes = _find_children_by_type(sibling, "package")
                if len(package_nodes) >= 2:
                    return _node_text(package_nodes[1], source)
        current = current.parent
    return "main"  # pragma: no cover - defensive


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], str]:
    """Extract all symbols from a parsed Perl file.

    Detects:
    - package_statement (module/package)
    - subroutine_declaration_statement (sub)

    Returns:
        Tuple of (symbols list, package name)
    """
    symbols: list[Symbol] = []
    package_name = "main"  # Default Perl package

    for node in iter_tree(tree.root_node):
        if node.type == "package_statement":
            # Extract package name
            package_nodes = _find_children_by_type(node, "package")
            if len(package_nodes) >= 2:
                # First is keyword "package", second is the actual name
                package_name = _node_text(package_nodes[1], source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, package_name, "module")
                symbols.append(Symbol(
                    id=sym_id,
                    name=package_name,
                    kind="module",
                    language="perl",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        elif node.type == "subroutine_declaration_statement":
            # Extract subroutine name from bareword child
            bareword = _find_child_by_type(node, "bareword")
            if bareword:
                sub_name = _node_text(bareword, source)
                # Get current package context for this subroutine
                current_pkg = _get_current_package(node, source)
                if current_pkg != "main":
                    package_name = current_pkg
                # Qualify with package name for cross-file resolution
                qualified_name = f"{package_name}::{sub_name}" if package_name != "main" else sub_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, sub_name, "function")
                symbols.append(Symbol(
                    id=sym_id,
                    name=qualified_name,
                    kind="function",
                    language="perl",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_perl_signature(node, source),
                ))

    return symbols, package_name


def _find_enclosing_function_perl(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    package_name: str,
) -> Optional[Symbol]:
    """Find the subroutine that contains this node by walking up parents."""
    current = node.parent
    while current:
        if current.type == "subroutine_declaration_statement":
            bareword = _find_child_by_type(current, "bareword")
            if bareword:
                name = _node_text(bareword, source)
                qualified = f"{package_name}::{name}" if package_name != "main" else name
                return local_symbols.get(qualified) or local_symbols.get(name)
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    package_name: str,
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed Perl file.

    Detects:
    - use_statement: use Module;
    - require_expression: require 'file.pl';
    - function calls (ambiguous_function_call_expression, method calls)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    # Build local symbol map for this file (unqualified name -> symbol)
    local_symbols: dict[str, Symbol] = {}
    for s in file_symbols:
        # Store both qualified and unqualified names
        local_symbols[s.name] = s
        if "::" in s.name:
            unqualified = s.name.rsplit("::", 1)[-1]
            local_symbols[unqualified] = s

    for node in iter_tree(tree.root_node):
        # Handle use statements
        if node.type == "use_statement":
            package_nodes = _find_children_by_type(node, "package")
            if package_nodes:
                module_name = _node_text(package_nodes[0], source)
                # Skip pragmas like 'strict', 'warnings'
                if module_name not in ("strict", "warnings", "utf8", "vars", "constant"):
                    module_id = f"perl:{module_name}:0-0:module:module"
                    edge = Edge.create(
                        src=file_id,
                        dst=module_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="use",
                        confidence=0.95,
                    )
                    edges.append(edge)

        # Handle require expressions
        elif node.type == "require_expression":
            string_node = _find_child_by_type(node, "string_literal")
            if string_node:
                content = _find_child_by_type(string_node, "string_content")
                if content:
                    required_file = _node_text(content, source)
                    module_id = f"perl:{required_file}:0-0:file:file"
                    edge = Edge.create(
                        src=file_id,
                        dst=module_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="require",
                        confidence=0.90,
                    )
                    edges.append(edge)

        # Handle function calls
        elif node.type in ("function_call_expression", "ambiguous_function_call_expression",
                           "func0op_call_expression", "func1op_call_expression"):
            # Get function name from first child (usually 'function' type or bareword)
            func_node = _find_child_by_type(node, "function")
            if func_node:
                func_name = _node_text(func_node, source)
                # Skip builtins
                if func_name not in ("print", "say", "die", "warn", "exit", "return",
                                      "shift", "push", "pop", "splice", "join", "split",
                                      "open", "close", "read", "write", "defined", "ref",
                                      "bless", "keys", "values", "each", "exists", "delete",
                                      "length", "substr", "index", "rindex", "chomp", "chop",
                                      "lc", "uc", "lcfirst", "ucfirst", "scalar", "wantarray"):
                    caller = _find_enclosing_function_perl(node, source, local_symbols, package_name)
                    if caller:
                        # Try to resolve callee
                        callee = local_symbols.get(func_name) or global_symbol_registry.get(func_name)
                        if callee:
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=0.85,
                            )
                            edges.append(edge)
                        else:
                            # Unresolved call
                            unresolved_id = f"perl:?:0-0:{func_name}:function"
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

        # Handle method calls (arrow operator)
        elif node.type == "method_call_expression":
            # $obj->method() or ClassName->method()
            method_node = _find_child_by_type(node, "method")
            if method_node:
                method_name = _node_text(method_node, source)
                caller = _find_enclosing_function_perl(node, source, local_symbols, package_name)
                if caller:
                    callee = local_symbols.get(method_name) or global_symbol_registry.get(method_name)
                    if callee:
                        edge = Edge.create(
                            src=caller.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="method_call",
                            confidence=0.75,
                        )
                        edges.append(edge)

    return edges


def analyze_perl(repo_root: Path) -> PerlAnalysisResult:
    """Analyze Perl files in a repository.

    Returns a PerlAnalysisResult with symbols, edges, and provenance.
    If tree-sitter with Perl support is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_perl_tree_sitter_available():
        skip_reason = (
            "Perl analysis skipped: requires tree-sitter-language-pack "
            "(pip install tree-sitter-language-pack)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return PerlAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("perl")
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for perl_file in find_perl_files(repo_root):
        try:
            source = perl_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(perl_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name=rel_path,
            kind="file",
            language="perl",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols, package_name = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym
            # Also register unqualified name
            if "::" in sym.name:
                unqualified = sym.name.rsplit("::", 1)[-1]
                if unqualified not in global_symbol_registry:
                    global_symbol_registry[unqualified] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
            package_name=package_name,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            global_symbol_registry,
            fa.package_name,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return PerlAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
