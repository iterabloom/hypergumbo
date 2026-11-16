# SPDX-License-Identifier: AGPL-3.0-or-later
"""reStructuredText analyzer using tree-sitter.

reStructuredText (RST) is the standard documentation format for Python projects,
used by Sphinx to generate documentation. It's also used in docstrings and
standalone documentation files.

How It Works
------------
1. Uses tree-sitter-rst grammar from tree-sitter-language-pack to parse files
2. Extracts document structure (sections, titles)
3. Extracts directives (function, class, module definitions)
4. Extracts cross-references and toctree relationships
5. Populates docstrings onto the symbols just extracted
   (``populate_docstrings_from_tree``)

Every symbol's ``id`` is minted through ``make_doc_symbol_ids`` (the
INV-dulah contract for documentation families). Targets also take the
``stable_id`` that call issues; sections and directives discard it and take
their ``stable_id`` from a separate ``make_doc_stable_id`` call that hashes
the section title (or the directive name plus its arguments) instead of the
ordinal their ``id`` carries (WI-tusad). The ``id`` carries the span so two
same-named sections stay distinct, while the ``stable_id`` survives the
section moving down the file. The ``stable_id`` hashes the titles of the sections a symbol
sits in (INV-punig; ADR-0035 §1, scheme v9). tree-sitter-rst emits every
section as a sibling, so the hierarchy comes from the adornment styles, in
the order the document first uses them, as reStructuredText defines it
(``_section_level``); ``meta["level"]`` reports that level.

Symbols Extracted
-----------------
- **Sections**: Document sections with titles at various levels
- **Directives**: RST directives (function, class, module, note, warning, etc.)
- **Targets**: Reference targets for cross-linking

Edges Extracted
---------------
- **references**: Cross-document references (:ref:, :doc:, etc.), from the
  referring file's node (``make_file_id``)
- **includes**: toctree and include relationships, from the directive

Every dst is the id of a node or a stand-in in the id grammar (they were
``rst:file:<path>``, ``rst:doc:<entry>`` and ``rst:<role>:<target>``, which are
no ids, so finalize folded every include of a repository into one
``<unknown>`` node per spelling):

- an ``include`` path and a ``:doc:`` / toctree document name are looked up
  on disk (``edge_target.in_tree_file_id``), next to the referring file, or
  for a leading ``/`` under each directory above it, nearest first (Sphinx
  reads it from its source directory, which this analyzer does not know). A
  document name is tried as written, then with ``.rst`` and ``.md``. A hit is
  that file's node, in its own language; a miss is the
  ``make_module_import_dst`` stand-in for the name as written;
- a ``:ref:`` label is looked up among the run's ``target`` symbols once
  every file is read (labels are global, and compared lowercased as Sphinx
  does): exactly one is that symbol, else ``rst:ref:0-0:<label>:unresolved``;
- any other role names an object of another domain and is
  ``rst:<role>:0-0:<target>:unresolved``.

A toctree entry is one LINE of the directive's content (tree-sitter-rst splits
the content into a text node per word and punctuation mark, so ``api/index``
used to give three edges); ``Title <name>`` names ``name``, as it does in a
role, and ``self`` is the document itself.

Why This Design
---------------
- RST is the primary documentation format for Python ecosystem
- Understanding document structure helps navigate large docs
- Cross-references map documentation dependencies
- Directive extraction captures API documentation
"""

from __future__ import annotations

import posixpath
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol,
    _get_python_toolchain, make_pass_id,
)
from hypergumbo_core.analyze.base import (
    AnalysisResult, TreeSitterAnalyzer, make_doc_stable_id, make_doc_symbol_ids,
    make_file_id, make_module_import_dst, populate_docstrings_from_tree,
)
from hypergumbo_core.analyze.edge_target import external_target, in_tree_file_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.base import node_own_text as _get_node_text
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = make_pass_id("rst")




def find_rst_files(repo_root: Path) -> list[Path]:
    """Find all RST files in the repository."""
    return sorted(find_files(repo_root, ["*.rst"]))



# Documentation directives that define API elements
API_DIRECTIVES = frozenset({
    "function", "class", "method", "module", "attribute", "data",
    "exception", "decorator", "property",
    # Autodoc directives
    "autofunction", "autoclass", "automethod", "automodule", "autodata",
    "autoexception", "autodecorator",
    # C domain
    "c:function", "c:type", "c:struct", "c:macro",
    # Other domains
    "py:function", "py:class", "py:method", "py:module",
    "js:function", "js:class", "js:method",
})

# Admonition directives (notes, warnings, etc.)
ADMONITION_DIRECTIVES = frozenset({
    "note", "warning", "tip", "important", "caution", "danger",
    "attention", "error", "hint", "seealso", "todo",
    "admonition", "versionadded", "versionchanged", "deprecated",
})


#: The suffixes a document name (``:doc:``, a toctree entry) is tried with.
_DOCUMENT_SUFFIXES = ("", ".rst", ".md")


def _explicit_target(text: str) -> str:
    """The target of ``Title <target>``; ``text`` itself when it has no title.

    A leading ``~`` or ``!`` (Sphinx's display modifiers) is not part of it.
    """
    text = text.strip()
    if text.endswith(">") and "<" in text:
        text = text[text.rindex("<") + 1:-1].strip()
    return text.lstrip("~!")


def _path_candidates(rel_path: str, written: str, suffixes: tuple[str, ...]) -> list[str]:
    """The repository-relative paths ``written`` can name, read in ``rel_path``."""
    directory = posixpath.dirname(rel_path)
    if not written.startswith("/"):
        return [posixpath.join(directory, written + suffix) for suffix in suffixes]
    # ``/x`` is read from Sphinx's source directory: some directory at or
    # above this file, tried nearest first.
    directories = [directory]
    while directories[-1]:
        directories.append(posixpath.dirname(directories[-1]))
    return [
        posixpath.join(above, written.lstrip("/") + suffix)
        for above in directories for suffix in suffixes
    ]


class _RSTExtractor:
    """Analyzer for RST files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._section_counter = 0
        self._directive_counter = 0
        # Per file: adornment styles in the order the document first uses
        # them (a style's index is its section level), and the open sections
        # as (level, title), outermost first.
        self._styles: list[tuple[str, bool]] = []
        self._open_sections: list[tuple[int, str]] = []
        # Each ``:ref:`` as (referring file's node id, label, line), resolved
        # against the run's targets once every file is read.
        self._pending_refs: list[tuple[str, str, int]] = []

    def _file_dst(
        self, path: Path, written: str, suffixes: tuple[str, ...] = ("",),
    ) -> tuple[str, bool]:
        """The dst for a file named by path, and whether it is in the tree."""
        rel_path = path.relative_to(self.repo_root).as_posix()
        found = in_tree_file_id(
            path, rel_path, _path_candidates(rel_path, written, suffixes),
        )
        if found is not None:
            return found, True
        return make_module_import_dst("rst", written), False

    def _resolve_refs(self) -> None:
        """One ``references`` edge per ``:ref:``, to the target it names."""
        labels: dict[str, list[str]] = {}
        for sym in self._symbols:
            if sym.kind == "target":
                labels.setdefault(" ".join(sym.name.lower().split()), []).append(sym.id)
        for src_id, label, line in self._pending_refs:
            found = labels.get(" ".join(label.lower().split()), [])
            dst_ref = None
            if len(found) == 1:
                dst = found[0]
            else:
                dst, dst_ref = external_target("rst", label, "ref")
            self._edges.append(Edge.create(
                src=src_id,
                dst=dst,
                edge_type="references",
                line=line,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                evidence_lang="rst",
                is_resolved=len(found) == 1,
                dst_ref=dst_ref,
            ))

    def analyze(self) -> AnalysisResult:
        """Run the RST analysis."""
        start_time = time.time()

        files = find_rst_files(self.repo_root)
        if not files:
            return AnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("rst")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._section_counter = 0
                self._directive_counter = 0
                self._styles = []
                self._open_sections = []
                before = len(self._symbols)
                self._extract_symbols(tree.root_node, path)
                populate_docstrings_from_tree(tree.root_node, content, self._symbols[before:])
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        self._resolve_refs()

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain=_get_python_toolchain(),
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return AnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "section":
            self._extract_section(node, path)
        elif node.type == "directive":
            self._extract_directive(node, path)
        elif node.type == "target":
            self._extract_target(node, path)
        elif node.type == "interpreted_text":
            self._extract_reference(node, path)
        else:
            for child in node.children:
                self._extract_symbols(child, path)

    def _section_level(self, node: "tree_sitter.Node") -> int:
        """The level of a section, from its adornment style.

        reStructuredText has no fixed heading levels: the first adornment
        style a document uses (underline character, with or without an
        overline) is level 1, the next new one level 2, and so on.
        tree-sitter-rst emits every section as a sibling, so the level
        cannot come from the tree.
        """
        overline = False
        char = ""
        seen_title = False
        for child in node.children:
            if child.type == "title":
                seen_title = True
            elif child.type == "adornment":
                text = _get_node_text(child).strip()
                char = text[:1]
                overline = overline or not seen_title
        style = (char, overline)
        if style not in self._styles:
            self._styles.append(style)
        return self._styles.index(style) + 1

    def _section_scope(self) -> list[str]:
        """The titles of the sections open at this point, outermost first."""
        return [title for _, title in self._open_sections]

    def _extract_section(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a section with its title."""
        title_text = ""

        for child in node.children:
            if child.type == "title":
                title_text = " ".join(
                    _get_node_text(c) for c in child.children if c.type == "text"
                ).strip()
                break

        if not title_text:  # pragma: no cover
            # Process children without creating a symbol
            for child in node.children:
                self._extract_symbols(child, path)
            return

        # A section closes every open section at its level or deeper.
        level = self._section_level(node)
        while self._open_sections and self._open_sections[-1][0] >= level:
            self._open_sections.pop()

        self._section_counter += 1
        rel_path = path.relative_to(self.repo_root)
        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # INV-dulah: node.id and canonical stable_id minted together from one arg
        # set (make_doc_symbol_ids) so they can never drift. node.id GAINS the
        # start_line here (was line-less), disambiguating same-name siblings.
        symbol_id, _ordinal_stable_id = make_doc_symbol_ids(
            "rst", rel_path, "section",
            f"section_{self._section_counter}", span.start_line, span.end_line,
        )
        # WI-tusad: the stable_id hashes the title, not the ordinal the node id
        # carries: an ordinal renumbers every later section when an earlier one
        # is deleted. INV-punig: it hashes the titles of the sections it sits
        # in too, so ``Details`` under ``Alpha`` and under ``Beta`` are two
        # identities. Two same-titled sections in one parent are a same-scope
        # tie.
        stable_id = make_doc_stable_id(
            "rst", str(rel_path), "section", title_text, span.start_line, span.end_line,
            self._section_scope(),
        )
        self._open_sections.append((level, title_text))

        symbol = Symbol(
            id=symbol_id,
            stable_id=stable_id,
            name=title_text,
            kind="section",
            language="rst",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{'#' * level} {title_text}",
            meta={"level": level},
        )
        self._symbols.append(symbol)

        # Process nested content
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_directive(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a directive."""
        directive_type = ""
        arguments = ""

        for child in node.children:
            if child.type == "type":
                directive_type = _get_node_text(child)
            elif child.type == "body":
                for body_child in child.children:
                    if body_child.type == "arguments":
                        # Use node text directly to preserve paths with slashes
                        arguments = _get_node_text(body_child).strip()
                        break

        if not directive_type:
            return  # pragma: no cover

        self._directive_counter += 1
        rel_path = path.relative_to(self.repo_root)
        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # INV-dulah: node.id + canonical stable_id minted together (gains
        # start_line, was line-less).
        symbol_id, _ordinal_stable_id = make_doc_symbol_ids(
            "rst", rel_path, "directive",
            f"directive_{self._directive_counter}", span.start_line, span.end_line,
        )
        # WI-tusad: hash the directive and its arguments, not the ordinal;
        # INV-punig: under the sections it sits in.
        stable_id = make_doc_stable_id(
            "rst", str(rel_path), "directive", directive_type,
            span.start_line, span.end_line,
            [*self._section_scope(), *((arguments,) if arguments else ())],
        )

        # Determine directive category
        is_api = directive_type in API_DIRECTIVES
        is_admonition = directive_type in ADMONITION_DIRECTIVES

        # Build signature
        if arguments:
            sig = f".. {directive_type}:: {arguments[:50]}{'...' if len(arguments) > 50 else ''}"
        else:
            sig = f".. {directive_type}::"

        # Use arguments as name if it's an API directive, otherwise use type
        name = arguments if (arguments and is_api) else directive_type

        symbol = Symbol(
            id=symbol_id,
            stable_id=stable_id,
            name=name,
            kind="directive",
            language="rst",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=sig,
            meta={
                "directive_type": directive_type,
                "arguments": arguments,
                "is_api": is_api,
                "is_admonition": is_admonition,
            },
        )
        self._symbols.append(symbol)

        # Extract toctree entries as edges
        if directive_type == "toctree":
            self._extract_toctree_entries(node, path, symbol_id)

        # Extract include directive as edge
        if directive_type == "include" and arguments:
            dst, in_tree = self._file_dst(path, arguments)
            edge = Edge.create(
                src=symbol_id,
                dst=dst,
                edge_type="includes",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                evidence_lang="rst",
                is_resolved=in_tree,
            )
            self._edges.append(edge)

    def _extract_toctree_entries(
        self, node: "tree_sitter.Node", path: Path, source_id: str
    ) -> None:
        """Extract entries from a toctree directive, one per content line."""
        for child in node.children:
            if child.type == "body":
                for body_child in child.children:
                    if body_child.type == "content":
                        first_line = body_child.start_point[0] + 1
                        lines = _get_node_text(body_child).splitlines()
                        for offset, line in enumerate(lines):
                            entry = _explicit_target(line)
                            if not entry or entry.startswith(":"):
                                continue
                            if entry == "self":
                                dst, in_tree = make_file_id(
                                    "rst", path.relative_to(self.repo_root).as_posix(),
                                ), True
                            else:
                                dst, in_tree = self._file_dst(
                                    path, entry, _DOCUMENT_SUFFIXES,
                                )
                            edge = Edge.create(
                                src=source_id,
                                dst=dst,
                                edge_type="includes",
                                line=first_line + offset,
                                origin=PASS_ID,
                                origin_run_id=self._execution_id,
                                evidence_type="static",
                                evidence_lang="rst",
                                is_resolved=in_tree,
                            )
                            self._edges.append(edge)

    def _extract_target(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a reference target."""
        target_name = _get_node_text(node).strip()
        # Remove leading .. _ and trailing :
        if target_name.startswith(".. _"):
            target_name = target_name[4:]
        if target_name.endswith(":"):
            target_name = target_name[:-1]

        if not target_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # INV-dulah: node.id + canonical stable_id minted together (gains
        # start_line, was line-less).
        symbol_id, stable_id = make_doc_symbol_ids(
            "rst", rel_path, "target", target_name,
            span.start_line, span.end_line, self._section_scope(),
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=stable_id,
            name=target_name,
            kind="target",
            language="rst",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f".. _{target_name}:",
            meta={},
        )
        self._symbols.append(symbol)

    def _extract_reference(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a cross-reference."""
        role = ""
        target = ""

        for child in node.children:
            if child.type == "role":
                role = _get_node_text(child).strip(":")
            elif child.type == "interpreted_text":
                target = _get_node_text(child).strip("`")

        if not role or not target:  # pragma: no cover
            return

        # Only track cross-document references
        if role not in ("ref", "doc", "any", "mod", "func", "class", "meth", "attr"):
            return

        src_id = make_file_id("rst", path.relative_to(self.repo_root).as_posix())
        target = _explicit_target(target)
        line = node.start_point[0] + 1
        if not target:
            return
        if role == "ref":
            # Labels are global: resolved once every file's targets are known.
            self._pending_refs.append((src_id, target, line))
            return

        dst_ref: Optional[ExternalRef] = None
        if role == "doc":
            dst, in_tree = self._file_dst(path, target, _DOCUMENT_SUFFIXES)
        else:
            # An object of another domain (``:func:`pkg.mod.fn```).
            (dst, dst_ref), in_tree = external_target("rst", target, role), False

        edge = Edge.create(
            src=src_id,
            dst=dst,
            edge_type="references",
            line=line,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="static",
            evidence_lang="rst",
            is_resolved=in_tree,
            dst_ref=dst_ref,
        )
        self._edges.append(edge)


class RSTAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based analyzer for reStructuredText files.

    Extracts sections, directives, targets, and cross-reference edges.
    Uses language_pack_name for the rst grammar.
    """

    lang = "rst"
    file_patterns: ClassVar[list[str]] = ["**/*.rst"]
    language_pack_name = "rst"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run RST analysis."""
        if not self._check_grammar_available():
            import warnings
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        extractor = _RSTExtractor(repo_root)
        return extractor.analyze()


_analyzer = RSTAnalyzer()


def is_rst_tree_sitter_available() -> bool:
    """Check if tree-sitter-rst is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("rst")
def analyze_rst(repo_root: Path) -> AnalysisResult:
    """Analyze RST files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
