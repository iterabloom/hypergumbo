# SPDX-License-Identifier: AGPL-3.0-or-later
r"""LaTeX analyzer using tree-sitter.

This module provides static analysis of LaTeX documents to extract
document structure (sections, labels, commands, environments) and
cross-reference relationships.

How It Works
------------
Overrides ``analyze()`` with its own two-pass loop and builds the
``AnalysisResult`` itself. It uses the TreeSitterAnalyzer base only for
``_check_grammar_available()`` and ``_create_parser()``; file discovery
(``_find_source_files()``, for .tex/.sty/.cls) is overridden here, and
the module provides the LaTeX-specific extraction logic.

Pass 1 (Symbol Extraction):
- Sections: \section{}, \subsection{}, \chapter{}, etc.
- Labels: \label{} definitions
- Custom commands: \newcommand{} definitions
- Custom environments: \newenvironment{} definitions

Pass 2 (Edge Extraction):
- Reference edges (``references``, meta ref_type label/citation):
  \ref{}, \cite{}, \autoref{}, etc.
- Include edges (``includes``): \input{}, \include{}
- Package edges (``imports``): \usepackage{}

Every item of a list argument is one edge (``\usepackage{a,b}``,
``\cite{k1,k2}``, ``\cref{x,y}``). Every edge starts at the file's node
(``make_file_id``) and ends on a node id (INV-zipaj): a ``\ref`` on the label
symbol it names (this file's, else the one label of that name in the tree),
an ``\input`` / ``\include`` / ``\usepackage`` on the ``.tex`` / ``.sty`` file
of the tree it names (``edge_target.in_tree_file_id``). What the tree does not
hold ends on a stand-in in the id grammar: a label or citation key through
``edge_target.external_target`` (module slot ``label`` / ``citation``; the key
as written, colons included, is on ``dst_ref.name``), a package or file
through ``make_module_import_dst``. The analyzer used to write
``src=f"{rel_path}:file"`` and ``dst=<raw name>``, which finalize parsed with
the file path in the language slot and every ``\ref{sec:...}`` cut to ``sec``.
Not read: ``\import``/``\subimport``, ``\bibliography``, ``\includegraphics``,
``\documentclass`` (they emit no edge).

LaTeX-Specific Considerations
-----------------------------
LaTeX documents are structured differently from programming languages:
- Document structure is hierarchical (chapters > sections > subsections)
- Cross-references use labels and refs
- Packages extend functionality
- Custom commands/environments define reusable constructs

Identity (ADR-0035, WI-tusad): every symbol mints
``overload_arity.scoped_stable_id``. A section or label is scoped by the
titles of the sections enclosing it (the grammar nests ``\subsection`` in its
``\section``), so ``\subsection{Setup}`` under ``Intro`` and under ``Usage``
are two symbols that the floor key ``(kind, path, name)`` nulled. Command and
environment definitions are global and take no scope.
"""

import posixpath
from pathlib import Path
from typing import TYPE_CHECKING, AbstractSet, Any, ClassVar, Iterable, Iterator, Optional

from hypergumbo_core.analyze.base import (
    claim_declaration_silence,
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    ancestor_labels,
    iter_tree,
    make_file_id,
    make_module_import_dst,
    make_symbol_id,
    populate_docstrings_from_tree,
)
from hypergumbo_core.analyze.edge_target import external_target, in_tree_file_id
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import ExternalRef, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("latex")


def _extract_text(node: "tree_sitter.Node", source_bytes: bytes) -> str:
    """Extract text from a node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _find_child(
    node: "tree_sitter.Node", child_type: str,
) -> "tree_sitter.Node | None":
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover


def _find_all_descendants(
    node: "tree_sitter.Node", target_types: AbstractSet[str],
) -> list["tree_sitter.Node"]:
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


_SECTION_TYPES = frozenset({
    "chapter", "section", "subsection", "subsubsection",
    "paragraph", "subparagraph",
})


def _section_chain(node: "tree_sitter.Node", source_bytes: bytes) -> list[str]:
    """Titles of the sections enclosing ``node``, outermost first."""
    return ancestor_labels(
        node,
        lambda n: _get_curly_group_text(n, source_bytes) if n.type in _SECTION_TYPES else None,
    )


def _extract_symbols_from_file(
    rel_path: str, source_bytes: bytes, tree: "tree_sitter.Tree", run: "AnalysisRun"
) -> list[Symbol]:
    """Extract symbols from a parsed LaTeX file."""
    symbols = []

    root = tree.root_node

    # Section-like structures
    section_types = _SECTION_TYPES

    for node in _find_all_descendants(root, section_types):
        # Get section title from curly group
        title = _get_curly_group_text(node, source_bytes)
        if title:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbols.append(
                Symbol(
                    id=make_symbol_id("latex", rel_path, start_line, end_line, title, "section"),
                    stable_id=scoped_stable_id(
                        kind="section", lang="latex", path=rel_path, name=title,
                        scope=_section_chain(node, source_bytes),
                    ),
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
                    stable_id=scoped_stable_id(
                        kind="label", lang="latex", path=rel_path, name=label_name,
                        scope=_section_chain(node, source_bytes),
                    ),
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
                    stable_id=scoped_stable_id(
                        kind="command", lang="latex", path=rel_path, name=cmd_name,
                    ),
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
                    stable_id=scoped_stable_id(
                        kind="environment", lang="latex", path=rel_path, name=env_name,
                    ),
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


def _group_items(
    node: "tree_sitter.Node", source_bytes: bytes, item_types: AbstractSet[str],
) -> list[str]:
    r"""Every item of ``node``'s brace groups, in order: ``{a,b}`` is ``a``, ``b``.

    The grammar parses a list argument (``\usepackage{a,b}``, ``\cite{a,b}``,
    ``\cref{a,b}``) as one ``path`` / ``text`` / ``label`` child per item.
    Reading only the first child, or the braces' whole text, lost every item
    after the first, or made one name of ``a,b``.
    """
    items: list[str] = []
    for child in node.children:
        if not child.type.startswith("curly_group"):
            continue
        for inner in child.children:
            if inner.type in item_types:
                text = _extract_text(inner, source_bytes).strip()
                if text:
                    items.append(text)
    return items


def _label_target(
    name: str, rel_path: str, labels: dict[str, list[Symbol]],
) -> Optional[str]:
    r"""The label symbol ``\ref{name}`` names: this file's, else the one in the tree.

    LaTeX labels are global to a document, and a repository can hold several
    documents. A label this file defines once is the one it refers to; else a
    label defined once in the whole tree. Anything else (none, or several
    elsewhere) is not guessed: the edge ends on a stand-in.
    """
    found = labels.get(name, [])
    own = [s for s in found if s.path == rel_path]
    if len(own) == 1:
        return own[0].id
    if not own and len(found) == 1:
        return found[0].id
    return None


def _file_candidates(rel_path: str, written: str, suffix: str) -> list[str]:
    """Repository-relative paths a file named ``written`` may be.

    TeX looks a name up relative to the directory it compiles in, which for a
    document split over directories is the main file's, not the including
    file's; neither is known here, so both the including file's directory and
    the repository root are tried, each with the suffix TeX appends and as
    written.
    """
    here = posixpath.dirname(rel_path)
    names = [written + suffix, written] if not written.endswith(suffix) else [written]
    return [posixpath.join(here, n) for n in names] + names


def _extract_edges_from_file(
    file_path: Path, rel_path: str, source_bytes: bytes, tree: "tree_sitter.Tree",
    labels: dict[str, list[Symbol]], *, run_id: str,
) -> list[Edge]:
    r"""Extract edges from a parsed LaTeX file.

    Every edge starts at this file's node (``make_file_id``) and ends on a
    node id (INV-zipaj): a label symbol, a file of the tree, or a stand-in in
    the id grammar (``external_target`` for a label or citation key, which
    keeps the name as written on ``dst_ref``; ``make_module_import_dst`` for a
    package or file outside the tree).
    """
    edges: list[Edge] = []
    src = make_file_id("latex", rel_path)
    root = tree.root_node

    def add(dst: str, edge_type: str, node: "tree_sitter.Node", evidence: str,
            meta: dict[str, Any], *, resolved: bool,
            dst_ref: Optional[ExternalRef] = None) -> None:
        edges.append(Edge.create(
            src=src,
            dst=dst,
            edge_type=edge_type,
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            evidence_type=evidence,
            origin_run_id=run_id,
            is_resolved=resolved,
            dst_ref=dst_ref,
            meta=meta,
        ))

    # Label references (\ref, \autoref, \eqref, \cref{a,b}, ...)
    for node in _find_all_descendants(root, {"label_reference", "label_reference_range"}):
        for name in _group_items(node, source_bytes, {"label"}):
            target = _label_target(name, rel_path, labels)
            if target is not None:
                add(target, "references", node, "ast_ref", {"ref_type": "label"},
                    resolved=True)
            else:
                dst, ref = external_target("latex", name, "label")
                add(dst, "references", node, "ast_ref", {"ref_type": "label"},
                    resolved=False, dst_ref=ref)

    # Citations (\cite{key1,key2}): bibliography keys, outside the analysed files.
    for node in _find_all_descendants(root, {"citation"}):
        for key in _group_items(node, source_bytes, {"text"}):
            dst, ref = external_target("latex", key, "citation")
            add(dst, "references", node, "ast_cite", {"ref_type": "citation"},
                resolved=False, dst_ref=ref)

    # Includes (\input, \include): a file of the tree, else the path as written.
    for node in _find_all_descendants(root, {"text_include", "latex_include"}):
        for written in _group_items(node, source_bytes, {"path"}):
            found = in_tree_file_id(
                file_path, rel_path, _file_candidates(rel_path, written, ".tex"),
            )
            add(found or make_module_import_dst("latex", written), "includes", node,
                "ast_include", {"include_type": node.type}, resolved=found is not None)

    # Packages (\usepackage{a,b}): a .sty of the tree, else the package name.
    for node in _find_all_descendants(root, {"package_include"}):
        for pkg in _group_items(node, source_bytes, {"path"}):
            found = in_tree_file_id(
                file_path, rel_path, _file_candidates(rel_path, pkg, ".sty"),
            )
            add(found or make_module_import_dst("latex", pkg), "imports", node,
                "ast_package", {"import_type": "package"}, resolved=found is not None)

    return edges


def _labels_by_name(symbols: "Iterable[Symbol]") -> dict[str, list[Symbol]]:
    r"""The run's label symbols by name, for ``\ref`` resolution."""
    labels: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if isinstance(sym, Symbol) and sym.kind == "label":
            labels.setdefault(sym.name, []).append(sym)
    return labels


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
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Any],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract reference, citation, include, and import edges.

        Note: This template method is not called by the custom analyze()
        override below, which calls the module-level _extract_edges_from_file()
        directly. Kept for interface compatibility with TreeSitterAnalyzer.
        """
        labels = _labels_by_name(global_symbols.values())
        return _extract_edges_from_file(
            file_path, rel_path, source, tree, labels, run_id=run.execution_id,
        )

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None,
    ) -> AnalysisResult:
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
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
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
                populate_docstrings_from_tree(tree.root_node, source_bytes, file_symbols)
            except (OSError, IOError):  # pragma: no cover
                continue

        labels = _labels_by_name(symbols)

        # Pass 2: Extract edges
        for file_path, (source_bytes, tree) in file_trees.items():
            rel_path = str(file_path.relative_to(repo_root))
            file_edges = _extract_edges_from_file(
                file_path, rel_path, source_bytes, tree, labels,
                run_id=run.execution_id,
            )
            edges.extend(file_edges)

        # Update run stats
        run.files_analyzed = len(file_trees)
        claim_declaration_silence(run, symbols, symbols, edges)
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
