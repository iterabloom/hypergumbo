# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fortran analysis pass using tree-sitter-fortran.

This analyzer uses tree-sitter to parse Fortran files and extract:
- Module definitions
- Program definitions
- Function definitions
- Subroutine definitions
- Derived type definitions
- Use statements (imports)
- Subroutine calls (the explicit `call foo(...)` statement form; function calls in expression context are not emitted as call edges)

If tree-sitter-fortran is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract modules, programs, functions, subroutines, types with signatures
2. Pass 2: Extract call edges and import edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Fortran-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-fortran package for grammar (grammar_module)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Fortran-Specific Considerations
-------------------------------
- Fortran is used in scientific computing, HPC, and legacy codebases
- Modules organize code hierarchically
- Subroutines and functions are first-class constructs
- USE statements import modules (like imports)
- Derived types provide structured data
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("fortran")

# Fortran file extensions
FORTRAN_EXTENSIONS = ["*.f", "*.f90", "*.f95", "*.f03", "*.f08", "*.F", "*.F90", "*.F95", "*.F03", "*.F08", "*.for", "*.fpp"]

# Program-unit containers whose ``variable_declaration`` children are top-level
# state -> ``kind="variable"`` (WI-jusus). A ``variable_declaration`` whose
# effective scope is a ``derived_type_definition`` is a field; one whose scope
# is a ``subroutine``/``function``/``block_construct``/interface body is a local
# or parameter-type declaration and is skipped. ``block_data`` is included: its
# declarations are COMMON-block / global state, not procedure locals.
# Classification walks past transparent preprocessor conditionals (see
# ``_effective_scope``) — a ``#ifdef``-guarded module variable is still module
# state — so it is a scope-WALK, not a bare immediate-parent check.
_FORTRAN_VARIABLE_SCOPES = frozenset({"module", "program", "submodule", "block_data"})

# The <x>_statement opening a top-level program unit. Used to recover the unit
# scope when an unparseable trailing statement made the parser emit the whole
# unit as an ERROR node (see ``_scope_is_variable_unit``).
_FORTRAN_UNIT_STATEMENTS = frozenset({
    "module_statement", "program_statement", "submodule_statement",
})

# Declarator wrapper nodes: a ``variable_declaration`` binds each name through
# one of these (or a bare ``identifier``). The NAME is invariably the LEFTMOST
# child of the wrapper; the non-name portion — an array ``size``, a
# ``coarray_size`` codimension, a pointer-association RHS (``=>`` target), or an
# initializer (``=`` value) — always follows it. So descending the leftmost
# child alone reaches the name and never the target/dimensions/value.
_FORTRAN_DECLARATOR_WRAPPERS = frozenset({
    "sized_declarator", "pointer_init_declarator",
    "init_declarator", "coarray_declarator",
})


def find_fortran_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Fortran files in the repository."""
    yield from find_files(repo_root, FORTRAN_EXTENSIONS)


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a definition node."""
    for child in node.children:
        if child.type == "name":
            return node_text(child, source).lower()
    return None  # pragma: no cover


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract type name from derived_type_definition."""
    for child in node.children:
        if child.type == "derived_type_statement":
            for grandchild in child.children:
                if grandchild.type == "type_name":
                    return node_text(grandchild, source).lower()
    return None  # pragma: no cover


def _get_statement_name(node: "tree_sitter.Node", source: bytes, stmt_type: str) -> Optional[str]:
    """Extract name from a statement node (function_statement, subroutine_statement, etc.)."""
    for child in node.children:
        if child.type == stmt_type:
            for grandchild in child.children:
                if grandchild.type == "name":
                    return node_text(grandchild, source).lower()
    return None


def _declarator_name(child: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Return the lowercased declared name from a single declarator child, or None.

    A ``variable_declaration`` binds each name through one of five declarator
    shapes (bundled ``tree_sitter_fortran``), which nest arbitrarily:
    - a bare ``identifier`` (``integer :: x``)
    - a ``sized_declarator`` — inline-dimensioned array (``real :: grid(nx,ny)``,
      ``real, allocatable :: w(:)``); the trailing ``size`` holds the dims
    - a ``coarray_declarator`` — coarray (``real :: field(10)[*]``); the trailing
      ``coarray_size`` holds the codimensions
    - a ``pointer_init_declarator`` (``integer, pointer :: q(:) => null()``); the
      part after ``=>`` is the association TARGET, not the name
    - an ``init_declarator`` (``PI = 3.14`` / ``arr(3) = [1,2,3]``); the part after
      ``=`` is the initializer VALUE

    The name is the LEFTMOST child of a wrapper (recursively for nested wrappers
    such as ``init_declarator`` > ``sized_declarator``), so descending
    ``children[0]`` alone reaches the name and never the array dimensions,
    coarray codimensions, pointer target, or initializer value that follow it.
    Any other child (``::``, ``,``, ``intrinsic_type``, ``type_qualifier``)
    yields None.
    """
    if child.type == "identifier":
        return node_text(child, source).lower()
    if child.type in _FORTRAN_DECLARATOR_WRAPPERS:
        return _declarator_name(child.children[0], source)
    return None


def _effective_scope(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """Walk up past transparent preprocessor conditionals to the enclosing scope.

    The bundled ``tree_sitter_fortran`` grammar does NOT flatten preprocessor
    conditionals, so in a ``.F``/``.F90``/``.fpp`` file a ``variable_declaration``
    inside ``#ifdef``/``#ifndef``/``#if``/``#elif``/``#else`` has a ``preproc_*``
    immediate parent. These are transparent for scope classification — a
    conditionally-compiled module variable is still module state, a guarded
    derived-type component is still a field. Returns the nearest non-preproc
    ancestor (the effective scope), or None.
    """
    current = node.parent
    while current is not None and current.type.startswith("preproc_"):
        current = current.parent
    return current


def _scope_is_variable_unit(scope: "tree_sitter.Node") -> bool:
    """True if ``scope`` is a top-level program unit whose direct declarations
    are module/program/submodule/block_data state.

    Includes an ``ERROR`` node the parser produced IN PLACE OF a unit: an
    unparseable trailing statement (a standalone F2003 ``asynchronous ::`` /
    F2008 ``codimension ::`` spec statement) can prevent the parser from
    recognizing a whole module/program, leaving an ERROR node that still holds
    the unit's ``<x>_statement`` and its declaration nodes. Recovering the unit
    scope for those declarations is relaxation-only: it only ADDS valid module
    variables — it never drops one and never mis-owns (a variable has no owner).
    A malformed non-unit construct (an ERROR-wrapped subroutine) has no unit
    statement, so its locals stay excluded.
    """
    if scope.type in _FORTRAN_VARIABLE_SCOPES:
        return True
    return scope.type == "ERROR" and any(
        child.type in _FORTRAN_UNIT_STATEMENTS for child in scope.children
    )


def _is_pdt_type_parameter(node: "tree_sitter.Node", source: bytes) -> bool:
    """True for a parameterized-derived-type KIND/LEN parameter declaration.

    ``type :: matrix(k, n); integer, kind :: k; integer, len :: n; …`` — the
    ``k``/``n`` declarations carry a bare ``kind``/``len`` ``type_qualifier``.
    They are parameterization slots (F2003 4.5.2), NOT data components, so they
    must never become ``field``/``variable`` symbols. A bare ``kind``/``len``
    qualifier appears only in this position, so the match is PDT-specific.
    """
    for child in node.children:
        if child.type == "type_qualifier" and node_text(child, source).lower() in ("kind", "len"):
            return True
    return False


def _iter_vardecl_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Yield the lowercased declared names from a ``variable_declaration``.

    A single declaration may bind several names of mixed declarator shapes
    (``real :: a, b(3), c`` -> three names via identifier / sized_declarator /
    identifier). See ``_declarator_name`` for the shapes.

    The firm contract has two halves, and their priority is asymmetric:

    * NEVER drop a valid symbol on compilable code (a recall loss is the worst
      outcome), and
    * best-effort suppress phantoms on malformed / non-standard input.

    That priority rules OUT a ``node.has_error`` / ``scope.has_error`` deferral:
    the bundled ``tree_sitter_fortran`` grammar sets ``has_error`` on VALID
    F2003 it does not support — ``integer, asynchronous :: buf`` (ubiquitous in
    MPI-3 nonblocking I/O) parses with an ERROR child around ``asynchronous`` —
    so any ``has_error`` gate silently drops those valid declarations. Only
    signals that CANNOT fire on compilable code are used:

    1. An attribute list without the mandatory ``::`` (``real, target dimension(3)``):
       F90+ requires ``::`` whenever an attribute is present, so this is a
       misparse that harvests the attribute (``dimension``) as a phantom name.
       Gate on the ATTR LIST, not the name, so a valid no-attribute F77 array
       literally named ``dimension`` (Fortran has no reserved words) still emits.
    2. Harvest only the CLEAN PREFIX of declarator children: stop at the first
       in-vardecl ``ERROR`` child. On a clean parse this is the full declarator
       list (no ERROR child). On a grammar-unsupported but COMPILABLE construct
       it recovers the valid leading name(s) that have clean nodes — a fixed-form
       column-6 continuation's leading name (``INTEGER COUNT,`` -> ``count``;
       ``TOTAL`` stays buried in the trailing ERROR) — while suppressing a keyword
       fragment that FOLLOWS an ERROR child (inline ``asynchronous :: buf`` ->
       the fragment ``synchronous``) and garbage after a mid-declaration ERROR
       (``integer :: @#$ broken``). A ``child_count``-based "keyword wrapper"
       skip is deliberately NOT used: a VALID kind-selected name parses the same
       way (``real(dp) :: value`` gives ``value`` a child), so it would drop
       valid symbols. This provably never drops a valid clean-node name on
       compilable code and never re-admits an after-ERROR fragment.

    A symmetric prev-sibling guard was deliberately NOT kept: the only phantom it
    caught (a DEC/VAX ``structure`` component) shares its ERROR-node boundary
    (via a swallowed trailing newline) with a fully-VALID declaration on the next
    line (a standalone F2003 ``asynchronous ::`` / F2008 ``codimension ::``
    statement is such an ERROR), so any prev-sibling gate that suppressed the
    phantom also dropped the valid follower — a recall loss. Dropping it trades
    that for re-admitting the accepted phantom below.

    Accepted residuals (spurious EXTRA, never a wrong EDGE — field/variable stay
    out of call resolution; no crash; no VALID symbol dropped):
    - DEC/VAX ``STRUCTURE``/``RECORD``/``UNION``/``MAP`` (non-standard legacy
      extensions the bundled grammar cannot parse): inner components may leak a
      phantom module variable. No safe suppression signal exists (both candidate
      signals — end-statement-name-mismatch and synthesized-program-unit — fire
      on VALID constructs: ``END FILE 10``, a valid headerless main).
    - A name the grammar buries INSIDE an ERROR node (a fixed-form CONTINUED name
      ``TOTAL``, the inline-async name ``buf``) is unrecoverable — a grammar
      RECALL limit, not a guard misfire; nothing is emitted for it.
    """
    has_qualifier = any(child.type == "type_qualifier" for child in node.children)
    has_double_colon = any(child.type == "::" for child in node.children)
    if has_qualifier and not has_double_colon:
        return []
    names: list[str] = []
    for child in node.children:
        if child.type == "ERROR":
            break
        name = _declarator_name(child, source)
        if name is not None:
            names.append(name)
    return names


def _extract_vardecl_type(node: "tree_sitter.Node", source: bytes) -> str:
    """Return the declared type of a ``variable_declaration`` as its signature.

    The type specifier is invariably the FIRST child of the declaration —
    ``intrinsic_type`` (``integer``/``real``/``character(len=10)``),
    ``derived_type`` (``type(point)``/``class(shape)``), or ``procedure``
    (``procedure(iface)`` for a procedure pointer). Returned verbatim so the
    type name keeps its case. Only ever called for a name-bearing (hence
    child-bearing) declaration, so ``children[0]`` is safe.
    """
    return node_text(node.children[0], source)


def _make_data_symbol(
    name: str,
    kind: str,
    decl_node: "tree_sitter.Node",
    rel_path: str,
    run_id: str,
    signature: Optional[str],
) -> Symbol:
    """Build a field/variable Symbol anchored on a ``variable_declaration`` node."""
    start_line = decl_node.start_point[0] + 1
    end_line = decl_node.end_point[0] + 1
    symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, kind)
    return Symbol(
        id=symbol_id,
        stable_id=None,
        shape_id=None,
        fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this
        kind=kind,
        name=name,
        path=rel_path,
        language="fortran",
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=decl_node.start_point[1],
            end_col=decl_node.end_point[1],
        ),
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=signature,
    )


def _extract_fortran_signature(
    node: "tree_sitter.Node", source: bytes, is_function: bool = True
) -> Optional[str]:
    """Extract function/subroutine signature from a Fortran function/subroutine node.

    Returns signature like:
    - "(x, y): integer" for functions with known return type
    - "(message)" for subroutines (no return type)

    Note: Fortran declares parameter types separately, so we collect parameter names
    from the function/subroutine statement and try to find their types from
    variable declarations.

    Args:
        node: The function or subroutine node.
        source: The source code bytes.
        is_function: True for functions, False for subroutines.

    Returns:
        The signature string, or None if extraction fails.
    """
    param_names: list[str] = []
    result_var: Optional[str] = None
    param_types: dict[str, str] = {}

    # First pass: collect parameter names and result variable from statement
    stmt_type = "function_statement" if is_function else "subroutine_statement"
    for child in node.children:
        if child.type == stmt_type:
            for grandchild in child.children:
                if grandchild.type == "parameters":
                    for param_child in grandchild.children:
                        if param_child.type == "identifier":
                            param_names.append(node_text(param_child, source).lower())
                elif grandchild.type == "function_result":
                    for result_child in grandchild.children:
                        if result_child.type == "identifier":
                            result_var = node_text(result_child, source).lower()

    # Second pass: collect type declarations
    for child in node.children:
        if child.type == "variable_declaration":
            var_type: Optional[str] = None
            var_names: list[str] = []

            for decl_child in child.children:
                if decl_child.type == "intrinsic_type":
                    for type_child in decl_child.children:
                        if type_child.type in ("integer", "real", "character", "logical",
                                               "double", "complex"):
                            var_type = type_child.type
                            break
                elif decl_child.type == "identifier":
                    var_names.append(node_text(decl_child, source).lower())

            if var_type and var_names:
                for vn in var_names:
                    param_types[vn] = var_type

    # Build the signature
    params_str = ", ".join(param_names)
    signature = f"({params_str})"

    # For functions, try to get the return type from the result variable
    if is_function and result_var and result_var in param_types:
        signature += f": {param_types[result_var]}"

    return signature


def _get_enclosing_fortran_symbol(
    node: "tree_sitter.Node",
    source: bytes,
    symbol_registry: dict[str, str],
) -> Optional[str]:
    """Walk up to find the enclosing symbol (module, program, function, subroutine)."""
    current = node.parent
    while current is not None:
        if current.type == "module":  # pragma: no cover - call context
            for child in current.children:  # pragma: no cover - call context
                if child.type == "module_statement":  # pragma: no cover - call context
                    name = _get_name(child, source)  # pragma: no cover - call context
                    if name and name in symbol_registry:  # pragma: no cover - call context
                        return symbol_registry[name]  # pragma: no cover - call context
        elif current.type == "program":
            for child in current.children:
                if child.type == "program_statement":
                    name = _get_name(child, source)
                    if name and name in symbol_registry:
                        return symbol_registry[name]
        elif current.type == "function":  # pragma: no cover - call context
            name = _get_statement_name(current, source, "function_statement")  # pragma: no cover - call context
            if name and name in symbol_registry:  # pragma: no cover - call context
                return symbol_registry[name]  # pragma: no cover - call context
        elif current.type == "subroutine":  # pragma: no cover - call context
            name = _get_statement_name(current, source, "subroutine_statement")  # pragma: no cover - call context
            if name and name in symbol_registry:  # pragma: no cover - call context
                return symbol_registry[name]  # pragma: no cover - call context
        current = current.parent  # pragma: no cover - loop continuation
    return None  # pragma: no cover - defensive


def _extract_fortran_symbols(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    run_id: str,
    symbols: list[Symbol],
    symbol_registry: dict[str, Symbol],
) -> None:
    """Extract symbols from Fortran AST tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        run_id: The execution ID for provenance
        symbols: List to append symbols to
        symbol_registry: Registry mapping symbol names to Symbol objects
    """
    for node in iter_tree(root_node):
        # Module definitions
        if node.type == "module":
            name = None
            for child in node.children:
                if child.type == "module_statement":
                    name = _get_name(child, source)
                    break

            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "module")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
                    kind="module",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Program definitions
        elif node.type == "program":
            name = None
            for child in node.children:
                if child.type == "program_statement":
                    name = _get_name(child, source)
                    break

            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "program")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
                    kind="program",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Function definitions
        elif node.type == "function":
            name = _get_statement_name(node, source, "function_statement")
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "function")

                # Extract signature
                signature = _extract_fortran_signature(node, source, is_function=True)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
                    kind="function",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=signature,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "fortran"),
                    line_span=end_line - start_line + 1,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Subroutine definitions
        elif node.type == "subroutine":
            name = _get_statement_name(node, source, "subroutine_statement")
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "subroutine")

                # Extract signature
                signature = _extract_fortran_signature(node, source, is_function=False)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
                    kind="subroutine",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=signature,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "fortran"),
                    line_span=end_line - start_line + 1,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Derived type definitions
        elif node.type == "derived_type_definition":
            name = _get_type_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("fortran", rel_path, start_line, end_line, name, "type")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
                    kind="type",
                    name=name,
                    path=rel_path,
                    language="fortran",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                symbol_registry[name] = sym

        # Derived-type fields + module/program/submodule variables (WI-jusus).
        # These are DATA anchors: appended to ``symbols`` (so they reach
        # output/search/centrality) but NOT to ``symbol_registry`` and skipped
        # by ``register_symbol`` (so they never enter call resolution).
        elif node.type == "variable_declaration":
            if _is_pdt_type_parameter(node, source):
                continue  # PDT kind/len parameter -> a type slot, not a data component
            names = _iter_vardecl_names(node, source)
            if not names:
                continue  # ERROR-deferred or nameless
            signature = _extract_vardecl_type(node, source)
            scope = _effective_scope(node)
            if scope is None:
                continue  # pragma: no cover - a variable_declaration is always within a unit
            if scope.type == "derived_type_definition":
                owner = _get_type_name(scope, source)
                if owner:
                    for var_name in names:
                        symbols.append(_make_data_symbol(
                            f"{owner}.{var_name}", "field",
                            node, rel_path, run_id, signature,
                        ))
            elif _scope_is_variable_unit(scope):
                for var_name in names:
                    symbols.append(_make_data_symbol(
                        var_name, "variable",
                        node, rel_path, run_id, signature,
                    ))
            # else: subroutine/function local, block-construct local, or
            # interface-body parameter -> skipped.


def _extract_use_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases from Fortran use statements (ADR-0007).

    Fortran renaming syntax in use statements:
        use linear_algebra, only: my_solve => solve
        use std_io, only: print_msg, my_read => read

    The use_alias node contains:
    - local_name: the alias (e.g., "my_solve")
    - identifier: the original name (e.g., "solve")

    Returns:
        Dict mapping alias -> "module_name.original_name" for path_hint resolution.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "use_statement":
            # Get the module name
            module_name = None
            for child in node.children:
                if child.type == "module_name":
                    module_name = node_text(child, source).lower()
                    break

            if not module_name:
                continue  # pragma: no cover - use_statement always has module_name

            # Look for included_items with use_alias children
            for child in node.children:
                if child.type == "included_items":
                    for item in child.children:
                        if item.type == "use_alias":
                            local_name = None
                            original_name = None
                            for alias_child in item.children:
                                if alias_child.type == "local_name":
                                    local_name = node_text(alias_child, source).lower()
                                elif alias_child.type == "identifier":
                                    original_name = node_text(alias_child, source).lower()
                            if local_name and original_name:
                                # Map alias to qualified path: module.original
                                aliases[local_name] = f"{module_name}.{original_name}"

    return aliases


def _extract_fortran_edges(
    root_node: "tree_sitter.Node",
    source: bytes,
    edges: list[Edge],
    local_symbols: dict[str, Symbol],
    resolver: "NameResolver",
    run_id: str,
    import_aliases: dict[str, str] | None = None,
) -> None:
    """Extract edges from Fortran AST tree (pass 2).

    Uses NameResolver for callee resolution to enable cross-file symbol lookup.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        edges: List to append edges to
        local_symbols: Local symbol registry for finding enclosing functions
        resolver: NameResolver for callee resolution
        run_id: The execution ID for provenance
        import_aliases: Mapping of alias -> module.original for path_hint (ADR-0007)
    """
    if import_aliases is None:
        import_aliases = {}  # pragma: no cover - always passed by caller
    # Build local ID registry for _get_enclosing_fortran_symbol
    local_id_registry: dict[str, str] = {name: sym.id for name, sym in local_symbols.items()}

    for node in iter_tree(root_node):
        # Use statements (imports)
        if node.type == "use_statement":
            mod_name = None
            for child in node.children:
                if child.type == "module_name":
                    mod_name = node_text(child, source).lower()
                    break

            current_symbol = _get_enclosing_fortran_symbol(node, source, local_id_registry)
            if mod_name and current_symbol:
                start_line = node.start_point[0] + 1
                # Use resolver for callee resolution
                lookup_result = resolver.lookup(mod_name)
                if lookup_result.found and lookup_result.symbol:
                    dst_id = lookup_result.symbol.id
                    confidence = 0.90 * lookup_result.confidence
                else:
                    # External module not in our codebase
                    dst_id = f"fortran:external:{mod_name}"
                    confidence = 0.70

                edge = Edge.create(
                    src=current_symbol,
                    dst=dst_id,
                    edge_type="imports",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="static",
                )
                edges.append(edge)

        # Subroutine calls
        elif node.type == "subroutine_call":
            call_name = None
            for child in node.children:
                if child.type == "identifier":
                    call_name = node_text(child, source).lower()
                    break

            current_symbol = _get_enclosing_fortran_symbol(node, source, local_id_registry)
            if call_name and current_symbol:
                start_line = node.start_point[0] + 1
                # Use path_hint from import aliases if available (ADR-0007)
                path_hint = import_aliases.get(call_name)
                # Use resolver for callee resolution
                lookup_result = resolver.lookup(call_name, path_hint=path_hint)
                if lookup_result.found and lookup_result.symbol:
                    dst_id = lookup_result.symbol.id
                    confidence = 0.90 * lookup_result.confidence
                else:
                    # External subroutine not in our codebase
                    dst_id = f"fortran:external:{call_name}"
                    confidence = 0.70

                edge = Edge.create(
                    src=current_symbol,
                    dst=dst_id,
                    edge_type="calls",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="static",
                )
                edges.append(edge)


class FortranAnalyzer(TreeSitterAnalyzer):
    """Fortran language analyzer using tree-sitter-fortran."""

    lang = "fortran"
    file_patterns: ClassVar[list[str]] = FORTRAN_EXTENSIONS
    grammar_module = "tree_sitter_fortran"
    create_file_symbols = False

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Keep ``field``/``variable`` data anchors OUT of call resolution (WI-jusus).

        Fortran is imperative, so BOTH kinds must be skipped: a derived-type
        ``field`` (``box.label``) would let the ``NameResolver`` suffix index
        mint a wrong ``calls`` edge for a bare ``call label()``, and a module
        ``variable`` (``compute``) would EXACT-match a bare ``call compute()``
        and clobber a same-named subroutine. Neither is ever a call target.
        Both still reach output/search/centrality because the output symbol set
        is assembled from ``analysis.symbols`` independently of this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        super().register_symbol(symbol, global_symbols)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract modules, programs, functions, subroutines, types from a Fortran file."""
        analysis = FileAnalysis()

        _extract_fortran_symbols(
            tree.root_node, source, rel_path, run.execution_id,
            analysis.symbols, analysis.symbol_by_name,
        )

        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Fortran use-statement aliases."""
        return _extract_use_aliases(tree.root_node, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Fortran file."""
        edges: list[Edge] = []
        _extract_fortran_edges(
            tree.root_node, source, edges,
            local_symbols, resolver, run.execution_id,
            import_aliases=import_aliases,
        )
        return edges


_analyzer = FortranAnalyzer()


def is_fortran_tree_sitter_available() -> bool:
    """Check if tree-sitter with Fortran grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("fortran")
def analyze_fortran_files(repo_root: Path) -> AnalysisResult:
    """Analyze Fortran files in the repository."""
    return _analyzer.analyze(repo_root)
