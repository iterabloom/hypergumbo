"""CSS stylesheet analysis using tree-sitter-css.

This module parses CSS files to extract structure information useful for
understanding project styling and theming.

How It Works
------------
Uses tree-sitter-css to parse CSS files and extract:
- @import statements (for cross-file dependencies)
- CSS variables (custom properties like --primary-color)
- @keyframes animations
- @media queries (breakpoints)
- @font-face declarations

The analyzer produces Symbols for:
- import: @import rules
- variable: CSS custom properties (--var-name)
- keyframes: @keyframes animations
- media: @media query breakpoints
- font_face: @font-face declarations

Why This Design
---------------
- CSS analysis helps understand theming and styling patterns
- @import tracking enables cross-file dependency resolution
- Variable detection helps identify design system patterns
- Useful for frontend-heavy applications
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
    key = f"css:{path}:{line}:{name}:{kind}"
    return f"css:sha256:{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate a unique edge ID."""
    key = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(key.encode()).hexdigest()[:16]}"


PASS_ID = "css-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def is_css_tree_sitter_available() -> bool:
    """Check if tree-sitter-css is available."""
    try:
        import tree_sitter
        import tree_sitter_css

        tree_sitter.Language(tree_sitter_css.language())
        return True
    except (ImportError, OSError, Exception):  # pragma: no cover
        return False  # pragma: no cover


@dataclass
class CSSAnalysisResult:
    """Result of CSS analysis."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None
    run: AnalysisRun | None = None


def find_css_files(root: Path) -> Iterator[Path]:
    """Find all CSS files in a directory tree, excluding vendor dirs."""
    yield from find_files(root, ["*.css"])


def _get_node_text(node, source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _extract_import_path(node, source: bytes) -> str | None:
    """Extract the path from an @import statement."""
    for child in node.children:
        if child.type == "string_value":
            text = _get_node_text(child, source)
            return text.strip("'\"")
        elif child.type == "call_expression":
            # url() function
            for sub in child.children:
                if sub.type == "arguments":
                    for arg in sub.children:
                        if arg.type == "string_value":
                            text = _get_node_text(arg, source)
                            return text.strip("'\"")
    return None  # pragma: no cover - defensive


def _extract_variable_name(node, source: bytes) -> str | None:
    """Extract the variable name from a declaration."""
    for child in node.children:
        if child.type == "property_name":
            name = _get_node_text(child, source)
            if name.startswith("--"):
                return name
    return None


def _extract_keyframes_name(node, source: bytes) -> str | None:
    """Extract the name from a @keyframes rule."""
    for child in node.children:
        if child.type == "keyframes_name":
            return _get_node_text(child, source)
    return None  # pragma: no cover - defensive


def _extract_media_query(node, source: bytes) -> str:
    """Extract the media query string."""
    # Get the query part between @media and {
    text = _get_node_text(node, source)
    # Extract just the query part
    if text.startswith("@media"):
        brace_idx = text.find("{")
        if brace_idx > 0:
            query = text[6:brace_idx].strip()
            # Truncate long queries
            if len(query) > 50:
                return query[:47] + "..."  # pragma: no cover - edge case
            return query
    return "unknown"  # pragma: no cover - defensive


def _extract_font_family(node, source: bytes) -> str | None:
    """Extract the font-family from a @font-face rule."""
    for child in node.children:
        if child.type == "block":
            for decl in child.children:
                if decl.type == "declaration":
                    prop_name = None
                    for part in decl.children:
                        if part.type == "property_name":
                            prop_name = _get_node_text(part, source)
                        elif prop_name == "font-family":
                            # Get the value
                            for val in decl.children:
                                if val.type in ("string_value", "plain_value"):
                                    return _get_node_text(val, source).strip("'\"")
    return None  # pragma: no cover - defensive


def _process_css_tree(
    root_node,
    symbols: list[Symbol],
    edges: list[Edge],
    rel_path: str,
    source: bytes,
    file_symbol_id: str,
) -> None:
    """Process a CSS AST tree and extract symbols using iterative traversal."""
    for node in iter_tree(root_node):
        if node.type == "import_statement":
            import_path = _extract_import_path(node, source)
            if import_path:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, import_path, "import")
                fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=import_path,
                        fingerprint=fingerprint,
                        kind="import",
                        name=import_path,
                        path=rel_path,
                        language="css",
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )

                # Create import edge from file to imported path
                edge_id = _make_edge_id(file_symbol_id, import_path, "imports")
                edges.append(
                    Edge(
                        id=edge_id,
                        src=file_symbol_id,
                        dst=import_path,
                        edge_type="imports",
                        line=start_line,
                        confidence=1.0,
                        origin=PASS_ID,
                    )
                )

        elif node.type == "declaration":
            var_name = _extract_variable_name(node, source)
            if var_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, var_name, "variable")
                fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=var_name,
                        fingerprint=fingerprint,
                        kind="variable",
                        name=var_name,
                        path=rel_path,
                        language="css",
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )

        elif node.type == "keyframes_statement":
            name = _extract_keyframes_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, name, "keyframes")
                fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=name,
                        fingerprint=fingerprint,
                        kind="keyframes",
                        name=name,
                        path=rel_path,
                        language="css",
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )

        elif node.type == "media_statement":
            query = _extract_media_query(node, source)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, query, "media")
            fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

            symbols.append(
                Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=query,
                    fingerprint=fingerprint,
                    kind="media",
                    name=query,
                    path=rel_path,
                    language="css",
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
            )
            # iter_tree will automatically recurse into media block children

        elif node.type == "at_rule":
            # Check what kind of at-rule this is by looking at the at_keyword
            at_keyword = None
            for child in node.children:
                if child.type == "at_keyword":
                    at_keyword = _get_node_text(child, source)
                    break

            if at_keyword == "@font-face":
                font_family = _extract_font_family(node, source)
                name = font_family or "unnamed"
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, name, "font_face")
                fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=name,
                        fingerprint=fingerprint,
                        kind="font_face",
                        name=name,
                        path=rel_path,
                        language="css",
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )
            # Other at-rules (e.g. @charset, @namespace) - iter_tree will recurse

        elif node.type == "class_selector":
            # Extract class name (includes the dot)
            class_name = _get_node_text(node, source)
            if class_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, class_name, "class_selector")
                fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=class_name,
                        fingerprint=fingerprint,
                        kind="class_selector",
                        name=class_name,
                        path=rel_path,
                        language="css",
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )

        elif node.type == "id_selector":
            # Extract ID name (includes the hash)
            id_name = _get_node_text(node, source)
            if id_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, id_name, "id_selector")
                fingerprint = hashlib.sha256(source[node.start_byte : node.end_byte]).hexdigest()[:16]

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=id_name,
                        fingerprint=fingerprint,
                        kind="id_selector",
                        name=id_name,
                        path=rel_path,
                        language="css",
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                )
        # iter_tree automatically handles recursion for all node types


def analyze_css_files(root: Path) -> CSSAnalysisResult:
    """Analyze CSS files in a directory.

    Args:
        root: Directory to analyze (can be a file path for single file)

    Returns:
        CSSAnalysisResult containing symbols and edges
    """
    if not is_css_tree_sitter_available():  # pragma: no cover - css installed
        return CSSAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-css not installed",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_css

    lang = tree_sitter.Language(tree_sitter_css.language())
    parser = tree_sitter.Parser(lang)

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []
    start_time = time.time()

    # Handle single file or directory
    if root.is_file():  # pragma: no cover - single file mode
        css_files = [root] if root.suffix == ".css" else []  # pragma: no cover
    else:
        css_files = list(find_css_files(root))

    for css_file in css_files:
        try:
            source = css_file.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            rel_path = str(css_file.relative_to(root) if root.is_dir() else css_file.name)

            # Create a file symbol for import edges
            file_symbol_id = _make_symbol_id(rel_path, 1, rel_path, "file")

            # Process the tree
            _process_css_tree(
                tree.root_node, symbols, edges, rel_path, source, file_symbol_id
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

    return CSSAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
