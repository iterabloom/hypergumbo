"""CMake analysis pass using tree-sitter-cmake.

This analyzer uses tree-sitter to parse CMakeLists.txt files and extract:
- Project definitions
- Library targets (add_library)
- Executable targets (add_executable)
- Function definitions
- Macro definitions
- Target link dependencies
- Subdirectory includes

If tree-sitter-cmake is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-cmake is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all target/function/macro definitions
   - Pass 2: Resolve target_link_libraries and create edges
4. Create links edges for library dependencies

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-cmake package for grammar
- Two-pass allows cross-file target resolution
- Build-system-specific: targets, functions, macros are first-class
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "cmake-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_cmake_files(repo_root: Path) -> Iterator[Path]:
    """Yield all CMake files in the repository."""
    yield from find_files(repo_root, ["CMakeLists.txt", "*.cmake"])


def is_cmake_tree_sitter_available() -> bool:
    """Check if tree-sitter with CMake grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_cmake") is None:
        return False  # pragma: no cover
    return True


@dataclass
class CMakeAnalysisResult:
    """Result of analyzing CMake files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"cmake:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_command_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the command name from a normal_command node."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source).lower()
    return None  # pragma: no cover


def _get_arguments(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract arguments from argument_list in a command."""
    args = []
    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "argument":
                    args.append(_node_text(arg, source))
    return args


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function/macro name from function_command or macro_command."""
    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "argument":
                    return _node_text(arg, source)
    return None  # pragma: no cover


def _extract_cmake_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function/macro signature from function_command or macro_command.

    CMake functions are defined as: function(name ARG1 ARG2 ...)
    Returns signature in format: (ARG1, ARG2, ...)
    """
    params: list[str] = []
    found_name = False

    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "argument":
                    if not found_name:
                        # First argument is the function name, skip it
                        found_name = True
                    else:
                        params.append(_node_text(arg, source))

    return f"({', '.join(params)})"


def _process_cmake_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    target_registry: dict[str, str],
) -> None:
    """Process CMake AST tree to extract symbols and edges.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        target_registry: Registry mapping target names to symbol IDs
    """
    for node in iter_tree(root_node):
        if node.type == "normal_command":
            cmd_name = _get_command_name(node, source)
            args = _get_arguments(node, source)

            if cmd_name == "project" and args:
                # Project definition
                project_name = args[0]
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, project_name, "project")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=project_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="project",
                    name=project_name,
                    path=rel_path,
                    language="cmake",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[project_name.lower()] = symbol_id

            elif cmd_name == "add_library" and args:
                # Library target
                lib_name = args[0]
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, lib_name, "library")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=lib_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="library",
                    name=lib_name,
                    path=rel_path,
                    language="cmake",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[lib_name.lower()] = symbol_id

            elif cmd_name == "add_executable" and args:
                # Executable target
                exe_name = args[0]
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, exe_name, "executable")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=exe_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="executable",
                    name=exe_name,
                    path=rel_path,
                    language="cmake",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[exe_name.lower()] = symbol_id

            elif cmd_name == "target_link_libraries" and len(args) >= 2:
                # Link dependency edges
                target_name = args[0]
                start_line = node.start_point[0] + 1

                # Get source target ID
                src_id = target_registry.get(target_name.lower())
                if src_id:
                    # Skip visibility keywords
                    visibility_keywords = {"public", "private", "interface"}
                    for dep in args[1:]:
                        if dep.lower() in visibility_keywords:
                            continue
                        # Look up dependency
                        if dep.lower() in target_registry:
                            dst_id = target_registry[dep.lower()]
                            confidence = 0.90
                        else:
                            # External library reference
                            dst_id = f"cmake:external:{dep}:library"
                            confidence = 0.70

                        edge = Edge(
                            id=_make_edge_id(src_id, dst_id, "links"),
                            src=src_id,
                            dst=dst_id,
                            edge_type="links",
                            line=start_line,
                            confidence=confidence,
                            origin=PASS_ID,
                            evidence_type="cmake_target_link",
                        )
                        edges.append(edge)

            elif cmd_name == "add_subdirectory" and args:
                # Subdirectory include
                subdir = args[0]
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, subdir, "subdirectory")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=subdir,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="subdirectory",
                    name=subdir,
                    path=rel_path,
                    language="cmake",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

            elif cmd_name == "find_package" and args:
                # External package dependency
                pkg_name = args[0]
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, "package")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=pkg_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="package",
                    name=pkg_name,
                    path=rel_path,
                    language="cmake",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

        elif node.type == "function_def":
            # Function definition
            for child in node.children:
                if child.type == "function_command":
                    func_name = _get_function_name(child, source)
                    if func_name:
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        symbol_id = _make_symbol_id(rel_path, start_line, end_line, func_name, "function")

                        sym = Symbol(
                            id=symbol_id,
                            stable_id=None,
                            shape_id=None,
                            canonical_name=func_name,
                            fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                            kind="function",
                            name=func_name,
                            path=rel_path,
                            language="cmake",
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            signature=_extract_cmake_signature(child, source),
                        )
                        symbols.append(sym)
                        target_registry[func_name.lower()] = symbol_id
                    break

        elif node.type == "macro_def":
            # Macro definition
            for child in node.children:
                if child.type == "macro_command":
                    macro_name = _get_function_name(child, source)
                    if macro_name:
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        symbol_id = _make_symbol_id(rel_path, start_line, end_line, macro_name, "macro")

                        sym = Symbol(
                            id=symbol_id,
                            stable_id=None,
                            shape_id=None,
                            canonical_name=macro_name,
                            fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                            kind="macro",
                            name=macro_name,
                            path=rel_path,
                            language="cmake",
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            signature=_extract_cmake_signature(child, source),
                        )
                        symbols.append(sym)
                        target_registry[macro_name.lower()] = symbol_id
                    break


def analyze_cmake_files(repo_root: Path) -> CMakeAnalysisResult:
    """Analyze CMake files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        CMakeAnalysisResult with symbols and edges
    """
    if not is_cmake_tree_sitter_available():  # pragma: no cover
        return CMakeAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-cmake not installed (pip install tree-sitter-cmake)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_cmake

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Target registry for cross-file resolution: name -> symbol_id
    target_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_cmake.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize CMake parser: {e}")
        return CMakeAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    cmake_files = list(find_cmake_files(repo_root))

    for cmake_path in cmake_files:
        try:
            rel_path = str(cmake_path.relative_to(repo_root))
            source = cmake_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_cmake_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                target_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {cmake_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return CMakeAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
