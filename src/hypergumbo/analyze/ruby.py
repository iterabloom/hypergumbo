"""Ruby analysis pass using tree-sitter-ruby.

This analyzer uses tree-sitter to parse Ruby files and extract:
- Method definitions (def)
- Class declarations (class)
- Module declarations (module)
- Method call relationships
- Require/require_relative statements

If tree-sitter with Ruby support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-ruby is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect method calls and require statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ruby package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Rust/Elixir/Java/PHP/C analyzers for consistency
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "ruby-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# HTTP methods for Rails route detection (deprecated - use rails.yaml patterns)
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Deprecation tracking for analyzer-level route detection (ADR-0003 v1.0.x)
# Framework-specific route detection is deprecated in favor of YAML patterns
_deprecated_route_warnings_emitted: set[str] = set()


def _emit_route_deprecation_warning(framework: str) -> None:
    """Emit deprecation warning for analyzer-level route detection.

    This is deprecated in ADR-0003 v1.0.x. Use YAML patterns instead.
    Warning emitted once per framework per session.
    """
    if framework in _deprecated_route_warnings_emitted:
        return
    _deprecated_route_warnings_emitted.add(framework)
    warnings.warn(
        f"{framework} analyzer-level route detection is deprecated. "
        f"Use framework YAML patterns (--frameworks) for semantic detection. "
        f"See ADR-0003 for migration guidance.",
        DeprecationWarning,
        stacklevel=4,
    )


def find_ruby_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Ruby files in the repository."""
    yield from find_files(repo_root, ["*.rb"])


def is_ruby_tree_sitter_available() -> bool:
    """Check if tree-sitter with Ruby grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_ruby") is None:
        return False
    return True


@dataclass
class RubyAnalysisResult:
    """Result of analyzing Ruby files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"ruby:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Ruby file node (used as import edge source)."""
    return f"ruby:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_class_or_module(node: "tree_sitter.Node", source: bytes) -> tuple[Optional[str], str]:
    """Walk up the tree to find the enclosing class or module name.

    Returns (name, type) where type is 'class' or 'module'.
    """
    current = node.parent
    while current is not None:
        if current.type == "class":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return _node_text(name_node, source), "class"
        elif current.type == "module":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return _node_text(name_node, source), "module"
        current = current.parent
    return None, ""  # pragma: no cover - defensive


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method."""
    current = node.parent
    while current is not None:
        if current.type == "method":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                if method_name in local_symbols:
                    return local_symbols[method_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_ruby_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from a method node.

    Returns signature in format: (param, param2 = ..., keyword:, &block)
    Ruby is dynamically typed, so no type annotations are included.
    """
    params: list[str] = []

    # Find parameters node
    params_node = _find_child_by_field(node, "parameters")
    if params_node is None:
        return "()"

    for child in params_node.children:
        if child.type == "identifier":
            # Simple positional parameter
            params.append(_node_text(child, source))
        elif child.type == "optional_parameter":
            # Parameter with default value: name = value
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"{param_name} = ...")
        elif child.type == "keyword_parameter":
            # Keyword parameter: name: or name: value
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                # Check if it has a default value (look for value node after identifier)
                has_value = False
                for pc in child.children:
                    # Skip the identifier and punctuation - look for an actual value
                    if pc.type not in ("identifier", ":"):
                        has_value = True
                        break
                if has_value:
                    params.append(f"{param_name}: ...")
                else:
                    params.append(f"{param_name}:")
        elif child.type == "splat_parameter":
            # *args
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"*{param_name}")
            else:
                params.append("*")  # pragma: no cover - bare splat
        elif child.type == "hash_splat_parameter":
            # **kwargs
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"**{param_name}")
            else:
                params.append("**")  # pragma: no cover - bare hash splat
        elif child.type == "block_parameter":
            # &block
            name_node = _find_child_by_type(child, "identifier")
            if name_node:
                param_name = _node_text(name_node, source)
                params.append(f"&{param_name}")

    params_str = ", ".join(params)
    return f"({params_str})"


def _detect_rails_route(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None, str | None]:
    """Detect Rails route DSL calls.

    Returns (http_method, route_path, controller_action) if a route is found.

    Supported patterns:
    - get '/path', to: 'controller#action'
    - post '/path', to: 'controller#action'
    - resources :name

    The call must be of form <http_method> <path> for HTTP routes,
    or 'resources' <symbol> for resource routes.
    """
    if node.type != "call":  # pragma: no cover
        return None, None, None

    # Get the method name from identifier child
    method_node = None
    for child in node.children:
        if child.type == "identifier":
            method_node = child
            break

    if method_node is None:  # pragma: no cover
        return None, None, None

    method_name = _node_text(method_node, source).lower()

    # Check if it's an HTTP method route
    if method_name in HTTP_METHODS:
        # Extract route path from first argument (should be a string)
        args_node = _find_child_by_field(node, "arguments")
        if args_node:
            route_path = None
            controller_action = None
            for arg in args_node.children:
                if arg.type == "string":
                    content_node = _find_child_by_type(arg, "string_content")
                    if content_node:
                        route_path = _node_text(content_node, source)
                        break
                # Also check for string without string_content
                elif arg.type == "string_content":  # pragma: no cover
                    route_path = _node_text(arg, source)
                    break
            # Try to extract controller#action from 'to:' option
            for arg in args_node.children:
                if arg.type == "pair":
                    key_node = None
                    value_node = None
                    for pair_child in arg.children:
                        if pair_child.type == "hash_key_symbol" or pair_child.type == "simple_symbol":
                            key_text = _node_text(pair_child, source).strip(":")
                            if key_text == "to":
                                key_node = pair_child
                        elif pair_child.type == "string":
                            content = _find_child_by_type(pair_child, "string_content")
                            if content:
                                value_node = content
                    if key_node and value_node:
                        controller_action = _node_text(value_node, source)
            # Only return if we found a valid route path (string argument)
            if route_path:
                return method_name, route_path, controller_action

    # Check if it's a resources/resource call
    if method_name in ("resources", "resource"):
        args_node = _find_child_by_field(node, "arguments")
        if args_node:
            for arg in args_node.children:
                # Resources typically use symbols: resources :users
                if arg.type == "simple_symbol":
                    resource_name = _node_text(arg, source).strip(":")
                    return "resources", resource_name, None
    return None, None, None


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Ruby file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    for node in iter_tree(tree.root_node):
        # Method definition
        if node.type == "method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = _node_text(name_node, source)
                # Qualify with class/module name if inside one
                enclosing_name, enclosing_type = _get_enclosing_class_or_module(node, source)
                if enclosing_type == "class" and enclosing_name:
                    full_name = f"{enclosing_name}#{method_name}"
                elif enclosing_type == "module" and enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=_extract_ruby_signature(node, source),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Class definition
        elif node.type == "class":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                class_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, class_name, "class"),
                    name=class_name,
                    kind="class",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[class_name] = symbol

        # Module definition
        elif node.type == "module":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                module_name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[module_name] = symbol

        # Rails route detection (deprecated - use YAML patterns)
        elif node.type == "call":
            http_method, route_path, controller_action = _detect_rails_route(node, source)
            if http_method:
                _emit_route_deprecation_warning("Rails")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Build route name
                if http_method == "resources":
                    route_name = f"resources:{route_path}"
                else:
                    route_name = f"{http_method.upper()} {route_path or '/'}"

                # Build meta
                meta: dict[str, str] = {}
                if route_path:
                    meta["route_path"] = route_path
                if controller_action:
                    meta["controller_action"] = controller_action

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, route_name, "route"),
                    name=route_name,
                    kind="route",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=http_method,
                    meta=meta if meta else None,
                )
                analysis.symbols.append(symbol)

    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract call and import edges from a file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    for node in iter_tree(tree.root_node):
        # Detect call nodes (require statements and method calls)
        if node.type == "call":
            # Get method name from identifier child
            method_node = None
            for child in node.children:
                if child.type == "identifier":
                    method_node = child
                    break

            if method_node:
                callee_name = _node_text(method_node, source)

                # Handle require/require_relative as imports
                if callee_name in ("require", "require_relative"):
                    args_node = _find_child_by_field(node, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "string":
                                content_node = _find_child_by_type(arg, "string_content")
                                if content_node:
                                    import_path = _node_text(content_node, source)
                                    edges.append(Edge.create(
                                        src=file_id,
                                        dst=f"ruby:{import_path}:0-0:file:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                        evidence_type="require_statement",
                                        confidence=0.95,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                    ))

                # Handle regular method calls
                else:
                    current_method = _get_enclosing_method(node, source, local_symbols)
                    if current_method is not None:
                        # Check local symbols first
                        if callee_name in local_symbols:
                            callee = local_symbols[callee_name]
                            edges.append(Edge.create(
                                src=current_method.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="method_call",
                                confidence=0.85,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                        # Check global symbols
                        elif callee_name in global_symbols:
                            callee = global_symbols[callee_name]
                            edges.append(Edge.create(
                                src=current_method.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="method_call",
                                confidence=0.80,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

        # Detect bare method calls (identifier nodes that are method names)
        elif node.type == "identifier":
            current_method = _get_enclosing_method(node, source, local_symbols)
            if current_method is not None:
                callee_name = _node_text(node, source)
                # Check if this identifier is a known method
                if callee_name in local_symbols:
                    callee = local_symbols[callee_name]
                    if callee.kind == "method" and callee.id != current_method.id:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="bare_method_call",
                            confidence=0.75,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                elif callee_name in global_symbols:
                    callee = global_symbols[callee_name]
                    if callee.kind == "method" and callee.id != current_method.id:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="bare_method_call",
                            confidence=0.70,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

    return edges


def analyze_ruby(repo_root: Path) -> RubyAnalysisResult:
    """Analyze all Ruby files in a repository.

    Returns a RubyAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-ruby is not available, returns a skipped result.
    """
    if not is_ruby_tree_sitter_available():
        warnings.warn(
            "tree-sitter-ruby not available. Install with: pip install hypergumbo[ruby]",
            stacklevel=2,
        )
        return RubyAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-ruby not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-ruby
    try:
        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return RubyAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Ruby parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for rb_file in find_ruby_files(repo_root):
        analysis = _extract_symbols_from_file(rb_file, parser, run)
        if analysis.symbols:
            file_analyses[rb_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split("#")[-1] if "#" in symbol.name else symbol.name
            short_name = short_name.split(".")[-1] if "." in short_name else short_name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for rb_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            rb_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return RubyAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
