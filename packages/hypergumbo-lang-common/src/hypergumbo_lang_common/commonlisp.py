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
1. Check if tree-sitter-commonlisp is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and use-package statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-commonlisp for grammar
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

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "commonlisp-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_commonlisp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Common Lisp files in the repository."""
    yield from find_files(repo_root, ["*.lisp", "*.lsp", "*.cl", "*.asd"])


def is_commonlisp_tree_sitter_available() -> bool:
    """Check if tree-sitter with Common Lisp grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_commonlisp") is None:
        return False  # pragma: no cover - commonlisp grammar not installed
    try:
        import tree_sitter
        import tree_sitter_commonlisp
        tree_sitter.Language(tree_sitter_commonlisp.language())
        return True
    except Exception:  # pragma: no cover - grammar loading failed
        return False


@dataclass
class CommonLispAnalysisResult:
    """Result of analyzing Common Lisp files."""

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
    return f"commonlisp:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Common Lisp file node (used as import edge source)."""
    return f"commonlisp:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive fallback


def _get_sym_lit_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a sym_lit node."""
    return _node_text(node, source).lower()  # Common Lisp is case-insensitive


def _get_name_from_node(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract name from either sym_lit or kwd_lit node."""
    if node.type == "sym_lit":
        return _get_sym_lit_text(node, source)
    elif node.type == "kwd_lit":
        # Keyword like :myapp.core - keep the colon
        return _node_text(node, source).lower()
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
    header = _find_child_by_type(node, "defun_header")
    if not header:
        return None  # pragma: no cover - malformed defun

    header_text = _node_text(header, source).strip()
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
                sig_text = _node_text(inner[2], source)
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
                sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
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
                sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
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
    file_id = _make_file_id(file_path)

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


def analyze_commonlisp(repo_root: Path) -> CommonLispAnalysisResult:
    """Analyze Common Lisp files in a repository.

    Returns a CommonLispAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-commonlisp is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_commonlisp_tree_sitter_available():  # pragma: no cover - tested via mock
        skip_reason = (
            "Common Lisp analysis skipped: requires tree-sitter-commonlisp "
            "(pip install tree-sitter-commonlisp)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return CommonLispAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_commonlisp

    lang = tree_sitter.Language(tree_sitter_commonlisp.language())
    parser = tree_sitter.Parser(lang)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for lisp_file in find_commonlisp_files(repo_root):
        try:
            source = lisp_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(lisp_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="commonlisp",
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
    resolver = NameResolver(global_symbol_registry)

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            resolver,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return CommonLispAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
