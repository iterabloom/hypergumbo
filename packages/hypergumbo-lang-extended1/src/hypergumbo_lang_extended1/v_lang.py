# SPDX-License-Identifier: AGPL-3.0-or-later
"""V language analyzer using tree-sitter.

This module provides static analysis for V source code, extracting symbols
(functions, structs, enums, interfaces) and edges (imports, calls).

V is a statically typed compiled programming language designed to be simple,
fast, and safe. It aims to be a pragmatic alternative to C with modern features.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for V grammar
- Handles V-specific constructs: pub visibility (meta ``is_public``), structs
  (kind ``class``), enums and interfaces; ``module`` declarations are not
  handled

Identity (WI-tusad): a plain function mints ``compute_stable_id``. A method
``fn (b B) run(x int)`` has no name of its own beyond ``run``, and its first
parameter list is the RECEIVER, which ``compute_stable_id`` reads as the
parameters; so ``fn (a A) run()`` and ``fn (b B) run(x int)`` tied and the
per-file split numbered them. A method now hashes its receiver type as scope
(``B/run``) and its declared parameters (``meta["parameters"]``, written for
every function, receiver excluded) through
``overload_arity.node_declared_arity_stable_id``: the stable_id scheme bump.
A struct, enum or interface named by ONE capital letter (``struct A {}``)
is recovered from the ``ERROR`` the grammar makes of it
(``_recover_single_letter_type``); it emitted nothing before.
A method's ``signature`` shows its receiver TYPE and declared parameters
(``fn (B) (x) int``); it listed the receiver variable among the parameters
(``fn(b, x) int``), so renaming that variable changed it.

Key constructs extracted:
- function_declaration: fn name(params) return_type { body }
- struct_declaration: struct Name { fields } (kind ``class``)
- enum_declaration: enum Name { variants }
- interface_declaration: interface Name { methods }
- import_declaration: import module
- call_expression: func(args)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_symbol_id,
    make_unresolved_edge,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.analyze.base import node_own_text as _get_node_text
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, nested_qualified_name, node_declared_arity_stable_id,
    parameter_entry,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("v")


def find_v_files(root: Path) -> Iterator[Path]:
    """Find all V files in the given directory.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(root)



def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_type_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type identifier from a node's children."""
    for child in node.children:
        if child.type == "type_identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _is_public(node: "tree_sitter.Node") -> bool:
    """Check if a declaration is public (has 'pub' keyword)."""
    for child in node.children:
        if child.type == "pub":
            return True
    return False


def _extract_function_params(params: Optional["tree_sitter.Node"]) -> list[str]:
    """The parameter names of a declared parameter list (never the receiver's:
    a method's signature read ``fn(b, x)``, so renaming the receiver variable
    rewrote it)."""
    names: list[str] = []
    for param_child in params.children if params is not None else ():
        if param_child.type == "parameter_declaration":
            for param_part in param_child.children:
                if param_part.type == "identifier":
                    names.append(_get_node_text(param_part))
                    break
    return names


def _receiver_and_parameter_lists(
    node: "tree_sitter.Node",
) -> tuple[Optional["tree_sitter.Node"], Optional["tree_sitter.Node"]]:
    """``(receiver list, parameter list)`` of a function declaration.

    A method ``fn (b B) run(x int)`` has two ``parameter_list`` children, the
    receiver's before the name; a plain function has one, after it.
    """
    receiver: Optional["tree_sitter.Node"] = None
    params: Optional["tree_sitter.Node"] = None
    seen_name = False
    for child in node.children:
        if child.type == "identifier":
            seen_name = True
        elif child.type == "parameter_list":
            if seen_name:
                params = child
            else:
                receiver = child
    return receiver, params


def _receiver_type(receiver: "tree_sitter.Node") -> str:
    """The receiver's type name (``B`` for ``(b B)``, ``(mut b &B)``)."""
    for decl in receiver.children:
        if decl.type == "parameter_declaration":
            for part in decl.children:
                if part.type not in ("identifier", "mutable_identifier"):
                    return _get_node_text(part).lstrip("&").strip()
    return ""  # pragma: no cover - a receiver always declares a type


def _parameter_entries(params: Optional["tree_sitter.Node"]) -> list[dict[str, object]]:
    """``meta["parameters"]`` for a V parameter list: typed, ``mut`` kept in the
    type (it changes how the argument is passed), ``...T`` variadic."""
    entries: list[dict[str, object]] = []
    if params is None:
        return entries  # pragma: no cover - the grammar always has a list
    for decl in params.children:
        if decl.type != "parameter_declaration":
            continue
        name, type_, mutable = "", "", False
        for part in decl.children:
            if part.type == "identifier":
                name = _get_node_text(part)
            elif part.type == "mutable_identifier":
                mutable = True
                name = _get_node_text(part).split()[-1]
            else:
                type_ = _get_node_text(part)
        variadic = type_.startswith("...")
        entries.append(parameter_entry(
            name, ("mut " if mutable else "") + type_.removeprefix("..."),
            variadic=variadic,
        ))
    return entries


def _extract_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Extract return type from a function declaration."""
    saw_params = False
    for child in node.children:
        if child.type == "parameter_list":
            saw_params = True
        elif saw_params and child.type in ("builtin_type", "type_identifier", "pointer_type"):
            return _get_node_text(child).strip()
        elif child.type == "block":
            break  # Stop at function body
    return None


#: Declaration keywords whose single-letter name the grammar misreads, with the
#: kind slot of the id and the emitted kind (as the regular branches below).
_V_TYPE_KEYWORDS: dict[str, tuple[str, str]] = {
    "struct": ("struct", "class"), "enum": ("enum", "enum"),
    "interface": ("interface", "interface"),
}


def _recover_single_letter_type(
    node: "tree_sitter.Node",
) -> Optional[tuple[str, str, "tree_sitter.Node", bool]]:
    """``(keyword, name, body, is_public)`` of a type declaration the grammar
    could not parse because its name is ONE capital letter, else None.

    tree-sitter-v lexes a lone capital (``A``) as a ``type_placeholder`` (the
    ``T`` of a generic), so ``struct A {}`` / ``enum E {...}`` / ``interface
    I {...}`` became an ``ERROR`` holding the keyword, followed by a
    ``type_initializer`` (``A {}`` read as a struct literal) or a ``block``,
    and no symbol was emitted (the gate fixture's ``struct A {}`` among them).
    ``node`` is the ``ERROR``; ``body`` is the sibling ending the declaration.
    """
    kids = node.children
    keyword = next((c for c in kids if c.type in _V_TYPE_KEYWORDS), None)
    if keyword is None:
        return None
    body = node.next_sibling
    if kids[-1].type == "type_placeholder":
        placeholder = kids[-1]
        if body is None or body.type != "block":
            return None
    elif (
        body is not None and body.type == "type_initializer"
        and body.named_children and body.named_children[0].type == "type_placeholder"
    ):
        placeholder = body.named_children[0]
    else:
        return None
    return (
        keyword.type, _get_node_text(placeholder), body,
        any(c.type == "pub" for c in kids),
    )


def _body_member_lines(body: "tree_sitter.Node") -> int:
    """Members of a recovered declaration's body, one per non-empty line (the
    grammar garbles the members themselves: ``y string`` reads as two names).
    A ``type_initializer`` body (``A {...}``) is read from its braces on."""
    if body.type == "type_initializer":
        body = body.named_children[-1]
    return sum(
        1 for line in _get_node_text(body).splitlines()
        if line.strip() not in ("", "{", "}", "{}")
    )


def _count_struct_fields(node: "tree_sitter.Node") -> int:
    """Count fields in a struct declaration."""
    for child in node.children:
        if child.type == "struct_field_declaration_list":
            count = 0
            for field_child in child.children:
                if field_child.type == "struct_field_declaration":
                    count += 1
            return count
    return 0  # pragma: no cover


def _count_enum_variants(node: "tree_sitter.Node") -> int:
    """Count variants in an enum declaration."""
    for child in node.children:
        if child.type == "enum_member_declaration_list":
            count = 0
            for member_child in child.children:
                if member_child.type == "enum_member":
                    count += 1
            return count
    return 0  # pragma: no cover


def _find_enclosing_function(
    node: "tree_sitter.Node", path: Path, repo_root: Path,
) -> Optional[str]:
    """Find the enclosing function for a node."""
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name = _get_identifier(current)
            if name:
                return make_symbol_id("v", str(path.relative_to(repo_root)), current.start_point[0] + 1, current.end_point[0] + 1, name, "fn")
        current = current.parent
    return None  # pragma: no cover


class VAnalyzer(TreeSitterAnalyzer):
    """Analyzer for V source files using TreeSitterAnalyzer base class."""

    lang = "v"
    file_patterns: ClassVar[list[str]] = ["*.v"]
    language_pack_name = "v"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, struct, enum, and interface symbols from a V file."""
        analysis = FileAnalysis()
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        # WI-bokab (v7): file-identity anchor for this file's symbols. ``rel_path`` is
        # the repo-relative path threaded from the base extract loop; folded into
        # compute_stable_id's containing slot so same-(kind, name, qualified_name)
        # symbols in different files hash distinctly. Computed once and reused.
        file_stable_id = self._file_anchor(rel_path)

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run, analysis,
            file_stable_id,
        )
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
        file_stable_id: str,
    ) -> None:
        """Extract symbols from a syntax tree node recursively."""
        if node.type == "function_declaration":
            name = _get_identifier(node)
            if name:
                return_type = _extract_return_type(node)
                is_pub = _is_public(node)
                receiver, param_list = _receiver_and_parameter_lists(node)
                params = _extract_function_params(param_list)

                # A method shows its receiver TYPE, never the receiver
                # variable's name: ``fn (B) (x) int``.
                signature = f"fn({', '.join(params)})" if receiver is None else (
                    f"fn ({_receiver_type(receiver)}) ({', '.join(params)})"
                )
                if return_type:
                    signature += f" {return_type}"
                meta: dict[str, object] = {
                    "is_public": is_pub,
                    PARAMETERS_KEY: _parameter_entries(param_list),
                }
                if receiver is None:
                    stable_id = self.compute_stable_id(
                        node, kind="fn", name=name, file_stable_id=file_stable_id,
                    )
                else:
                    # A method: the receiver type scopes it, and its arity is
                    # the declared parameter list, not the receiver list
                    # compute_stable_id would read (WI-tusad scheme bump).
                    stable_id = node_declared_arity_stable_id(
                        self, node, kind="fn", name=name, meta=meta,
                        file_stable_id=file_stable_id,
                        qualified_name=nested_qualified_name(
                            [_receiver_type(receiver)], name,
                        ),
                    )

                sym = Symbol(
                    id=make_symbol_id("v", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "fn"),
                    stable_id=stable_id,
                    name=name,
                    kind="function",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                    meta=meta,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "v"),
                    line_span=node.end_point[0] - node.start_point[0] + 1,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[name] = sym

        elif node.type == "struct_declaration":
            name = _get_type_identifier(node)
            if name:
                field_count = _count_struct_fields(node)
                is_pub = _is_public(node)
                sym = Symbol(
                    id=make_symbol_id("v", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "struct"),
                    stable_id=self.compute_stable_id(node, kind="struct", name=name, file_stable_id=file_stable_id),
                    name=name,
                    kind="class",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_public": is_pub, "field_count": field_count},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "enum_declaration":
            name = _get_type_identifier(node)
            if name:
                variant_count = _count_enum_variants(node)
                is_pub = _is_public(node)
                sym = Symbol(
                    id=make_symbol_id("v", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "enum"),
                    stable_id=self.compute_stable_id(node, kind="enum", name=name, file_stable_id=file_stable_id),
                    name=name,
                    kind="enum",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_public": is_pub, "variant_count": variant_count},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "interface_declaration":
            name = _get_type_identifier(node)
            if name:
                is_pub = _is_public(node)
                sym = Symbol(
                    id=make_symbol_id("v", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "interface"),
                    stable_id=self.compute_stable_id(node, kind="interface", name=name, file_stable_id=file_stable_id),
                    name=name,
                    kind="interface",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"is_public": is_pub},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

        elif node.type == "ERROR" and (recovered := _recover_single_letter_type(node)):
            keyword, name, body, is_pub = recovered
            id_kind, kind = _V_TYPE_KEYWORDS[keyword]
            rec_meta: dict[str, object] = {"is_public": is_pub}
            if keyword == "struct":
                rec_meta["field_count"] = _body_member_lines(body)
            elif keyword == "enum":
                rec_meta["variant_count"] = _body_member_lines(body)
            sym = Symbol(
                id=make_symbol_id("v", rel_path, node.start_point[0] + 1, body.end_point[0] + 1, name, id_kind),
                stable_id=self.compute_stable_id(node, kind=id_kind, name=name, file_stable_id=file_stable_id),
                name=name,
                kind=kind,
                language="v",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=body.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=body.end_point[1],
                ),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                meta=rec_meta,
            )
            analysis.symbols.append(sym)
            analysis.node_for_symbol[sym.id] = node

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis, file_stable_id
            )

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a V file."""
        edges: list[Edge] = []
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_edges_recursive(
            tree.root_node, file_path, repo_root, rel_path,
            global_symbols, run, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, global_symbols: dict[str, Symbol],
        run: "AnalysisRun", edges: list[Edge],
    ) -> None:
        """Extract edges from a syntax tree node recursively."""
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_path":
                    import_path = _get_node_text(child)
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=f"file:{rel_path}",
                        dst=f"v:import:{import_path}",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_import",
                        evidence_lang="v",
                    )
                    edges.append(edge)

        elif node.type == "call_expression":
            caller_id = _find_enclosing_function(node, path, repo_root)
            if caller_id:
                callee_name = _get_identifier(node)

                if callee_name:
                    callee_sym = global_symbols.get(callee_name)
                    if callee_sym is not None:
                        edge = Edge.create(
                            src=caller_id,
                            dst=callee_sym.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_call_direct",
                            evidence_lang="v",
                        )
                    else:
                        edge = make_unresolved_edge(
                            "v", caller_id, callee_name,
                            node.start_point[0] + 1,
                            PASS_ID, run.execution_id,
                        )
                    edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, rel_path, global_symbols, run, edges,
            )


_analyzer = VAnalyzer()


def is_v_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with V support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("v", language_state="no_taxonomy_spec")  # WI-futin
def analyze_v(repo_root: Path) -> AnalysisResult:
    """Analyze V source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
