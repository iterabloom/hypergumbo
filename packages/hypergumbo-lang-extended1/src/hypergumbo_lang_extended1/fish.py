"""Fish shell analysis pass using tree-sitter.

Detects:
- Function definitions (with --argument options)
- Alias declarations
- Global variable assignments (set -g, set -gx, set -U)
- Source statements for imports
- Command calls

Fish is a modern, user-friendly shell with clean syntax.
The tree-sitter-fish parser handles .fish configuration and script files.

How It Works
------------
1. Check if tree-sitter with Fish grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .fish files
4. Extract function definitions with argument signatures
5. Extract alias declarations
6. Track global variable assignments
7. Track source statements as import edges
8. Track function/command calls

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Fish grammar
- Fish is a popular shell alternative with many users
- Enables analysis of Fish configurations for DevOps/automation
"""
from __future__ import annotations

import importlib.util
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "fish-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_fish_files(repo_root: Path) -> Iterator[Path]:
    """Find all Fish shell files in the repository."""
    yield from find_files(repo_root, ["*.fish"])


@dataclass
class FishAnalysisResult:
    """Result of analyzing Fish shell files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def is_fish_tree_sitter_available() -> bool:
    """Check if tree-sitter-fish is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("fish")
        return True
    except Exception:  # pragma: no cover - fish grammar not available
        return False


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_children_by_type(
    node: "tree_sitter.Node", child_type: str
) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == child_type]


@dataclass
class _FileContext:
    """Context for processing a single file."""

    source: bytes
    rel_path: str
    file_stable_id: str
    run_id: str
    symbols: list[Symbol]
    edges: list[Edge]
    local_symbols: dict[str, Symbol] = field(default_factory=dict)


def _make_symbol(ctx: _FileContext, node: "tree_sitter.Node", name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"fish:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="fish",
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"fish:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _get_enclosing_function_fish(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up parent chain to find enclosing function Symbol."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            words = _find_children_by_type(current, "word")
            if words:
                func_name = _node_text(words[0], source)
                return local_symbols.get(func_name)
        current = current.parent  # pragma: no cover - loop until function found
    return None  # pragma: no cover - no enclosing function found


def _process_function(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function definition (pass 1: symbol extraction)."""
    words = _find_children_by_type(node, "word")
    if not words:
        return  # pragma: no cover

    # First word after 'function' keyword is the name
    func_name = None
    arguments = []
    in_arguments = False

    for word in words:
        text = _node_text(word, ctx.source)
        if func_name is None:
            func_name = text
        elif text == "--argument":
            in_arguments = True
        elif text.startswith("--"):
            in_arguments = False
        elif in_arguments:
            arguments.append(text)

    if func_name:
        signature = f"({', '.join(arguments)})" if arguments else "()"
        sym = _make_symbol(ctx, node, func_name, "function", signature=signature)
        ctx.symbols.append(sym)
        ctx.local_symbols[func_name] = sym


def _process_command_symbols(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a command for symbol extraction (pass 1)."""
    words = _find_children_by_type(node, "word")
    if not words:
        return  # pragma: no cover

    cmd_name = _node_text(words[0], ctx.source)

    if cmd_name == "alias" and len(words) >= 2:
        # alias name "value"
        alias_name = _node_text(words[1], ctx.source)
        ctx.symbols.append(_make_symbol(ctx, node, alias_name, "alias"))

    elif cmd_name == "set" and len(words) >= 2:
        # set -g VAR value, set -gx VAR value, set -U VAR value
        var_name = None
        is_global = False

        for word in words[1:]:
            text = _node_text(word, ctx.source)
            if text in ["-g", "-gx", "-U", "-Ux", "-x"]:
                is_global = True
            elif not text.startswith("-") and var_name is None:
                var_name = text
                break

        if var_name and is_global:
            ctx.symbols.append(_make_symbol(ctx, node, var_name, "variable"))


def _process_command_edges(
    ctx: _FileContext,
    node: "tree_sitter.Node",
    resolver: NameResolver,
) -> None:
    """Process a command for edge extraction (pass 2)."""
    words = _find_children_by_type(node, "word")
    if not words:
        return  # pragma: no cover - defensive

    cmd_name = _node_text(words[0], ctx.source)

    if cmd_name == "source":
        # source path
        # Get the source path - could be word or concatenation
        source_path = None
        found_source_cmd = False
        for child in node.children:
            if child.type == "word":
                text = _node_text(child, ctx.source)
                if text == "source":
                    found_source_cmd = True
                elif found_source_cmd:
                    source_path = text
                    break
            elif child.type == "concatenation" and found_source_cmd:
                source_path = _node_text(child, ctx.source)
                break

        if source_path:
            ctx.edges.append(
                Edge(
                    id=f"edge:fish:{uuid.uuid4().hex[:12]}",
                    src=ctx.file_stable_id,
                    dst=f"fish:{source_path}:file",
                    edge_type="sources",
                    line=node.start_point[0] + 1,
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=ctx.run_id,
                )
            )

    elif cmd_name not in ("alias", "set"):
        # Check if inside a function for call edge
        caller = _get_enclosing_function_fish(node, ctx.source, ctx.local_symbols)
        if caller:
            # Try to resolve the callee
            result = resolver.lookup(cmd_name)
            if result.symbol is not None:
                dst_id = result.symbol.id
                confidence = 0.85 * result.confidence
            else:
                dst_id = f"fish:external:{cmd_name}:function"
                confidence = 0.70

            # This is a function/command call inside a function body
            ctx.edges.append(
                Edge(
                    id=f"edge:fish:{uuid.uuid4().hex[:12]}",
                    src=caller.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=node.start_point[0] + 1,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=ctx.run_id,
                )
            )


def _extract_fish_symbols(ctx: _FileContext, tree: "tree_sitter.Tree") -> None:
    """Extract symbols from a tree-sitter tree (pass 1)."""
    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            _process_function(ctx, node)
        elif node.type == "command":
            _process_command_symbols(ctx, node)


def _extract_fish_edges(
    ctx: _FileContext,
    tree: "tree_sitter.Tree",
    resolver: NameResolver,
) -> None:
    """Extract edges from a tree-sitter tree (pass 2)."""
    for node in iter_tree(tree.root_node):
        if node.type == "command":
            _process_command_edges(ctx, node, resolver)


def analyze_fish(repo_root: Path) -> FishAnalysisResult:
    """Analyze Fish shell files in a repository.

    Returns a FishAnalysisResult with symbols for functions, aliases, and variables,
    plus edges for source statements and function calls.

    Uses two-pass analysis for cross-file call resolution.
    """
    if not is_fish_tree_sitter_available():
        warnings.warn("Fish analysis skipped: tree-sitter-fish unavailable")
        return FishAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-fish unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("fish")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    # Collect file data for two-pass processing
    file_data: list[tuple[Path, bytes, object]] = []
    file_local_symbols: dict[str, dict[str, Symbol]] = {}

    # Pass 1: Extract all symbols from all files
    for file_path in find_fish_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1
        file_data.append((file_path, source, tree))

        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"fish:{rel_path}:file:"
        local_symbols: dict[str, Symbol] = {}

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=symbols,
            edges=edges,
            local_symbols=local_symbols,
        )

        _extract_fish_symbols(ctx, tree)
        file_local_symbols[rel_path] = local_symbols

    # Build resolver from all function symbols
    global_symbols: dict[str, Symbol] = {}
    for sym in symbols:
        if sym.kind == "function":
            global_symbols[sym.name] = sym
    resolver = NameResolver(global_symbols)

    # Pass 2: Extract edges using resolver
    for file_path, source, tree in file_data:
        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"fish:{rel_path}:file:"
        local_symbols = file_local_symbols.get(rel_path, {})

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=[],  # Don't collect symbols again
            edges=edges,
            local_symbols=local_symbols,
        )

        _extract_fish_edges(ctx, tree, resolver)

    duration_ms = int((time.time() - start_time) * 1000)
    return FishAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=AnalysisRun(
            execution_id=run_id,
            pass_id=PASS_ID,
            version=PASS_VERSION,
            files_analyzed=files_analyzed,
            duration_ms=duration_ms,
        ),
    )
