# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pascal language analyzer using tree-sitter.

This module provides static analysis for Pascal source code, extracting symbols
(procedures, functions, programs, units) and edges (calls).

Pascal is a classic imperative and procedural programming language designed for
teaching structured programming. It's still widely used through Delphi, Free Pascal,
and Lazarus IDE. Modern Object Pascal supports object-oriented programming.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Collect programs (kind ``program``), units (kind ``module``) and
   every procedure/function, meta ``proc_kind`` (``function`` /
   ``procedure`` / ``constructor`` / ``destructor`` / ``operator``). A
   method implementation ``procedure TA.Run`` is kind ``method`` named
   ``TA.Run`` (WI-darik). A procedure nested in another is kind
   ``function`` with ``meta["is_local"]`` (WI-sigit).
2. Pass 2: Extract call edges from exprCall and identifier-statement patterns

Every call edge is drawn from the declaration that holds it, found by its
node's POSITION (``symbols_at`` over ``file_symbols()``): the innermost
procedure; outside every procedure, the ``program`` or ``unit`` whose main
block or ``initialization`` / ``finalization`` section holds the call, else
the file anchor (a ``library`` and a fragment emit no symbol of their own).
Before WI-darik / WI-sigit a method body's calls were lost, a nested
procedure's calls carried a src id no symbol had, and main-block calls were
dropped.

Name resolution follows Pascal's lookup order and is case-insensitive, like
Pascal itself (``_resolve_callee``): a procedure nested in an enclosing
routine first (found by position; a local procedure is never registered
run-wide), then a member of the enclosing method's class (inside ``TA.Run``
a bare ``Get`` is ``Self.Get``, registered as ``ta.get``), then the run's
routine of that name. ``PascalAnalyzer.register_symbol`` stores every other
symbol under its lowercased name. Calls to names in ``_PASCAL_BUILTINS``
(``writeln``, ``inc``, ``length``, ``inttostr``, ...) emit no edge; unmatched
calls become unresolved edges via ``make_unresolved_edge``. A call on a
receiver (``A.Run()``, ``Self.Get``) is not read: ``_get_call_name`` takes a
bare identifier only.

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Pascal-specific extraction
logic.

Key constructs extracted:
- program ... - main program definition
- unit ... - module definition (kind ``module``)
- function name(args): type - function definitions
- procedure name(args) - procedure definitions
- procedure TA.Run / constructor TA.Create ... - method implementations
- a procedure declared inside another - a local function
- name(args) - procedure/function calls
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    enclosing_declared_symbol,
    file_anchor_symbol,
    iter_tree,
    make_symbol_id,
    make_unresolved_edge,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("pascal")


def is_pascal_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Pascal support is available."""
    return _analyzer._check_grammar_available()


def find_pascal_files(root: Path) -> Iterator[Path]:
    """Find all Pascal files in the given directory."""
    extensions = ("*.pas", "*.pp", "*.dpr", "*.lpr")
    for ext in extensions:
        for path in find_files(root, [ext]):
            if path.is_file():
                yield path



def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier child of a node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_proc_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the name of a procedure/function from a defProc node.

    A method implementation names itself with a ``genericDot``
    (``procedure TA.Run``): its name is the whole dotted text, ``TA.Run``
    (WI-darik). A plain one with a bare ``identifier``.
    """
    for child in node.children:
        if child.type == "declProc":
            for sub in child.children:
                if sub.type == "genericDot":
                    return "".join(_get_node_text(sub).split())
            return _get_identifier(child)
    return None  # pragma: no cover


def _proc_owner(name: str) -> Optional[str]:
    """The class a qualified procedure name belongs to: ``TA`` for ``TA.Run``,
    ``TA.TInner`` for ``TA.TInner.Run``; None for an unqualified name."""
    owner, dot, _ = name.rpartition(".")
    return owner if dot else None


#: The routine keyword of a ``declProc`` -> ``meta["proc_kind"]``.
_PROC_KEYWORDS = {
    "kFunction": "function",
    "kProcedure": "procedure",
    "kConstructor": "constructor",
    "kDestructor": "destructor",
    "kOperator": "operator",
}


def _get_proc_kind(node: "tree_sitter.Node") -> str:
    """Which routine keyword a defProc uses (``function``, ``procedure``,
    ``constructor``, ``destructor`` or ``operator``)."""
    for child in node.children:
        if child.type == "declProc":
            for subchild in child.children:
                if subchild.type in _PROC_KEYWORDS:
                    return _PROC_KEYWORDS[subchild.type]
    return "procedure"  # default  # pragma: no cover


def _get_proc_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a defProc node."""
    params = []
    for child in node.children:
        if child.type == "declProc":
            for subchild in child.children:
                if subchild.type == "declArgs":
                    for arg in subchild.children:
                        if arg.type == "declArg":
                            for arg_child in arg.children:
                                if arg_child.type == "identifier":
                                    params.append(_get_node_text(arg_child))
    return params


def _get_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Get the return type of a function from a defProc node."""
    for child in node.children:
        if child.type == "declProc":
            found_colon = False
            for subchild in child.children:
                if subchild.type == ":":
                    found_colon = True
                elif found_colon and subchild.type == "typeref":
                    return _get_identifier(subchild)
    return None


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function/procedure name from an exprCall node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _proc_ancestors(node: "tree_sitter.Node") -> Iterator["tree_sitter.Node"]:
    """The ``defProc`` nodes enclosing ``node``, innermost first."""
    current = node.parent
    while current is not None:
        if current.type == "defProc":
            yield current
        current = current.parent


def _find_caller(
    node: "tree_sitter.Node", index: SymbolsAt, file_anchor: Symbol,
) -> Optional[Symbol]:
    """The symbol a call at ``node`` is drawn from, found by POSITION.

    The innermost enclosing ``defProc`` -- Pass 1 emits a symbol for every one,
    nested and qualified included, so the id is never minted for a declaration
    that has no node (WI-sigit). Outside every ``defProc``: the ``program`` or
    ``unit`` whose main block / ``initialization`` / ``finalization`` holds the
    call, else the file anchor (a ``library`` or a fragment emits no symbol of
    its own).
    """
    for proc in _proc_ancestors(node):
        return symbol_declared_by(proc, index)
    return enclosing_declared_symbol(node, index, _MODULE_NODES) or file_anchor


#: The compilation units Pass 1 emits a symbol for.
_MODULE_NODES = frozenset({"program", "unit"})


def _resolve_callee(
    node: "tree_sitter.Node", call_name: str, index: SymbolsAt,
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """The routine a bare call names, in Pascal's lookup order.

    1. A procedure nested in an enclosing routine (innermost first): it is
       visible only there, so it is never registered globally and is found by
       its declaration node's POSITION.
    2. A member of the enclosing method's class: inside ``TA.Run`` a bare
       ``Get`` is ``Self.Get`` (registered as ``ta.get``).
    3. The run's routine of that name.
    """
    key = call_name.lower()
    owner: Optional[str] = None
    for proc in _proc_ancestors(node):
        for child in proc.children:
            if child.type == "defProc" and (_get_proc_name(child) or "").lower() == key:
                return symbol_declared_by(child, index)
        if owner is None:
            owner = _proc_owner(_get_proc_name(proc) or "")
    if owner is not None:
        member = global_symbols.get(f"{owner}.{key}".lower())
        if isinstance(member, Symbol):
            return member
    found = global_symbols.get(key)
    return found if isinstance(found, Symbol) else None


# Pascal builtins to skip during edge extraction
_PASCAL_BUILTINS = {
    # I/O
    "write", "writeln", "read", "readln", "readkey",
    # Memory
    "new", "dispose", "getmem", "freemem", "setlength",
    # String
    "length", "copy", "delete", "insert", "pos", "concat",
    "uppercase", "lowercase", "trim", "stringreplace",
    # Math
    "inc", "dec", "abs", "sqr", "sqrt", "sin", "cos", "tan",
    "exp", "ln", "log", "power", "round", "trunc", "frac",
    "random", "randomize",
    # Conversion
    "ord", "chr", "inttostr", "strtoint", "floattostr",
    "strtofloat", "formatfloat", "format",
    # System
    "halt", "exit", "break", "continue", "sleep",
    "assigned", "sizeof", "typeof", "high", "low",
    # File
    "assign", "reset", "rewrite", "append", "close",
    "eof", "eoln", "fileexists",
    # Array
    "fillchar", "move",
}


# ---------------------------------------------------------------------------
# PascalAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class PascalAnalyzer(TreeSitterAnalyzer):
    """Pascal language analyzer using tree-sitter-language-pack."""

    lang = "pascal"
    file_patterns: ClassVar[list[str]] = ["*.pas", "*.pp", "*.dpr", "*.lpr"]
    language_pack_name = "pascal"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract program, unit, function, and procedure symbols from Pascal."""
        analysis = FileAnalysis()
        # WI-bokab (v7): file-identity anchor for this file's symbols. ``rel_path``
        # is repo-relative (the base two-pass orchestrator passes it). Folded into
        # compute_stable_id's containing slot so same-name programs/units/functions
        # in different files hash distinctly.
        file_anchor = self._file_anchor(rel_path)
        self._extract_symbols_recursive(
            tree.root_node, rel_path, analysis, file_anchor,
        )
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
        file_anchor: str, enclosing: Optional[str] = None,
    ) -> None:
        """Recursively extract symbols. ``enclosing`` is the name of the routine
        whose declarations are being walked (None at unit / program level)."""
        if node.type == "program":
            name = _get_identifier(node)
            if name is None:
                for child in node.children:
                    if child.type == "moduleName":
                        name = _get_node_text(child)
                        break
            if name:
                sym_id = make_symbol_id(
                    "pascal", rel_path,
                    node.start_point[0] + 1, node.end_point[0] + 1,
                    name, "program",
                )
                sym = Symbol(
                    id=sym_id,
                    stable_id=self.compute_stable_id(
                        node, kind="program", name=name,
                        file_stable_id=file_anchor,
                    ),
                    name=name,
                    kind="program",
                    language="pascal",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "unit":
            name = None
            for child in node.children:
                if child.type == "moduleName":
                    name = _get_identifier(child)
                    if name is None:  # pragma: no cover
                        name = _get_node_text(child)
                    break
            if name:
                sym_id = make_symbol_id(
                    "pascal", rel_path,
                    node.start_point[0] + 1, node.end_point[0] + 1,
                    name, "module",
                )
                sym = Symbol(
                    id=sym_id,
                    stable_id=self.compute_stable_id(
                        node, kind="module", name=name,
                        file_stable_id=file_anchor,
                    ),
                    name=name,
                    kind="module",
                    language="pascal",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "defProc":
            name = _get_proc_name(node)
            if name:
                self._emit_proc(node, name, rel_path, analysis, file_anchor, enclosing)
                enclosing = name
            # A nested procedure is a declaration of its own (WI-sigit): descend
            # with this one as its parent, so it is emitted as a local symbol.

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, rel_path, analysis, file_anchor, enclosing,
            )

    def _emit_proc(
        self, node: "tree_sitter.Node", name: str, rel_path: str,
        analysis: FileAnalysis, file_anchor: str, enclosing: Optional[str],
    ) -> None:
        """Emit the symbol for one ``defProc``.

        A qualified name (``TA.Run``) is a method of ``TA`` (WI-darik). A
        procedure nested in another is a ``function`` with ``meta["is_local"]``:
        it is visible only inside its parent, so it is not registered for
        run-wide lookup (``register_symbol``) and its stable id folds in the
        parent's name, so ``Inner`` in ``A`` and ``Inner`` in ``B`` hash apart.
        """
        proc_kind = _get_proc_kind(node)
        params = _get_proc_params(node)
        return_type = _get_return_type(node)
        if proc_kind == "function":
            signature = f"function {name}({', '.join(params)}): {return_type or 'unknown'}"
        else:
            signature = f"{proc_kind} {name}({', '.join(params)})"
        kind = "method" if _proc_owner(name) else "function"
        meta: dict[str, object] = {"param_count": len(params), "proc_kind": proc_kind}
        if enclosing is not None:
            meta["is_local"] = True
        sym = Symbol(
            id=make_symbol_id(
                "pascal", rel_path,
                node.start_point[0] + 1, node.end_point[0] + 1,
                name, kind,
            ),
            stable_id=self.compute_stable_id(
                node, kind=kind, name=name,
                qualified_name=f"{enclosing}.{name}" if enclosing else "",
                file_stable_id=file_anchor,
            ),
            name=name,
            kind=kind,
            language="pascal",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            signature=signature,
            meta=meta,
            cyclomatic_complexity=compute_cyclomatic_complexity(node, "pascal"),
            line_span=node.end_point[0] - node.start_point[0] + 1,
        )
        analysis.symbols.append(sym)
        if enclosing is None:
            analysis.symbol_by_name[name] = sym
        analysis.node_for_symbol[sym.id] = node

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Register symbols with lowercase names for case-insensitive matching.

        A nested procedure is not registered: it is in scope only inside its
        parent, where ``_resolve_callee`` finds it by position. Registered, a
        local ``Inner`` would capture every unit-level call to ``Inner``.
        """
        if (symbol.meta or {}).get("is_local"):
            return
        global_symbols[symbol.name.lower()] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from Pascal exprCall and statement nodes."""
        edges: list[Edge] = []
        index = symbols_at(self.file_symbols(local_symbols))
        file_anchor = file_anchor_symbol("pascal", rel_path, PASS_ID, run.execution_id)
        for node in iter_tree(tree.root_node):
            call_name = None
            call_node = node
            if node.type == "exprCall":
                call_name = _get_call_name(node)
            elif node.type == "statement":
                children = [c for c in node.children if c.type not in (";",)]
                if len(children) == 1 and children[0].type == "identifier":
                    call_name = _get_node_text(children[0])
                    call_node = children[0]
            if not call_name or call_name.lower() in _PASCAL_BUILTINS:
                continue
            caller = _find_caller(node, index, file_anchor)
            if caller is None:  # pragma: no cover - every named defProc is a symbol
                continue
            callee = _resolve_callee(node, call_name, index, global_symbols)
            if callee is not None:
                edges.append(Edge.create(
                    src=caller.id,
                    dst=callee.id,
                    edge_type="calls",
                    line=call_node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    evidence_lang="pascal",
                ))
            else:
                edges.append(make_unresolved_edge(
                    "pascal", caller.id, call_name,
                    call_node.start_point[0] + 1,
                    PASS_ID, run.execution_id,
                ))
        return edges


_analyzer = PascalAnalyzer()


@register_analyzer("pascal", language_state="no_taxonomy_spec")  # WI-futin
def analyze_pascal(repo_root: Path) -> AnalysisResult:
    """Analyze Pascal source files in a repository."""
    return _analyzer.analyze(repo_root)
