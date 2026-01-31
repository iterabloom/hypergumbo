"""F# analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse F# files and extract:
- Module definitions (named_module)
- Function/value definitions (function_or_value_defn)
- Record type definitions (record_type_defn)
- Discriminated union definitions (union_type_defn)
- Open statements (import_decl)
- Function call relationships

If tree-sitter with F# support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-language-pack (fsharp) is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (fsharp)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

F#-Specific Considerations
--------------------------
- F# is a functional-first language on .NET
- Functions defined with `let` keyword
- Record types for structured data
- Discriminated unions (sum types) for variants
- `open` statements import namespaces/modules
- Modules organize code hierarchically
- Pattern matching is pervasive
"""
from __future__ import annotations

import importlib.util
import time
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

PASS_ID = "fsharp-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Forth files often use .fs extension (Open Firmware Forth, GForth, etc.)
# These patterns indicate Forth rather than F#
_FORTH_PATTERNS = [
    r"^\\ ",  # Forth line comment (backslash followed by space)
    r"^: \w",  # Forth word definition (colon followed by space and word)
    r"\bVALUE\b",  # Forth VALUE word
    r"\bCONSTANT\b",  # Forth CONSTANT word
    r"\bVARIABLE\b",  # Forth VARIABLE word
    r"\bCREATE\b",  # Forth CREATE word
    r"\bDOES>\b",  # Forth DOES> word
    r"\bINCLUDE\b",  # Forth INCLUDE word
]


def _is_likely_forth_file(path: Path, sample_lines: int = 30) -> bool:
    """Check if a .fs file is likely Forth rather than F#.

    Forth and F# both use .fs extension. This function reads the first N lines
    and checks for Forth-specific patterns to avoid parsing Forth as F#.

    Args:
        path: Path to the .fs file.
        sample_lines: Number of lines to sample for detection.

    Returns:
        True if the file appears to be Forth, False if it appears to be F#.
    """
    import re

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # Read first N lines for pattern matching
            lines = []
            for i, line in enumerate(f):
                if i >= sample_lines:
                    break
                lines.append(line)
            content = "".join(lines)

            # Check for Forth patterns
            for pattern in _FORTH_PATTERNS:
                if re.search(pattern, content, re.MULTILINE):
                    return True

            return False
    except (OSError, IOError):
        return False


def find_fsharp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all F# files in the repository.

    Filters out .fs files that appear to be Forth (Open Firmware Forth, GForth)
    based on content heuristics.
    """
    for path in find_files(repo_root, ["*.fs", "*.fsi", "*.fsx"]):
        # .fsi and .fsx are unambiguously F#, only .fs needs disambiguation
        if path.suffix == ".fs" and _is_likely_forth_file(path):
            continue
        yield path


def is_fsharp_tree_sitter_available() -> bool:
    """Check if tree-sitter with F# grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("fsharp")
        return True
    except Exception:  # pragma: no cover - fsharp not supported
        return False


@dataclass
class FsharpAnalysisResult:
    """Result of analyzing F# files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]
    module_name: str  # F# module name from module declaration
    module_aliases: dict[str, str] = field(default_factory=dict)  # alias -> module path


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"fsharp:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an F# file node (used as import edge source)."""
    return f"fsharp:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive fallback


def _extract_long_identifier(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract full identifier from long_identifier node."""
    parts = []
    for child in node.children:
        if child.type == "identifier":
            parts.append(_node_text(child, source))
    return ".".join(parts)


def _extract_fsharp_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from an F# function_or_value_defn node.

    Returns signature like:
    - "(x: int, y: int): int" for functions with return type
    - "(message: string)" for functions without explicit return type
    - "(): int" for unit parameter functions

    Args:
        node: The function_or_value_defn node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type: Optional[str] = None
    found_func_decl = False

    for child in node.children:
        if child.type == "function_declaration_left":
            found_func_decl = True
            # Look for argument_patterns
            for grandchild in child.children:
                if grandchild.type == "argument_patterns":
                    for arg_child in grandchild.children:
                        if arg_child.type == "typed_pattern":
                            # Pattern: identifier_pattern : simple_type
                            param_name = None
                            param_type = None
                            for pattern_child in arg_child.children:
                                if pattern_child.type == "identifier_pattern":
                                    id_node = _find_child_by_type(pattern_child, "long_identifier_or_op")
                                    if id_node:
                                        name_node = _find_child_by_type(id_node, "identifier")
                                        if name_node:
                                            param_name = _node_text(name_node, source)
                                    else:  # pragma: no cover - defensive fallback
                                        # May be a direct identifier
                                        param_name = _node_text(pattern_child, source)
                                elif pattern_child.type == "simple_type":
                                    param_type = _node_text(pattern_child, source)
                            if param_name and param_type:
                                params.append(f"{param_name}: {param_type}")
                        elif arg_child.type == "const":
                            # Check for unit ()
                            unit_node = _find_child_by_type(arg_child, "unit")
                            if unit_node:
                                pass  # unit means no params, just skip
        elif found_func_decl and child.type == "simple_type":
            # Return type annotation after function_declaration_left
            return_type = _node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type:
        signature += f": {return_type}"

    return signature


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], str]:
    """Extract all symbols from a parsed F# file.

    Returns (symbols, module_name).

    Detects:
    - named_module (module name)
    - function_or_value_defn (functions/values)
    - record_type_defn (record types)
    - union_type_defn (discriminated unions)
    """
    symbols: list[Symbol] = []
    module_name = ""

    for node in iter_tree(tree.root_node):
        # Module declaration
        if node.type == "named_module":
            long_id = _find_child_by_type(node, "long_identifier")
            if long_id:
                module_name = _extract_long_identifier(long_id, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, module_name, "module")
                symbols.append(Symbol(
                    id=sym_id,
                    name=module_name,
                    kind="module",
                    language="fsharp",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Function/value definition
        elif node.type == "function_or_value_defn":
            # Check for function_declaration_left (functions with params)
            func_left = _find_child_by_type(node, "function_declaration_left")
            if func_left:
                name_node = _find_child_by_type(func_left, "identifier")
                if name_node:
                    func_name = _node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = _make_symbol_id(file_path, start_line, end_line, func_name, "function")

                    # Extract signature
                    signature = _extract_fsharp_signature(node, source)

                    symbols.append(Symbol(
                        id=sym_id,
                        name=func_name,
                        kind="function",
                        language="fsharp",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        signature=signature,
                    ))
            else:
                # Check for value_declaration_left (values without params)
                val_left = _find_child_by_type(node, "value_declaration_left")
                if val_left:
                    id_pattern = _find_child_by_type(val_left, "identifier_pattern")
                    if id_pattern:
                        long_id = _find_child_by_type(id_pattern, "long_identifier_or_op")
                        if long_id:
                            name_node = _find_child_by_type(long_id, "identifier")
                            if name_node:
                                val_name = _node_text(name_node, source)
                                start_line = node.start_point[0] + 1
                                end_line = node.end_point[0] + 1
                                span = Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=node.start_point[1],
                                    end_col=node.end_point[1],
                                )
                                sym_id = _make_symbol_id(file_path, start_line, end_line, val_name, "value")
                                symbols.append(Symbol(
                                    id=sym_id,
                                    name=val_name,
                                    kind="value",
                                    language="fsharp",
                                    path=file_path,
                                    span=span,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

        # Type definition
        elif node.type == "type_definition":
            # Record type
            record = _find_child_by_type(node, "record_type_defn")
            if record:
                type_name_node = _find_child_by_type(record, "type_name")
                if type_name_node:
                    name_node = _find_child_by_type(type_name_node, "identifier")
                    if name_node:
                        type_name = _node_text(name_node, source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        span = Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        sym_id = _make_symbol_id(file_path, start_line, end_line, type_name, "record")
                        symbols.append(Symbol(
                            id=sym_id,
                            name=type_name,
                            kind="record",
                            language="fsharp",
                            path=file_path,
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

            # Discriminated union type
            union = _find_child_by_type(node, "union_type_defn")
            if union:
                type_name_node = _find_child_by_type(union, "type_name")
                if type_name_node:
                    name_node = _find_child_by_type(type_name_node, "identifier")
                    if name_node:
                        type_name = _node_text(name_node, source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        span = Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        sym_id = _make_symbol_id(file_path, start_line, end_line, type_name, "union")
                        symbols.append(Symbol(
                            id=sym_id,
                            name=type_name,
                            kind="union",
                            language="fsharp",
                            path=file_path,
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

    return symbols, module_name


def _get_enclosing_function_fsharp(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Walk up parent chain to find enclosing function."""
    current = node.parent
    while current is not None:
        if current.type == "function_or_value_defn":
            func_left = _find_child_by_type(current, "function_declaration_left")
            if func_left:
                name_node = _find_child_by_type(func_left, "identifier")
                if name_node:
                    func_name = _node_text(name_node, source)
                    sym = local_symbols.get(func_name)
                    if sym:
                        return sym
        current = current.parent
    return None


def _extract_module_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract module aliases for disambiguation.

    In F#:
        module M = List -> M maps to List (produces module_abbrev)
        module M = System.IO -> M maps to System.IO (produces module_abbrev)

    Returns a dict mapping alias names to module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        alias_name = None
        module_name = None

        if node.type == "module_abbrev":
            # module_abbrev: module <identifier> = <long_identifier>
            for child in node.children:
                if child.type == "identifier" and alias_name is None:
                    alias_name = _node_text(child, source)
                elif child.type == "long_identifier":
                    module_name = _extract_long_identifier(child, source)

        elif node.type == "module_defn":  # pragma: no cover - complex module defs rarely used
            # module_defn: module <identifier> = <long_identifier_or_op>
            for child in node.children:
                if child.type == "identifier" and alias_name is None:
                    alias_name = _node_text(child, source)
                elif child.type == "long_identifier_or_op":
                    # Get the full module path
                    long_id = _find_child_by_type(child, "long_identifier")
                    if long_id:
                        module_name = _extract_long_identifier(long_id, source)
                    else:
                        id_node = _find_child_by_type(child, "identifier")
                        if id_node:
                            module_name = _node_text(id_node, source)

        if alias_name and module_name:
            aliases[alias_name] = module_name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    module_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed F# file.

    Detects:
    - Function calls (application_expression)
    - Open statements (import_decl)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)
    if module_aliases is None:  # pragma: no cover - defensive
        module_aliases = {}

    # Build local symbol map (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "import_decl":
            # open Module.Submodule
            long_id = _find_child_by_type(node, "long_identifier")
            if long_id:
                module_name = _extract_long_identifier(long_id, source)
                module_id = f"fsharp:{module_name}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="import",
                    confidence=0.95,
                )
                edges.append(edge)

        elif node.type == "application_expression":
            # Function application - first child is the function being called
            caller = _get_enclosing_function_fsharp(node, source, local_symbols)
            if caller and node.children:
                first_child = node.children[0]
                # Look for long_identifier_or_op
                if first_child.type == "long_identifier_or_op":
                    callee_name: Optional[str] = None
                    path_hint: Optional[str] = None

                    # Check for qualified call (long_identifier with dots)
                    long_id = _find_child_by_type(first_child, "long_identifier")
                    if long_id:
                        # Get all identifiers in the long_identifier
                        identifiers = [
                            _node_text(c, source)
                            for c in long_id.children
                            if c.type == "identifier"
                        ]
                        if len(identifiers) >= 2:
                            # First part might be a module alias
                            receiver = identifiers[0]
                            callee_name = identifiers[-1]
                            path_hint = module_aliases.get(receiver)
                        elif len(identifiers) == 1:  # pragma: no cover - single ident fallback
                            callee_name = identifiers[0]
                    else:
                        # Simple identifier
                        name_node = _find_child_by_type(first_child, "identifier")
                        if name_node:
                            callee_name = _node_text(name_node, source)

                    if callee_name:
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=confidence,
                            )
                            edges.append(edge)

    return edges


def analyze_fsharp(repo_root: Path) -> FsharpAnalysisResult:
    """Analyze F# files in a repository.

    Returns a FsharpAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_fsharp_tree_sitter_available():  # pragma: no cover - tested via mock
        skip_reason = (
            "F# analysis skipped: requires tree-sitter-language-pack "
            "(pip install tree-sitter-language-pack)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return FsharpAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("fsharp")
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for fsharp_file in find_fsharp_files(repo_root):
        try:
            source = fsharp_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(fsharp_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="fsharp",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols, module_name = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Extract module aliases for disambiguation
        module_aliases = _extract_module_aliases(tree, source)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
            module_name=module_name,
            module_aliases=module_aliases,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []
    resolver = NameResolver(global_symbol_registry)

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            resolver,
            run_id,
            module_aliases=fa.module_aliases,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return FsharpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
