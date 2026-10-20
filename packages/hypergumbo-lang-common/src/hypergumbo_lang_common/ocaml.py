# SPDX-License-Identifier: AGPL-3.0-or-later
"""OCaml analysis pass using tree-sitter-ocaml.

This analyzer uses tree-sitter to parse OCaml files and extract:
- Function declarations (let bindings)
- Type definitions (type declarations)
- Module definitions
- Open statements (imports)
- Function call relationships

If tree-sitter with OCaml support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, file discovery,
and result assembly. This module overrides _create_parser() because
tree-sitter-ocaml uses language_ocaml() instead of the standard language().

1. Check if tree-sitter-ocaml is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and open statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ocaml package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

OCaml-Specific Considerations
-----------------------------
- OCaml has `let` bindings for function definitions
- Types are defined with `type` keyword
- Modules are defined with `module X = struct ... end`
- `open Module` brings module into scope
- Function application uses whitespace (no parens needed)

Scope and identity (INV-kagib, WI-tusad)
----------------------------------------
Every ``let`` binding of a structure is its own symbol: each ``and``-joined
binding of a ``let rec ... and ...``, a shadowing redefinition of a name, and a
same-name binding in another module. Before, a per-file set of seen names kept
only the first, so ``module B``'s ``run`` was absent from the graph whenever
``module A`` also bound one. A binding inside ``module A = struct ... end`` is
named ``A.run`` (nested modules chain: ``A.C.deep``), the way OCaml code outside
the module refers to it; a top-level binding keeps its bare name. Type and
module symbols are emitted per declaration and named the same way (``A.t``,
``A.C``; WI-tusad scheme bump, INV-fabih): they kept their bare names, so
``type t`` in modules ``A`` and ``B`` shared one table key and deleting the
first moved the second's id. Their ids are the backstop's type/module table
formula over that name.

A ``let`` local to an expression (``let g z = z in ...``) is emitted only when it
binds a function (it has parameters or its body is a ``fun`` / ``function``),
named bare, with the enclosing bindings in its scope chain (``f/g``); a local
value binding (``let x = 1 in``) is a variable, not a function, and is not
emitted. The first binding of a definition spans the whole ``value_definition``
(as before); a later ``and`` binding spans its own ``let_binding``.

Each binding records its parameters in ``meta["parameters"]`` (an optional
``?x`` parameter is a default; the empty list for a value) and mints the
ADR-0035 producer stable_id through ``overload_arity.declared_arity_stable_id``
over the scope chain, so deleting an earlier same-name binding moves no other
binding's id. Calls are anchored on the enclosing binding by its declaration
position, never its name, and a call written ``A.run`` first tries the
module-qualified name.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("ocaml")


def find_ocaml_files(repo_root: Path) -> Iterator[Path]:
    """Yield all OCaml files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type name from type_binding node."""
    type_constructor = find_child_by_type(node, "type_constructor")
    if type_constructor:
        return node_text(type_constructor, source)
    return ""  # pragma: no cover


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module name from module_binding node."""
    module_name = find_child_by_type(node, "module_name")
    if module_name:
        return node_text(module_name, source)
    return ""  # pragma: no cover


def _get_open_module_path(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module path from open_module node."""
    module_path = find_child_by_type(node, "module_path")
    if module_path:
        return node_text(module_path, source)
    return ""  # pragma: no cover


def _extract_ocaml_signature(
    let_binding: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from an OCaml let binding.

    OCaml function syntax: let name param1 param2 = body
    Returns signature string like "(param1, param2)" or None.
    """
    params: list[str] = []
    found_name = False

    for child in let_binding.children:
        if child.type == "value_name" and not found_name:
            found_name = True
            continue
        if not found_name:  # pragma: no cover - value_name is always first
            continue  # pragma: no cover

        # Stop at = sign (start of function body)
        if child.type == "=":
            break

        # Collect parameter nodes
        if child.type == "parameter":
            # Extract the value_pattern from inside the parameter
            param_text = node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "parenthesized_pattern":  # pragma: no cover - rare pattern
            # Pattern like (x, y) or (x : int)
            params.append(node_text(child, source))  # pragma: no cover
        elif child.type == "typed_pattern":  # pragma: no cover - rare pattern
            # Pattern like (x : int)
            params.append(node_text(child, source))  # pragma: no cover
        elif child.type == "unit_pattern":  # pragma: no cover - rare pattern
            # Pattern like ()
            params.append("()")  # pragma: no cover
        elif child.type == "wildcard_pattern":  # pragma: no cover - rare pattern
            # Pattern like _
            params.append("_")  # pragma: no cover

    if params:
        return "(" + ", ".join(params) + ")"
    return None  # No params = value, not function


#: Parents of a ``value_definition`` that make it a structure-level binding
#: (top of the file or a ``struct ... end`` body), as opposed to a local
#: ``let ... in`` (parent ``let_expression``).
_OCAML_STRUCTURE_PARENTS = frozenset({"compilation_unit", "structure"})

#: Bodies that make a parameterless binding a function (``let f = fun x -> x``).
_OCAML_FUNCTION_BODIES = frozenset({"fun_expression", "function_expression"})


def _ocaml_let_bindings(value_def: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """Every ``let_binding`` of a ``value_definition`` (``let ... and ...``)."""
    return [c for c in value_def.children if c.type == "let_binding"]


def _ocaml_symbol_node(let_binding: "tree_sitter.Node") -> "tree_sitter.Node":
    """The node a binding's symbol spans and is keyed by.

    The first binding of a definition spans the whole ``value_definition`` (the
    span it always had); a later ``and`` binding spans its own ``let_binding``.
    """
    value_def = let_binding.parent
    if value_def is not None and _ocaml_let_bindings(value_def)[0] == let_binding:
        return value_def
    return let_binding


def _ocaml_module_chain(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Names of the modules enclosing ``node``, outermost first."""
    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "module_binding":
            name = _get_module_name(current, source)
            if name:
                chain.append(name)
        current = current.parent
    return list(reversed(chain))


def _ocaml_is_function_binding(let_binding: "tree_sitter.Node") -> bool:
    """True when the binding takes parameters or its body is a function."""
    return any(
        c.type == "parameter" or c.type in _OCAML_FUNCTION_BODIES
        for c in let_binding.children
    )


def _ocaml_parameters(
    let_binding: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """``meta["parameters"]`` of a ``let_binding``: one entry per ``parameter``.

    The entry's name is the parameter as written (``x``, ``~l``, ``(a, b)``);
    an optional ``?x`` / ``?(x = 1)`` parameter is a default.
    """
    entries: list[dict[str, object]] = []
    for child in let_binding.children:
        if child.type == "=":
            break
        if child.type == "parameter":
            text = node_text(child, source).strip()
            entries.append(parameter_entry(text, default=text.startswith("?")))
    return entries


def _ocaml_binding_names(
    let_binding: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str]]:
    """``(symbol name, scope chain)`` of a binding, or None when not emitted.

    A structure-level binding is named with its module chain (``A.run``) and
    that is its scope chain. A local binding is emitted only when it binds a
    function: named bare, its chain is the enclosing binding's chain plus
    ``/name``. A binding without a ``value_name`` (``let () = ...``, a pattern,
    an operator) is not emitted, as before.
    """
    value_name = find_child_by_type(let_binding, "value_name")
    value_def = let_binding.parent
    if value_name is None or value_def is None:
        return None
    name = node_text(value_name, source)
    if value_def.parent is None or value_def.parent.type in _OCAML_STRUCTURE_PARENTS:
        qualified = ".".join([*_ocaml_module_chain(value_def, source), name])
        return qualified, qualified
    if not _ocaml_is_function_binding(let_binding):
        return None
    current: Optional["tree_sitter.Node"] = value_def.parent
    while current is not None:
        if current.type == "let_binding":
            outer = _ocaml_binding_names(current, source)
            if outer is not None:
                return name, f"{outer[1]}/{name}"
        current = current.parent
    return name, name  # a local of `let () = ...`: no named binding encloses it


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract all symbols from a parsed OCaml file.

    Detects:
    - value_definition: every let binding (see the module docstring for scope)
    - type_definition: type declarations
    - module_definition: module declarations
    """
    analysis = FileAnalysis()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        signature: Optional[str] = None,
        cc_node: "Optional[tree_sitter.Node]" = None,
        meta: Optional[dict[str, object]] = None,
        stable_id: Optional[str] = None,
    ) -> None:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )
        sym_id = make_symbol_id("ocaml", file_path, start_line, end_line, name, kind)
        sym = Symbol(
            id=sym_id,
            stable_id=stable_id,
            name=name,
            kind=kind,
            language="ocaml",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            meta=meta,
            # INV-loguk: CC only for callables (kind=="function"); type/module
            # subtrees would otherwise aggregate. LOC likewise callable-only.
            cyclomatic_complexity=(
                compute_cyclomatic_complexity(cc_node or node, "ocaml")
                if kind == "function" else None
            ),
            line_span=(end_line - start_line + 1) if kind == "function" else None,
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[name] = sym

    for node in iter_tree(tree.root_node):
        if node.type == "value_definition":
            for let_binding in _ocaml_let_bindings(node):
                names = _ocaml_binding_names(let_binding, source)
                if names is None:
                    continue
                name, chain = names
                meta: dict[str, object] = {
                    PARAMETERS_KEY: _ocaml_parameters(let_binding, source),
                }
                add_symbol(
                    _ocaml_symbol_node(let_binding), name, "function",
                    signature=_extract_ocaml_signature(let_binding, source),
                    meta=meta,
                    stable_id=declared_arity_stable_id(
                        kind="function", lang="ocaml", path=file_path,
                        name=name.rsplit(".", 1)[-1], qualified_name=chain,
                        arity=declared_arity(meta),
                    ),
                )

        elif node.type == "type_definition":
            # Type definition
            type_binding = find_child_by_type(node, "type_binding")
            if type_binding:
                name = _get_type_name(type_binding, source)
                if name:
                    # INV-fabih: named with the module chain (``A.t``) like a
                    # binding, so the backstop's table formula over the name
                    # tells two modules' ``type t`` apart.
                    add_symbol(node, ".".join([*_ocaml_module_chain(node, source), name]), "type")

        elif node.type == "module_definition":
            # Module definition
            module_binding = find_child_by_type(node, "module_binding")
            if module_binding:
                name = _get_module_name(module_binding, source)
                if name:
                    add_symbol(node, ".".join([*_ocaml_module_chain(node, source), name]), "module")

    return analysis


def _find_enclosing_ocaml_function(
    node: "tree_sitter.Node",
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The emitted binding whose ``let_binding`` contains ``node``.

    Keyed by the binding's declaration POSITION (INV-midag), never its name:
    ``A.run`` and ``B.run`` are both ``run`` in the tree. A local value binding
    has no symbol and is walked past to the function holding it.
    """
    current = node.parent
    while current:
        if current.type == "let_binding":
            sym = symbol_declared_by(_ocaml_symbol_node(current), decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_module_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract module aliases for disambiguation.

    In OCaml:
        module L = List -> L maps to List

    Returns a dict mapping alias names to full module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "module_definition":
            continue

        # Find module_binding: module L = List
        binding = find_child_by_type(node, "module_binding")
        if not binding:  # pragma: no cover - defensive for malformed AST
            continue

        # Get alias name (first module_name in binding)
        alias_node = find_child_by_type(binding, "module_name")
        if not alias_node:  # pragma: no cover - defensive for malformed AST
            continue

        alias_name = node_text(alias_node, source)

        # Get original module path (after =)
        module_path = find_child_by_type(binding, "module_path")
        if module_path:
            original = node_text(module_path, source)
            aliases[alias_name] = original

    return aliases


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    module_aliases: dict[str, str],
) -> list[Edge]:
    """Extract call and import edges from a parsed OCaml file.

    Args:
        module_aliases: Dict mapping module aliases to full names.

    Detects:
    - open_module: Import statements
    - application_expression: Function application (calls)
    """
    edges: list[Edge] = []
    file_id = make_file_id("ocaml", file_path)
    decl_index = symbols_at(file_symbols)

    for node in iter_tree(tree.root_node):
        if node.type == "open_module":
            # Open statement (import)
            module_path = _get_open_module_path(node, source)
            if module_path:
                module_id = f"ocaml:{module_path}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="open",
                )
                edges.append(edge)

        elif node.type == "application_expression":
            # Function application
            # First child that's a value_path contains the function name
            if node.children:
                first_child = node.children[0]
                callee_name = None
                path_hint: Optional[str] = None
                module_prefix: Optional[str] = None

                if first_child.type == "value_path":
                    value_name = find_child_by_type(first_child, "value_name")
                    if value_name:
                        callee_name = node_text(value_name, source)

                    # Check for module prefix (L.map -> module_path 'L')
                    module_path_node = find_child_by_type(first_child, "module_path")
                    if module_path_node:
                        module_name_node = find_child_by_type(module_path_node, "module_name")
                        if module_name_node:
                            module_alias = node_text(module_name_node, source)
                            path_hint = module_aliases.get(module_alias)
                            module_prefix = node_text(module_path_node, source)

                if callee_name and callee_name not in ("print_int", "print_string", "print_endline", "print_newline"):
                    # Find the caller (enclosing function)
                    caller = _find_enclosing_ocaml_function(node, decl_index)
                    if caller:
                        # A module-qualified call (``A.run``) names a binding
                        # this analyzer emits as ``A.run``; try that first.
                        lookup_result = resolver.lookup(
                            f"{module_prefix}.{callee_name}", allow_suffix=False,
                        ) if module_prefix else None
                        if lookup_result is None or not lookup_result.found:
                            lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="ast_call",
                                resolution_score=lookup_result.confidence,
                                meta={"call_construct": "application"},
                            )
                            edges.append(edge)
                        else:
                            # Unresolved call - create edge to unknown target
                            unresolved_id = f"ocaml:?:0-0:{callee_name}:function"
                            edge = Edge.create(
                                src=caller.id,
                                dst=unresolved_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="ast_call",
                                meta={"call_construct": "application"},
                            )
                            edges.append(edge)

    return edges


class OcamlAnalyzer(TreeSitterAnalyzer):
    """OCaml language analyzer using tree-sitter-ocaml.

    Overrides _create_parser() because tree-sitter-ocaml uses language_ocaml()
    instead of the standard language() entry point.
    """

    lang = "ocaml"
    file_patterns: ClassVar[list[str]] = ["*.ml"]
    grammar_module = "tree_sitter_ocaml"
    create_file_symbols = True

    def _create_parser(self) -> "tree_sitter.Parser":
        """Create parser using OCaml's non-standard language_ocaml() entry point."""
        import tree_sitter
        import tree_sitter_ocaml

        lang = tree_sitter.Language(tree_sitter_ocaml.language_ocaml())
        return tree_sitter.Parser(lang)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract OCaml symbols from a single file."""
        return _extract_symbols_from_tree(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract OCaml module aliases (module L = List)."""
        return _extract_module_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from an OCaml file."""
        return _extract_edges_from_tree(
            tree, source, rel_path, self.file_symbols(local_symbols),
            resolver, run.execution_id, import_aliases,
        )


_analyzer = OcamlAnalyzer()


def is_ocaml_tree_sitter_available() -> bool:
    """Check if tree-sitter with OCaml grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("ocaml")
def analyze_ocaml(repo_root: Path) -> AnalysisResult:
    """Analyze OCaml files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-ocaml is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
