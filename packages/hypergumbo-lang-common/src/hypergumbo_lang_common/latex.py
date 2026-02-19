r"""LaTeX analyzer using tree-sitter.

This module provides static analysis of LaTeX documents to extract
document structure (sections, labels, commands, environments) and
cross-reference relationships.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, parser creation,
and result assembly. This module overrides file discovery for
LaTeX-specific patterns (.tex, .sty, .cls) and provides the
LaTeX-specific extraction logic.

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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("latex")


def _extract_text(node: "tree_sitter.Node", source_bytes: bytes) -> str:
    """Extract text from a node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _find_child(node: "tree_sitter.Node", child_type: str):
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover


def _find_all_descendants(node: "tree_sitter.Node", target_types: set):
    """Find all descendant nodes of given types."""
    results = []
    for n in iter_tree(node):
        if n.type in target_types:
            results.append(n)
    return results


def _get_curly_group_text(node: "tree_sitter.Node", source_bytes: bytes) -> str:
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
    rel_path: str, source_bytes: bytes, tree: "tree_sitter.Tree", run: "AnalysisRun"
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
                    id=make_symbol_id("latex", rel_path, start_line, end_line, title, "section"),
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
                    id=make_symbol_id("latex", rel_path, start_line, end_line, label_name, "label"),
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
                    id=make_symbol_id("latex", rel_path, start_line, end_line, cmd_name, "command"),
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
                    id=make_symbol_id("latex", rel_path, start_line, end_line, env_name, "environment"),
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
    rel_path: str, source_bytes: bytes, tree: "tree_sitter.Tree", labels: set[str]
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


def is_latex_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with LaTeX support is available."""
    return _analyzer._check_grammar_available()


class LatexAnalyzer(TreeSitterAnalyzer):
    """LaTeX document analyzer using tree-sitter-language-pack."""

    lang = "latex"
    pass_version = "0.1.0"
    file_patterns: ClassVar[list[str]] = ["*.tex", "*.sty", "*.cls"]
    language_pack_name = "latex"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Find LaTeX files with recursive patterns and deduplication."""
        seen: set[Path] = set()
        for pattern in ["**/*.tex", "**/*.sty", "**/*.cls"]:
            for path in find_files(repo_root, [pattern]):
                if path not in seen:
                    seen.add(path)
                    yield path

    def extract_symbols_from_file(  # pragma: no cover
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract LaTeX symbols from a single file.

        Note: This template method is not called by the custom analyze()
        override below, which calls the module-level _extract_symbols_from_file()
        directly. Kept for interface compatibility with TreeSitterAnalyzer.
        """
        analysis = FileAnalysis()
        file_symbols = _extract_symbols_from_file(rel_path, source, tree, run)
        analysis.symbols.extend(file_symbols)
        for sym in file_symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(  # pragma: no cover
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract reference, citation, include, and import edges.

        Note: This template method is not called by the custom analyze()
        override below, which calls the module-level _extract_edges_from_file()
        directly. Kept for interface compatibility with TreeSitterAnalyzer.
        """
        # Build labels set from global symbols
        labels = {sym.name for sym in global_symbols.values()
                  if isinstance(sym, Symbol) and sym.kind == "label"}
        return _extract_edges_from_file(rel_path, source, tree, labels)

    def analyze(self, repo_root: Path, max_files=None) -> AnalysisResult:
        """Override analyze to use custom file discovery."""
        import time
        import warnings

        start_time = time.time()
        run_module = __import__("hypergumbo_core.ir", fromlist=["AnalysisRun"])
        AnalysisRun = run_module.AnalysisRun

        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        parser = self._create_parser()

        # Find LaTeX files using custom discovery
        latex_files: list[Path] = list(self._find_source_files(repo_root))

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
        run.duration_ms = int((time.time() - start_time) * 1000)

        return AnalysisResult(symbols=symbols, edges=edges, run=run)


_analyzer = LatexAnalyzer()


@register_analyzer("latex")
def analyze_latex(repo_root: Path) -> AnalysisResult:
    """Analyze LaTeX files in the repository.

    Args:
        repo_root: Root directory of the repository

    Returns:
        AnalysisResult with symbols and edges from LaTeX files
    """
    return _analyzer.analyze(repo_root)
