"""Svelte component analyzer using tree-sitter.

Svelte is a modern web framework that compiles components to efficient
vanilla JavaScript. .svelte files contain HTML, JavaScript (<script>),
and CSS (<style>) in a single-file component format.

How It Works
------------
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
import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "svelte.tree_sitter"
PASS_VERSION = "0.1.0"


class SvelteAnalysisResult:
    """Result of Svelte component analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges = edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_svelte_tree_sitter_available() -> bool:
    """Check if tree-sitter-svelte is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("svelte")
        return True
    except Exception:  # pragma: no cover
        return False


def find_svelte_files(repo_root: Path) -> list[Path]:
    """Find all Svelte component files in the repository."""
    return sorted(repo_root.glob("**/*.svelte"))


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


class SvelteAnalyzer:
    """Analyzer for Svelte component files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._current_imports: dict[str, str] = {}  # component name -> import path

    def analyze(self) -> SvelteAnalysisResult:
        """Run the Svelte analysis."""
        start_time = time.time()

        files = find_svelte_files(self.repo_root)
        if not files:
            return SvelteAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("svelte")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_imports = {}
                self._extract_symbols(tree.root_node, path, content)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "svelte", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return SvelteAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "script_element":
            self._extract_script_imports(node, path)
        elif node.type == "element":
            self._extract_element(node, path)
            # Recurse into element children but skip start_tag/self_closing_tag
            # since _extract_element already handled them
            for child in node.children:
                if child.type not in ("start_tag", "self_closing_tag"):
                    self._extract_symbols(child, path, content)
            return  # Already handled children above, skip default recursion
        elif node.type == "if_statement":
            self._extract_control_block(node, path, "if")
        elif node.type == "each_statement":
            self._extract_control_block(node, path, "each")
        elif node.type == "await_statement":
            self._extract_control_block(node, path, "await")

        for child in node.children:
            self._extract_symbols(child, path, content)

    def _extract_script_imports(
        self, node: "tree_sitter.Node", path: Path
    ) -> None:
        """Extract component imports from script element."""
        # Find raw_text child which contains the script content
        for child in node.children:
            if child.type == "raw_text":
                script_content = _get_node_text(child)
                self._parse_imports(script_content, path, child.start_point[0] + 1)
                break

    def _parse_imports(self, script: str, path: Path, base_line: int) -> None:
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
                        self._current_imports[default_import] = import_path
                    if named_imports:
                        for name in named_imports.split(","):
                            name = name.strip()
                            if name and name[0].isupper():
                                self._current_imports[name] = import_path

    def _extract_element(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract information from an HTML element."""
        # Find start_tag or self_closing_tag to get tag info
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

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Check if this is a component reference (capitalized = always a component in Svelte)
        # In Svelte, any tag starting with uppercase is a component, even if lowercase
        # version would be an HTML element (e.g., Header component vs header element)
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
            self._symbols.append(symbol)

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
                self._symbols.append(symbol)

    def _extract_control_block(
        self, node: "tree_sitter.Node", path: Path, block_type: str
    ) -> None:
        """Extract a Svelte control flow block."""
        rel_path = path.relative_to(self.repo_root)
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
        self._symbols.append(symbol)


def analyze_svelte(repo_root: Path) -> SvelteAnalysisResult:
    """Analyze Svelte component files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        SvelteAnalysisResult containing extracted symbols and edges
    """
    if not is_svelte_tree_sitter_available():
        warnings.warn(
            "Svelte analysis skipped: tree-sitter-svelte not available",
            UserWarning,
            stacklevel=2,
        )
        return SvelteAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "svelte", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-svelte not available",
        )

    analyzer = SvelteAnalyzer(repo_root)
    return analyzer.analyze()
