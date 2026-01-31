"""BitBake analyzer using tree-sitter.

BitBake is the build tool used by Yocto Project and OpenEmbedded for embedded
Linux development. It processes recipe files (.bb, .bbappend) and class files
(.bbclass) to build complete Linux distributions.

How It Works
------------
1. Uses tree-sitter-bitbake grammar from tree-sitter-language-pack to parse files
2. Extracts variable assignments (SUMMARY, LICENSE, SRC_URI, etc.)
3. Extracts inherit directives (class dependencies)
4. Extracts task functions (do_configure, do_compile, do_install, etc.)

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
import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "bitbake.tree_sitter"
PASS_VERSION = "0.1.0"


class BitBakeAnalysisResult:
    """Result of BitBake analysis."""

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


def is_bitbake_tree_sitter_available() -> bool:
    """Check if tree-sitter-bitbake is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("bitbake")
        return True
    except Exception:  # pragma: no cover
        return False


def find_bitbake_files(repo_root: Path) -> list[Path]:
    """Find all BitBake files in the repository."""
    files: list[Path] = []
    for pattern in ["**/*.bb", "**/*.bbappend", "**/*.bbclass", "**/*.inc"]:
        files.extend(repo_root.glob(pattern))
    return sorted(files)


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


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


class BitBakeAnalyzer:
    """Analyzer for BitBake files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> BitBakeAnalysisResult:
        """Run the BitBake analysis."""
        start_time = time.time()

        files = find_bitbake_files(self.repo_root)
        if not files:
            return BitBakeAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("bitbake")

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
            toolchain={"name": "bitbake", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return BitBakeAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "variable_assignment":
            self._extract_variable(node, path)
        elif node.type == "inherit_directive":
            self._extract_inherit(node, path)
        elif node.type == "function_definition":
            self._extract_function(node, path)
        elif node.type == "anonymous_python_function":
            self._extract_python_function(node, path)
        elif node.type == "addtask_statement":
            self._extract_addtask(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_variable(self, node: "tree_sitter.Node", path: Path) -> None:
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

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, var_name, "variable")

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
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{var_name} = {var_value[:50]}..." if len(var_value) > 50 else f"{var_name} = {var_value}",
            meta={"value": var_value[:200]} if var_value else {},
        )
        self._symbols.append(symbol)

        # Extract dependency edges from DEPENDS/RDEPENDS
        if var_name in ("DEPENDS", "RDEPENDS"):
            self._extract_dependency_edges(var_value, rel_path, node.start_point[0] + 1)

    def _extract_dependency_edges(self, value: str, rel_path: Path, line: int) -> None:
        """Extract dependency edges from DEPENDS/RDEPENDS value."""
        # Parse package names from value (space-separated, may include ${PN} etc.)
        # Remove variable references and get clean package names
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
                origin_run_id=self._execution_id,
                evidence_type="static",
                confidence=0.8,
                evidence_lang="bitbake",
            )
            self._edges.append(edge)

    def _extract_inherit(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract an inherit directive."""
        classes: list[str] = []

        for child in node.children:
            if child.type == "inherit_path":
                classes.append(_get_node_text(child))

        if not classes:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)

        for cls in classes:
            symbol_id = _make_symbol_id(rel_path, cls, "inherit")

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
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"inherit {cls}",
                meta={"class": cls},
            )
            self._symbols.append(symbol)

            # Add inherit edge
            edge = Edge.create(
                src=f"bitbake:{rel_path}",
                dst=f"bitbake:class:{cls}",
                edge_type="inherits",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                confidence=1.0,
                evidence_lang="bitbake",
            )
            self._edges.append(edge)

    def _extract_function(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a shell function definition (task)."""
        func_name = None

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
                break

        if not func_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, func_name, "task")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Determine task type based on name
        task_type = "custom"
        if func_name.startswith("do_"):
            task_type = "standard"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=func_name,
            kind="task",
            language="bitbake",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{func_name}()",
            meta={"task_type": task_type},
        )
        self._symbols.append(symbol)

    def _extract_python_function(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a Python function definition."""
        func_name = None

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
                break

        if not func_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, func_name, "python_task")

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
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"python {func_name}()",
            meta={"language": "python"},
        )
        self._symbols.append(symbol)

    def _extract_addtask(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract an addtask directive."""
        task_name = None

        for child in node.children:
            if child.type == "identifier":
                task_name = _get_node_text(child)
                break

        if not task_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, task_name, "addtask")

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
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"addtask {task_name}",
            meta={},
        )
        self._symbols.append(symbol)


def analyze_bitbake(repo_root: Path) -> BitBakeAnalysisResult:
    """Analyze BitBake files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        BitBakeAnalysisResult containing extracted symbols and edges
    """
    if not is_bitbake_tree_sitter_available():
        warnings.warn(
            "BitBake analysis skipped: tree-sitter-bitbake not available",
            UserWarning,
            stacklevel=2,
        )
        return BitBakeAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "bitbake", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-bitbake not available",
        )

    analyzer = BitBakeAnalyzer(repo_root)
    return analyzer.analyze()
