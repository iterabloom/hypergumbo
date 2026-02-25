"""Astro component analyzer using tree-sitter.

Astro is a modern web framework for building content-focused websites.
.astro files are component files with a frontmatter section (JavaScript),
HTML template, optional script, and optional style sections.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extracts frontmatter imports, variables, component refs, slots, directives
2. Pass 2: (No separate edge pass -- edges created during symbol extraction)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Astro-specific
extraction logic.

Symbols Extracted
-----------------
- **Components**: Imported components used in template (capitalized tags)
- **Imports**: Import statements from frontmatter
- **Variables**: Variables defined in frontmatter
- **Slots**: Slot elements in template
- **Client directives**: Hydration directives (client:load, etc.)

Edges Extracted
---------------
- **imports_component**: Links component usage to import paths

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Astro components have a unique frontmatter/template structure
- Component imports reveal dependencies
- Client directives indicate hydration strategy
- Understanding component hierarchy is key for Astro projects
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("astro")


def find_astro_files(repo_root: Path) -> list[Path]:
    """Find all Astro component files in the repository, excluding vendor dirs."""
    return sorted(find_files(repo_root, ["*.astro"]))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"astro:{path}:{kind}:{line}:{name}"


# Built-in HTML elements should not be treated as components
HTML_ELEMENTS = {
    "a", "abbr", "address", "area", "article", "aside", "audio", "b", "base",
    "bdi", "bdo", "blockquote", "body", "br", "button", "canvas", "caption",
    "cite", "code", "col", "colgroup", "data", "datalist", "dd", "del",
    "details", "dfn", "dialog", "div", "dl", "dt", "em", "embed", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "head", "header", "hgroup", "hr", "html", "i", "iframe", "img",
    "input", "ins", "kbd", "label", "legend", "li", "link", "main", "map",
    "mark", "menu", "meta", "meter", "nav", "noscript", "object", "ol",
    "optgroup", "option", "output", "p", "picture", "pre", "progress", "q",
    "rp", "rt", "ruby", "s", "samp", "script", "section", "select", "slot",
    "small", "source", "span", "strong", "style", "sub", "summary", "sup",
    "table", "tbody", "td", "template", "textarea", "tfoot", "th", "thead",
    "time", "title", "tr", "track", "u", "ul", "var", "video", "wbr",
    # SVG elements
    "svg", "path", "circle", "rect", "line", "polyline", "polygon", "text",
    "g", "defs", "use", "symbol", "clipPath", "mask", "pattern", "image",
    "linearGradient", "radialGradient", "stop", "filter", "feBlend",
    "feColorMatrix", "feGaussianBlur",
}

# Astro client directives
CLIENT_DIRECTIVES = {
    "client:load", "client:idle", "client:visible", "client:media",
    "client:only",
}


class _AstroFileExtractor:
    """Per-file extraction helper for Astro component files.

    Maintains stateful ``_current_imports`` mapping that is populated
    during frontmatter pass and consumed during template pass. A fresh
    instance is created for each file.
    """

    def __init__(self, repo_root: Path, run_id: str) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = run_id
        self._current_imports: dict[str, str] = {}  # component name -> import path

    def extract(
        self, tree: "tree_sitter.Tree", path: Path, content: bytes
    ) -> tuple[list[Symbol], list[Edge]]:
        """Extract symbols and edges from a single Astro file."""
        self._current_imports = {}
        self._symbols = []
        self._edges = []

        # First pass: extract frontmatter imports
        self._extract_frontmatter_pass(tree.root_node, path, content)

        # Second pass: extract template content
        self._extract_template_pass(tree.root_node, path)

        return self._symbols, self._edges

    def _extract_frontmatter_pass(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """First pass: extract frontmatter content to populate imports."""
        if node.type == "frontmatter":
            self._extract_frontmatter(node, path, content)

        for child in node.children:
            self._extract_frontmatter_pass(child, path, content)

    def _extract_template_pass(
        self, node: "tree_sitter.Node", path: Path
    ) -> None:
        """Second pass: extract template content."""
        if node.type == "element":
            self._process_element(node, path)
            # Recurse into element children but skip already-processed tags
            for child in node.children:
                if child.type not in ("start_tag", "self_closing_tag"):
                    self._extract_template_pass(child, path)
            return

        for child in node.children:
            self._extract_template_pass(child, path)

    def _extract_frontmatter(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract imports and variables from frontmatter."""
        # Find frontmatter_js_block
        for child in node.children:
            if child.type == "frontmatter_js_block":
                js_content = _get_node_text(child)
                base_line = child.start_point[0] + 1
                self._parse_frontmatter_js(js_content, path, base_line)
                break

    def _parse_frontmatter_js(
        self, js_content: str, path: Path, base_line: int
    ) -> None:
        """Parse JavaScript content in frontmatter."""
        rel_path = path.relative_to(self.repo_root)

        # Extract imports
        import_pattern = re.compile(
            r"import\s+(?:(\w+)|{\s*([^}]+)\s*})\s+from\s+['\"]([^'\"]+)['\"]",
            re.MULTILINE,
        )

        for _line_offset, line in enumerate(js_content.split("\n")):
            for match in import_pattern.finditer(line):
                default_import = match.group(1)
                named_imports = match.group(2)
                import_path = match.group(3)

                # Track Astro component imports
                if import_path.endswith(".astro"):
                    if default_import:
                        self._current_imports[default_import] = import_path
                        # Create import symbol
                        line_num = base_line + _line_offset
                        symbol_id = _make_symbol_id(
                            rel_path, default_import, "import", line_num
                        )
                        span = Span(
                            start_line=line_num,
                            start_col=0,
                            end_line=line_num,
                            end_col=len(line),
                        )

                        symbol = Symbol(
                            id=symbol_id,
                            stable_id=symbol_id,
                            name=default_import,
                            kind="import",
                            language="astro",
                            path=str(rel_path),
                            span=span,
                            origin=PASS_ID,
                            signature=f"import {default_import} from '{import_path}'",
                            meta={"import_path": import_path},
                        )
                        self._symbols.append(symbol)

                    if named_imports:
                        for name in named_imports.split(","):
                            name = name.strip()
                            if name and name[0].isupper():
                                self._current_imports[name] = import_path

        # Extract const/let/var declarations
        var_pattern = re.compile(
            r"(?:const|let|var)\s+(\w+)\s*=",
            re.MULTILINE,
        )

        for line_offset, line in enumerate(js_content.split("\n")):
            for match in var_pattern.finditer(line):
                var_name = match.group(1)
                line_num = base_line + line_offset

                symbol_id = _make_symbol_id(rel_path, var_name, "variable", line_num)
                span = Span(
                    start_line=line_num,
                    start_col=match.start(),
                    end_line=line_num,
                    end_col=match.end(),
                )

                symbol = Symbol(
                    id=symbol_id,
                    stable_id=symbol_id,
                    name=var_name,
                    kind="variable",
                    language="astro",
                    path=str(rel_path),
                    span=span,
                    origin=PASS_ID,
                    signature=f"const {var_name}",
                    meta={"section": "frontmatter"},
                )
                self._symbols.append(symbol)

    def _process_element(self, node: "tree_sitter.Node", path: Path) -> None:
        """Process an element node."""
        for child in node.children:
            if child.type == "start_tag":
                self._process_tag(child, path)
                break
            elif child.type == "self_closing_tag":
                self._process_tag(child, path)
                break

    def _process_tag(self, node: "tree_sitter.Node", path: Path) -> None:
        """Process a tag node (start_tag or self_closing_tag)."""
        tag_name = ""
        client_directive = ""
        attributes: list[str] = []

        for child in node.children:
            if child.type == "tag_name":
                tag_name = _get_node_text(child)
            elif child.type == "attribute":
                attr_name = ""
                for attr_child in child.children:
                    if attr_child.type == "attribute_name":
                        attr_name = _get_node_text(attr_child)
                        break

                attributes.append(attr_name)

                # Check for client directives
                if attr_name.startswith("client:"):
                    client_directive = attr_name

        if not tag_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Check if this is a component reference (capitalized)
        if tag_name[0].isupper():
            symbol_id = _make_symbol_id(rel_path, tag_name, "component_ref", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            import_path = self._current_imports.get(tag_name, "")

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=tag_name,
                kind="component_ref",
                language="astro",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"<{tag_name}>",
                meta={
                    "import_path": import_path,
                    "client_directive": client_directive,
                    "attributes": attributes,
                },
            )
            self._symbols.append(symbol)

            # Create edge if we have import info
            if import_path:
                edge = Edge.create(
                    src=symbol_id,
                    dst=import_path,
                    edge_type="imports_component",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=self._execution_id,
                    evidence_type="import",
                    confidence=0.95,
                )
                self._edges.append(edge)

            # Create symbol for client directive if present
            if client_directive:
                directive_id = _make_symbol_id(
                    rel_path, f"{tag_name}:{client_directive}", "directive", line
                )
                directive_span = Span(
                    start_line=line,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )

                directive_symbol = Symbol(
                    id=directive_id,
                    stable_id=directive_id,
                    name=client_directive,
                    kind="directive",
                    language="astro",
                    path=str(rel_path),
                    span=directive_span,
                    origin=PASS_ID,
                    signature=client_directive,
                    meta={"element": tag_name, "directive_type": "client"},
                )
                self._symbols.append(directive_symbol)

        # Check for slot elements
        elif tag_name == "slot":
            slot_name = "default"
            # Look for name attribute
            for child in node.children:
                if child.type == "attribute":
                    attr_name = ""
                    attr_value = ""
                    for attr_child in child.children:
                        if attr_child.type == "attribute_name":
                            attr_name = _get_node_text(attr_child)
                        elif attr_child.type == "quoted_attribute_value":
                            attr_value = _get_node_text(attr_child).strip("\"'")
                    if attr_name == "name" and attr_value:
                        slot_name = attr_value

            symbol_id = _make_symbol_id(rel_path, slot_name, "slot", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            slot_sig = '<slot name="' + slot_name + '">' if slot_name != "default" else "<slot>"

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=slot_name,
                kind="slot",
                language="astro",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=slot_sig,
                meta={"is_default": slot_name == "default"},
            )
            self._symbols.append(symbol)


class AstroAnalyzer(TreeSitterAnalyzer):
    """Astro component analyzer using tree-sitter-language-pack."""

    lang = "astro"
    file_patterns: ClassVar[list[str]] = ["*.astro"]
    language_pack_name = "astro"
    create_file_symbols = False

    # Stash repo_root and run_id for the per-file extractor
    _repo_root: Path | None = None
    _run_id: str = ""

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from an Astro file.

        Uses _AstroFileExtractor to handle the stateful frontmatter/template
        two-pass extraction per file. Edges (imports_component) are also
        created during this pass.
        """
        analysis = FileAnalysis()

        # Determine repo_root from file_path and rel_path
        repo_root = file_path.parent
        rel_parts = Path(rel_path).parts
        for _ in rel_parts:
            repo_root = repo_root.parent

        extractor = _AstroFileExtractor(repo_root, run.execution_id)
        symbols, edges = extractor.extract(tree, file_path, source)
        analysis.symbols = symbols
        # Store edges in import_aliases dict using a special key
        # so they can be recovered in extract_edges_from_file
        analysis._astro_edges = edges  # type: ignore[attr-defined]
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Return edges already extracted during symbol pass."""
        # Edges were already created during extract_symbols_from_file
        # We need to re-extract them since the base class doesn't pass
        # them through. Re-run the extraction.
        repo_root = file_path.parent
        rel_parts = Path(rel_path).parts
        for _ in rel_parts:
            repo_root = repo_root.parent

        extractor = _AstroFileExtractor(repo_root, run.execution_id)
        _, edges = extractor.extract(tree, file_path, source)
        return edges


_analyzer = AstroAnalyzer()


def is_astro_tree_sitter_available() -> bool:
    """Check if tree-sitter-astro is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("astro")
def analyze_astro(repo_root: Path) -> AnalysisResult:
    """Analyze Astro component files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
