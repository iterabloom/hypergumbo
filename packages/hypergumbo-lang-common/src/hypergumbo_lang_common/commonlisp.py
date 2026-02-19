"""Common Lisp analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Common Lisp files and extract:
- Package definitions (defpackage)
- Function definitions (defun)
- Method definitions (defmethod)
- Macro definitions (defmacro)
- Class definitions (defclass)
- Variable definitions (defvar, defparameter, defconstant)
- Generic function definitions (defgeneric)
- Function call relationships

If tree-sitter with Common Lisp support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect calls and resolve against global symbol registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Common Lisp-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Uses tree-sitter-commonlisp for grammar (standalone package)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Common Lisp-Specific Considerations
-----------------------------------
- Common Lisp is a multi-paradigm Lisp dialect
- defpackage creates namespaces (packages)
- defun creates functions
- defmacro creates macros (compile-time transformations)
- defclass creates CLOS classes
- defmethod creates methods specialized on classes
- defgeneric creates generic functions
- defvar, defparameter, defconstant create special variables
- *earmuffs* convention for special variables (e.g., *config*)
- +plus-signs+ convention for constants (e.g., +pi+)
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

PASS_ID = make_pass_id("commonlisp")


def find_commonlisp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Common Lisp files in the repository."""
    yield from find_files(repo_root, ["*.lisp", "*.lsp", "*.cl", "*.asd"])




def _get_sym_lit_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a sym_lit node."""
    return node_text(node, source).lower()  # Common Lisp is case-insensitive


def _get_name_from_node(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract name from either sym_lit or kwd_lit node."""
    if node.type == "sym_lit":
        return _get_sym_lit_text(node, source)
    elif node.type == "kwd_lit":
        # Keyword like :myapp.core - keep the colon
        return node_text(node, source).lower()
    return None  # pragma: no cover - defensive fallback for unexpected node types


def _extract_defun_info(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str, str, Optional[str]] | None:
    """Extract info from a defun node.

    The tree-sitter-commonlisp grammar uses 'defun' node type for:
    - defun (functions)
    - defmacro (macros)
    - defmethod (methods)
    - defgeneric (generic functions)

    Returns (name, kind, signature) or None if extraction fails.
    """
    # Find defun_header child which contains the definition form and name
    header = find_child_by_type(node, "defun_header")
    if not header:
        return None  # pragma: no cover - malformed defun

    header_text = node_text(header, source).strip()
    # Header looks like: "defun name (params)" or "defmethod name ((x type) y)"
    parts = header_text.split()
    if len(parts) < 2:
        return None  # pragma: no cover - malformed header

    form = parts[0].lower()
    name = parts[1].lower()

    # Determine kind based on the defining form
    kind_map = {
        "defun": "function",
        "defmacro": "macro",
        "defmethod": "method",
        "defgeneric": "generic",
    }
    kind = kind_map.get(form, "function")

    # Extract signature (parameters)
    # Find the parameter list which starts with (
    signature = None
    paren_idx = header_text.find("(", len(form) + len(name) + 1)
    if paren_idx >= 0:
        sig_start = paren_idx
        # Find matching close paren
        depth = 0
        for i, c in enumerate(header_text[sig_start:]):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    signature = header_text[sig_start:sig_start + i + 1]
                    break

    return (name, kind, signature)


def _is_def_list(inner: list["tree_sitter.Node"], source: bytes) -> tuple[str, str, str | None] | None:
    """Check if a list is a def-like form for variables/classes or defun-style.

    Returns (kind, name, signature) if it's a def form, None otherwise.
    The signature is only set for function-like forms.
    """
    if not inner or inner[0].type != "sym_lit":
        return None

    first_sym = _get_sym_lit_text(inner[0], source)

    # Check for variable/class definitions
    var_defs = {
        "defvar": "variable",
        "defparameter": "variable",
        "defconstant": "constant",
        "defclass": "class",
        "defpackage": "package",
        "defstruct": "struct",
    }

    if first_sym in var_defs and len(inner) > 1:
        # Name can be sym_lit or kwd_lit (for packages like :myapp)
        name = _get_name_from_node(inner[1], source)
        if name:
            kind = var_defs[first_sym]
            return (kind, name, None)

    # Check for defun-style forms (when parser doesn't create defun node - uppercase)
    defun_defs = {
        "defun": "function",
        "defmacro": "macro",
        "defmethod": "method",
        "defgeneric": "generic",
    }

    if first_sym in defun_defs and len(inner) > 1:
        name = _get_name_from_node(inner[1], source)
        if name:
            kind = defun_defs[first_sym]
            # Try to extract signature from param list
            signature = None
            if len(inner) > 2 and inner[2].type == "list_lit":
                sig_text = node_text(inner[2], source)
                signature = sig_text
            return (kind, name, signature)

    return None


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Common Lisp file.

    Detects:
    - defpackage (packages)
    - defun (functions)
    - defmacro (macros)
    - defmethod (methods)
    - defgeneric (generic functions)
    - defclass (classes)
    - defstruct (structures)
    - defvar, defparameter, defconstant (variables)
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Handle defun-style nodes (defun, defmacro, defmethod, defgeneric)
        if node.type == "defun":
            info = _extract_defun_info(node, source)
            if info:
                name, kind, signature = info
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("commonlisp", file_path, start_line, end_line, name, kind)
                symbols.append(Symbol(
                    id=sym_id,
                    name=name,
                    kind=kind,
                    language="commonlisp",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=signature,
                ))

        # Handle list_lit for defvar, defparameter, defconstant, defclass, etc.
        # Also handles uppercase DEFUN/DEFMACRO etc. that don't get defun node type
        elif node.type == "list_lit":
            children = node.children
            inner = [c for c in children if c.type not in ("(", ")")]

            def_info = _is_def_list(inner, source)
            if def_info:
                kind, name, signature = def_info
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("commonlisp", file_path, start_line, end_line, name, kind)
                symbols.append(Symbol(
                    id=sym_id,
                    name=name,
                    kind=kind,
                    language="commonlisp",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=signature,
                ))

    return symbols


def _find_enclosing_defun(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Find the enclosing defun for a given node.

    Uses local_symbols only - the caller is always in the same file.
    """
    current = node.parent
    while current:
        if current.type == "defun":
            info = _extract_defun_info(current, source)
            if info:
                name = info[0]
                sym = local_symbols.get(name)
                if sym:
                    return sym
        # Also check for list_lit containing uppercase DEFUN
        elif current.type == "list_lit":
            children = current.children
            inner = [c for c in children if c.type not in ("(", ")")]
            def_info = _is_def_list(inner, source)
            if def_info:
                kind, name, _ = def_info
                if kind in ("function", "macro", "method", "generic"):
                    sym = local_symbols.get(name)
                    if sym:
                        return sym
        current = current.parent
    return None


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed Common Lisp file.

    Detects:
    - Function calls (list_lit starting with sym_lit)
    - use-package statements
    """
    edges: list[Edge] = []
    file_id = make_file_id("commonlisp", file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    # Set of defining forms to skip
    def_forms = {
        "defun", "defmacro", "defmethod", "defgeneric",
        "defvar", "defparameter", "defconstant",
        "defclass", "defstruct", "defpackage",
        "in-package", "use-package", "export", "import",
        "let", "let*", "flet", "labels", "lambda",
        "if", "when", "unless", "cond", "case",
        "loop", "do", "dolist", "dotimes",
        "progn", "prog1", "prog2", "block", "return-from",
        "setf", "setq", "quote", "function",
    }

    for node in iter_tree(tree.root_node):
        if node.type == "list_lit":
            children = node.children
            inner = [c for c in children if c.type not in ("(", ")")]

            if inner and inner[0].type == "sym_lit":
                first_sym = _get_sym_lit_text(inner[0], source)

                # Handle use-package
                if first_sym == "use-package" and len(inner) > 1:
                    # Package name can be sym_lit or kwd_lit (like :mylib)
                    pkg_name = _get_name_from_node(inner[1], source)
                    if pkg_name:
                        pkg_id = f"commonlisp:{pkg_name}:0-0:package:package"
                        edge = Edge.create(
                            src=file_id,
                            dst=pkg_id,
                            edge_type="imports",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="use-package",
                            confidence=0.95,
                        )
                        edges.append(edge)

                # Handle function calls (not special forms or def forms)
                elif first_sym not in def_forms:
                    caller = _find_enclosing_defun(node, source, local_symbols)
                    if caller:
                        callee_name = first_sym
                        lookup_result = resolver.lookup(callee_name)
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


class CommonLispAnalyzer(TreeSitterAnalyzer):
    """Common Lisp language analyzer using tree-sitter-commonlisp.

    Uses the standalone tree_sitter_commonlisp grammar module (not
    language-pack) since Common Lisp has its own dedicated package.
    """

    lang = "commonlisp"
    file_patterns: ClassVar[list[str]] = ["*.lisp", "*.lsp", "*.cl", "*.asd"]
    grammar_module = "tree_sitter_commonlisp"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a Common Lisp file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Common Lisp file."""
        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path, file_symbols,
            resolver, run.execution_id,
        )


_analyzer = CommonLispAnalyzer()


def is_commonlisp_tree_sitter_available() -> bool:
    """Check if tree-sitter with Common Lisp grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("commonlisp")
def analyze_commonlisp(repo_root: Path) -> AnalysisResult:
    """Analyze Common Lisp files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-commonlisp is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
