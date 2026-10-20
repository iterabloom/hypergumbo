# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLVM IR analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse LLVM IR files (.ll) and extract:
- Function definitions (define)
- Function declarations (declare)
- Global variable definitions
- Function call relationships

If tree-sitter with LLVM IR support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Parse all files, extract all symbols into global registry
- Pass 2: Walk the file for its ``call`` instructions and resolve them

A call edge is emitted only when both ends are known: a call to a name the
repository neither defines nor declares is dropped. The caller is the
``define`` that contains the instruction, found by POSITION in this file (the
``define``'s start, looked up in a ``symbols_at`` index; INV-nafij, WI-kosar).
The callee is this file's ``define`` of that name when there is one, else
whatever the repo-wide registry holds under the name, which may be a
``declare``. Before, both ends went through the registry by bare name, so two
files defining ``@foo`` had one file's calls credited to the other's.

Known gap: an indirect call (``call void %fp(ptr @g)``) is credited to the
first ``@name`` in the instruction, here the argument ``@g``.

Identity (WI-tusad): a ``define`` (kind ``function``) and a ``declare`` (kind
``declaration``) record their typed parameters in ``meta["parameters"]``
(``...`` as a variadic entry) and mint the ADR-0035 producer id from them with
``overload_arity.declared_arity_stable_id``. A global ``@name`` is unique in its
module, so the scope chain is the name itself. Globals are kind ``variable``
and take the backstop's variable formula.

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the LLVM IR-specific
extraction logic.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-llvm for grammar (direct grammar module)
- Two-pass allows cross-file call resolution (for multi-file IR projects)
- Same pattern as other tree-sitter analyzers for consistency

LLVM IR-Specific Considerations
-------------------------------
- LLVM IR is the intermediate representation used by LLVM-based compilers
- Functions are defined with `define` and declared with `declare`
- Global variables are defined with `@name = global/constant`
- Local variables use `%` prefix, global identifiers use `@` prefix
- Functions can have attributes like `dso_local`, `noinline`, etc.
- Call instructions reference functions by their `@name`
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.overload_arity import (
    declared_arity, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_symbol_id,
    node_text,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("llvm_ir")


def find_llvm_ir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all LLVM IR files in the repository."""
    yield from find_files(repo_root, ["*.ll"])


def _get_function_name(header_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract function name from function_header node.

    The function name is stored in a global_var node (e.g., @add).
    """
    for child in iter_tree(header_node):
        if child.type == "global_var":
            name = node_text(child, source)
            # Remove @ prefix for internal use
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _get_global_var_name(global_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract global variable name from global_global node."""
    for child in global_node.children:
        if child.type == "global_var":
            name = node_text(child, source)
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _get_call_target(call_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the called function name from an instruction_call node.

    The target is typically in value > var > global_var.
    """
    for child in iter_tree(call_node):
        if child.type == "global_var":
            name = node_text(child, source)
            return name.lstrip("@")
    return None  # pragma: no cover - defensive fallback


def _enclosing_define(node: "tree_sitter.Node") -> "tree_sitter.Node | None":
    """The ``fn_define`` node containing ``node``; its start position is the
    key of the function symbol Pass 1 emitted from it."""
    current = node.parent
    while current is not None:
        if current.type == "fn_define":
            return current
        current = current.parent
    return None  # pragma: no cover - a call instruction sits in a define


def _extract_arguments(header_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature from function_header."""
    for child in iter_tree(header_node):
        if child.type == "argument_list":
            return node_text(child, source)
    return "()"  # pragma: no cover - defensive fallback


def _parameters(header_node: "tree_sitter.Node", source: bytes) -> list[dict[str, Any]]:
    """``meta["parameters"]`` of a ``define`` / ``declare`` header.

    One :func:`~hypergumbo_core.overload_arity.parameter_entry` per argument:
    its type (``i32``, ``ptr``; parameter attributes such as ``noundef`` are
    not part of it), its ``%name`` without the sigil (empty in a ``declare``),
    and ``...`` as a variadic entry.
    """
    entries: list[dict[str, Any]] = []
    for child in iter_tree(header_node):
        if child.type != "argument_list":
            continue
        for arg in child.named_children:
            type_node = find_child_by_type(arg, "type")
            if type_node is None:
                entries.append(parameter_entry("...", None, variadic=True))
                continue
            name = ""
            for part in iter_tree(arg):
                if part.type == "local_var":
                    name = node_text(part, source).lstrip("%")
                    break
            entries.append(parameter_entry(name, node_text(type_node, source)))
        break
    return entries


def _callable_stable_id(kind: str, rel_path: str, name: str, meta: dict[str, Any]) -> str:
    """The ADR-0035 producer id of a ``define`` / ``declare`` (WI-tusad).

    Its typed parameters through ``overload_arity.declared_arity_stable_id``;
    a global ``@name`` is unique in its module, so the chain is the name.
    """
    return declared_arity_stable_id(
        kind=kind, lang="llvm_ir", path=rel_path, name=name, qualified_name=name,
        arity=declared_arity(meta),
    )


def _get_return_type(header_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract return type from function_header."""
    for child in header_node.children:
        if child.type == "type":
            return node_text(child, source)
    return None  # pragma: no cover - defensive fallback


#: A call site: the enclosing ``define``'s (start_line, start_col), the callee
#: name, and the instruction's line and column.
CallSite = tuple[tuple[int, int], str, int, int]


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree", source: bytes, rel_path: str,
    run_id: str, analysis: FileAnalysis,
) -> None:
    """Extract the symbols of a single LLVM IR file into ``analysis``."""
    for node in iter_tree(tree.root_node):
        if node.type == "fn_define":
            # Function definition
            header = None
            for child in node.children:
                if child.type == "function_header":
                    header = child
                    break

            if header is not None:
                name = _get_function_name(header, source)
                if name:
                    ret_type = _get_return_type(header, source)
                    args = _extract_arguments(header, source)
                    signature = f"{ret_type or 'void'} @{name}{args}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym_id = make_symbol_id("llvm_ir", rel_path, start_line, end_line, name, "function")
                    meta = {"parameters": _parameters(header, source)}

                    symbol = Symbol(
                        id=sym_id,
                        stable_id=_callable_stable_id("function", rel_path, name, meta),
                        name=name,
                        kind="function",
                        language="llvm_ir",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        display_label=f"@{name}",
                        signature=signature,
                        meta=meta,
                        cyclomatic_complexity=compute_cyclomatic_complexity(node, "llvm_ir"),
                        line_span=end_line - start_line + 1,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[name] = symbol

        elif node.type == "declare":
            # Function declaration (external function)
            header = None
            for child in node.children:
                if child.type == "function_header":
                    header = child
                    break

            if header is not None:
                name = _get_function_name(header, source)
                if name:
                    ret_type = _get_return_type(header, source)
                    args = _extract_arguments(header, source)
                    signature = f"{ret_type or 'void'} @{name}{args}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    sym_id = make_symbol_id("llvm_ir", rel_path, start_line, end_line, name, "declaration")
                    meta = {"parameters": _parameters(header, source)}

                    symbol = Symbol(
                        id=sym_id,
                        stable_id=_callable_stable_id("declaration", rel_path, name, meta),
                        name=name,
                        kind="declaration",
                        language="llvm_ir",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        display_label=f"@{name}",
                        signature=signature,
                        meta=meta,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[name] = symbol

        elif node.type == "global_global":
            # Global variable definition
            name = _get_global_var_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                sym_id = make_symbol_id("llvm_ir", rel_path, start_line, end_line, name, "variable")

                symbol = Symbol(
                    id=sym_id,
                    name=name,
                    kind="variable",
                    language="llvm_ir",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    display_label=f"@{name}",
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol



def _collect_calls(tree: "tree_sitter.Tree", source: bytes) -> list[CallSite]:
    """Every ``call`` instruction of the file with the position of the
    ``define`` that contains it."""
    calls: list[CallSite] = []
    for node in iter_tree(tree.root_node):
        if node.type != "instruction_call":
            continue
        define = _enclosing_define(node)
        callee = _get_call_target(node, source)
        if define is not None and callee:
            calls.append((
                (define.start_point[0] + 1, define.start_point[1]),
                callee,
                node.start_point[0] + 1,
                node.start_point[1],
            ))
    return calls


def _resolve_calls(
    all_calls: list[CallSite],
    resolver: NameResolver,
    run_id: str,
    decl_index: SymbolsAt,
    local_symbols: dict[str, Symbol],
) -> list[Edge]:
    """Resolve call sites to known symbols (pass 2).

    The caller is the symbol at the enclosing ``define``'s position. The callee
    is this file's ``define`` of that name when it has one (exact), else
    whatever the repo-wide resolver finds; a ``declare`` in this file is a
    promise that the definition is elsewhere, so it does not pre-empt another
    file's ``define``. A call with either end unknown is dropped.
    """
    edges: list[Edge] = []

    for caller_pos, callee_name, line, _col in all_calls:
        caller_sym = decl_index.get(caller_pos)
        if caller_sym is None:
            continue  # pragma: no cover - every define is emitted in Pass 1
        callee_sym = local_symbols.get(callee_name)
        if callee_sym is not None and callee_sym.kind == "function":
            resolution_score = resolver.CONFIDENCE_EXACT
        else:
            callee_result = resolver.lookup(callee_name)
            callee_sym = callee_result.symbol
            if callee_sym is None:
                continue
            # ADR-0039 ruling 6: the resolver's grade interpolates in the
            # ast_call_direct band.
            resolution_score = callee_result.confidence
        edges.append(Edge.create(
            src=caller_sym.id,
            dst=callee_sym.id,
            edge_type="calls",
            line=line,
            resolution_score=resolution_score,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="ast_call_direct",
        ))

    return edges


# ---------------------------------------------------------------------------
# LlvmIrAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class LlvmIrAnalyzer(TreeSitterAnalyzer):
    """Analyzer for LLVM IR files using TreeSitterAnalyzer base class."""

    lang = "llvm_ir"
    file_patterns: ClassVar[list[str]] = ["*.ll"]
    grammar_module = "tree_sitter_llvm"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single LLVM IR file."""
        analysis = FileAnalysis()
        _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id, analysis,
        )
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a single LLVM IR file."""
        decl_index = symbols_at(self.file_symbols(local_symbols))
        return _resolve_calls(
            _collect_calls(tree, source), resolver, run.execution_id,
            decl_index, local_symbols,
        )


_analyzer = LlvmIrAnalyzer()


def is_llvm_tree_sitter_available() -> bool:
    """Check if tree-sitter with LLVM IR grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("llvm_ir")
def analyze_llvm_ir(repo_root: Path) -> AnalysisResult:
    """Analyze LLVM IR files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
