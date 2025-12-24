"""JavaScript/TypeScript analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse JS/TS files and extract:
- Function and class declarations (symbols)
- Import/require statements (edges)
- Function call relationships (edges)

If tree-sitter is not installed, the analyzer gracefully degrades and
reports the pass as skipped with reason.

How It Works
------------
1. Check if tree-sitter and language grammars are available
2. If not available, return empty result with skip reason
3. If available, parse each file and extract symbols/edges
4. Use tree-sitter queries to identify relevant nodes

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Graceful degradation ensures CLI still works without tree-sitter
- Tree-sitter provides accurate parsing even for complex syntax
"""
from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "javascript-ts-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_js_ts_files(repo_root: Path) -> Iterator[Path]:
    """Yield all JS/TS files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.js", "*.jsx", "*.ts", "*.tsx"])


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter and required grammars are available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_javascript") is None:
        return False
    return True


@dataclass
class JsAnalysisResult:
    """Result of analyzing JavaScript/TypeScript files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str, lang: str) -> str:
    """Generate location-based ID."""
    return f"{lang}:{path}:{start_line}-{end_line}:{name}:{kind}"


def _get_language_for_file(file_path: Path) -> str:
    """Determine language based on file extension."""
    suffix = file_path.suffix.lower()
    if suffix in (".ts", ".tsx"):
        return "typescript"
    return "javascript"


def _get_parser_for_file(file_path: Path) -> Optional["tree_sitter.Parser"]:
    """Get appropriate tree-sitter parser for file type."""
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        return None

    suffix = file_path.suffix.lower()
    parser = tree_sitter.Parser()

    if suffix in (".ts", ".tsx"):
        try:
            import tree_sitter_typescript

            if suffix == ".tsx":
                lang_ptr = tree_sitter_typescript.language_tsx()
            else:
                lang_ptr = tree_sitter_typescript.language_typescript()
            parser.language = tree_sitter.Language(lang_ptr)
            return parser
        except ImportError:
            # Fall back to JavaScript parser for TS files
            parser.language = tree_sitter.Language(tree_sitter_javascript.language())
            return parser
    else:
        parser.language = tree_sitter.Language(tree_sitter_javascript.language())
        return parser


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_name_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
        if child.type == "property_identifier":
            return _node_text(child, source)
        # TypeScript uses type_identifier for class names
        if child.type == "type_identifier":
            return _node_text(child, source)
    return None


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    lang: str,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract symbols and edges from a parsed tree."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    symbol_by_name: dict[str, Symbol] = {}

    def visit(node: "tree_sitter.Node", current_function: Optional[Symbol] = None) -> None:

        # Function declarations
        if node.type == "function_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                    name=name,
                    kind="function",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol
                # Visit body with this as current function
                for child in node.children:
                    visit(child, symbol)
                return

        # Arrow functions assigned to variables: const foo = () => {}
        if node.type == "lexical_declaration" or node.type == "variable_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = None
                    value_node = None
                    call_node = None
                    for grandchild in child.children:
                        if grandchild.type == "identifier":
                            name_node = grandchild
                        elif grandchild.type == "arrow_function":
                            value_node = grandchild
                        elif grandchild.type == "call_expression":
                            call_node = grandchild
                    if name_node and value_node:
                        name = _node_text(name_node, source)
                        span = Span(
                            start_line=value_node.start_point[0] + 1,
                            end_line=value_node.end_point[0] + 1,
                            start_col=value_node.start_point[1],
                            end_col=value_node.end_point[1],
                        )
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                        symbols.append(symbol)
                        symbol_by_name[name] = symbol
                        # Visit body with this as current function
                        for vc in value_node.children:
                            visit(vc, symbol)
                    # Handle require() calls: const x = require('module')
                    if call_node:
                        visit(call_node, current_function)
            return

        # Class declarations
        if node.type == "class_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "class", lang),
                    name=name,
                    kind="class",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

        # Method definitions inside classes (including getters/setters)
        if node.type == "method_definition":
            name = _find_name_in_children(node, source)
            if name:
                # Determine method kind (getter, setter, or regular method)
                kind = "method"
                for child in node.children:
                    if child.type == "get":
                        kind = "getter"
                        break
                    elif child.type == "set":
                        kind = "setter"
                        break

                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, kind, lang),
                    name=name,
                    kind=kind,
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol
                # Visit method body for call detection
                for child in node.children:
                    visit(child, symbol)
                return

        # Export default function
        if node.type == "export_statement":
            for child in node.children:
                if child.type == "function_declaration":
                    name = _find_name_in_children(child, source)
                    if name:
                        span = Span(
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                            start_col=child.start_point[1],
                            end_col=child.end_point[1],
                        )
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                        symbols.append(symbol)
                        symbol_by_name[name] = symbol
                        for fc in child.children:
                            visit(fc, symbol)
                        return

        # Import statements: import { x } from 'module'
        if node.type == "import_statement":
            # Find the source string
            for child in node.children:
                if child.type == "string":
                    module_name = _node_text(child, source).strip("'\"")
                    # Create file symbol for this file if not exists
                    file_id = _make_symbol_id(str(file_path), 1, 1, file_path.name, "file", lang)

                    # Create import edge
                    dst_id = f"{lang}:{module_name}:0-0:module:module"
                    edge = Edge.create(
                        src=file_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="import_static",
                        confidence=0.95,
                    )
                    edges.append(edge)
                    break

        # Require calls: const x = require('module')
        if node.type == "call_expression":
            func_node = None
            args_node = None
            for child in node.children:
                if child.type == "identifier":
                    func_node = child
                elif child.type == "arguments":
                    args_node = child

            if func_node and _node_text(func_node, source) == "require" and args_node:
                # Get the first argument
                for arg in args_node.children:
                    if arg.type == "string":
                        module_name = _node_text(arg, source).strip("'\"")
                        file_id = _make_symbol_id(str(file_path), 1, 1, file_path.name, "file", lang)
                        dst_id = f"{lang}:{module_name}:0-0:module:module"
                        edge = Edge.create(
                            src=file_id,
                            dst=dst_id,
                            edge_type="imports",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="require_static",
                            confidence=0.90,
                        )
                        edges.append(edge)
                        break
                    elif arg.type == "identifier":
                        # Dynamic require
                        var_name = _node_text(arg, source)
                        file_id = _make_symbol_id(str(file_path), 1, 1, file_path.name, "file", lang)
                        dst_id = f"{lang}:<dynamic:{var_name}>:0-0:module:module"
                        edge = Edge.create(
                            src=file_id,
                            dst=dst_id,
                            edge_type="imports",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="require_dynamic",
                            confidence=0.40,
                        )
                        edges.append(edge)
                        break

            # Regular function calls within a function
            elif func_node and current_function:
                callee_name = _node_text(func_node, source)
                if callee_name in symbol_by_name:
                    callee_symbol = symbol_by_name[callee_name]
                    edge = Edge.create(
                        src=current_function.id,
                        dst=callee_symbol.id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_call_direct",
                        confidence=0.85,
                    )
                    edges.append(edge)

        # Recurse into children
        for child in node.children:
            visit(child, current_function)

    visit(tree.root_node)
    return symbols, edges


def _analyze_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single JS/TS file.

    Returns (symbols, edges, success).
    """
    parser = _get_parser_for_file(file_path)
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    # Check for parse errors (tree-sitter creates error nodes)
    if tree.root_node.has_error:
        # Still try to extract what we can, but note the error
        pass

    lang = _get_language_for_file(file_path)
    symbols, edges = _extract_symbols_and_edges(tree, source, file_path, lang, run)

    return symbols, edges, True


def analyze_javascript(repo_root: Path) -> JsAnalysisResult:
    """Analyze all JavaScript/TypeScript files in a repository.

    Returns a JsAnalysisResult with symbols, edges, and provenance.
    If tree-sitter is not available, returns empty result with skip info.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter availability
    if not is_tree_sitter_available():
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JsAnalysisResult(
            run=run,
            skipped=True,
            skip_reason="requires tree-sitter: pip install hypergumbo[javascript]",
        )

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_js_ts_files(repo_root):
        symbols, edges, success = _analyze_file(file_path, run)
        if success:
            files_analyzed += 1
            all_symbols.extend(symbols)
            all_edges.extend(edges)
        else:
            files_skipped += 1

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JsAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
