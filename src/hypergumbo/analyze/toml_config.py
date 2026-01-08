"""TOML configuration file analyzer using tree-sitter-toml.

This module parses TOML configuration files (Cargo.toml, pyproject.toml, etc.)
to extract structure information useful for understanding project configuration.

How It Works
------------
Uses tree-sitter-toml to parse TOML files and extract:
- Table definitions (sections like [package], [dependencies])
- Array of tables (like [[bin]], [[test]])
- Key-value bindings for important configuration
- Dependencies (from Cargo.toml and pyproject.toml)

The analyzer produces Symbols for:
- Tables and nested tables
- Dependencies (Rust crates, Python packages)
- Binary targets ([[bin]])
- Library configuration ([lib])
- Workspaces ([workspace])
- Project metadata ([project] in pyproject.toml)

Why This Design
---------------
- TOML is used extensively for Rust (Cargo.toml), Python (pyproject.toml),
  and other configuration files
- Extracting dependencies helps understand project structure
- Binary/library targets help understand build outputs
- Workspace detection identifies monorepo structures
"""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    """Generate a unique symbol ID."""
    key = f"toml:{path}:{line}:{name}:{kind}"
    return f"toml:sha256:{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate a unique edge ID."""
    key = f"toml:{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(key.encode()).hexdigest()[:16]}"


PASS_ID = "toml-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def is_toml_tree_sitter_available() -> bool:
    """Check if tree-sitter-toml is available."""
    try:
        import tree_sitter
        import tree_sitter_toml

        tree_sitter.Language(tree_sitter_toml.language())
        return True
    except (ImportError, OSError, Exception):  # pragma: no cover
        return False  # pragma: no cover


@dataclass
class TomlAnalysisResult:
    """Result of TOML analysis."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None
    run: AnalysisRun | None = None


def find_toml_files(root: Path) -> Iterator[Path]:
    """Find all TOML files in a directory tree, excluding vendor dirs."""
    yield from find_files(root, ["*.toml"])


def _get_key_text(node) -> str:
    """Extract key text from a key node."""
    if node.type == "bare_key":
        return node.text.decode("utf-8")
    elif node.type == "quoted_key":  # pragma: no cover - rare in practice
        text = node.text.decode("utf-8")  # pragma: no cover - rare in practice
        return text.strip('"').strip("'")  # pragma: no cover - rare in practice
    elif node.type == "dotted_key":
        # Join dotted key parts
        parts = []
        for child in node.children:
            if child.type in ("bare_key", "quoted_key"):
                parts.append(_get_key_text(child))
        return ".".join(parts)
    return node.text.decode("utf-8") if node.text else ""  # pragma: no cover


def _get_string_value(node) -> str:
    """Extract string value from a string node."""
    if node is None:  # pragma: no cover - defensive
        return ""  # pragma: no cover - defensive
    text = node.text.decode("utf-8") if node.text else ""
    # Strip quotes - various quote styles supported by TOML
    if text.startswith('"""') and text.endswith('"""'):  # pragma: no cover - multi-line
        return text[3:-3]  # pragma: no cover - multi-line
    elif text.startswith("'''") and text.endswith("'''"):  # pragma: no cover - multi-line
        return text[3:-3]  # pragma: no cover - multi-line
    elif text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    elif text.startswith("'") and text.endswith("'"):  # pragma: no cover - literal
        return text[1:-1]  # pragma: no cover - literal
    return text  # pragma: no cover - fallback


def _extract_table_name(node) -> str:
    """Extract the full table name from a table or table_array_element."""
    # Find the key nodes between [ ] or [[ ]]
    parts = []
    for child in node.children:
        if child.type in ("bare_key", "quoted_key", "dotted_key"):
            parts.append(_get_key_text(child))
    return ".".join(parts)


def _find_pair_value(table_node, key: str) -> str | None:
    """Find the value of a specific key in a table."""
    for child in table_node.children:
        if child.type == "pair":
            pair_key = None
            pair_value = None
            for pair_child in child.children:
                if pair_child.type in ("bare_key", "quoted_key", "dotted_key"):
                    pair_key = _get_key_text(pair_child)
                elif pair_child.type == "string":
                    pair_value = _get_string_value(pair_child)
            if pair_key == key and pair_value:
                return pair_value
    return None  # pragma: no cover - key not found


def _process_toml_tree(
    root,
    symbols: list[Symbol],
    edges: list[Edge],
    rel_path: str,
    content: str,
    is_cargo: bool,
    is_pyproject: bool,
) -> None:
    """Process a TOML AST tree iteratively and extract symbols and edges."""
    for node in iter_tree(root):
        if node.type == "table":
            table_name = _extract_table_name(node)

            # Determine kind based on table name
            kind = "table"
            name = table_name

            if table_name == "workspace":
                kind = "workspace"
            elif table_name == "lib":
                kind = "library"
                # Try to get library name
                lib_name = _find_pair_value(node, "name")
                if lib_name:
                    name = lib_name
            elif table_name == "project" and is_pyproject:
                kind = "project"
                proj_name = _find_pair_value(node, "name")
                if proj_name:
                    name = proj_name
            elif table_name == "package" and is_cargo:
                kind = "package"
                pkg_name = _find_pair_value(node, "name")
                if pkg_name:
                    name = pkg_name

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, name, kind)
            node_bytes = content[node.start_byte : node.end_byte].encode()

            symbols.append(
                Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(node_bytes).hexdigest()[:16],
                    kind=kind,
                    name=name,
                    path=rel_path,
                    language="toml",
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
            )

            # Process dependencies if this is a dependency table
            if is_cargo and table_name in (
                "dependencies",
                "dev-dependencies",
                "build-dependencies",
            ):
                _extract_cargo_dependencies(node, rel_path, symbols, content)
            elif is_cargo and table_name.endswith(".dependencies"):  # pragma: no cover - nested deps
                _extract_cargo_dependencies(node, rel_path, symbols, content)  # pragma: no cover
            elif is_pyproject and table_name == "project":
                _extract_pyproject_dependencies(node, rel_path, symbols, content)

        elif node.type == "table_array_element":
            table_name = _extract_table_name(node)

            # Determine kind based on table array name
            kind = "table_array"
            name = table_name
            target_path = None  # Source file path for build targets

            if table_name == "bin" and is_cargo:
                kind = "binary"
                bin_name = _find_pair_value(node, "name")
                if bin_name:
                    name = bin_name
                target_path = _find_pair_value(node, "path")
            elif table_name == "test" and is_cargo:
                kind = "test"
                test_name = _find_pair_value(node, "name")
                if test_name:
                    name = test_name
                target_path = _find_pair_value(node, "path")
            elif table_name == "example" and is_cargo:
                kind = "example"
                ex_name = _find_pair_value(node, "name")
                if ex_name:
                    name = ex_name
                target_path = _find_pair_value(node, "path")
            elif table_name == "bench" and is_cargo:
                kind = "benchmark"
                bench_name = _find_pair_value(node, "name")
                if bench_name:
                    name = bench_name
                target_path = _find_pair_value(node, "path")

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, name, kind)
            node_bytes = content[node.start_byte : node.end_byte].encode()

            # Build meta with path if present
            meta = None
            if target_path:
                meta = {"path": target_path}

            symbols.append(
                Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(node_bytes).hexdigest()[:16],
                    kind=kind,
                    name=name,
                    path=rel_path,
                    language="toml",
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta,
                )
            )

            # Create edge from build target to source file
            if target_path:
                edge_id = _make_edge_id(symbol_id, target_path, "defines_target")
                edges.append(
                    Edge(
                        id=edge_id,
                        src=symbol_id,
                        dst=target_path,
                        edge_type="defines_target",
                        line=start_line,
                        confidence=1.0,
                        origin=PASS_ID,
                    )
                )


def analyze_toml_files(root: Path) -> TomlAnalysisResult:
    """Analyze TOML files in a directory.

    Args:
        root: Directory to analyze (can be a file path for single file)

    Returns:
        TomlAnalysisResult containing symbols and edges
    """
    if not is_toml_tree_sitter_available():  # pragma: no cover - toml installed
        return TomlAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-toml not installed",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_toml

    lang = tree_sitter.Language(tree_sitter_toml.language())
    parser = tree_sitter.Parser(lang)

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []
    start_time = time.time()

    # Handle single file or directory
    if root.is_file():  # pragma: no cover - single file mode
        toml_files = [root] if root.suffix == ".toml" else []  # pragma: no cover
    else:
        toml_files = list(set(find_toml_files(root)))

    for toml_file in toml_files:
        try:
            content = toml_file.read_text(encoding="utf-8", errors="replace")
            tree = parser.parse(bytes(content, "utf-8"))
            files_analyzed += 1

            rel_path = str(toml_file.relative_to(root) if root.is_dir() else toml_file.name)
            is_cargo = toml_file.name == "Cargo.toml"
            is_pyproject = toml_file.name == "pyproject.toml"

            # Process the tree using iterative traversal
            _process_toml_tree(
                tree.root_node, symbols, edges, rel_path, content, is_cargo, is_pyproject
            )

        except (OSError, IOError):  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            continue  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return TomlAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


def _extract_cargo_dependencies(
    table_node, rel_path: str, symbols: list[Symbol], content: str
):
    """Extract dependencies from a Cargo.toml dependencies table."""
    for child in table_node.children:
        if child.type == "pair":
            dep_name = None
            for pair_child in child.children:
                if pair_child.type in ("bare_key", "quoted_key"):
                    dep_name = _get_key_text(pair_child)
                    break

            if dep_name:
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, dep_name, "dependency")
                node_bytes = content[child.start_byte : child.end_byte].encode()

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=dep_name,
                        fingerprint=hashlib.sha256(node_bytes).hexdigest()[:16],
                        kind="dependency",
                        name=dep_name,
                        path=rel_path,
                        language="toml",
                        span=Span(
                            start_line=start_line,
                            start_col=child.start_point[1],
                            end_line=end_line,
                            end_col=child.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )


def _parse_pyproject_dependency(dep_str: str) -> str:
    """Parse a PEP 508 dependency string and extract the package name.

    Examples:
        "requests>=2.0" -> "requests"
        "click" -> "click"
        "pytest[testing]>=7.0" -> "pytest"
        "numpy>=1.0,<2.0" -> "numpy"
    """
    import re
    # PEP 508: package name is alphanumeric, underscores, hyphens
    # followed by optional extras, version specifiers, etc.
    match = re.match(r"^([a-zA-Z0-9_-]+)", dep_str.strip())
    if match:
        return match.group(1)
    return dep_str.strip()  # pragma: no cover - fallback


def _extract_pyproject_dependencies(
    table_node, rel_path: str, symbols: list[Symbol], content: str
):
    """Extract dependencies from a pyproject.toml [project] table.

    Looks for:
    - dependencies = ["pkg1", "pkg2>=1.0"]
    """
    for child in table_node.children:
        if child.type == "pair":
            pair_key = None
            pair_value = None
            for pair_child in child.children:
                if pair_child.type in ("bare_key", "quoted_key", "dotted_key"):
                    pair_key = _get_key_text(pair_child)
                elif pair_child.type == "array":
                    pair_value = pair_child

            if pair_key == "dependencies" and pair_value is not None:
                # Extract each dependency from the array
                for elem in pair_value.children:
                    if elem.type == "string":
                        dep_str = _get_string_value(elem)
                        dep_name = _parse_pyproject_dependency(dep_str)

                        start_line = elem.start_point[0] + 1
                        end_line = elem.end_point[0] + 1
                        symbol_id = _make_symbol_id(
                            rel_path, start_line, dep_name, "dependency"
                        )
                        node_bytes = content[elem.start_byte : elem.end_byte].encode()

                        symbols.append(
                            Symbol(
                                id=symbol_id,
                                stable_id=None,
                                shape_id=None,
                                canonical_name=dep_name,
                                fingerprint=hashlib.sha256(node_bytes).hexdigest()[:16],
                                kind="dependency",
                                name=dep_name,
                                path=rel_path,
                                language="toml",
                                span=Span(
                                    start_line=start_line,
                                    start_col=elem.start_point[1],
                                    end_line=end_line,
                                    end_col=elem.end_point[1],
                                ),
                                origin=PASS_ID,
                            )
                        )
