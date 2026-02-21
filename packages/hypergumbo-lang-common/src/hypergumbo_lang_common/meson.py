"""Meson build system analyzer using tree-sitter.

This module provides static analysis for Neson build files, extracting symbols
(projects, executables, libraries, variables) and edges (dependencies, subdirs).

Meson is a modern build system designed to be fast and user-friendly. It uses
a simple declarative language in meson.build files that define build targets
and their dependencies.

Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
The analyze() method is fully overridden because Meson requires:
- Non-standard file patterns (meson.build, meson_options.txt, meson.options)
- Stateful target registry for cross-file dependency resolution
- Empty result (no run) when no Meson files found

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
from pathlib import Path
from typing import ClassVar, Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, TreeSitterAnalyzer, make_symbol_id, populate_docstrings_from_tree
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("meson")


def find_meson_files(root: Path) -> Iterator[Path]:
    """Find all Meson build files in the given directory."""
    # Standard meson.build files
    for path in find_files(root, ["meson.build"]):
        if path.is_file():
            yield path
    # meson_options.txt files
    for path in find_files(root, ["meson_options.txt"]):
        if path.is_file():
            yield path
    # meson.options files (newer format)
    for path in find_files(root, ["meson.options"]):
        if path.is_file():
            yield path


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


def _extract_symbols_recursive(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
    target_registry: dict[str, str],
    run_id: str,
    analyzer: "MesonAnalyzer",
) -> None:
    """Extract symbols from a syntax tree node recursively."""
    if node.type == "normal_command":
        cmd_name = _get_command_name(node)
        if cmd_name:
            if cmd_name == "project":
                # Project definition
                proj_name = _get_first_argument(node)
                if proj_name:
                    rel_path = str(path.relative_to(repo_root))
                    sym = Symbol(
                        id=make_symbol_id(
                            "meson", rel_path,
                            node.start_point[0] + 1, node.end_point[0] + 1,
                            proj_name, "project",
                        ),
                        stable_id=analyzer.compute_stable_id(node, kind="project"),
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
                    symbols.append(sym)

            elif _is_target_command(cmd_name):
                # Build target
                target_name = _get_first_argument(node)
                if target_name:
                    rel_path = str(path.relative_to(repo_root))

                    # Determine kind based on command
                    if cmd_name == "executable":
                        kind = "executable"
                    elif cmd_name in ("library", "shared_library", "static_library", "both_libraries"):
                        kind = "library"
                    else:
                        kind = "target"

                    sym = Symbol(
                        id=make_symbol_id(
                            "meson", rel_path,
                            node.start_point[0] + 1, node.end_point[0] + 1,
                            target_name, kind,
                        ),
                        stable_id=analyzer.compute_stable_id(node, kind=kind),
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
                    symbols.append(sym)

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
                            target_kind = "library" if "library" in cmd_name else "executable"
                            rel_path = str(path.relative_to(repo_root))
                            target_id = make_symbol_id(
                                "meson", rel_path,
                                child.start_point[0] + 1, child.end_point[0] + 1,
                                target_name, target_kind,
                            )
                            target_registry[var_name] = target_id

    # Recursively process children
    for child in node.children:
        _extract_symbols_recursive(child, path, repo_root, symbols, target_registry, run_id, analyzer)


def _extract_edges_recursive(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    edges: list[Edge],
    symbols: list[Symbol],
    target_registry: dict[str, str],
    run_id: str,
) -> None:
    """Extract edges from a syntax tree node recursively."""
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
                                dep_id = target_registry.get(dep_var)
                                if dep_id:
                                    # Create dependency edge
                                    src_kind = "library" if "library" in cmd_name else "executable"
                                    src_id = make_symbol_id(
                                        "meson", str(path.relative_to(repo_root)),
                                        child.start_point[0] + 1, child.end_point[0] + 1,
                                        target_name, src_kind,
                                    )
                                    edge = Edge.create(
                                        src=src_id,
                                        dst=dep_id,
                                        edge_type="depends_on",
                                        line=node.start_point[0] + 1,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        evidence_type="build_dependency",
                                        confidence=1.0,
                                        evidence_lang="meson",
                                    )
                                    edges.append(edge)

    elif node.type == "normal_command":
        cmd_name = _get_command_name(node)
        if cmd_name == "subdir":
            # subdir() includes another meson.build
            subdir_name = _get_first_argument(node)
            if subdir_name:
                # Create include edge (but only if we have a project symbol)
                if symbols:
                    project_sym = next(
                        (s for s in symbols if s.kind == "project"),
                        None
                    )
                    if project_sym:
                        edge = Edge.create(
                            src=project_sym.id,
                            dst=f"meson:subdir:{subdir_name}",
                            edge_type="includes",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="subdir_include",
                            confidence=1.0,
                            evidence_lang="meson",
                        )
                        edges.append(edge)

    # Recursively process children
    for child in node.children:
        _extract_edges_recursive(child, path, repo_root, edges, symbols, target_registry, run_id)


class MesonAnalyzer(TreeSitterAnalyzer):
    """Meson build system analyzer using tree-sitter-language-pack.

    Overrides analyze() entirely because Meson requires:
    - Non-standard file patterns (meson.build, meson_options.txt, meson.options)
    - Stateful target registry for cross-file dependency resolution
    - Empty result (no run) when no Meson files found
    """

    lang = "meson"
    file_patterns: ClassVar[list[str]] = ["meson.build", "meson_options.txt", "meson.options"]
    language_pack_name = "meson"

    def analyze(self, repo_root: Path, max_files=None) -> AnalysisResult:
        """Analyze all Meson files in the repository."""
        if not self._check_grammar_available():
            warnings.warn(
                "Meson analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module

        start_time = time.time()
        run_id = f"uuid:{uuid_module.uuid4()}"

        parser = self._create_parser()
        meson_files = list(find_meson_files(repo_root))

        if not meson_files:
            return AnalysisResult()

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        target_registry: dict[str, str] = {}  # var_name -> symbol_id

        # Pass 1: Collect all symbols
        for path in meson_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                before = len(symbols)
                _extract_symbols_recursive(
                    tree.root_node, path, repo_root, symbols, target_registry, run_id, self
                )
                populate_docstrings_from_tree(tree.root_node, content, symbols[before:])
            except Exception:  # pragma: no cover
                pass

        # Pass 2: Extract edges (dependencies between targets)
        for path in meson_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                _extract_edges_recursive(
                    tree.root_node, path, repo_root, edges, symbols, target_registry, run_id
                )
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "meson", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = MesonAnalyzer()


def is_meson_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Meson support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("meson")
def analyze_meson(repo_root: Path) -> AnalysisResult:
    """Analyze Meson build files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
