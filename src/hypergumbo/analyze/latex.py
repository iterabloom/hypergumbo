r"""LaTeX analyzer using tree-sitter.

This module provides static analysis of LaTeX documents to extract
document structure (sections, labels, commands, environments) and
cross-reference relationships.

How It Works
------------
Uses tree-sitter-language-pack for LaTeX parsing. Two-pass analysis:

Pass 1 (Symbol Extraction):
- Sections: \section{}, \subsection{}, \chapter{}, etc.
- Labels: \label{} definitions
- Custom commands: \newcommand{} definitions
- Custom environments: \newenvironment{} definitions

Pass 2 (Edge Extraction):
- Reference edges: \ref{}, \cite{}, \autoref{}, etc.
- Include edges: \input{}, \include{}, \usepackage{}

LaTeX-Specific Considerations
-----------------------------
LaTeX documents are structured differently from programming languages:
- Document structure is hierarchical (chapters > sections > subsections)
- Cross-references use labels and refs
- Packages extend functionality
- Custom commands/environments define reusable constructs
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .base import iter_tree
from hypergumbo.ir import AnalysisRun, Edge, Span, Symbol

PASS_ID = "latex"


@dataclass
class LaTeXAnalysisResult:
    """Result of analyzing LaTeX files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skipped_reason: str = ""


def is_latex_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with LaTeX support is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("latex")
        return True
    except (ImportError, KeyError):  # pragma: no cover
        return False


def _get_parser():
    """Get a parser for LaTeX."""
    from tree_sitter_language_pack import get_parser

    return get_parser("latex")


def _make_symbol_id(
    path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate location-based ID for a symbol."""
    return f"latex:{path}:{start_line}-{end_line}:{name}:{kind}"


def _extract_text(node, source_bytes: bytes) -> str:
    """Extract text from a node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _find_child(node, child_type: str):
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover


def _find_all_descendants(node, target_types: set):
    """Find all descendant nodes of given types."""
    results = []
    for n in iter_tree(node):
        if n.type in target_types:
            results.append(n)
    return results


def _get_curly_group_text(node, source_bytes: bytes) -> str:
    """Extract text content from a curly group node."""
    # Look for curly_group, curly_group_text, or similar
    for child in node.children:
        if child.type.startswith("curly_group"):
            # Find the text or path inside
            for inner in child.children:
                if inner.type in {"text", "path", "label", "command_name"}:
                    return _extract_text(inner, source_bytes).strip()
            # If no specific inner type, get all text between braces
            text = _extract_text(child, source_bytes)  # pragma: no cover
            if text.startswith("{") and text.endswith("}"):  # pragma: no cover
                return text[1:-1].strip()  # pragma: no cover
    return ""  # pragma: no cover


def _extract_symbols_from_file(
    rel_path: str, source_bytes: bytes, tree, run: AnalysisRun
) -> list[Symbol]:
    """Extract symbols from a parsed LaTeX file."""
    symbols = []

    root = tree.root_node

    # Section-like structures
    section_types = {
        "chapter", "section", "subsection", "subsubsection",
        "paragraph", "subparagraph"
    }

    for node in _find_all_descendants(root, section_types):
        # Get section title from curly group
        title = _get_curly_group_text(node, source_bytes)
        if title:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbols.append(
                Symbol(
                    id=_make_symbol_id(rel_path, start_line, end_line, title, "section"),
                    name=title,
                    kind="section",
                    language="latex",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"section_type": node.type},
                )
            )

    # Label definitions
    for node in _find_all_descendants(root, {"label_definition"}):
        # Get label name
        label_name = ""
        for child in node.children:
            if child.type == "curly_group_label":
                label_child = _find_child(child, "label")
                if label_child:
                    label_name = _extract_text(label_child, source_bytes).strip()
                    break

        if label_name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbols.append(
                Symbol(
                    id=_make_symbol_id(rel_path, start_line, end_line, label_name, "label"),
                    name=label_name,
                    kind="label",
                    language="latex",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
            )

    # Custom command definitions (\newcommand)
    for node in _find_all_descendants(root, {"new_command_definition"}):
        cmd_name = ""
        for child in node.children:
            if child.type == "curly_group_command_name":
                name_child = _find_child(child, "command_name")
                if name_child:
                    cmd_name = _extract_text(name_child, source_bytes).strip()
                    break

        if cmd_name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbols.append(
                Symbol(
                    id=_make_symbol_id(rel_path, start_line, end_line, cmd_name, "command"),
                    name=cmd_name,
                    kind="command",
                    language="latex",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
            )

    # Custom environment definitions (\newenvironment)
    for node in _find_all_descendants(root, {"environment_definition"}):
        env_name = ""
        for child in node.children:
            if child.type == "curly_group_text":
                text_child = _find_child(child, "text")
                if text_child:
                    env_name = _extract_text(text_child, source_bytes).strip()
                    break

        if env_name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbols.append(
                Symbol(
                    id=_make_symbol_id(rel_path, start_line, end_line, env_name, "environment"),
                    name=env_name,
                    kind="environment",
                    language="latex",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
            )

    return symbols


def _extract_edges_from_file(
    rel_path: str, source_bytes: bytes, tree, labels: set[str]
) -> list[Edge]:
    """Extract edges from a parsed LaTeX file."""
    edges = []

    root = tree.root_node

    # Find label references (\ref, \autoref, \eqref, etc.)
    ref_types = {"label_reference", "label_reference_range"}
    for node in _find_all_descendants(root, ref_types):
        ref_name = ""
        for child in node.children:
            # Handle both curly_group_label and curly_group_label_list
            if child.type in {"curly_group_label", "curly_group_label_list"}:
                # Extract the label text from inside the braces
                label_text = _extract_text(child, source_bytes).strip()
                if label_text.startswith("{") and label_text.endswith("}"):
                    ref_name = label_text[1:-1].strip()
                    break

        if ref_name:
            edges.append(
                Edge.create(
                    src=f"{rel_path}:file",
                    dst=ref_name,
                    edge_type="references",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    evidence_type="ast_ref",
                )
            )
            edges[-1].meta = {"ref_type": "label"}

    # Find citation references (\cite)
    for node in _find_all_descendants(root, {"citation"}):
        cite_keys = ""
        for child in node.children:
            if child.type.startswith("curly_group"):
                # Citations can have multiple keys
                cite_keys = _get_curly_group_text(node, source_bytes)
                break

        if cite_keys:
            # Handle multiple citations (e.g., \cite{key1,key2})
            for key in cite_keys.split(","):
                key = key.strip()
                if key:
                    edges.append(
                        Edge.create(
                            src=f"{rel_path}:file",
                            dst=key,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            evidence_type="ast_cite",
                        )
                    )
                    edges[-1].meta = {"ref_type": "citation"}

    # Find includes (\input, \include)
    for node in _find_all_descendants(root, {"text_include", "latex_include"}):
        included_file = ""
        for child in node.children:
            if child.type.startswith("curly_group"):
                path_child = _find_child(child, "path")
                if path_child:
                    included_file = _extract_text(path_child, source_bytes).strip()
                    break

        if included_file:
            edges.append(
                Edge.create(
                    src=f"{rel_path}:file",
                    dst=included_file,
                    edge_type="includes",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    evidence_type="ast_include",
                )
            )
            edges[-1].meta = {"include_type": node.type}

    # Find package includes (\usepackage)
    for node in _find_all_descendants(root, {"package_include"}):
        packages = ""
        for child in node.children:
            if child.type == "curly_group_path_list":
                path_child = _find_child(child, "path")
                if path_child:
                    packages = _extract_text(path_child, source_bytes).strip()
                    break

        if packages:
            # Handle multiple packages (e.g., \usepackage{pkg1,pkg2})
            for pkg in packages.split(","):
                pkg = pkg.strip()
                if pkg:
                    edges.append(
                        Edge.create(
                            src=f"{rel_path}:file",
                            dst=pkg,
                            edge_type="imports",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            evidence_type="ast_package",
                        )
                    )
                    edges[-1].meta = {"import_type": "package"}

    return edges


def analyze_latex(repo_root: Path) -> LaTeXAnalysisResult:
    """Analyze LaTeX files in the repository.

    Args:
        repo_root: Root directory of the repository

    Returns:
        LaTeXAnalysisResult with symbols and edges from LaTeX files
    """
    import warnings

    if not is_latex_tree_sitter_available():
        warnings.warn(
            "tree-sitter-language-pack with LaTeX support not available. "
            "Install with: pip install tree-sitter-language-pack",
            UserWarning,
            stacklevel=2,
        )
        return LaTeXAnalysisResult(
            symbols=[],
            edges=[],
            skipped=True,
            skipped_reason="tree-sitter-latex not available",
        )

    parser = _get_parser()

    # Create analysis run
    run = AnalysisRun.create(pass_id=PASS_ID, version="0.1.0")

    # Find LaTeX files
    latex_patterns = ["**/*.tex", "**/*.sty", "**/*.cls"]
    latex_files: list[Path] = []
    for pattern in latex_patterns:
        latex_files.extend(repo_root.glob(pattern))

    # Deduplicate
    latex_files = list(set(latex_files))

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Pass 1: Extract symbols
    file_trees: dict[Path, tuple[bytes, object]] = {}
    for file_path in latex_files:
        try:
            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            file_trees[file_path] = (source_bytes, tree)

            rel_path = str(file_path.relative_to(repo_root))
            file_symbols = _extract_symbols_from_file(rel_path, source_bytes, tree, run)
            symbols.extend(file_symbols)
        except (OSError, IOError):  # pragma: no cover
            continue

    # Build label set for edge resolution
    labels = {s.name for s in symbols if s.kind == "label"}

    # Pass 2: Extract edges
    for file_path, (source_bytes, tree) in file_trees.items():
        rel_path = str(file_path.relative_to(repo_root))
        file_edges = _extract_edges_from_file(rel_path, source_bytes, tree, labels)
        edges.extend(file_edges)

    # Update run stats
    run.files_analyzed = len(file_trees)

    return LaTeXAnalysisResult(symbols=symbols, edges=edges, run=run)
