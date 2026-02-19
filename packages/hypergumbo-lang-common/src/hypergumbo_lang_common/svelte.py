"""Svelte component analyzer using tree-sitter.

Svelte is a modern web framework that compiles components to efficient
vanilla JavaScript. .svelte files contain HTML, JavaScript (<script>),
and CSS (<style>) in a single-file component format.

How It Works
------------
Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
1. Uses tree-sitter-svelte grammar from tree-sitter-language-pack
2. Extracts component structure (script, style, template)
3. Identifies component references and event handlers
4. Tracks control flow blocks (#if, #each, #await)

Symbols Extracted
-----------------
- **Components**: Imported components used in template (capitalized tags)
- **Slots**: Named and default slot definitions
- **Blocks**: Control flow blocks (if, each, await)
- **Events**: Event handlers on elements

Edges Extracted
---------------
- **imports_component**: Links component usage to imported component paths

Why This Design
---------------
- Svelte uses single-file components with three sections
- Understanding component hierarchy reveals app structure
- Slot definitions show component composition patterns
- Control flow blocks indicate dynamic rendering logic
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = make_pass_id("svelte")


def find_svelte_files(repo_root: Path) -> list[Path]:
    """Find all Svelte component files in the repository."""
    return sorted(find_files(repo_root, ["*.svelte"]))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"svelte:{path}:{kind}:{line}:{name}"


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

# Svelte special elements
SVELTE_SPECIAL_ELEMENTS = {
    "svelte:self", "svelte:component", "svelte:window", "svelte:document",
    "svelte:body", "svelte:head", "svelte:options", "svelte:fragment",
    "svelte:element",
}


def _extract_script_imports(
    node: "tree_sitter.Node",
    current_imports: dict[str, str],
) -> None:
    """Extract component imports from script element."""
    for child in node.children:
        if child.type == "raw_text":
            script_content = _get_node_text(child)
            _parse_imports(script_content, current_imports)
            break


def _parse_imports(script: str, current_imports: dict[str, str]) -> None:
    """Parse import statements from script content."""
    # Match: import Component from './Component.svelte';
    # Match: import { A, B } from './components';
    import_pattern = re.compile(
        r"import\s+(?:(\w+)|{\s*([^}]+)\s*})\s+from\s+['\"]([^'\"]+)['\"]",
        re.MULTILINE,
    )

    for _line_offset, line in enumerate(script.split("\n")):
        for match in import_pattern.finditer(line):
            default_import = match.group(1)
            named_imports = match.group(2)
            import_path = match.group(3)

            # Track Svelte component imports
            if import_path.endswith(".svelte"):
                if default_import:
                    current_imports[default_import] = import_path
                if named_imports:
                    for name in named_imports.split(","):
                        name = name.strip()
                        if name and name[0].isupper():
                            current_imports[name] = import_path


def _extract_svelte_symbols(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    content: bytes,
    symbols: list[Symbol],
    edges: list[Edge],
    execution_id: str,
    current_imports: dict[str, str],
) -> None:
    """Extract symbols from a syntax tree node (recursive)."""
    if node.type == "script_element":
        _extract_script_imports(node, current_imports)
    elif node.type == "element":
        _extract_element(node, path, repo_root, symbols, edges, execution_id, current_imports)
        # Recurse into element children but skip start_tag/self_closing_tag
        for child in node.children:
            if child.type not in ("start_tag", "self_closing_tag"):
                _extract_svelte_symbols(child, path, repo_root, content, symbols, edges, execution_id, current_imports)
        return  # Already handled children above
    elif node.type == "if_statement":
        _extract_control_block(node, path, repo_root, symbols, "if")
    elif node.type == "each_statement":
        _extract_control_block(node, path, repo_root, symbols, "each")
    elif node.type == "await_statement":
        _extract_control_block(node, path, repo_root, symbols, "await")

    for child in node.children:
        _extract_svelte_symbols(child, path, repo_root, content, symbols, edges, execution_id, current_imports)


def _extract_element(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
    execution_id: str,
    current_imports: dict[str, str],
) -> None:
    """Extract information from an HTML element."""
    for child in node.children:
        if child.type == "start_tag":
            _process_tag(child, path, repo_root, symbols, edges, execution_id, current_imports)
            break
        elif child.type == "self_closing_tag":
            _process_tag(child, path, repo_root, symbols, edges, execution_id, current_imports)
            break


def _process_tag(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
    execution_id: str,
    current_imports: dict[str, str],
) -> None:
    """Process a tag node (start_tag or self_closing_tag)."""
    tag_name = ""
    events: list[str] = []
    has_slot_attr = False

    for child in node.children:
        if child.type == "tag_name":
            tag_name = _get_node_text(child)
        elif child.type == "attribute":
            attr_name = ""
            for attr_child in child.children:
                if attr_child.type == "attribute_name":
                    attr_name = _get_node_text(attr_child)
                    break

            # Check for event handlers
            if attr_name.startswith("on:"):
                event_name = attr_name[3:]
                events.append(event_name)

            # Check for slot attribute
            if attr_name == "slot":
                has_slot_attr = True

    if not tag_name:
        return  # pragma: no cover

    rel_path = path.relative_to(repo_root)
    line = node.start_point[0] + 1

    # Check if this is a component reference (capitalized = always a component in Svelte)
    if tag_name[0].isupper():
        symbol_id = _make_symbol_id(rel_path, tag_name, "component_ref", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        import_path = current_imports.get(tag_name, "")

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=tag_name,
            kind="component_ref",
            language="svelte",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"<{tag_name}>",
            meta={
                "import_path": import_path,
                "events": events,
                "has_slot_attr": has_slot_attr,
            },
        )
        symbols.append(symbol)

        # Create edge if we have import info
        if import_path:
            edge = Edge.create(
                src=symbol_id,
                dst=import_path,
                edge_type="imports_component",
                line=line,
                origin=PASS_ID,
                origin_run_id=execution_id,
                evidence_type="import",
                confidence=0.95,
            )
            edges.append(edge)

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

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=slot_name,
            kind="slot",
            language="svelte",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"<slot name=\"{slot_name}\">" if slot_name != "default" else "<slot>",
            meta={"is_default": slot_name == "default"},
        )
        symbols.append(symbol)

    # Record event handlers
    if events:
        for event in events:
            symbol_id = _make_symbol_id(rel_path, f"{tag_name}:{event}", "event", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=event,
                kind="event",
                language="svelte",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"on:{event}",
                meta={"element": tag_name},
            )
            symbols.append(symbol)


def _extract_control_block(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
    block_type: str,
) -> None:
    """Extract a Svelte control flow block."""
    rel_path = path.relative_to(repo_root)
    line = node.start_point[0] + 1

    # Get the expression from the start block
    expression = ""
    for child in node.children:
        if child.type in ("if_start_expr", "each_start_expr", "await_start_expr"):
            for expr_child in child.children:
                if expr_child.type in ("raw_text_expr", "raw_text_each"):
                    expression = _get_node_text(expr_child).strip()
                    break
            break

    symbol_id = _make_symbol_id(rel_path, f"{block_type}:{expression[:20]}", "block", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Count nested elements
    nested_count = 0
    for child in node.children:
        if child.type == "element":
            nested_count += 1

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=f"#{block_type}",
        kind="block",
        language="svelte",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{#{block_type} {expression[:30]}}}",
        meta={
            "block_type": block_type,
            "expression": expression,
            "nested_elements": nested_count,
        },
    )
    symbols.append(symbol)


class SvelteAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Svelte component files using TreeSitterAnalyzer base class."""

    lang = "svelte"
    file_patterns: ClassVar[list[str]] = ["*.svelte"]
    language_pack_name = "svelte"

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Override analyze for Svelte's single-file component parsing."""
        import time as _time
        import warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        files = find_svelte_files(repo_root)
        if not files:
            return AnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        parser = self._create_parser()
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        files_analyzed = 0

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                current_imports: dict[str, str] = {}
                _extract_svelte_symbols(
                    tree.root_node, path, repo_root, content,
                    symbols, edges, run.execution_id, current_imports,
                )
                files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        run.duration_ms = int((_time.time() - start_time) * 1000)
        run.files_analyzed = files_analyzed

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = SvelteAnalyzer()


def is_svelte_tree_sitter_available() -> bool:
    """Check if tree-sitter-svelte is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("svelte")
def analyze_svelte(repo_root: Path) -> AnalysisResult:
    """Analyze Svelte component files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
