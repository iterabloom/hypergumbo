"""Assembly language analysis pass using tree-sitter.

Extracts labels (functions), data symbols, and call edges from assembly source
files (.s, .asm, .S). Assembly is used in operating system kernels, bootloaders,
embedded systems, and performance-critical routines within larger C/C++ projects.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract labels as symbols
2. Pass 2: Detect call instructions and resolve targets against label registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the assembly-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- The tree-sitter-asm grammar produces label nodes and instruction nodes
- Labels serve as both function entry points and data labels
- Call instructions reference labels by name -- simple string matching resolves them
- Two-pass allows cross-file resolution (common in multi-file assembly projects)

Assembly-Specific Considerations
-------------------------------
- Labels are the primary symbols (no function keyword in assembly)
- .global directives indicate exported symbols
- Call targets may be external (libc functions, syscalls)
- Section directives (.text, .data, .bss) hint at symbol purpose
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("asm")


def find_asm_files(repo_root: Path) -> Iterator[Path]:
    """Yield all assembly files in the repository."""
    yield from find_files(repo_root, ["*.s", "*.asm", "*.S"])


def is_asm_tree_sitter_available() -> bool:
    """Check if tree-sitter with asm grammar is available."""
    return _analyzer._check_grammar_available()


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _determine_label_kind(current_section: str) -> str:
    """Determine label kind based on current section.

    Labels in .text are functions; labels in .data/.bss/.rodata are variables.
    Default to function for unknown sections.
    """
    if current_section in (".data", ".bss", ".rodata"):
        return "variable"
    return "function"


def _find_enclosing_label(
    line: int, file_labels: list[tuple[int, Symbol]]
) -> Optional[Symbol]:
    """Find the most recent label before the given line.

    Assembly doesn't have explicit function boundaries -- a label's scope
    extends until the next label. So the enclosing function for a call
    instruction at line N is the last label defined before line N.
    """
    result = None
    for label_line, sym in file_labels:
        if label_line <= line:
            result = sym
        else:
            break
    return result


# ---------------------------------------------------------------------------
# AsmAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class AsmAnalyzer(TreeSitterAnalyzer):
    """Assembly language analyzer using tree-sitter-language-pack."""

    lang = "asm"
    file_patterns: ClassVar[list[str]] = ["*.s", "*.asm", "*.S"]
    language_pack_name = "asm"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract labels from an assembly file, tracking section context."""
        analysis = FileAnalysis()
        current_section = ".text"

        for node in iter_tree(tree.root_node):
            # Track section changes
            if node.type == "meta":
                meta_ident = find_child_by_type(node, "meta_ident")
                if meta_ident:
                    directive = node_text(meta_ident, source)
                    if directive == ".section":
                        ident = find_child_by_type(node, "ident")
                        if ident:
                            section_name = node_text(ident, source)
                            current_section = section_name

            # Extract labels
            elif node.type == "label":
                ident = find_child_by_type(node, "ident")
                if ident:
                    label_name = node_text(ident, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    kind = _determine_label_kind(current_section)

                    sym = Symbol(
                        id=make_symbol_id("asm", rel_path, start_line, end_line, label_name, kind),
                        name=label_name,
                        canonical_name=label_name,
                        kind=kind,
                        language="asm",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        stable_id=f"asm:{rel_path}:{label_name}",
                    )
                    analysis.symbols.append(sym)
                    if kind == "function":
                        analysis.symbol_by_name[label_name] = sym

        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from assembly instructions."""
        edges: list[Edge] = []

        # Build file_labels list from global_symbols filtered to this rel_path
        file_labels: list[tuple[int, Symbol]] = sorted(
            [(s.span.start_line, s) for s in global_symbols.values()
             if isinstance(s, Symbol) and s.path == rel_path and s.kind == "function"],
            key=lambda x: x[0],
        )

        for node in iter_tree(tree.root_node):
            if node.type != "instruction":
                continue

            # Check if this is a call instruction
            word_node = find_child_by_type(node, "word")
            if not word_node:
                continue  # pragma: no cover - defensive against unusual AST shape
            opcode = node_text(word_node, source).lower()
            if opcode != "call":
                continue

            # Get call target
            target_node = find_child_by_type(node, "ident")
            if not target_node:
                continue  # pragma: no cover - defensive
            target_name = node_text(target_node, source)

            # Find enclosing label (function) for this call instruction
            call_line = node.start_point[0] + 1
            caller = _find_enclosing_label(call_line, file_labels)
            if not caller:
                continue  # pragma: no cover - defensive

            # Resolve call target
            target_sym = global_symbols.get(target_name)
            if isinstance(target_sym, Symbol):
                dst_id = target_sym.id
                confidence = 0.85
            else:
                dst_id = f"asm:external:{target_name}:function"
                confidence = 0.70

            edges.append(Edge(
                id=_make_edge_id(caller.id, dst_id, "calls"),
                src=caller.id,
                dst=dst_id,
                edge_type="calls",
                line=call_line,
                confidence=confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))

        return edges


_analyzer = AsmAnalyzer()


@register_analyzer("asm")
def analyze_asm(repo_root: Path) -> AnalysisResult:
    """Analyze assembly language files in a repository."""
    return _analyzer.analyze(repo_root)
