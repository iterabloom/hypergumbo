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

How It Works
------------
Uses the TreeSitterAnalyzer base class for two-pass orchestration:
1. extract_symbols_from_file: extracts functions, exports, aliases
2. register_symbol: only registers function symbols for cross-file resolution
3. extract_edges_from_file: resolves source/dot imports and function calls
4. _find_source_files: overridden for shebang-based file discovery

Why override _find_source_files: Bash scripts can have no extension but
a shebang line (#!/bin/bash), requiring special detection beyond glob patterns.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files, is_excluded
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("bash")


def is_bash_tree_sitter_available() -> bool:
    """Check if tree-sitter and bash grammar are available."""
    return _analyzer._check_grammar_available()


def _is_bash_shebang(first_line: str) -> bool:
    """Check if a shebang line indicates a bash/sh script."""
    if not first_line.startswith("#!"):
        return False
    shebang = first_line[2:].strip()
    # Match /bin/bash, /usr/bin/bash, /bin/sh, /usr/bin/env bash, etc.
    bash_patterns = ["/bash", "/sh", "env bash", "env sh"]
    return any(p in shebang for p in bash_patterns)


def find_bash_files(root: Path) -> list[Path]:
    """Find all Bash/shell script files in a directory tree, excluding vendor dirs.

    Identifies files by:
    - .sh extension
    - .bash extension
    - No extension but with bash/sh shebang
    """
    bash_files: list[Path] = []

    # Get .sh and .bash files using find_files (respects DEFAULT_EXCLUDES)
    bash_files.extend(find_files(root, ["*.sh", "*.bash"]))

    # For files without extension, check shebang (still need to walk)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix != "":
            continue  # Already handled above or not a shell script
        if is_excluded(path, root):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                first_line = f.readline()
                if _is_bash_shebang(first_line):
                    bash_files.append(path)
        except (OSError, IOError):  # pragma: no cover
            pass

    return bash_files


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract function name from function_definition node."""
    word_node = find_child_by_type(node, "word")
    if word_node:
        return node_text(word_node, source)
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
            text = node_text(child, source)
            if "=" in text:
                return text.split("=")[0]
            return text  # pragma: no cover - unusual alias format
        elif child.type == "concatenation":
            first = find_child_by_type(child, "word")
            if first:
                text = node_text(first, source)
                # Remove trailing = if present (alias ll='value' parses as 'll=')
                if text.endswith("="):
                    return text[:-1]
                return text  # pragma: no cover - unusual alias format

    return None  # pragma: no cover


class BashAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Bash/shell script analyzer.

    Uses tree-sitter-bash to parse .sh, .bash, and extensionless shebang files.
    Extracts functions, exported variables, aliases, source/dot imports, and
    function call edges.

    Overrides ``_find_source_files`` because bash scripts can lack file extensions
    and require shebang-line detection (#!/bin/bash).

    Overrides ``register_symbol`` to only register function symbols for cross-file
    call resolution (exports and aliases are file-local).
    """

    lang = "bash"
    file_patterns: ClassVar[list[str]] = ["*.sh", "*.bash"]
    grammar_module = "tree_sitter_bash"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield bash source files, including extensionless shebang scripts."""
        yield from find_bash_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single Bash file.

        Detects function definitions (both 'function name()' and 'name()' styles),
        exported variables (export VAR=value), and alias definitions.
        """
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                func_name = _extract_function_name(node, source)
                if func_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("bash", rel_path, start_line, end_line, func_name, "function")

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
                export_node = find_child_by_type(node, "export")
                if export_node:
                    var_node = find_child_by_type(node, "variable_assignment")
                    if var_node:
                        name_node = find_child_by_type(var_node, "variable_name")
                        if name_node:
                            var_name = node_text(name_node, source)
                            start_line = node.start_point[0] + 1
                            symbol_id = make_symbol_id("bash", rel_path, start_line, start_line, var_name, "export")

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
                cmd_name_node = find_child_by_type(node, "command_name")
                if cmd_name_node:
                    word_node = find_child_by_type(cmd_name_node, "word")
                    if word_node:
                        cmd_name = node_text(word_node, source)

                        if cmd_name == "alias":
                            alias_name = _extract_alias_info(node, source)
                            if alias_name:
                                start_line = node.start_point[0] + 1
                                symbol_id = make_symbol_id("bash", rel_path, start_line, start_line, alias_name, "alias")

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

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register only function symbols for cross-file call resolution.

        Exports and aliases are file-local; only functions can be called
        from other files via 'source' + function-name invocation.
        """
        if symbol.kind == "function":
            global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract edges from a file using global symbol knowledge.

        Detects:
        - source/dot imports (source utils.sh, . ./local.sh)
        - function calls (both local and cross-file via resolver)
        """
        edges: list[Edge] = []
        file_id = make_file_id("bash", rel_path)

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
                cmd_name_node = find_child_by_type(node, "command_name")
                if cmd_name_node:
                    word_node = find_child_by_type(cmd_name_node, "word")
                    if word_node:
                        cmd_name = node_text(word_node, source)
                        line = node.start_point[0] + 1

                        # Handle source/. commands
                        if cmd_name in ("source", "."):
                            words = [
                                c for c in node.children if c.type == "word" and c != word_node
                            ]
                            if words:
                                sourced_path = node_text(words[0], source)
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
                                else:
                                    # Check global symbols via resolver
                                    lookup_result = resolver.lookup(cmd_name)
                                    if lookup_result.found and lookup_result.symbol is not None:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=lookup_result.symbol.id,
                                            edge_type="calls",
                                            line=line,
                                            evidence_type="cross_file_call",
                                            confidence=0.80 * lookup_result.confidence,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                        ))

        return edges


_analyzer = BashAnalyzer()


@register_analyzer("bash")
def analyze_bash(root: Path) -> AnalysisResult:
    """Analyze Bash/shell scripts in a directory.

    Uses tree-sitter-bash for parsing. Falls back gracefully if not available.
    """
    return _analyzer.analyze(root)
