"""Meson build system analyzer using tree-sitter.

This module provides static analysis for Meson build files, extracting symbols
(projects, executables, libraries, variables) and edges (dependencies, subdirs).

Meson is a modern build system designed to be fast and user-friendly. It uses
a simple declarative language in meson.build files that define build targets
and their dependencies.

Implementation approach:
- Uses tree-sitter-language-pack for Meson grammar
- Extracts project definitions, build targets, and variable assignments
- Detects dependency relationships between targets

Key constructs extracted:
- project('name', ...) - project definition
- executable('name', ...) - executable target
- library/shared_library/static_library('name', ...) - library targets
- var = command(...) - variable assignments
- subdir('path') - subdirectory includes
- dependencies: [...] - target dependencies
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "meson.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class MesonAnalysisResult:
    """Result of analyzing Meson files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_meson_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Meson support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("meson")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_meson_files(root: Path) -> Iterator[Path]:
    """Find all Meson build files in the given directory."""
    # Standard meson.build files
    for path in root.rglob("meson.build"):
        if path.is_file():
            yield path
    # meson_options.txt files
    for path in root.rglob("meson_options.txt"):
        if path.is_file():
            yield path
    # meson.options files (newer format)
    for path in root.rglob("meson.options"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Meson symbol."""
    rel_path = path.relative_to(repo_root)
    return f"meson:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier child of a node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_string_value(node: "tree_sitter.Node") -> Optional[str]:
    """Get the string value from a variableunit node."""
    for child in node.children:
        if child.type == "string":
            text = _get_node_text(child)
            # Remove quotes
            if text.startswith("'") and text.endswith("'"):
                return text[1:-1]
            elif text.startswith('"') and text.endswith('"'):  # pragma: no cover
                return text[1:-1]
    return None  # pragma: no cover


def _get_command_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a normal_command node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_first_argument(node: "tree_sitter.Node") -> Optional[str]:
    """Get the first string argument from a command."""
    for child in node.children:
        if child.type == "variableunit":
            value = _get_string_value(child)
            if value is not None:
                return value
    return None  # pragma: no cover


def _is_target_command(name: str) -> bool:
    """Check if a command creates a build target."""
    return name in {
        "executable",
        "library",
        "shared_library",
        "static_library",
        "both_libraries",
        "custom_target",
        "run_target",
    }


def _get_dependencies_from_command(node: "tree_sitter.Node") -> list[str]:
    """Extract dependency names from a command's 'dependencies' argument."""
    deps = []
    for child in node.children:
        if child.type == "pair":
            # Check if this is a 'dependencies' pair
            key_id = _get_identifier(child)
            if key_id == "dependencies":
                # Find the list or single dependency
                for pair_child in child.children:
                    # Handle list: [dep1, dep2]
                    if pair_child.type == "list":
                        for list_child in pair_child.children:
                            if list_child.type == "identifier":
                                deps.append(_get_node_text(list_child))
                            elif list_child.type == "variableunit":  # pragma: no cover
                                for vc in list_child.children:
                                    if vc.type == "identifier":
                                        deps.append(_get_node_text(vc))
                    # Handle array: [dep1, dep2] (alternate grammar)
                    elif pair_child.type == "array":  # pragma: no cover
                        for array_child in pair_child.children:
                            if array_child.type == "identifier":
                                deps.append(_get_node_text(array_child))
                            elif array_child.type == "variableunit":
                                for vc in array_child.children:
                                    if vc.type == "identifier":
                                        deps.append(_get_node_text(vc))
                    # Handle single dependency reference
                    elif pair_child.type == "identifier":
                        deps.append(_get_node_text(pair_child))
                    elif pair_child.type == "variableunit":  # pragma: no cover
                        for vc in pair_child.children:
                            if vc.type == "identifier":
                                deps.append(_get_node_text(vc))
    return deps


class MesonAnalyzer:
    """Analyzer for Meson build files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._target_registry: dict[str, str] = {}  # var_name -> symbol_id
        self._run_id: str = ""

    def analyze(self) -> MesonAnalysisResult:
        """Analyze all Meson files in the repository."""
        if not is_meson_tree_sitter_available():
            warnings.warn(
                "Meson analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return MesonAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("meson")
        meson_files = list(find_meson_files(self.repo_root))

        if not meson_files:
            return MesonAnalysisResult()

        # Pass 1: Collect all symbols
        for path in meson_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Pass 2: Extract edges (dependencies between targets)
        for path in meson_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "meson", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return MesonAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "normal_command":
            cmd_name = _get_command_name(node)
            if cmd_name:
                if cmd_name == "project":
                    # Project definition
                    proj_name = _get_first_argument(node)
                    if proj_name:
                        rel_path = str(path.relative_to(self.repo_root))
                        sym = Symbol(
                            id=_make_stable_id(path, self.repo_root, proj_name, "project"),
                            stable_id=_make_stable_id(path, self.repo_root, proj_name, "project"),
                            name=proj_name,
                            kind="project",
                            language="meson",
                            path=rel_path,
                            span=Span(
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                        )
                        self.symbols.append(sym)

                elif _is_target_command(cmd_name):
                    # Build target
                    target_name = _get_first_argument(node)
                    if target_name:
                        rel_path = str(path.relative_to(self.repo_root))

                        # Determine kind based on command
                        if cmd_name == "executable":
                            kind = "executable"
                        elif cmd_name in ("library", "shared_library", "static_library", "both_libraries"):
                            kind = "library"
                        else:
                            kind = "target"

                        sym = Symbol(
                            id=_make_stable_id(path, self.repo_root, target_name, kind),
                            stable_id=_make_stable_id(path, self.repo_root, target_name, kind),
                            name=target_name,
                            kind=kind,
                            language="meson",
                            path=rel_path,
                            span=Span(
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            meta={"command": cmd_name},
                        )
                        self.symbols.append(sym)

        elif node.type == "operatorunit":
            # Variable assignment: var = command(...)
            var_name = _get_identifier(node)
            if var_name:
                # Find the command being assigned
                for child in node.children:
                    if child.type == "normal_command":
                        cmd_name = _get_command_name(child)
                        if cmd_name and _is_target_command(cmd_name):
                            target_name = _get_first_argument(child)
                            if target_name:
                                # Register this variable as pointing to a target
                                target_id = _make_stable_id(
                                    path, self.repo_root, target_name,
                                    "library" if "library" in cmd_name else "executable"
                                )
                                self._target_registry[var_name] = target_id

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "operatorunit":
            # Check for target with dependencies
            var_name = _get_identifier(node)
            if var_name:
                for child in node.children:
                    if child.type == "normal_command":
                        cmd_name = _get_command_name(child)
                        if cmd_name and _is_target_command(cmd_name):
                            target_name = _get_first_argument(child)
                            if target_name:
                                # Get dependencies
                                deps = _get_dependencies_from_command(child)
                                for dep_var in deps:
                                    dep_id = self._target_registry.get(dep_var)
                                    if dep_id:
                                        # Create dependency edge
                                        src_kind = "library" if "library" in cmd_name else "executable"
                                        src_id = _make_stable_id(
                                            path, self.repo_root, target_name, src_kind
                                        )
                                        edge = Edge.create(
                                            src=src_id,
                                            dst=dep_id,
                                            edge_type="depends_on",
                                            line=node.start_point[0] + 1,
                                            origin=PASS_ID,
                                            origin_run_id=self._run_id,
                                            evidence_type="build_dependency",
                                            confidence=1.0,
                                            evidence_lang="meson",
                                        )
                                        self.edges.append(edge)

        elif node.type == "normal_command":
            cmd_name = _get_command_name(node)
            if cmd_name == "subdir":
                # subdir() includes another meson.build
                subdir_name = _get_first_argument(node)
                if subdir_name:
                    # Create include edge (but only if we have a project symbol)
                    if self.symbols:
                        project_sym = next(
                            (s for s in self.symbols if s.kind == "project"),
                            None
                        )
                        if project_sym:
                            edge = Edge.create(
                                src=project_sym.id,
                                dst=f"meson:subdir:{subdir_name}",
                                edge_type="includes",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=self._run_id,
                                evidence_type="subdir_include",
                                confidence=1.0,
                                evidence_lang="meson",
                            )
                            self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)


def analyze_meson(repo_root: Path) -> MesonAnalysisResult:
    """Analyze Meson build files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        MesonAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = MesonAnalyzer(repo_root)
    return analyzer.analyze()
