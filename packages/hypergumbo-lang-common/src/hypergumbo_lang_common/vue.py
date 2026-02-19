"""Vue.js component analyzer using tree-sitter.

Vue.js is a progressive JavaScript framework for building user interfaces.
.vue files (Single-File Components) contain template, script, and style
sections in a single file.

How It Works
------------
Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
1. Uses tree-sitter-vue grammar from tree-sitter-language-pack
2. Extracts component structure (template, script, style)
3. Identifies component references and directive usage
4. Parses script section for component exports

Symbols Extracted
-----------------
- **Components**: Imported components used in template (capitalized tags)
- **Directives**: Vue directives (v-if, v-for, v-model, @click, :prop)
- **Slots**: Named and default slot definitions
- **Props**: Component props definitions

Note: Methods and computed properties are NOT extracted here. The JS/TS
tree-sitter analyzer handles ``<script>`` sections with full tree-sitter
precision, emitting these as ``language="javascript"`` symbols. Duplicating
them here with regex would create orphaned ``language="vue"`` symbols.

Edges Extracted
---------------
- **imports_component**: Links component usage to imported component paths

Why This Design
---------------
- Vue uses single-file components with three sections
- Understanding component hierarchy reveals app architecture
- Directive usage shows data binding and event patterns
- Method/computed extraction helps understand component logic
"""

from __future__ import annotations

import re
import uuid
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


PASS_ID = make_pass_id("vue")


def find_vue_files(repo_root: Path) -> list[Path]:
    """Find all Vue component files in the repository."""
    return sorted(find_files(repo_root, ["*.vue"]))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"vue:{path}:{kind}:{line}:{name}"


# Identifiers that are Vue runtime built-ins, not component methods.
_VUE_RUNTIME_NAMES = frozenset({
    "$emit", "$event", "$refs", "$el", "$nextTick", "$parent", "$root",
    "$data", "$options", "$slots", "$attrs", "$forceUpdate", "$destroy",
    "$watch", "$set", "$delete", "$on", "$off", "$once",
    "true", "false", "null", "undefined",
    "console", "window", "document", "Math", "Date", "Object", "Array",
    "String", "Number", "Boolean", "JSON", "parseInt", "parseFloat",
    "isNaN", "typeof", "instanceof", "new", "delete",
})

# Matches the first JS identifier in a handler expression.
_HANDLER_IDENT_RE = re.compile(r"[a-zA-Z_$][a-zA-Z0-9_$]*")


def _extract_handler_name(directive_text: str) -> str:
    """Extract the method name from a v-on directive expression.

    Given ``@click="handleDelete(item)"`` returns ``"handleDelete"``.
    Returns empty string for inline expressions, Vue built-ins, or
    when the handler name cannot be determined.
    """
    parts = directive_text.split("=", 1)
    if len(parts) < 2:
        return ""
    value = parts[1].strip().strip("\"'")
    if not value:
        return ""
    m = _HANDLER_IDENT_RE.match(value)
    if not m:
        return ""
    name = m.group(0)
    if name in _VUE_RUNTIME_NAMES:
        return ""
    return name


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

# Vue built-in components
VUE_BUILTINS = {
    "component", "transition", "transition-group", "keep-alive", "slot",
    "teleport", "suspense", "router-view", "router-link",
}


def _extract_braced_content(text: str, start_pos: int) -> str:
    """Extract content between matching braces, handling nesting."""
    if start_pos >= len(text) or text[start_pos] != "{":
        return ""  # pragma: no cover

    depth = 0
    i = start_pos
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start_pos:i + 1]
        i += 1
    return ""  # pragma: no cover - unclosed brace


def _build_style_signature(is_scoped: bool, is_module: bool, lang: str) -> str:
    """Build signature string for style block."""
    parts = ["<style"]
    if is_scoped:
        parts.append(" scoped")
    if is_module:
        parts.append(" module")
    if lang != "css":
        parts.append(' lang="')
        parts.append(lang)
        parts.append('"')
    parts.append(">")
    return "".join(parts)


class VueAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Vue component files using TreeSitterAnalyzer base class."""

    lang = "vue"
    file_patterns: ClassVar[list[str]] = ["*.vue"]
    language_pack_name = "vue"

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Override analyze for Vue's single-file component parsing."""
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

        files = find_vue_files(repo_root)
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
        current_imports: dict[str, str] = {}

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                current_imports = {}
                self._extract_symbols(
                    tree.root_node, path, repo_root, content,
                    symbols, edges, current_imports,
                )
                files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((_time.time() - start_time) * 1000)
        run.files_analyzed = files_analyzed
        run.duration_ms = duration_ms

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        content: bytes, symbols: list[Symbol], edges: list[Edge],
        current_imports: dict[str, str],
    ) -> None:
        """Extract symbols from a syntax tree node.

        Process in two passes: script first (to collect imports), then template.
        """
        # First pass: extract script content to populate imports
        self._extract_script_pass(node, path, repo_root, content, symbols, current_imports)

        # Second pass: extract template and style content
        self._extract_template_style_pass(node, path, repo_root, content, symbols, edges, current_imports)

    def _extract_script_pass(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        content: bytes, symbols: list[Symbol], current_imports: dict[str, str],
    ) -> None:
        """First pass: extract script content to populate imports."""
        if node.type == "script_element":
            self._extract_script_content(node, path, repo_root, symbols, current_imports)

        for child in node.children:
            self._extract_script_pass(child, path, repo_root, content, symbols, current_imports)

    def _extract_template_style_pass(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        content: bytes, symbols: list[Symbol], edges: list[Edge],
        current_imports: dict[str, str],
    ) -> None:
        """Second pass: extract template and style content."""
        if node.type == "template_element":
            self._extract_template_content(node, path, repo_root, symbols, edges, current_imports)
        elif node.type == "style_element":
            self._extract_style_info(node, path, repo_root, symbols)

        for child in node.children:
            self._extract_template_style_pass(child, path, repo_root, content, symbols, edges, current_imports)

    def _extract_script_content(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        symbols: list[Symbol], current_imports: dict[str, str],
    ) -> None:
        """Extract component definition from script element."""
        for child in node.children:
            if child.type == "raw_text":
                script_content = _get_node_text(child)
                base_line = child.start_point[0] + 1
                self._parse_script(script_content, path, repo_root, base_line, symbols, current_imports)
                break

    def _parse_script(
        self, script: str, path: Path, repo_root: Path,
        base_line: int, symbols: list[Symbol],
        current_imports: dict[str, str],
    ) -> None:
        """Parse script content for imports and component definition."""
        rel_path = path.relative_to(repo_root)

        # Extract imports
        import_pattern = re.compile(
            r"import\s+(?:(\w+)|{\s*([^}]+)\s*})\s+from\s+['\"]([^'\"]+)['\"]",
            re.MULTILINE,
        )

        for _line_offset, line in enumerate(script.split("\n")):
            for match in import_pattern.finditer(line):
                default_import = match.group(1)
                named_imports = match.group(2)
                import_path = match.group(3)

                # Track Vue component imports
                if import_path.endswith(".vue"):
                    if default_import:
                        current_imports[default_import] = import_path
                    if named_imports:
                        for name in named_imports.split(","):
                            name = name.strip()
                            if name and name[0].isupper():
                                current_imports[name] = import_path

        # NOTE: Methods and computed properties are NOT extracted here.

        # Extract props (Options API)
        props_array_match = re.search(r"props\s*:\s*\[", script)
        props_object_match = re.search(r"props\s*:\s*\{", script)

        if props_array_match:
            # Array syntax: props: ['title', 'message']
            bracket_start = props_array_match.end() - 1
            depth = 0
            i = bracket_start
            while i < len(script):
                if script[i] == "[":
                    depth += 1
                elif script[i] == "]":
                    depth -= 1
                    if depth == 0:
                        props_content = script[bracket_start:i + 1]
                        break
                i += 1
            else:
                props_content = ""  # pragma: no cover

            if props_content:
                line_num = base_line + script[:props_array_match.start()].count("\n")
                prop_pattern = re.compile(r"['\"](\w+)['\"]")
                for match in prop_pattern.finditer(props_content):
                    prop_name = match.group(1)
                    symbol_id = _make_symbol_id(rel_path, prop_name, "prop", line_num)
                    span = Span(
                        start_line=line_num,
                        start_col=0,
                        end_line=line_num,
                        end_col=len(prop_name),
                    )

                    symbol = Symbol(
                        id=symbol_id,
                        stable_id=symbol_id,
                        name=prop_name,
                        kind="prop",
                        language="vue",
                        path=str(rel_path),
                        span=span,
                        origin=PASS_ID,
                        signature=f"prop {prop_name}",
                        meta={"section": "props"},
                    )
                    symbols.append(symbol)

        elif props_object_match:
            # Object syntax: props: { title: String, message: { type: String } }
            props_content = _extract_braced_content(
                script, props_object_match.end() - 1
            )
            if props_content:
                line_num = base_line + script[:props_object_match.start()].count("\n")
                self._extract_prop_names(props_content, rel_path, line_num, symbols)

    def _extract_prop_names(
        self, props_content: str, rel_path: Path, line_num: int,
        symbols: list[Symbol],
    ) -> None:
        """Extract prop names from props object content.

        Only extracts top-level keys, not nested properties like type, default.
        """
        depth = 0
        i = 0
        key_start = None

        while i < len(props_content):
            char = props_content[i]

            if char == "{":
                depth += 1
                if depth == 1:
                    key_start = i + 1
            elif char == "}":
                depth -= 1
            elif depth == 1:
                if char == ":" and key_start is not None:
                    key_text = props_content[key_start:i].strip()
                    key_match = re.search(r"(\w+)\s*$", key_text)
                    if key_match:
                        prop_name = key_match.group(1)
                        symbol_id = _make_symbol_id(rel_path, prop_name, "prop", line_num)
                        span = Span(
                            start_line=line_num,
                            start_col=0,
                            end_line=line_num,
                            end_col=len(prop_name),
                        )

                        symbol = Symbol(
                            id=symbol_id,
                            stable_id=symbol_id,
                            name=prop_name,
                            kind="prop",
                            language="vue",
                            path=str(rel_path),
                            span=span,
                            origin=PASS_ID,
                            signature=f"prop {prop_name}",
                            meta={"section": "props"},
                        )
                        symbols.append(symbol)
                    key_start = None
                elif char == ",":
                    key_start = i + 1
            i += 1

    def _extract_template_content(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        symbols: list[Symbol], edges: list[Edge],
        current_imports: dict[str, str],
    ) -> None:
        """Extract component usage from template element."""
        self._extract_template_node(node, path, repo_root, symbols, edges, current_imports)

    def _extract_template_node(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        symbols: list[Symbol], edges: list[Edge],
        current_imports: dict[str, str],
    ) -> None:
        """Recursively extract from template nodes."""
        if node.type == "element":
            self._process_element(node, path, repo_root, symbols, edges, current_imports)
            for child in node.children:
                if child.type not in ("start_tag", "self_closing_tag"):
                    self._extract_template_node(child, path, repo_root, symbols, edges, current_imports)
            return

        for child in node.children:
            self._extract_template_node(child, path, repo_root, symbols, edges, current_imports)

    def _process_element(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        symbols: list[Symbol], edges: list[Edge],
        current_imports: dict[str, str],
    ) -> None:
        """Process an element node."""
        for child in node.children:
            if child.type == "start_tag":
                self._process_tag(child, path, repo_root, symbols, edges, current_imports)
                break
            elif child.type == "self_closing_tag":
                self._process_tag(child, path, repo_root, symbols, edges, current_imports)
                break

    def _process_tag(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        symbols: list[Symbol], edges: list[Edge],
        current_imports: dict[str, str],
    ) -> None:
        """Process a tag node (start_tag or self_closing_tag)."""
        tag_name = ""
        directives: list[str] = []
        handler_expressions: list[str] = []
        has_slot_attr = False

        for child in node.children:
            if child.type == "tag_name":
                tag_name = _get_node_text(child)
            elif child.type == "directive_attribute":
                directive_text = _get_node_text(child)
                if directive_text.startswith("@"):
                    directives.append(f"v-on:{directive_text[1:].split('=')[0]}")
                    handler_expressions.append(
                        _extract_handler_name(directive_text)
                    )
                elif directive_text.startswith(":"):
                    directives.append(f"v-bind:{directive_text[1:].split('=')[0]}")
                    handler_expressions.append("")
                elif directive_text.startswith("v-"):
                    directives.append(directive_text.split("=")[0])
                    if directive_text.startswith("v-on"):
                        handler_expressions.append(
                            _extract_handler_name(directive_text)
                        )
                    else:
                        handler_expressions.append("")
            elif child.type == "attribute":
                attr_name = ""
                for attr_child in child.children:
                    if attr_child.type == "attribute_name":
                        attr_name = _get_node_text(attr_child)
                        break
                if attr_name == "slot":
                    has_slot_attr = True

        if not tag_name:
            return  # pragma: no cover

        rel_path = path.relative_to(repo_root)
        line = node.start_point[0] + 1

        is_component = (
            tag_name[0].isupper()
            or (
                "-" in tag_name
                and tag_name.lower() not in HTML_ELEMENTS
                and tag_name.lower() not in VUE_BUILTINS
            )
        )

        execution_id = f"uuid:{uuid.uuid4()}"

        if is_component:
            symbol_id = _make_symbol_id(rel_path, tag_name, "component_ref", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            import_path = current_imports.get(tag_name, "")
            if not import_path and "-" in tag_name:
                pascal_name = "".join(
                    word.capitalize() for word in tag_name.split("-")
                )
                import_path = current_imports.get(pascal_name, "")

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=tag_name,
                kind="component_ref",
                language="vue",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"<{tag_name}>",
                meta={
                    "import_path": import_path,
                    "directives": directives,
                    "has_slot_attr": has_slot_attr,
                },
            )
            symbols.append(symbol)

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

        # Record directives
        for i, directive in enumerate(directives):
            directive_name = directive.split(":")[0] if ":" in directive else directive
            symbol_id = _make_symbol_id(rel_path, f"{tag_name}:{directive}", "directive", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            meta: dict[str, str | bool] = {
                "element": tag_name,
                "directive_type": directive_name,
            }
            handler = handler_expressions[i] if i < len(handler_expressions) else ""
            if handler:
                meta["handler_expression"] = handler

            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=directive,
                kind="directive",
                language="vue",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=directive,
                meta=meta,
            )
            symbols.append(symbol)

        # Check for slot elements
        if tag_name == "slot":
            slot_name = "default"
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
                language="vue",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"<slot name=\"{slot_name}\">" if slot_name != "default" else "<slot>",
                meta={"is_default": slot_name == "default"},
            )
            symbols.append(symbol)

    def _extract_style_info(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        symbols: list[Symbol],
    ) -> None:
        """Extract style section info."""
        rel_path = path.relative_to(repo_root)
        line = node.start_point[0] + 1

        is_scoped = False
        is_module = False
        lang = "css"

        for child in node.children:
            if child.type == "start_tag":
                for attr in child.children:
                    if attr.type == "attribute":
                        attr_name = ""
                        attr_value = ""
                        for attr_child in attr.children:
                            if attr_child.type == "attribute_name":
                                attr_name = _get_node_text(attr_child)
                            elif attr_child.type == "quoted_attribute_value":
                                attr_value = _get_node_text(attr_child).strip("\"'")
                        if attr_name == "scoped":
                            is_scoped = True
                        elif attr_name == "module":
                            is_module = True
                        elif attr_name == "lang" and attr_value:
                            lang = attr_value
                break

        symbol_id = _make_symbol_id(rel_path, "style", "style_block", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name="style",
            kind="style_block",
            language="vue",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=_build_style_signature(is_scoped, is_module, lang),
            meta={
                "is_scoped": is_scoped,
                "is_module": is_module,
                "lang": lang,
            },
        )
        symbols.append(symbol)


_analyzer = VueAnalyzer()


def is_vue_tree_sitter_available() -> bool:
    """Check if tree-sitter-vue is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("vue")
def analyze_vue(repo_root: Path) -> AnalysisResult:
    """Analyze Vue component files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
