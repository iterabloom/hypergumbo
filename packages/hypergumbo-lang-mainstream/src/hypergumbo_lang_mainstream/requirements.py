"""Python requirements.txt analyzer using tree-sitter.

requirements.txt is the standard format for specifying Python package dependencies.
It's used by pip to install packages with specific version constraints.

How It Works
------------
1. Uses tree-sitter-requirements grammar from tree-sitter-language-pack
2. Extracts package requirements with version constraints
3. Extracts URL dependencies (git+, https:, etc.)
4. Tracks -r (requirements) and -c (constraints) file references

Symbols Extracted
-----------------
- **Requirements**: Package dependencies with version specs
- **URL deps**: URL-based dependencies (git+https://, etc.)

Edges Extracted
---------------
- **includes**: References to other requirements files (-r)
- **depends**: Package dependency edges

Why This Design
---------------
- requirements.txt is ubiquitous in Python projects
- Version constraints are critical for reproducible builds
- Understanding dependencies helps with supply chain analysis
- Cross-file references (-r) map the dependency graph
"""

from __future__ import annotations

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "requirements.tree_sitter"
PASS_VERSION = "0.1.0"


class RequirementsAnalysisResult:
    """Result of requirements.txt analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges = edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_requirements_tree_sitter_available() -> bool:
    """Check if tree-sitter-requirements is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("requirements")
        return True
    except Exception:  # pragma: no cover
        return False


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
        files.extend(repo_root.glob(pattern))
        files.extend(repo_root.glob(f"**/{pattern}"))
    # Deduplicate and sort
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"requirements:{path}:{kind}:{name}"


class RequirementsAnalyzer:
    """Analyzer for requirements.txt files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> RequirementsAnalysisResult:
        """Run the requirements analysis."""
        start_time = time.time()

        files = find_requirements_files(self.repo_root)
        if not files:
            return RequirementsAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("requirements")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "requirements", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return RequirementsAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "requirement":
            self._extract_requirement(node, path)
        elif node.type == "url":
            self._extract_url_requirement(node, path)
        elif node.type == "global_opt":
            self._extract_global_option(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_requirement(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a package requirement."""
        package_name = ""
        version_spec = ""
        extras: list[str] = []
        marker_spec = ""

        for child in node.children:
            if child.type == "package":
                package_name = _get_node_text(child)
            elif child.type == "version_spec":
                version_spec = _get_node_text(child)
            elif child.type == "extras":
                for extra_child in child.children:
                    if extra_child.type == "package":
                        extras.append(_get_node_text(extra_child))
            elif child.type == "marker_spec":
                marker_spec = _get_node_text(child).strip()

        if not package_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, package_name, "requirement")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Build signature
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
            signature=sig,
            meta={
                "version_spec": version_spec,
                "extras": extras,
                "marker": marker_spec,
            },
        )
        self._symbols.append(symbol)

        # Create dependency edge
        edge = Edge.create(
            src=f"requirements:{rel_path}",
            dst=f"pypi:package:{package_name}",
            edge_type="depends",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="static",
            confidence=1.0,
            evidence_lang="requirements",
        )
        self._edges.append(edge)

    def _extract_url_requirement(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a URL-based requirement."""
        url_text = _get_node_text(node).strip()

        if not url_text:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)

        # Extract package name from egg fragment if present
        package_name = ""
        if "#egg=" in url_text:
            package_name = url_text.split("#egg=")[-1].split("&")[0]
        else:
            # Try to extract from URL path
            parts = url_text.rstrip("/").split("/")
            if parts:
                package_name = parts[-1].replace(".git", "")
                if "@" in package_name:
                    package_name = package_name.split("@")[0]

        symbol_id = _make_symbol_id(rel_path, package_name or url_text[:40], "url_requirement")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Determine source type
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
            signature=url_text[:60] + ("..." if len(url_text) > 60 else ""),
            meta={
                "url": url_text,
                "source_type": source_type,
                "package_name": package_name,
            },
        )
        self._symbols.append(symbol)

        # Create dependency edge
        if package_name:
            edge = Edge.create(
                src=f"requirements:{rel_path}",
                dst=f"vcs:package:{package_name}",
                edge_type="depends",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                confidence=0.9,  # Slightly lower confidence for URL deps
                evidence_lang="requirements",
            )
            self._edges.append(edge)

    def _extract_global_option(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract global options like -r, -c, -e."""
        option = ""
        option_path = ""

        for child in node.children:
            if child.type == "option":
                option = _get_node_text(child)
            elif child.type == "path":
                option_path = _get_node_text(child)

        if not option:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)

        # Handle -r (requirements) and -c (constraints)
        if option in ("-r", "--requirement", "-c", "--constraint") and option_path:
            edge_type = "includes" if option in ("-r", "--requirement") else "constrains"
            edge = Edge.create(
                src=f"requirements:{rel_path}",
                dst=f"requirements:file:{option_path}",
                edge_type=edge_type,
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                confidence=1.0,
                evidence_lang="requirements",
            )
            self._edges.append(edge)

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
                signature=f"-e {option_path}",
                meta={"path": option_path, "editable": True},
            )
            self._symbols.append(symbol)


def analyze_requirements(repo_root: Path) -> RequirementsAnalysisResult:
    """Analyze requirements.txt files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        RequirementsAnalysisResult containing extracted symbols and edges
    """
    if not is_requirements_tree_sitter_available():
        warnings.warn(
            "Requirements analysis skipped: tree-sitter-requirements not available",
            UserWarning,
            stacklevel=2,
        )
        return RequirementsAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "requirements", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-requirements not available",
        )

    analyzer = RequirementsAnalyzer(repo_root)
    return analyzer.analyze()
