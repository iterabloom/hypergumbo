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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect calls and resolve against global symbol registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Clojure-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("clojure")


def find_clojure_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Clojure files in the repository."""
    yield from find_files(repo_root, ["*.clj", "*.cljs", "*.cljc", "*.edn"])




def _get_sym_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract symbol name from sym_lit node."""
    name_node = find_child_by_type(node, "sym_name")
    if name_node:
        return node_text(name_node, source)
    return node_text(node, source)  # pragma: no cover - fallback for unusual nodes


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
                        sym_id = make_symbol_id("clojure", file_path, start_line, end_line, ns_name, "module")
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
                        sym_id = make_symbol_id("clojure", file_path, start_line, end_line, def_name, kind)

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
) -> Symbol | None:
    """Find the enclosing defn/defn- function for a given node.

    Uses local_symbols only since the enclosing function must be in the same file.
    """
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
                        sym = local_symbols.get(def_name)
                        if sym:
                            return sym
        current = current.parent
    return None


def _extract_require_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract namespace aliases from require forms for disambiguation.

    In Clojure:
        (:require [clojure.string :as str]) -> str maps to clojure.string
        (:require [my.util :as u :refer [helper]]) -> u maps to my.util

    Returns a dict mapping alias names to full namespace paths.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        # Look for kwd_lit with :require
        if node.type == "kwd_lit":
            kwd_name_node = find_child_by_type(node, "kwd_name")
            if kwd_name_node and node_text(kwd_name_node, source) == "require":
                # Found :require - parent should be the list_lit for (:require ...)
                parent = node.parent
                if parent and parent.type == "list_lit":
                    # Iterate siblings (vec_lit nodes are the require specs)
                    for sibling in parent.children:
                        if sibling.type == "vec_lit":
                            # Parse [namespace :as alias]
                            vec_children = sibling.children
                            ns_name: Optional[str] = None
                            alias_name: Optional[str] = None
                            looking_for_alias = False

                            for child in vec_children:
                                if child.type == "sym_lit" and ns_name is None:
                                    ns_name = node_text(child, source)
                                elif child.type == "kwd_lit":
                                    kwd = node_text(child, source)
                                    if kwd == ":as":
                                        looking_for_alias = True
                                    else:
                                        looking_for_alias = False
                                elif child.type == "sym_lit" and looking_for_alias:
                                    alias_name = node_text(child, source)
                                    looking_for_alias = False

                            if ns_name and alias_name:
                                aliases[alias_name] = ns_name

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    require_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Clojure file.

    Args:
        require_aliases: Optional dict mapping namespace aliases to full paths.

    Detects:
    - Function calls (list_lit starting with sym_lit)
    - require statements (:require in ns form)
    """
    if require_aliases is None:  # pragma: no cover - defensive default
        require_aliases = {}
    edges: list[Edge] = []
    file_id = make_file_id("clojure", file_path)

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
                                kwd_text = node_text(list_inner[0], source)
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
                    caller = _find_enclosing_defn(node, source, local_symbols)
                    if caller:
                        callee_name = first_sym
                        path_hint: Optional[str] = None

                        # Check for namespaced call (str/join -> ns=str, fn=join)
                        sym_node = inner[0]
                        ns_node = find_child_by_type(sym_node, "sym_ns")
                        if ns_node:
                            ns_alias = node_text(ns_node, source)
                            # Look up the full namespace from require aliases
                            path_hint = require_aliases.get(ns_alias)

                        # Use resolver for all callee lookups
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            edge = Edge.create(
                                src=caller.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=0.85 * lookup_result.confidence,
                            )
                            edges.append(edge)

    return edges


class ClojureAnalyzer(TreeSitterAnalyzer):
    """Clojure language analyzer using tree-sitter-language-pack.

    Handles .clj, .cljs, .cljc, and .edn files. EDN files are discovered
    but skipped during symbol extraction (they are data files). The analyzer
    uses create_file_symbols=True so the base class creates file-level symbols
    automatically.
    """

    lang = "clojure"
    file_patterns: ClassVar[list[str]] = ["*.clj", "*.cljs", "*.cljc", "*.edn"]
    language_pack_name = "clojure"
    create_file_symbols = True

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield Clojure source files, skipping .edn data files."""
        for path in find_files(repo_root, self.file_patterns):
            if path.suffix != ".edn":
                yield path

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a Clojure file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract require aliases for disambiguation."""
        return _extract_require_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Clojure file."""
        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path, file_symbols,
            resolver, run.execution_id,
            require_aliases=import_aliases,
        )


_analyzer = ClojureAnalyzer()


def is_clojure_tree_sitter_available() -> bool:
    """Check if tree-sitter with Clojure grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("clojure")
def analyze_clojure(repo_root: Path) -> AnalysisResult:
    """Analyze Clojure files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
