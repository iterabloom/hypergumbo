"""Bash/shell script analyzer using tree-sitter.

This analyzer extracts functions, exported variables, aliases, and source statements
from Bash and shell scripts. It uses tree-sitter-bash for parsing when available,
falling back gracefully when the grammar is not installed.

Node types handled:
- function_definition: Both 'function name()' and 'name()' styles
- declaration_command with 'export': Exported variables
- command with 'alias': Alias definitions
- command with 'source' or '.': Source/import statements
- command: Function calls (when command_name matches a known function)

Two-pass analysis:
- Pass 1: Extract all symbols (functions, exports, aliases) from all files
- Pass 2: Resolve function calls using global symbol registry
"""

from __future__ import annotations

import importlib.util
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "bash-v1"
PASS_VERSION = "1.0.0"


def is_bash_tree_sitter_available() -> bool:
    """Check if tree-sitter and bash grammar are available."""
    ts_spec = importlib.util.find_spec("tree_sitter")
    if ts_spec is None:
        return False
    bash_spec = importlib.util.find_spec("tree_sitter_bash")
    return bash_spec is not None


def _is_bash_shebang(first_line: str) -> bool:
    """Check if a shebang line indicates a bash/sh script."""
    if not first_line.startswith("#!"):
        return False
    shebang = first_line[2:].strip()
    # Match /bin/bash, /usr/bin/bash, /bin/sh, /usr/bin/env bash, etc.
    bash_patterns = ["/bash", "/sh", "env bash", "env sh"]
    return any(p in shebang for p in bash_patterns)


def find_bash_files(root: Path) -> list[Path]:
    """Find all Bash/shell script files in a directory tree.

    Identifies files by:
    - .sh extension
    - .bash extension
    - No extension but with bash/sh shebang
    """
    bash_files: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Skip common non-script directories
        if any(part.startswith(".") or part == "node_modules" for part in path.parts):
            continue

        # Check extension first
        if path.suffix in (".sh", ".bash"):
            bash_files.append(path)
            continue

        # For files without extension, check shebang
        if path.suffix == "":
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    first_line = f.readline()
                    if _is_bash_shebang(first_line):
                        bash_files.append(path)
            except (OSError, IOError):  # pragma: no cover
                pass

    return bash_files


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first direct child with given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Get text content of a node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"bash:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Bash file node (used as source edge source)."""
    return f"bash:{path}:1-1:file:file"


@dataclass
class BashAnalysisResult:
    """Result of analyzing Bash files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract function name from function_definition node."""
    word_node = _find_child_by_type(node, "word")
    if word_node:
        return _node_text(word_node, source)
    return None  # pragma: no cover


def _extract_alias_info(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract alias name from alias command.

    alias name='value' or alias name="value"
    """
    children = [c for c in node.children if c.type not in ("command_name",)]
    if not children:
        return None  # pragma: no cover

    for child in children:
        if child.type == "word":
            text = _node_text(child, source)
            if "=" in text:
                return text.split("=")[0]
            return text  # pragma: no cover - unusual alias format
        elif child.type == "concatenation":
            first = _find_child_by_type(child, "word")
            if first:
                text = _node_text(first, source)
                # Remove trailing = if present (alias ll='value' parses as 'll=')
                if text.endswith("="):
                    return text[:-1]
                return text  # pragma: no cover - unusual alias format

    return None  # pragma: no cover


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Bash file."""
    analysis = FileAnalysis()
    rel_path = str(file_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return analysis

    tree = parser.parse(source)

    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            func_name = _extract_function_name(node, source)
            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, func_name, "function")

                # Bash functions don't have formal parameters - they use $1, $2, etc.
                # Signature is always "()" since there's no parameter declaration syntax
                symbol = Symbol(
                    id=symbol_id,
                    name=func_name,
                    kind="function",
                    language="bash",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature="()",
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

        elif node.type == "declaration_command":
            export_node = _find_child_by_type(node, "export")
            if export_node:
                var_node = _find_child_by_type(node, "variable_assignment")
                if var_node:
                    name_node = _find_child_by_type(var_node, "variable_name")
                    if name_node:
                        var_name = _node_text(name_node, source)
                        start_line = node.start_point[0] + 1
                        symbol_id = _make_symbol_id(rel_path, start_line, start_line, var_name, "export")

                        symbol = Symbol(
                            id=symbol_id,
                            name=var_name,
                            kind="export",
                            language="bash",
                            path=rel_path,
                            span=Span(
                                start_line=start_line,
                                end_line=start_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                        analysis.symbols.append(symbol)

        elif node.type == "command":
            cmd_name_node = _find_child_by_type(node, "command_name")
            if cmd_name_node:
                word_node = _find_child_by_type(cmd_name_node, "word")
                if word_node:
                    cmd_name = _node_text(word_node, source)

                    if cmd_name == "alias":
                        alias_name = _extract_alias_info(node, source)
                        if alias_name:
                            start_line = node.start_point[0] + 1
                            symbol_id = _make_symbol_id(rel_path, start_line, start_line, alias_name, "alias")

                            symbol = Symbol(
                                id=symbol_id,
                                name=alias_name,
                                kind="alias",
                                language="bash",
                                path=rel_path,
                                span=Span(
                                    start_line=start_line,
                                    end_line=start_line,
                                    start_col=node.start_point[1],
                                    end_col=node.end_point[1],
                                ),
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            )
                            analysis.symbols.append(symbol)

    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract edges from a file using global symbol knowledge."""
    edges: list[Edge] = []
    rel_path = str(file_path)
    file_id = _make_file_id(rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return edges

    tree = parser.parse(source)

    def _get_enclosing_function(node: "tree_sitter.Node") -> Optional[Symbol]:
        """Walk up the tree to find enclosing function."""
        current = node.parent
        while current is not None:
            if current.type == "function_definition":
                func_name = _extract_function_name(current, source)
                if func_name and func_name in local_symbols:
                    return local_symbols[func_name]
            current = current.parent
        return None  # pragma: no cover - defensive

    for node in iter_tree(tree.root_node):
        if node.type == "command":
            cmd_name_node = _find_child_by_type(node, "command_name")
            if cmd_name_node:
                word_node = _find_child_by_type(cmd_name_node, "word")
                if word_node:
                    cmd_name = _node_text(word_node, source)
                    line = node.start_point[0] + 1

                    # Handle source/. commands
                    if cmd_name in ("source", "."):
                        words = [
                            c for c in node.children if c.type == "word" and c != word_node
                        ]
                        if words:
                            sourced_path = _node_text(words[0], source)
                            edges.append(Edge.create(
                                src=file_id,
                                dst=sourced_path,
                                edge_type="sources",
                                line=line,
                                evidence_type="source_statement",
                                confidence=0.95,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

                    # Track function calls
                    else:
                        current_function = _get_enclosing_function(node)
                        if current_function is not None:
                            if cmd_name in local_symbols:
                                callee = local_symbols[cmd_name]
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=line,
                                    evidence_type="function_call",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                            elif cmd_name in global_symbols:
                                callee = global_symbols[cmd_name]
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=line,
                                    evidence_type="cross_file_call",
                                    confidence=0.80,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

    return edges


def analyze_bash(root: Path) -> BashAnalysisResult:
    """Analyze Bash/shell scripts in a directory.

    Uses tree-sitter-bash for parsing. Falls back gracefully if not available.
    """
    if not is_bash_tree_sitter_available():
        warnings.warn(
            "tree-sitter-bash not available. Install with: pip install hypergumbo[bash]"
        )
        return BashAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-bash not available",
        )

    try:
        import tree_sitter
        import tree_sitter_bash

        language = tree_sitter.Language(tree_sitter_bash.language())
        parser = tree_sitter.Parser(language)
    except Exception as e:
        return BashAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to load Bash parser: {e}",
        )

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_files = find_bash_files(root)
    if not all_files:
        return BashAnalysisResult(run=run)

    # Pass 1: Extract symbols from all files
    all_symbols: list[Symbol] = []
    file_analyses: dict[Path, FileAnalysis] = {}
    global_symbols: dict[str, Symbol] = {}

    for bash_file in all_files:
        analysis = _extract_symbols_from_file(bash_file, parser, run)
        file_analyses[bash_file] = analysis
        all_symbols.extend(analysis.symbols)

        for sym in analysis.symbols:
            if sym.kind == "function":
                global_symbols[sym.name] = sym

    # Pass 2: Extract edges using global symbol knowledge
    all_edges: list[Edge] = []

    for bash_file, analysis in file_analyses.items():
        edges = _extract_edges_from_file(
            bash_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    # Also extract edges from files without symbols (for source-only files)
    for bash_file in all_files:
        if bash_file not in file_analyses:  # pragma: no cover
            edges = _extract_edges_from_file(
                bash_file, parser, {}, global_symbols, run
            )
            all_edges.extend(edges)

    return BashAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
