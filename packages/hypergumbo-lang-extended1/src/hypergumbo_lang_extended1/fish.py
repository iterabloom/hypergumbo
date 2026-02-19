"""Fish shell analysis pass using tree-sitter.

Detects:
- Function definitions (with --argument options)
- Alias declarations
- Global variable assignments (set -g, set -gx, set -U)
- Source statements for imports
- Command calls

Fish is a modern, user-friendly shell with clean syntax.
The tree-sitter-fish parser handles .fish configuration and script files.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract function definitions, aliases, and global variables
2. Pass 2: Extract source (import) edges and function call edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Fish-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Fish grammar
- Fish is a popular shell alternative with many users
- Enables analysis of Fish configurations for DevOps/automation
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "fish-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_fish_files(repo_root: Path) -> Iterator[Path]:
    """Find all Fish shell files in the repository."""
    yield from find_files(repo_root, ["*.fish"])


def is_fish_tree_sitter_available() -> bool:
    """Check if tree-sitter-fish is available."""
    return _analyzer._check_grammar_available()


def _find_children_by_type(
    node: "tree_sitter.Node", child_type: str
) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == child_type]


def _make_symbol(
    rel_path: str, run_id: str, node: "tree_sitter.Node",
    source: bytes, name: str, kind: str,
    signature: Optional[str] = None, meta: Optional[dict] = None,
) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"fish:{rel_path}:{start_line}-{end_line}:{name}:{kind}"
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
        language="fish",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        stable_id=f"fish:{rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _get_enclosing_function_fish(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up parent chain to find enclosing function Symbol."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            words = _find_children_by_type(current, "word")
            if words:
                func_name = node_text(words[0], source)
                return local_symbols.get(func_name)
        current = current.parent  # pragma: no cover - loop until function found
    return None  # pragma: no cover - no enclosing function found


# ---------------------------------------------------------------------------
# FishAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class FishAnalyzer(TreeSitterAnalyzer):
    """Fish shell analyzer using tree-sitter-language-pack."""

    lang = "fish"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.fish"]
    language_pack_name = "fish"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, alias, and variable symbols from a Fish file."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                self._process_function(node, source, rel_path, run.execution_id, analysis)
            elif node.type == "command":
                self._process_command_symbols(node, source, rel_path, run.execution_id, analysis)

        return analysis

    def _process_function(
        self, node: "tree_sitter.Node", source: bytes,
        rel_path: str, run_id: str, analysis: FileAnalysis,
    ) -> None:
        """Process a function definition for symbol extraction."""
        words = _find_children_by_type(node, "word")
        if not words:
            return  # pragma: no cover

        func_name = None
        arguments = []
        in_arguments = False

        for word in words:
            text = node_text(word, source)
            if func_name is None:
                func_name = text
            elif text == "--argument":
                in_arguments = True
            elif text.startswith("--"):
                in_arguments = False
            elif in_arguments:
                arguments.append(text)

        if func_name:
            signature = f"({', '.join(arguments)})" if arguments else "()"
            sym = _make_symbol(rel_path, run_id, node, source, func_name, "function", signature=signature)
            analysis.symbols.append(sym)
            analysis.symbol_by_name[func_name] = sym

    def _process_command_symbols(
        self, node: "tree_sitter.Node", source: bytes,
        rel_path: str, run_id: str, analysis: FileAnalysis,
    ) -> None:
        """Process a command for symbol extraction (alias, set -g)."""
        words = _find_children_by_type(node, "word")
        if not words:
            return  # pragma: no cover

        cmd_name = node_text(words[0], source)

        if cmd_name == "alias" and len(words) >= 2:
            alias_name = node_text(words[1], source)
            analysis.symbols.append(_make_symbol(rel_path, run_id, node, source, alias_name, "alias"))

        elif cmd_name == "set" and len(words) >= 2:
            var_name = None
            is_global = False

            for word in words[1:]:
                text = node_text(word, source)
                if text in ["-g", "-gx", "-U", "-Ux", "-x"]:
                    is_global = True
                elif not text.startswith("-") and var_name is None:
                    var_name = text
                    break

            if var_name and is_global:
                analysis.symbols.append(_make_symbol(rel_path, run_id, node, source, var_name, "variable"))

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract source and call edges from Fish commands."""
        edges: list[Edge] = []
        file_stable_id = f"fish:{rel_path}:file:"

        for node in iter_tree(tree.root_node):
            if node.type == "command":
                self._process_command_edges(
                    node, source, rel_path, run.execution_id,
                    file_stable_id, local_symbols, resolver, edges,
                )

        return edges

    def _process_command_edges(
        self, node: "tree_sitter.Node", source: bytes,
        rel_path: str, run_id: str, file_stable_id: str,
        local_symbols: dict[str, Symbol], resolver: "NameResolver",
        edges: list[Edge],
    ) -> None:
        """Process a command for edge extraction."""
        words = _find_children_by_type(node, "word")
        if not words:
            return  # pragma: no cover - defensive

        cmd_name = node_text(words[0], source)

        if cmd_name == "source":
            source_path = None
            found_source_cmd = False
            for child in node.children:
                if child.type == "word":
                    text = node_text(child, source)
                    if text == "source":
                        found_source_cmd = True
                    elif found_source_cmd:
                        source_path = text
                        break
                elif child.type == "concatenation" and found_source_cmd:
                    source_path = node_text(child, source)
                    break

            if source_path:
                edges.append(
                    Edge(
                        id=f"edge:fish:{uuid.uuid4().hex[:12]}",
                        src=file_stable_id,
                        dst=f"fish:{source_path}:file",
                        edge_type="sources",
                        line=node.start_point[0] + 1,
                        confidence=0.9,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                )

        elif cmd_name not in ("alias", "set"):
            caller = _get_enclosing_function_fish(node, source, local_symbols)
            if caller:
                result = resolver.lookup(cmd_name)
                if result.symbol is not None:
                    dst_id = result.symbol.id
                    confidence = 0.85 * result.confidence
                else:
                    dst_id = f"fish:external:{cmd_name}:function"
                    confidence = 0.70

                edges.append(
                    Edge(
                        id=f"edge:fish:{uuid.uuid4().hex[:12]}",
                        src=caller.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        confidence=confidence,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                )


_analyzer = FishAnalyzer()


@register_analyzer("fish")
def analyze_fish(repo_root: Path) -> AnalysisResult:
    """Analyze Fish shell files in a repository."""
    return _analyzer.analyze(repo_root)
