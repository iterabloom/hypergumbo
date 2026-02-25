"""SCSS/Sass stylesheet analyzer using tree-sitter.

SCSS (Sassy CSS) is a CSS preprocessor that adds variables, mixins, nesting,
and other powerful features to CSS. Understanding SCSS structure helps with
styling architecture and design system analysis.

How It Works
------------
Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    populate_docstrings_from_tree,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = make_pass_id("scss")


def find_scss_files(repo_root: Path) -> list[Path]:
    """Find all SCSS/Sass files in the repository, excluding vendor dirs."""
    return sorted(set(find_files(repo_root, ["*.scss", "*.sass"])))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"scss:{path}:{kind}:{line}:{name}"


def _categorize_variable(name: str) -> str:
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


def _categorize_selector(selector: str) -> str:
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


def _extract_params(node: "tree_sitter.Node") -> list[str]:
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


def _extract_scss_symbols(
    node: "tree_sitter.Node",
    path: Path,
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
    execution_id: str,
    mixin_definitions: dict[str, str],
) -> None:
    """Extract symbols from a syntax tree node (recursive)."""
    # Check for top-level variable declarations
    if node.type == "declaration" and node.parent and node.parent.type == "stylesheet":
        _extract_variable(node, path, repo_root, symbols)
    elif node.type == "mixin_statement":
        _extract_mixin(node, path, repo_root, symbols, mixin_definitions)
    elif node.type == "function_statement":
        _extract_function(node, path, repo_root, symbols)
    elif node.type == "rule_set":
        _extract_rule_set(node, path, repo_root, symbols)
    elif node.type == "include_statement":
        _extract_include(node, path, repo_root, symbols, edges, execution_id, mixin_definitions)

    for child in node.children:
        _extract_scss_symbols(child, path, repo_root, symbols, edges, execution_id, mixin_definitions)


def _extract_variable(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
    symbols: list[Symbol],
) -> None:
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

    rel_path = path.relative_to(repo_root)
    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path, var_name, "variable", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Categorize variable by name
    category = _categorize_variable(var_name)

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
    symbols.append(symbol)


def _extract_mixin(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
    symbols: list[Symbol], mixin_definitions: dict[str, str],
) -> None:
    """Extract a mixin definition."""
    mixin_name = ""
    params: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            mixin_name = _get_node_text(child)
        elif child.type == "parameters":
            params = _extract_params(child)

    if not mixin_name:
        return  # pragma: no cover

    rel_path = path.relative_to(repo_root)
    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path, mixin_name, "mixin", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Track mixin for edge creation
    mixin_definitions[mixin_name] = symbol_id

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
    symbols.append(symbol)


def _extract_function(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
    symbols: list[Symbol],
) -> None:
    """Extract a function definition."""
    func_name = ""
    params: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            func_name = _get_node_text(child)
        elif child.type == "parameters":
            params = _extract_params(child)

    if not func_name:
        return  # pragma: no cover

    rel_path = path.relative_to(repo_root)
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
    symbols.append(symbol)


def _extract_rule_set(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
    symbols: list[Symbol],
) -> None:
    """Extract a rule set (selector + block)."""
    selector = ""

    for child in node.children:
        if child.type == "selectors":
            selector = _get_node_text(child).strip()
            break

    if not selector:
        return  # pragma: no cover

    rel_path = path.relative_to(repo_root)
    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path, selector[:30], "rule_set", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Categorize selector
    selector_type = _categorize_selector(selector)

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
    symbols.append(symbol)


def _extract_include(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
    symbols: list[Symbol], edges: list[Edge],
    execution_id: str, mixin_definitions: dict[str, str],
) -> None:
    """Extract a mixin include and create edge."""
    mixin_name = ""

    for child in node.children:
        if child.type == "identifier":
            mixin_name = _get_node_text(child)
            break

    if not mixin_name:
        return  # pragma: no cover

    rel_path = path.relative_to(repo_root)
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
    symbols.append(symbol)

    # Create edge to mixin if defined
    if mixin_name in mixin_definitions:
        edge = Edge.create(
            src=symbol_id,
            dst=mixin_definitions[mixin_name],
            edge_type="uses_mixin",
            line=line,
            origin=PASS_ID,
            origin_run_id=execution_id,
            evidence_type="include",
            confidence=0.95,
        )
        edges.append(edge)


class ScssAnalyzer(TreeSitterAnalyzer):
    """Analyzer for SCSS/Sass stylesheet files using TreeSitterAnalyzer base class."""

    lang = "scss"
    file_patterns: ClassVar[list[str]] = ["*.scss", "*.sass"]
    language_pack_name = "scss"

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Override analyze for SCSS's single-pass mixin tracking."""
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

        files = find_scss_files(repo_root)
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
        mixin_definitions: dict[str, str] = {}  # mixin name -> symbol id

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                before = len(symbols)
                _extract_scss_symbols(
                    tree.root_node, path, repo_root,
                    symbols, edges, run.execution_id, mixin_definitions,
                )
                populate_docstrings_from_tree(tree.root_node, content, symbols[before:])
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


_analyzer = ScssAnalyzer()


def is_scss_tree_sitter_available() -> bool:
    """Check if tree-sitter-scss is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("scss")
def analyze_scss(repo_root: Path) -> AnalysisResult:
    """Analyze SCSS/Sass stylesheet files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
