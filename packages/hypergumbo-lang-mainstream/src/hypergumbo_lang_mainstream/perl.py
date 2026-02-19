"""Perl analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Perl files and extract:
- Package declarations (modules)
- Subroutine declarations (sub)
- Function call relationships
- use/require statements (imports)

How It Works
------------
Uses the TreeSitterAnalyzer base class with the tree-sitter-perl grammar
from tree-sitter-language-pack. Full two-pass analysis:

1. extract_symbols_from_file: extracts packages and subroutines
2. register_symbol: dual-key registration (qualified Package::sub + unqualified sub)
3. extract_edges_from_file: resolves use/require, function calls, method calls
4. Per-file package names stored on instance for Pass 2 access

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Perl-Specific Considerations
----------------------------
- Perl packages define namespaces (package MyModule;)
- Subroutines are defined with ``sub name { ... }``
- ``use Module`` imports at compile time
- ``require 'file.pl'`` imports at runtime
- Method calls can be ``$obj->method()`` or ``ClassName->method()``
- Perl has complex calling conventions that can be hard to statically analyze
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

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
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("perl")


def find_perl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Perl files in the repository."""
    yield from find_files(repo_root, ["*.pl", "*.pm", "*.t"])


def _find_children_by_type(
    node: "tree_sitter.Node", type_name: str
) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]


def _extract_perl_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract subroutine signature from a subroutine_declaration_statement.

    Perl 5.20+ supports subroutine signatures:
        sub foo($x, $y) { ... }

    Traditional Perl uses @_ unpacking which we can't easily extract.

    Returns signature in format: ($param1, $param2) or () if no params found.
    """
    # Look for signature_params or param_list child (Perl 5.20+ signatures)
    for child in node.children:
        if child.type in ("signature", "signature_params"):  # pragma: no cover - rare
            sig_text = node_text(child, source).strip()
            return sig_text

    # Check for prototype (old style) - e.g., sub foo($) { }
    for child in node.children:
        if child.type == "prototype":  # pragma: no cover - rare syntax
            proto_text = node_text(child, source).strip()
            return proto_text

    # No explicit signature found
    return "()"


def _get_current_package(node: "tree_sitter.Node", source: bytes) -> str:
    """Walk up the tree to find the most recent package_statement before this node.

    Returns "main" if no package statement is found.
    """
    current = node.parent
    while current:
        found_self = False
        for sibling in reversed(current.children):
            if sibling is node or (sibling.end_byte <= node.start_byte):
                found_self = True
            if found_self and sibling.type == "package_statement":
                package_nodes = _find_children_by_type(sibling, "package")
                if len(package_nodes) >= 2:
                    return node_text(package_nodes[1], source)
        current = current.parent
    return "main"  # pragma: no cover - defensive


def _find_enclosing_function_perl(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    package_name: str,
) -> Optional[Symbol]:
    """Find the subroutine that contains this node by walking up parents."""
    current = node.parent
    while current:
        if current.type == "subroutine_declaration_statement":
            bareword = find_child_by_type(current, "bareword")
            if bareword:
                name = node_text(bareword, source)
                qualified = f"{package_name}::{name}" if package_name != "main" else name
                return local_symbols.get(qualified) or local_symbols.get(name)
        current = current.parent
    return None  # pragma: no cover - defensive


# Builtins to skip when resolving function calls
_PERL_BUILTINS = frozenset({
    "print", "say", "die", "warn", "exit", "return",
    "shift", "push", "pop", "splice", "join", "split",
    "open", "close", "read", "write", "defined", "ref",
    "bless", "keys", "values", "each", "exists", "delete",
    "length", "substr", "index", "rindex", "chomp", "chop",
    "lc", "uc", "lcfirst", "ucfirst", "scalar", "wantarray",
})

# Pragmas to skip for import edges
_PERL_PRAGMAS = frozenset({
    "strict", "warnings", "utf8", "vars", "constant",
})


class PerlAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Perl analyzer.

    Uses tree-sitter-perl from the language-pack to extract packages,
    subroutines, and cross-file call/import edges.

    Overrides ``register_symbol`` for dual-key registration: both the
    qualified name (Package::sub) and the unqualified name (sub) are
    stored in the global registry for cross-file resolution.

    Stores per-file package names on the instance (``_file_package_names``)
    for access during Pass 2 edge extraction.
    """

    lang = "perl"
    file_patterns: ClassVar[list[str]] = ["*.pl", "*.pm", "*.t"]
    language_pack_name = "perl"
    create_file_symbols = True

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None
    ) -> AnalysisResult:
        """Reset per-file state and delegate to the base class."""
        self._file_package_names: dict[str, str] = {}
        return super().analyze(repo_root, max_files)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract packages and subroutines from a single Perl file.

        Detects package_statement (module/namespace) and
        subroutine_declaration_statement (sub). Subroutines in non-main
        packages get qualified names (Package::sub).
        """
        analysis = FileAnalysis()
        package_name = "main"

        for node in iter_tree(tree.root_node):
            if node.type == "package_statement":
                package_nodes = _find_children_by_type(node, "package")
                if len(package_nodes) >= 2:
                    package_name = node_text(package_nodes[1], source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = make_symbol_id(
                        "perl", rel_path, start_line, end_line,
                        package_name, "module",
                    )
                    symbol = Symbol(
                        id=sym_id,
                        name=package_name,
                        kind="module",
                        language="perl",
                        path=rel_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[package_name] = symbol

            elif node.type == "subroutine_declaration_statement":
                bareword = find_child_by_type(node, "bareword")
                if bareword:
                    sub_name = node_text(bareword, source)
                    current_pkg = _get_current_package(node, source)
                    if current_pkg != "main":
                        package_name = current_pkg
                    qualified_name = (
                        f"{package_name}::{sub_name}"
                        if package_name != "main"
                        else sub_name
                    )
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = make_symbol_id(
                        "perl", rel_path, start_line, end_line,
                        sub_name, "function",
                    )
                    symbol = Symbol(
                        id=sym_id,
                        name=qualified_name,
                        kind="function",
                        language="perl",
                        path=rel_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        signature=_extract_perl_signature(node, source),
                    )
                    analysis.symbols.append(symbol)
                    # Store both qualified and unqualified for local lookup
                    analysis.symbol_by_name[qualified_name] = symbol
                    if "::" in qualified_name:
                        unqualified = qualified_name.rsplit("::", 1)[-1]
                        analysis.symbol_by_name[unqualified] = symbol

        # Store package name for Pass 2
        self._file_package_names[rel_path] = package_name
        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register both qualified and unqualified names in global registry.

        For example, Utils::double is registered as both "Utils::double"
        and "double" (if "double" isn't already taken).
        """
        global_symbols[symbol.name] = symbol
        if "::" in symbol.name:
            unqualified = symbol.name.rsplit("::", 1)[-1]
            if unqualified not in global_symbols:
                global_symbols[unqualified] = symbol

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract call and import edges from a Perl file.

        Detects use_statement, require_expression, function calls
        (ambiguous/func0op/func1op), and method calls (arrow operator).
        """
        edges: list[Edge] = []
        file_id = make_file_id("perl", rel_path)
        package_name = self._file_package_names.get(rel_path, "main")
        run_id = run.execution_id

        for node in iter_tree(tree.root_node):
            # Handle use statements
            if node.type == "use_statement":
                package_nodes = _find_children_by_type(node, "package")
                if package_nodes:
                    module_name = node_text(package_nodes[0], source)
                    if module_name not in _PERL_PRAGMAS:
                        module_id = f"perl:{module_name}:0-0:module:module"
                        edge = Edge.create(
                            src=file_id,
                            dst=module_id,
                            edge_type="imports",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="use",
                            confidence=0.95,
                        )
                        edges.append(edge)

            # Handle require expressions
            elif node.type == "require_expression":
                string_node = find_child_by_type(node, "string_literal")
                if string_node:
                    content = find_child_by_type(string_node, "string_content")
                    if content:
                        required_file = node_text(content, source)
                        module_id = f"perl:{required_file}:0-0:file:file"
                        edge = Edge.create(
                            src=file_id,
                            dst=module_id,
                            edge_type="imports",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="require",
                            confidence=0.90,
                        )
                        edges.append(edge)

            # Handle function calls
            elif node.type in (
                "function_call_expression",
                "ambiguous_function_call_expression",
                "func0op_call_expression",
                "func1op_call_expression",
            ):
                func_node = find_child_by_type(node, "function")
                if func_node:
                    func_name = node_text(func_node, source)
                    if func_name not in _PERL_BUILTINS:
                        caller = _find_enclosing_function_perl(
                            node, source, local_symbols, package_name,
                        )
                        if caller:
                            lookup_result = resolver.lookup(func_name)
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
                            else:
                                unresolved_id = f"perl:?:0-0:{func_name}:function"
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=unresolved_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="function_call",
                                    confidence=0.50,
                                )
                                edges.append(edge)

            # Handle method calls (arrow operator)
            elif node.type == "method_call_expression":
                method_node = find_child_by_type(node, "method")
                if method_node:
                    method_name = node_text(method_node, source)
                    caller = _find_enclosing_function_perl(
                        node, source, local_symbols, package_name,
                    )
                    if caller:
                        lookup_result = resolver.lookup(method_name)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.75 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="method_call",
                                confidence=confidence,
                            )
                            edges.append(edge)

        return edges


_analyzer = PerlAnalyzer()


def is_perl_tree_sitter_available() -> bool:
    """Check if tree-sitter with Perl grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("perl")
def analyze_perl(repo_root: Path) -> AnalysisResult:
    """Analyze Perl files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter with Perl support is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
