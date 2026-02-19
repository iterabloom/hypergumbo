"""Python requirements.txt analyzer using tree-sitter.

requirements.txt is the standard format for specifying Python package dependencies.
It's used by pip to install packages with specific version constraints.

How It Works
------------
Uses the TreeSitterAnalyzer base class with the tree-sitter-requirements grammar
from tree-sitter-language-pack. Single-pass for symbols; edges (depends, includes,
constrains) are collected during symbol extraction and flushed via post_process.

1. extract_symbols_from_file: walks AST to find requirements, URLs, global options
2. post_process: flushes accumulated dependency/include/constraint edges
3. _find_source_files: overridden for complex multi-pattern requirements discovery

Symbols Extracted
-----------------
- **Requirements**: Package dependencies with version specs
- **URL deps**: URL-based dependencies (git+https://, etc.)
- **Editable**: Editable installs (-e)

Edges Extracted
---------------
- **includes**: References to other requirements files (-r)
- **constrains**: References to constraints files (-c)
- **depends**: Package dependency edges

Why This Design
---------------
- requirements.txt is ubiquitous in Python projects
- Version constraints are critical for reproducible builds
- Understanding dependencies helps with supply chain analysis
- Cross-file references (-r) map the dependency graph
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "requirements.tree_sitter"
PASS_VERSION = "0.1.0"


def find_requirements_files(repo_root: Path) -> list[Path]:
    """Find all requirements files in the repository."""
    files: list[Path] = []
    # Common requirements file patterns
    patterns = [
        "requirements.txt",
        "requirements*.txt",
        "*requirements.txt",
        "requirements/*.txt",
        "reqs/*.txt",
    ]
    for pattern in patterns:
        files.extend(find_files(repo_root, [pattern], max_files=1))
        files.extend(find_files(repo_root, [pattern]))
    # Deduplicate and sort
    return sorted(set(files))


def _make_symbol_id(path: str, name: str, kind: str) -> str:
    """Create a stable symbol ID for requirements."""
    return f"requirements:{path}:{kind}:{name}"


class RequirementsAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based requirements.txt analyzer.

    Uses tree-sitter-requirements from the language-pack to extract package
    dependencies, URL requirements, and file inclusion directives.

    Edges (depends, includes, constrains) are collected during symbol
    extraction (Pass 1) and flushed via ``post_process``, since dependency
    symbols and their edges are tightly coupled.

    Overrides ``_find_source_files`` because requirements files use multiple
    naming patterns (requirements.txt, requirements-dev.txt, etc.) that
    need complex multi-pattern discovery with deduplication.
    """

    lang = "requirements"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["requirements*.txt", "*requirements.txt"]
    language_pack_name = "requirements"

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None
    ) -> AnalysisResult:
        """Reset pending edges and delegate to the base class."""
        self._pending_edges: list[Edge] = []
        return super().analyze(repo_root, max_files)

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield requirements files using multi-pattern discovery."""
        yield from find_requirements_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract requirements, URL deps, and global options from a file.

        Walks the AST iteratively. Dependency edges are accumulated on
        self._pending_edges for flushing in post_process.
        """
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            if node.type == "requirement":
                self._extract_requirement(
                    node, source, rel_path, run, analysis,
                )
            elif node.type == "url":
                self._extract_url_requirement(
                    node, source, rel_path, run, analysis,
                )
            elif node.type == "global_opt":
                self._extract_global_option(
                    node, source, rel_path, run, analysis,
                )

        return analysis

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list,
        run: AnalysisRun,
    ) -> tuple[list[Symbol], list[Edge], list]:
        """Flush accumulated dependency edges into the final edge list."""
        edges.extend(self._pending_edges)
        return symbols, edges, usage_contexts

    # -- Extraction helpers ---------------------------------------------------

    def _extract_requirement(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        rel_path: str,
        run: AnalysisRun,
        analysis: FileAnalysis,
    ) -> None:
        """Extract a package requirement symbol and depends edge."""
        package_name = ""
        version_spec = ""
        extras: list[str] = []
        marker_spec = ""

        for child in node.children:
            if child.type == "package":
                package_name = node_text(child, source)
            elif child.type == "version_spec":
                version_spec = node_text(child, source)
            elif child.type == "extras":
                for extra_child in child.children:
                    if extra_child.type == "package":
                        extras.append(node_text(extra_child, source))
            elif child.type == "marker_spec":
                marker_spec = node_text(child, source).strip()

        if not package_name:
            return  # pragma: no cover

        symbol_id = _make_symbol_id(rel_path, package_name, "requirement")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        sig = package_name
        if extras:
            sig += f"[{','.join(extras)}]"
        if version_spec:
            sig += version_spec

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=package_name,
            kind="requirement",
            language="requirements",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=sig,
            meta={
                "version_spec": version_spec,
                "extras": extras,
                "marker": marker_spec,
            },
        )
        analysis.symbols.append(symbol)

        edge = Edge.create(
            src=f"requirements:{rel_path}",
            dst=f"pypi:package:{package_name}",
            edge_type="depends",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="static",
            confidence=1.0,
            evidence_lang="requirements",
        )
        self._pending_edges.append(edge)

    def _extract_url_requirement(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        rel_path: str,
        run: AnalysisRun,
        analysis: FileAnalysis,
    ) -> None:
        """Extract a URL-based requirement symbol and optional depends edge."""
        url_text = node_text(node, source).strip()

        if not url_text:
            return  # pragma: no cover

        # Extract package name from egg fragment if present
        package_name = ""
        if "#egg=" in url_text:
            package_name = url_text.split("#egg=")[-1].split("&")[0]
        else:
            parts = url_text.rstrip("/").split("/")
            if parts:
                package_name = parts[-1].replace(".git", "")
                if "@" in package_name:
                    package_name = package_name.split("@")[0]

        symbol_id = _make_symbol_id(
            rel_path, package_name or url_text[:40], "url_requirement",
        )

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        source_type = "url"
        if url_text.startswith("git+"):
            source_type = "git"
        elif url_text.startswith("hg+"):
            source_type = "mercurial"
        elif url_text.startswith("svn+"):
            source_type = "svn"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=package_name or url_text[:40],
            kind="url_requirement",
            language="requirements",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=url_text[:60] + ("..." if len(url_text) > 60 else ""),
            meta={
                "url": url_text,
                "source_type": source_type,
                "package_name": package_name,
            },
        )
        analysis.symbols.append(symbol)

        if package_name:
            edge = Edge.create(
                src=f"requirements:{rel_path}",
                dst=f"vcs:package:{package_name}",
                edge_type="depends",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="static",
                confidence=0.9,
                evidence_lang="requirements",
            )
            self._pending_edges.append(edge)

    def _extract_global_option(
        self,
        node: "tree_sitter.Node",
        source: bytes,
        rel_path: str,
        run: AnalysisRun,
        analysis: FileAnalysis,
    ) -> None:
        """Extract global options like -r, -c, -e."""
        option = ""
        option_path = ""

        for child in node.children:
            if child.type == "option":
                option = node_text(child, source)
            elif child.type == "path":
                option_path = node_text(child, source)

        if not option:
            return  # pragma: no cover

        # Handle -r (requirements) and -c (constraints)
        if option in ("-r", "--requirement", "-c", "--constraint") and option_path:
            edge_type = "includes" if option in ("-r", "--requirement") else "constrains"
            edge = Edge.create(
                src=f"requirements:{rel_path}",
                dst=f"requirements:file:{option_path}",
                edge_type=edge_type,
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="static",
                confidence=1.0,
                evidence_lang="requirements",
            )
            self._pending_edges.append(edge)

        # Handle -e (editable)
        if option in ("-e", "--editable") and option_path:
            symbol_id = _make_symbol_id(rel_path, option_path, "editable")

            span = Span(
                start_line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=option_path,
                kind="editable",
                language="requirements",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                signature=f"-e {option_path}",
                meta={"path": option_path, "editable": True},
            )
            analysis.symbols.append(symbol)


_analyzer = RequirementsAnalyzer()


def is_requirements_tree_sitter_available() -> bool:
    """Check if tree-sitter-requirements is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("requirements")
def analyze_requirements(repo_root: Path) -> AnalysisResult:
    """Analyze requirements.txt files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
