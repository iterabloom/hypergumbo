"""Makefile analysis pass using tree-sitter-make.

This analyzer uses tree-sitter to parse Makefiles and extract:
- Variable definitions
- Target rules (explicit and pattern rules)
- Prerequisites (dependencies)
- Include directives
- Define blocks (functions/macros)

If tree-sitter-make is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses the TreeSitterAnalyzer base class with a custom ``analyze`` override
because Makefile prerequisite references need the target registry built
during the same pass (single-pass symbol+edge extraction).

1. Check if tree-sitter-make is available
2. If not available, return skipped result (not an error)
3. Single-pass analysis: parse all files, extract all targets, variables,
   and prerequisite dependency edges together
4. Create depends_on edges for target dependencies

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-make package for grammar
- Single-pass because target_link references need the registry built in-flight
- Build-system-specific: targets, prerequisites, variables are first-class
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, TreeSitterAnalyzer, iter_tree, make_symbol_id, node_text
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("make")


def find_make_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Makefile files in the repository."""
    yield from find_files(repo_root, ["Makefile", "makefile", "*.mk", "GNUmakefile"])


def is_make_tree_sitter_available() -> bool:
    """Check if tree-sitter with Make grammar is available."""
    return _analyzer._check_grammar_available()


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_target_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract target names from a targets node."""
    targets = []
    for child in node.children:
        if child.type == "word":
            targets.append(node_text(child, source))
    return targets


def _get_prerequisites(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract prerequisite names from a prerequisites node."""
    prereqs = []
    for child in node.children:
        if child.type == "word":
            prereqs.append(node_text(child, source))
        elif child.type == "variable_reference":
            # Include variable reference as a dependency marker
            prereqs.append(node_text(child, source))
    return prereqs


def _get_variable_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract variable name from a variable_assignment node."""
    for child in node.children:
        if child.type == "word":
            return node_text(child, source)
    return None  # pragma: no cover


def _get_define_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function/macro name from a define_directive node."""
    for child in node.children:
        if child.type == "word":
            return node_text(child, source)
    return None  # pragma: no cover


def _get_include_files(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract included file names from an include_directive node."""
    files = []
    for child in node.children:
        if child.type == "list":
            for subchild in child.children:
                if subchild.type == "word":
                    files.append(node_text(subchild, source))
    return files


def _process_make_tree(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    target_registry: dict[str, str],
) -> None:
    """Process Makefile AST tree to extract symbols and edges.

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        target_registry: Registry mapping target names to symbol IDs
    """
    # Track seen variable names in this file to dedupe (e.g., ASFLAGS := ... / ASFLAGS += ...)
    seen_variables: set[str] = set()

    for node in iter_tree(root):
        if node.type == "variable_assignment":
            var_name = _get_variable_name(node, source)
            if var_name:
                # Skip duplicate variable definitions (e.g., VAR := x / VAR += y)
                # Only emit a symbol for the first definition
                var_key = var_name.lower()
                if var_key in seen_variables:
                    continue
                seen_variables.add(var_key)

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("make", rel_path, start_line, end_line, var_name, "variable")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=var_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="variable",
                    name=var_name,
                    path=rel_path,
                    language="make",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[var_name.lower()] = symbol_id

        elif node.type == "rule":
            # Extract targets and prerequisites
            targets_node = None
            prereqs_node = None

            for child in node.children:
                if child.type == "targets":
                    targets_node = child
                elif child.type == "prerequisites":
                    prereqs_node = child

            if targets_node:
                target_names = _get_target_names(targets_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Determine if this is a pattern rule
                is_pattern = any("%" in t for t in target_names)
                kind = "pattern_rule" if is_pattern else "target"

                for target_name in target_names:
                    # Skip special targets like .PHONY
                    if target_name.startswith("."):
                        kind = "special_target"

                    symbol_id = make_symbol_id("make", rel_path, start_line, end_line, target_name, kind)

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=target_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind=kind,
                        name=target_name,
                        path=rel_path,
                        language="make",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    symbols.append(sym)
                    target_registry[target_name.lower()] = symbol_id

                    # Create edges for prerequisites
                    if prereqs_node:
                        prereqs = _get_prerequisites(prereqs_node, source)
                        for prereq in prereqs:
                            # Skip variable references for now (could resolve later)
                            if prereq.startswith("$"):
                                continue

                            if prereq.lower() in target_registry:
                                dst_id = target_registry[prereq.lower()]
                                confidence = 0.90
                            else:
                                # External file or unresolved target
                                dst_id = f"make:external:{prereq}:target"
                                confidence = 0.70

                            edge = Edge.create(
                                src=symbol_id,
                                dst=dst_id,
                                edge_type="depends_on",
                                line=start_line,
                                confidence=confidence,
                                origin=PASS_ID,
                                evidence_type="make_prerequisite",
                            )
                            edges.append(edge)

        elif node.type == "define_directive":
            define_name = _get_define_name(node, source)
            if define_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("make", rel_path, start_line, end_line, define_name, "function")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=define_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=define_name,
                    path=rel_path,
                    language="make",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[define_name.lower()] = symbol_id

        elif node.type == "include_directive":
            include_files = _get_include_files(node, source)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            for include_file in include_files:
                symbol_id = make_symbol_id("make", rel_path, start_line, end_line, include_file, "include")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=include_file,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="include",
                    name=include_file,
                    path=rel_path,
                    language="make",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)


class MakeAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Makefile analyzer.

    Uses tree-sitter-make to parse Makefiles (Makefile, makefile, *.mk, GNUmakefile).
    Extracts variables, targets, pattern rules, special targets, prerequisites,
    define blocks, and include directives.

    Overrides ``analyze`` because Make prerequisite references need the target
    registry built during the same pass (single-pass symbol+edge extraction),
    similar to CMake's target_link_libraries.
    """

    lang = "make"
    file_patterns: ClassVar[list[str]] = ["Makefile", "makefile", "*.mk", "GNUmakefile"]
    grammar_module = "tree_sitter_make"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Makefile analysis with single-pass symbol+edge extraction.

        Make prerequisite references need the target registry built during
        symbol extraction, so both symbols and edges are extracted in a
        single pass through ``_process_make_tree``.
        """
        import time as _time
        import warnings as _warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            _warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        parser = self._create_parser()

        files_analyzed = 0
        files_skipped = 0
        warnings_list: list[str] = []

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        target_registry: dict[str, str] = {}

        make_files = list(find_make_files(repo_root))

        for make_path in make_files:
            if max_files is not None and files_analyzed >= max_files:
                break  # pragma: no cover

            try:
                rel_path = str(make_path.relative_to(repo_root))
                source = make_path.read_bytes()
                tree = parser.parse(source)
                files_analyzed += 1

                # Process this file
                _process_make_tree(
                    tree.root_node,
                    source,
                    rel_path,
                    symbols,
                    edges,
                    target_registry,
                )

            except Exception as e:  # pragma: no cover
                files_skipped += 1  # pragma: no cover
                warnings_list.append(f"Failed to parse {make_path}: {e}")  # pragma: no cover

        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((_time.time() - start_time) * 1000)
        run.warnings = warnings_list

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = MakeAnalyzer()


@register_analyzer("make")
def analyze_make_files(repo_root: Path) -> AnalysisResult:
    """Analyze Makefile files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult with symbols and edges
    """
    return _analyzer.analyze(repo_root)
