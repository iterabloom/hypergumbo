"""Clojure analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Clojure files and extract:
- Namespace definitions (ns)
- Function definitions (defn, defn-)
- Variable definitions (def, defonce)
- Macro definitions (defmacro)
- Protocol definitions (defprotocol)
- Record/type definitions (defrecord, deftype)
- Multimethod definitions (defmulti)
- Function call relationships
- Require/import statements

If tree-sitter with Clojure support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-language-pack (clojure) is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and require statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (clojure)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Clojure-Specific Considerations
-------------------------------
- Clojure is a Lisp with S-expression syntax
- defn creates public functions, defn- creates private functions
- ns declares namespace with :require for imports
- Macros are first-class and common in idiomatic Clojure
- Protocols provide interface-like polymorphism
- Records are value types implementing protocols
- Multimethods provide ad-hoc polymorphism via dispatch function
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

PASS_ID = "clojure-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_clojure_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Clojure files in the repository."""
    yield from find_files(repo_root, ["*.clj", "*.cljs", "*.cljc", "*.edn"])


def is_clojure_tree_sitter_available() -> bool:
    """Check if tree-sitter with Clojure grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("clojure")
        return True
    except Exception:  # pragma: no cover - clojure not supported
        return False


@dataclass
class ClojureAnalysisResult:
    """Result of analyzing Clojure files."""

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


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"clojure:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Clojure file node (used as import edge source)."""
    return f"clojure:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive fallback


def _get_sym_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract symbol name from sym_lit node."""
    name_node = _find_child_by_type(node, "sym_name")
    if name_node:
        return _node_text(name_node, source)
    return _node_text(node, source)  # pragma: no cover - fallback for unusual nodes


def _extract_clojure_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Clojure defn form.

    Clojure functions use vector syntax for parameters:
    (defn name [param1 param2] body) -> [param1, param2]
    (defn name [x y & rest] body) -> [x, y, & rest]

    Returns signature string like "[x, y]" or None if not found.
    """
    # Find the vector literal (vec_lit) containing parameters
    # It should be after the function name
    children = [c for c in node.children if c.type not in ("(", ")")]
    if len(children) < 3:
        return None  # pragma: no cover - malformed defn

    # children[0] = defn/defn-, children[1] = name, children[2+] = docstring/params/body
    for i in range(2, len(children)):
        child = children[i]
        if child.type == "vec_lit":
            # Extract parameter names from vector
            params: list[str] = []
            for vec_child in child.children:
                if vec_child.type == "sym_lit":
                    param_name = _get_sym_name(vec_child, source)
                    params.append(param_name)
            if params:
                return "[" + ", ".join(params) + "]"
            return "[]"

    return None  # pragma: no cover - no params found


def _is_def_form(sym_name: str) -> tuple[str, str] | None:
    """Check if a symbol name is a def-like form.

    Returns (kind, "public"|"private") if it's a def form, None otherwise.
    """
    defs = {
        "def": ("variable", "public"),
        "defonce": ("variable", "public"),
        "defn": ("function", "public"),
        "defn-": ("function", "private"),
        "defmacro": ("macro", "public"),
        "defprotocol": ("protocol", "public"),
        "defrecord": ("record", "public"),
        "deftype": ("type", "public"),
        "defmulti": ("multimethod", "public"),
        "defmethod": ("method", "public"),
    }
    return defs.get(sym_name)


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Clojure file.

    Detects:
    - ns (namespace)
    - defn, defn- (functions)
    - def, defonce (variables)
    - defmacro (macros)
    - defprotocol (protocols)
    - defrecord, deftype (records/types)
    - defmulti (multimethods)
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        if node.type == "list_lit":
            children = node.children
            # Skip parens
            inner = [c for c in children if c.type not in ("(", ")")]

            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_name(inner[0], source)

                # Handle ns (namespace) form
                if first_sym == "ns" and len(inner) > 1:
                    if inner[1].type == "sym_lit":
                        ns_name = _get_sym_name(inner[1], source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        span = Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        sym_id = _make_symbol_id(file_path, start_line, end_line, ns_name, "module")
                        symbols.append(Symbol(
                            id=sym_id,
                            name=ns_name,
                            kind="module",
                            language="clojure",
                            path=file_path,
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

                # Handle def forms
                def_info = _is_def_form(first_sym)
                if def_info and len(inner) > 1:
                    kind, visibility = def_info
                    if inner[1].type == "sym_lit":
                        def_name = _get_sym_name(inner[1], source)
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        span = Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        sym_id = _make_symbol_id(file_path, start_line, end_line, def_name, kind)

                        # Extract signature for functions
                        signature = None
                        if kind == "function":
                            signature = _extract_clojure_signature(node, source)

                        symbols.append(Symbol(
                            id=sym_id,
                            name=def_name,
                            kind=kind,
                            language="clojure",
                            path=file_path,
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            signature=signature,
                            meta={"visibility": visibility} if visibility == "private" else None,
                        ))

    return symbols


def _find_enclosing_defn(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbol_registry: dict[str, Symbol],
) -> Symbol | None:
    """Find the enclosing defn/defn- function for a given node."""
    current = node.parent
    while current:
        if current.type == "list_lit":
            children = current.children
            inner = [c for c in children if c.type not in ("(", ")")]
            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_name(inner[0], source)
                if first_sym in ("defn", "defn-") and len(inner) > 1:
                    if inner[1].type == "sym_lit":
                        def_name = _get_sym_name(inner[1], source)
                        sym = local_symbols.get(def_name) or global_symbol_registry.get(def_name)
                        if sym:
                            return sym
        current = current.parent
    return None


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed Clojure file.

    Detects:
    - Function calls (list_lit starting with sym_lit)
    - require statements (:require in ns form)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "list_lit":
            children = node.children
            inner = [c for c in children if c.type not in ("(", ")")]

            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_name(inner[0], source)

                # Handle ns :require
                if first_sym == "ns":
                    for child in inner:
                        if child.type == "list_lit":
                            list_inner = [c for c in child.children if c.type not in ("(", ")")]
                            if list_inner and list_inner[0].type == "kwd_lit":
                                kwd_text = _node_text(list_inner[0], source)
                                if kwd_text == ":require":
                                    # Process require forms
                                    for req in list_inner[1:]:
                                        if req.type == "vec_lit":
                                            # [namespace :as alias] or [namespace :refer [...]]
                                            vec_inner = [c for c in req.children if c.type not in ("[", "]")]
                                            if vec_inner and vec_inner[0].type == "sym_lit":
                                                req_ns = _get_sym_name(vec_inner[0], source)
                                                module_id = f"clojure:{req_ns}:0-0:module:module"
                                                edge = Edge.create(
                                                    src=file_id,
                                                    dst=module_id,
                                                    edge_type="imports",
                                                    line=req.start_point[0] + 1,
                                                    origin=PASS_ID,
                                                    origin_run_id=run_id,
                                                    evidence_type="require",
                                                    confidence=0.95,
                                                )
                                                edges.append(edge)
                                        elif req.type == "sym_lit":
                                            # Simple require: just namespace
                                            req_ns = _get_sym_name(req, source)
                                            module_id = f"clojure:{req_ns}:0-0:module:module"
                                            edge = Edge.create(
                                                src=file_id,
                                                dst=module_id,
                                                edge_type="imports",
                                                line=req.start_point[0] + 1,
                                                origin=PASS_ID,
                                                origin_run_id=run_id,
                                                evidence_type="require",
                                                confidence=0.95,
                                            )
                                            edges.append(edge)

                # Handle function calls (not def forms)
                elif not _is_def_form(first_sym):
                    caller = _find_enclosing_defn(node, source, local_symbols, global_symbol_registry)
                    if caller:
                        callee_name = first_sym
                        callee = local_symbols.get(callee_name) or global_symbol_registry.get(callee_name)
                        if callee:
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=0.85,
                            )
                            edges.append(edge)

    return edges


def analyze_clojure(repo_root: Path) -> ClojureAnalysisResult:
    """Analyze Clojure files in a repository.

    Returns a ClojureAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_clojure_tree_sitter_available():  # pragma: no cover - tested via mock
        skip_reason = (
            "Clojure analysis skipped: requires tree-sitter-language-pack "
            "(pip install tree-sitter-language-pack)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ClojureAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("clojure")
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for clj_file in find_clojure_files(repo_root):
        # Skip .edn files for symbol extraction (data files only)
        if clj_file.suffix == ".edn":
            continue

        try:
            source = clj_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(clj_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="clojure",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            global_symbol_registry,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ClojureAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
