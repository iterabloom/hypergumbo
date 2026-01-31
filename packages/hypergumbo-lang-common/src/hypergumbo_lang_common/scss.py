"""SCSS/Sass stylesheet analyzer using tree-sitter.

SCSS (Sassy CSS) is a CSS preprocessor that adds variables, mixins, nesting,
and other powerful features to CSS. Understanding SCSS structure helps with
styling architecture and design system analysis.

How It Works
------------
1. Uses tree-sitter-scss grammar from tree-sitter-language-pack
2. Extracts variables, mixins, functions, and rule sets
3. Identifies variable usage and mixin includes

Symbols Extracted
-----------------
- **Variables**: SCSS variables ($variable-name)
- **Mixins**: Mixin definitions (@mixin name)
- **Functions**: Function definitions (@function name)
- **Rule sets**: CSS selectors with their blocks

Edges Extracted
---------------
- **uses_mixin**: Links @include to mixin definitions

Why This Design
---------------
- SCSS variables reveal design tokens and theming
- Mixins show reusable styling patterns
- Functions indicate computation in stylesheets
- Rule sets reveal component styling structure
"""

from __future__ import annotations

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "scss.tree_sitter"
PASS_VERSION = "0.1.0"


class ScssAnalysisResult:
    """Result of SCSS analysis."""

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


def is_scss_tree_sitter_available() -> bool:
    """Check if tree-sitter-scss is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("scss")
        return True
    except Exception:  # pragma: no cover
        return False


def find_scss_files(repo_root: Path) -> list[Path]:
    """Find all SCSS/Sass files in the repository."""
    files: list[Path] = []
    files.extend(repo_root.glob("**/*.scss"))
    files.extend(repo_root.glob("**/*.sass"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"scss:{path}:{kind}:{line}:{name}"


class ScssAnalyzer:
    """Analyzer for SCSS/Sass stylesheet files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._mixin_definitions: dict[str, str] = {}  # mixin name -> symbol id

    def analyze(self) -> ScssAnalysisResult:
        """Run the SCSS analysis."""
        start_time = time.time()

        files = find_scss_files(self.repo_root)
        if not files:
            return ScssAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("scss")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "scss", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return ScssAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        # Check for top-level variable declarations
        if node.type == "declaration" and node.parent and node.parent.type == "stylesheet":
            self._extract_variable(node, path)
        elif node.type == "mixin_statement":
            self._extract_mixin(node, path)
        elif node.type == "function_statement":
            self._extract_function(node, path)
        elif node.type == "rule_set":
            self._extract_rule_set(node, path)
        elif node.type == "include_statement":
            self._extract_include(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_variable(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a variable declaration."""
        var_name = ""
        var_value = ""

        for child in node.children:
            if child.type == "property_name" and _get_node_text(child).startswith("$"):
                var_name = _get_node_text(child)
            elif child.type not in (":", ";"):
                # Capture the value (could be color, number, string, etc.)
                if not var_value:
                    var_value = _get_node_text(child).strip()

        if not var_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, var_name, "variable", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Categorize variable by name
        category = self._categorize_variable(var_name)

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=var_name,
            kind="variable",
            language="scss",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{var_name}: {var_value}",
            meta={
                "value": var_value,
                "category": category,
            },
        )
        self._symbols.append(symbol)

    def _categorize_variable(self, name: str) -> str:
        """Categorize a variable by its name."""
        name_lower = name.lower()
        if "color" in name_lower or "bg" in name_lower or "foreground" in name_lower:
            return "color"
        elif "font" in name_lower or "text" in name_lower or "typography" in name_lower:
            return "typography"
        elif "spacing" in name_lower or "margin" in name_lower or "padding" in name_lower:
            return "spacing"
        elif "border" in name_lower or "radius" in name_lower:
            return "border"
        elif "breakpoint" in name_lower or "screen" in name_lower or "media" in name_lower:
            return "breakpoint"
        elif "z-index" in name_lower or "layer" in name_lower:
            return "layer"
        elif "shadow" in name_lower:
            return "shadow"
        elif "transition" in name_lower or "animation" in name_lower or "duration" in name_lower:
            return "animation"
        return "general"

    def _extract_mixin(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a mixin definition."""
        mixin_name = ""
        params: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                mixin_name = _get_node_text(child)
            elif child.type == "parameters":
                params = self._extract_params(child)

        if not mixin_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, mixin_name, "mixin", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Track mixin for edge creation
        self._mixin_definitions[mixin_name] = symbol_id

        param_str = ", ".join(params) if params else ""
        signature = f"@mixin {mixin_name}({param_str})" if params else f"@mixin {mixin_name}"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=mixin_name,
            kind="mixin",
            language="scss",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta={
                "params": params,
                "param_count": len(params),
            },
        )
        self._symbols.append(symbol)

    def _extract_function(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a function definition."""
        func_name = ""
        params: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
            elif child.type == "parameters":
                params = self._extract_params(child)

        if not func_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, func_name, "function", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        param_str = ", ".join(params) if params else ""
        signature = f"@function {func_name}({param_str})"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=func_name,
            kind="function",
            language="scss",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta={
                "params": params,
                "param_count": len(params),
            },
        )
        self._symbols.append(symbol)

    def _extract_params(self, node: "tree_sitter.Node") -> list[str]:
        """Extract parameter names from a parameters node."""
        params: list[str] = []
        for child in node.children:
            if child.type == "parameter":
                param_text = _get_node_text(child).strip()
                # Extract just the variable name
                if ":" in param_text:
                    param_text = param_text.split(":")[0].strip()
                if param_text.startswith("$"):
                    params.append(param_text)
        return params

    def _extract_rule_set(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a rule set (selector + block)."""
        selector = ""

        for child in node.children:
            if child.type == "selectors":
                selector = _get_node_text(child).strip()
                break

        if not selector:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, selector[:30], "rule_set", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Categorize selector
        selector_type = self._categorize_selector(selector)

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=selector,
            kind="rule_set",
            language="scss",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=selector,
            meta={
                "selector_type": selector_type,
            },
        )
        self._symbols.append(symbol)

    def _categorize_selector(self, selector: str) -> str:
        """Categorize a selector by its type."""
        selector = selector.strip()
        if selector.startswith("#"):
            return "id"
        elif selector.startswith("."):
            return "class"
        elif selector.startswith("&"):
            return "nesting"
        elif selector.startswith("@"):  # pragma: no cover
            return "at-rule"
        elif selector.startswith(":"):
            return "pseudo"
        elif selector.startswith("["):
            return "attribute"
        elif "," in selector:
            return "multiple"
        return "element"

    def _extract_include(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a mixin include and create edge."""
        mixin_name = ""

        for child in node.children:
            if child.type == "identifier":
                mixin_name = _get_node_text(child)
                break

        if not mixin_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Create symbol for the include
        symbol_id = _make_symbol_id(rel_path, f"@include {mixin_name}", "include", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=f"@include {mixin_name}",
            kind="include",
            language="scss",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"@include {mixin_name}",
            meta={"mixin_name": mixin_name},
        )
        self._symbols.append(symbol)

        # Create edge to mixin if defined
        if mixin_name in self._mixin_definitions:
            edge = Edge.create(
                src=symbol_id,
                dst=self._mixin_definitions[mixin_name],
                edge_type="uses_mixin",
                line=line,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="include",
                confidence=0.95,
            )
            self._edges.append(edge)


def analyze_scss(repo_root: Path) -> ScssAnalysisResult:
    """Analyze SCSS/Sass stylesheet files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        ScssAnalysisResult containing extracted symbols and edges
    """
    if not is_scss_tree_sitter_available():
        warnings.warn(
            "SCSS analysis skipped: tree-sitter-scss not available",
            UserWarning,
            stacklevel=2,
        )
        return ScssAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "scss", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-scss not available",
        )

    analyzer = ScssAnalyzer(repo_root)
    return analyzer.analyze()
