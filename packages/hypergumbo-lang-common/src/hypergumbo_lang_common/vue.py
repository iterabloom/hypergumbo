"""Vue.js component analyzer using tree-sitter.

Vue.js is a progressive JavaScript framework for building user interfaces.
.vue files (Single-File Components) contain template, script, and style
sections in a single file.

How It Works
------------
1. Uses tree-sitter-vue grammar from tree-sitter-language-pack
2. Extracts component structure (template, script, style)
3. Identifies component references and directive usage
4. Parses script section for component exports

Symbols Extracted
-----------------
- **Components**: Imported components used in template (capitalized tags)
- **Directives**: Vue directives (v-if, v-for, v-model, @click, :prop)
- **Slots**: Named and default slot definitions
- **Methods**: Component methods from script section
- **Computed**: Computed properties
- **Props**: Component props definitions

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
import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "vue.tree_sitter"
PASS_VERSION = "0.1.0"


class VueAnalysisResult:
    """Result of Vue component analysis."""

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


def is_vue_tree_sitter_available() -> bool:
    """Check if tree-sitter-vue is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("vue")
        return True
    except Exception:  # pragma: no cover
        return False


def find_vue_files(repo_root: Path) -> list[Path]:
    """Find all Vue component files in the repository."""
    return sorted(repo_root.glob("**/*.vue"))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"vue:{path}:{kind}:{line}:{name}"


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


class VueAnalyzer:
    """Analyzer for Vue component files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._current_imports: dict[str, str] = {}  # component name -> import path

    def analyze(self) -> VueAnalysisResult:
        """Run the Vue analysis."""
        start_time = time.time()

        files = find_vue_files(self.repo_root)
        if not files:
            return VueAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("vue")

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
            toolchain={"name": "vue", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return VueAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract symbols from a syntax tree node.

        Process in two passes: script first (to collect imports), then template.
        """
        # First pass: extract script content to populate imports
        self._extract_script_pass(node, path, content)

        # Second pass: extract template and style content
        self._extract_template_style_pass(node, path, content)

    def _extract_script_pass(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """First pass: extract script content to populate imports."""
        if node.type == "script_element":
            self._extract_script_content(node, path, content)

        for child in node.children:
            self._extract_script_pass(child, path, content)

    def _extract_template_style_pass(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Second pass: extract template and style content."""
        if node.type == "template_element":
            self._extract_template_content(node, path, content)
        elif node.type == "style_element":
            self._extract_style_info(node, path)

        for child in node.children:
            self._extract_template_style_pass(child, path, content)

    def _extract_script_content(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract component definition from script element."""
        # Find raw_text child which contains the script content
        for child in node.children:
            if child.type == "raw_text":
                script_content = _get_node_text(child)
                base_line = child.start_point[0] + 1
                self._parse_script(script_content, path, base_line)
                break

    def _extract_braced_content(self, text: str, start_pos: int) -> str:
        """Extract content between matching braces, handling nesting.

        Args:
            text: The full text to search in
            start_pos: Position of the opening brace

        Returns:
            The content between the braces (including the braces)
        """
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

    def _parse_script(self, script: str, path: Path, base_line: int) -> None:
        """Parse script content for imports and component definition."""
        rel_path = path.relative_to(self.repo_root)

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
                        self._current_imports[default_import] = import_path
                    if named_imports:
                        for name in named_imports.split(","):
                            name = name.strip()
                            if name and name[0].isupper():
                                self._current_imports[name] = import_path

        # Extract methods - find method definitions in methods: { ... }
        methods_start = re.search(r"methods\s*:\s*\{", script)
        if methods_start:
            # Find the matching closing brace
            methods_content = self._extract_braced_content(
                script, methods_start.end() - 1
            )
            if methods_content:
                method_pattern = re.compile(r"(\w+)\s*\([^)]*\)\s*\{")
                for match in method_pattern.finditer(methods_content):
                    method_name = match.group(1)
                    # Find line number
                    method_start_pos = methods_start.start() + match.start()
                    line_num = base_line + script[:method_start_pos].count("\n")

                    symbol_id = _make_symbol_id(rel_path, method_name, "method", line_num)
                    span = Span(
                        start_line=line_num,
                        start_col=0,
                        end_line=line_num,
                        end_col=len(method_name),
                    )

                    symbol = Symbol(
                        id=symbol_id,
                        stable_id=symbol_id,
                        name=method_name,
                        kind="method",
                        language="vue",
                        path=str(rel_path),
                        span=span,
                        origin=PASS_ID,
                        signature=f"{method_name}()",
                        meta={"section": "methods"},
                    )
                    self._symbols.append(symbol)

        # Extract computed properties
        computed_start = re.search(r"computed\s*:\s*\{", script)
        if computed_start:
            computed_content = self._extract_braced_content(
                script, computed_start.end() - 1
            )
            if computed_content:
                computed_pattern = re.compile(r"(\w+)\s*\([^)]*\)\s*\{")
                for match in computed_pattern.finditer(computed_content):
                    computed_name = match.group(1)
                    # Find line number
                    computed_start_pos = computed_start.start() + match.start()
                    line_num = base_line + script[:computed_start_pos].count("\n")

                    symbol_id = _make_symbol_id(rel_path, computed_name, "computed", line_num)
                    span = Span(
                        start_line=line_num,
                        start_col=0,
                        end_line=line_num,
                        end_col=len(computed_name),
                    )

                    symbol = Symbol(
                        id=symbol_id,
                        stable_id=symbol_id,
                        name=computed_name,
                        kind="computed",
                        language="vue",
                        path=str(rel_path),
                        span=span,
                        origin=PASS_ID,
                        signature=f"computed {computed_name}",
                        meta={"section": "computed"},
                    )
                    self._symbols.append(symbol)

        # Extract props (Options API)
        # First try to find props: [ ... ] (array syntax)
        props_array_match = re.search(r"props\s*:\s*\[", script)
        props_object_match = re.search(r"props\s*:\s*\{", script)

        if props_array_match:
            # Array syntax: props: ['title', 'message']
            # Find the closing bracket
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
                    self._symbols.append(symbol)

        elif props_object_match:
            # Object syntax: props: { title: String, message: { type: String } }
            props_content = self._extract_braced_content(
                script, props_object_match.end() - 1
            )
            if props_content:
                line_num = base_line + script[:props_object_match.start()].count("\n")
                # Find top-level keys only (not nested keys like type, default)
                # We need to be smarter: only match keys at the first level of nesting
                self._extract_prop_names(props_content, rel_path, line_num)

    def _extract_prop_names(
        self, props_content: str, rel_path: Path, line_num: int
    ) -> None:
        """Extract prop names from props object content.

        Only extracts top-level keys, not nested properties like type, default.
        """
        # Track brace depth to only capture top-level keys
        depth = 0
        i = 0
        key_start = None

        while i < len(props_content):
            char = props_content[i]

            if char == "{":
                depth += 1
                if depth == 1:
                    # Start looking for keys after opening brace
                    key_start = i + 1
            elif char == "}":
                depth -= 1
            elif depth == 1:
                # We're at the top level of the props object
                if char == ":" and key_start is not None:
                    # Found a key
                    key_text = props_content[key_start:i].strip()
                    # Extract the key name (handle whitespace and newlines)
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
                        self._symbols.append(symbol)
                    key_start = None
                elif char == ",":
                    # Start looking for next key after comma
                    key_start = i + 1
            i += 1

    def _extract_template_content(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract component usage from template element."""
        self._extract_template_node(node, path)

    def _extract_template_node(
        self, node: "tree_sitter.Node", path: Path
    ) -> None:
        """Recursively extract from template nodes."""
        if node.type == "element":
            self._process_element(node, path)
            # Recurse into element children but skip start_tag/self_closing_tag
            # since _process_element already handled them
            for child in node.children:
                if child.type not in ("start_tag", "self_closing_tag"):
                    self._extract_template_node(child, path)
            return  # Don't run default recursion

        for child in node.children:
            self._extract_template_node(child, path)

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
        directives: list[str] = []
        has_slot_attr = False

        for child in node.children:
            if child.type == "tag_name":
                tag_name = _get_node_text(child)
            elif child.type == "directive_attribute":
                directive_text = _get_node_text(child)
                # Extract directive name
                if directive_text.startswith("@"):
                    directives.append(f"v-on:{directive_text[1:].split('=')[0]}")
                elif directive_text.startswith(":"):
                    directives.append(f"v-bind:{directive_text[1:].split('=')[0]}")
                elif directive_text.startswith("v-"):
                    directives.append(directive_text.split("=")[0])
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

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Check if this is a component reference (capitalized or kebab-case with uppercase)
        is_component = (
            tag_name[0].isupper()
            or (
                "-" in tag_name
                and tag_name.lower() not in HTML_ELEMENTS
                and tag_name.lower() not in VUE_BUILTINS
            )
        )

        if is_component:
            symbol_id = _make_symbol_id(rel_path, tag_name, "component_ref", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

            # Check for import path (also check PascalCase version of kebab-case)
            import_path = self._current_imports.get(tag_name, "")
            if not import_path and "-" in tag_name:
                # Convert my-component to MyComponent
                pascal_name = "".join(
                    word.capitalize() for word in tag_name.split("-")
                )
                import_path = self._current_imports.get(pascal_name, "")

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

        # Record directives
        for directive in directives:
            directive_name = directive.split(":")[0] if ":" in directive else directive
            symbol_id = _make_symbol_id(rel_path, f"{tag_name}:{directive}", "directive", line)
            span = Span(
                start_line=line,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )

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
                meta={"element": tag_name, "directive_type": directive_name},
            )
            self._symbols.append(symbol)

        # Check for slot elements
        if tag_name == "slot":
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
                language="vue",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"<slot name=\"{slot_name}\">" if slot_name != "default" else "<slot>",
                meta={"is_default": slot_name == "default"},
            )
            self._symbols.append(symbol)

    def _build_style_signature(
        self, is_scoped: bool, is_module: bool, lang: str
    ) -> str:
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

    def _extract_style_info(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract style section info."""
        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Check for scoped or module attributes
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
            signature=self._build_style_signature(is_scoped, is_module, lang),
            meta={
                "is_scoped": is_scoped,
                "is_module": is_module,
                "lang": lang,
            },
        )
        self._symbols.append(symbol)


def analyze_vue(repo_root: Path) -> VueAnalysisResult:
    """Analyze Vue component files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        VueAnalysisResult containing extracted symbols and edges
    """
    if not is_vue_tree_sitter_available():
        warnings.warn(
            "Vue analysis skipped: tree-sitter-vue not available",
            UserWarning,
            stacklevel=2,
        )
        return VueAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "vue", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-vue not available",
        )

    analyzer = VueAnalyzer(repo_root)
    return analyzer.analyze()
