"""BitBake analyzer using tree-sitter.

BitBake is the build tool used by Yocto Project and OpenEmbedded for embedded
Linux development. It processes recipe files (.bb, .bbappend) and class files
(.bbclass) to build complete Linux distributions.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract variable assignments, inherit directives, task functions,
   addtask statements. Edges for inherits and depends are collected during
   symbol extraction and emitted in Pass 2.
2. Pass 2: No additional edges needed (all edges come from Pass 1 data).

Symbols Extracted
-----------------
- **Variables**: Key recipe metadata (SUMMARY, LICENSE, SRC_URI, DEPENDS, etc.)
- **Inherits**: Class inheritance declarations
- **Tasks**: Shell task functions (do_fetch, do_configure, do_compile, etc.)

Edges Extracted
---------------
- **inherits**: Inheritance from .bbclass files
- **depends**: Package dependencies from DEPENDS variable

Why This Design
---------------
- BitBake recipes define the entire build process for packages
- Variable assignments contain critical metadata and dependencies
- Inherit directives create class hierarchies important for understanding builds
- Task functions define the actual build steps
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("bitbake")


def find_bitbake_files(repo_root: Path) -> list[Path]:
    """Find all BitBake files in the repository."""
    files: list[Path] = []
    for pattern in ["**/*.bb", "**/*.bbappend", "**/*.bbclass", "**/*.inc"]:
        files.extend(find_files(repo_root, [pattern]))
    return sorted(files)


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"bitbake:{path}:{kind}:{name}"


# Important BitBake variables to track
IMPORTANT_VARIABLES = frozenset({
    "SUMMARY", "DESCRIPTION", "HOMEPAGE", "LICENSE", "LIC_FILES_CHKSUM",
    "SRC_URI", "S", "B", "D", "WORKDIR",
    "DEPENDS", "RDEPENDS", "RRECOMMENDS", "RCONFLICTS", "RPROVIDES",
    "PROVIDES", "PACKAGE_ARCH", "COMPATIBLE_HOST", "COMPATIBLE_MACHINE",
    "PN", "PV", "PR", "PF", "P", "BPN", "BP",
    "PACKAGES", "FILES", "CONFFILES",
    "EXTRA_OECONF", "EXTRA_OECMAKE", "EXTRA_OEMAKE",
    "CFLAGS", "LDFLAGS", "CXXFLAGS",
    "inherit",
})


class BitBakeAnalyzer(TreeSitterAnalyzer):
    """Analyzer for BitBake files using TreeSitterAnalyzer base class.

    BitBake is unusual: edges (inherits, depends) are derived during symbol
    extraction (Pass 1), not from cross-file call resolution (Pass 2).
    We store pending edges in the FileAnalysis.import_aliases dict (repurposed
    as a lightweight mechanism) and emit them in extract_edges_from_file.
    """

    lang = "bitbake"
    file_patterns: ClassVar[list[str]] = ["**/*.bb", "**/*.bbappend", "**/*.bbclass", "**/*.inc"]
    language_pack_name = "bitbake"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract variable, inherit, task, and addtask symbols from a BitBake file."""
        analysis = FileAnalysis()
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        # We store pending edges as a list in _pending_edges attribute on analysis
        # Since FileAnalysis doesn't have an edges field, we collect them
        # and store edge data serialized in import_aliases for retrieval in Pass 2
        pending_edges: list[Edge] = []

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run,
            analysis, pending_edges,
        )

        # Store pending edges count in import_aliases as a signal
        # We'll use a module-level dict keyed by rel_path to pass edges between passes
        _pending_edge_store[rel_path] = pending_edges

        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun",
        analysis: FileAnalysis, pending_edges: list[Edge],
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "variable_assignment":
            self._extract_variable(
                node, path, repo_root, rel_path, run, analysis, pending_edges,
            )
        elif node.type == "inherit_directive":
            self._extract_inherit(
                node, path, repo_root, rel_path, run, analysis, pending_edges,
            )
        elif node.type == "function_definition":
            self._extract_function(node, path, repo_root, rel_path, run, analysis)
        elif node.type == "anonymous_python_function":
            self._extract_python_function(node, path, repo_root, rel_path, run, analysis)
        elif node.type == "addtask_statement":
            self._extract_addtask(node, path, repo_root, rel_path, run, analysis)

        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis, pending_edges,
            )

    def _extract_variable(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun",
        analysis: FileAnalysis, pending_edges: list[Edge],
    ) -> None:
        """Extract a variable assignment."""
        var_name = None
        var_value = ""

        for child in node.children:
            if child.type == "identifier":
                var_name = _get_node_text(child)
            elif child.type == "literal":
                var_value = _get_node_text(child).strip('"\'')

        if not var_name:
            return  # pragma: no cover

        # Only track important variables to avoid noise
        base_name = var_name.split("_")[0]
        if base_name not in IMPORTANT_VARIABLES and var_name not in IMPORTANT_VARIABLES:
            return

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, var_name, "variable")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=var_name,
            kind="variable",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"{var_name} = {var_value[:50]}..." if len(var_value) > 50 else f"{var_name} = {var_value}",
            meta={"value": var_value[:200]} if var_value else {},
        )
        analysis.symbols.append(symbol)

        # Extract dependency edges from DEPENDS/RDEPENDS
        if var_name in ("DEPENDS", "RDEPENDS"):
            self._extract_dependency_edges(
                var_value, rel_p, node.start_point[0] + 1, run, pending_edges,
            )

    def _extract_dependency_edges(
        self, value: str, rel_path: Path, line: int,
        run: "AnalysisRun", pending_edges: list[Edge],
    ) -> None:
        """Extract dependency edges from DEPENDS/RDEPENDS value."""
        clean_value = re.sub(r"\$\{[^}]+\}", "", value)
        packages = clean_value.split()

        for pkg in packages:
            pkg = pkg.strip()
            if not pkg or pkg.startswith("$"):
                continue  # pragma: no cover - defensive after regex cleanup

            edge = Edge.create(
                src=f"bitbake:{rel_path}",
                dst=f"bitbake:package:{pkg}",
                edge_type="depends",
                line=line,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="static",
                confidence=0.8,
                evidence_lang="bitbake",
            )
            pending_edges.append(edge)

    def _extract_inherit(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun",
        analysis: FileAnalysis, pending_edges: list[Edge],
    ) -> None:
        """Extract an inherit directive."""
        classes: list[str] = []

        for child in node.children:
            if child.type == "inherit_path":
                classes.append(_get_node_text(child))

        if not classes:
            return  # pragma: no cover

        rel_p = Path(rel_path)

        for cls in classes:
            symbol_id = _make_symbol_id(rel_p, cls, "inherit")

            span = Span(
                start_line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=cls,
                kind="inherit",
                language="bitbake",
                path=str(rel_p),
                span=span,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                signature=f"inherit {cls}",
                meta={"class": cls},
            )
            analysis.symbols.append(symbol)

            # Add inherit edge
            edge = Edge.create(
                src=f"bitbake:{rel_p}",
                dst=f"bitbake:class:{cls}",
                edge_type="inherits",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="static",
                confidence=1.0,
                evidence_lang="bitbake",
            )
            pending_edges.append(edge)

    def _extract_function(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract a shell function definition (task)."""
        func_name = None

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
                break

        if not func_name:
            return  # pragma: no cover

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, func_name, "task")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        task_type = "custom"
        if func_name.startswith("do_"):
            task_type = "standard"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=func_name,
            kind="task",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"{func_name}()",
            meta={"task_type": task_type},
        )
        analysis.symbols.append(symbol)

    def _extract_python_function(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract a Python function definition."""
        func_name = None

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
                break

        if not func_name:
            return  # pragma: no cover

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, func_name, "python_task")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=func_name,
            kind="python_task",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"python {func_name}()",
            meta={"language": "python"},
        )
        analysis.symbols.append(symbol)

    def _extract_addtask(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract an addtask directive."""
        task_name = None

        for child in node.children:
            if child.type == "identifier":
                task_name = _get_node_text(child)
                break

        if not task_name:
            return  # pragma: no cover

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, task_name, "addtask")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=task_name,
            kind="addtask",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"addtask {task_name}",
            meta={},
        )
        analysis.symbols.append(symbol)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Return edges collected during Pass 1 symbol extraction."""
        return _pending_edge_store.pop(rel_path, [])


# Module-level store for edges collected during Pass 1
_pending_edge_store: dict[str, list[Edge]] = {}

_analyzer = BitBakeAnalyzer()


def is_bitbake_tree_sitter_available() -> bool:
    """Check if tree-sitter-bitbake is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("bitbake")
def analyze_bitbake(repo_root: Path) -> AnalysisResult:
    """Analyze BitBake files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
