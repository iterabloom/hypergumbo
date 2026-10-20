# SPDX-License-Identifier: AGPL-3.0-or-later
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

1. extract_symbols_from_file: extracts the file pseudo-node, packages and subroutines
2. register_symbol: registers by the qualified name only (Package::sub); short-name (unqualified) lookups are handled by the NameResolver suffix index, not by a second registry key
3. extract_edges_from_file: resolves use/require, function calls, method calls
4. No per-file package state is kept for Pass 2: a call's enclosing sub is found by the declaration's position (INV-midag)

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

Class-method calls (WI-mahik)
-----------------------------
``Pkg->m(...)`` names its owner: Perl searches ``Pkg``'s method table, so the
call is resolved by the invocant, never by the short name ``m`` across
packages. A bareword invocant (``Pkg``, ``Pkg::``, ``::Pkg``) or
``__PACKAGE__`` (the enclosing package) is looked up as ``Pkg::m`` exactly
(``main`` names the unqualified subs). A miss emits the external edge the
function-call arm already emits for ``LWP::Simple::get()``:
``perl:<Pkg>:0-0:<m>:unresolved`` with ``Pkg`` as written in the module slot
(ADR-0051's owner path), so a catalogue or overlay row for ``HTTP::Tiny`` /
``IO::File`` can classify it. That holds for a package the repository DOES
declare but whose sub ``m`` it does not: the method may be inherited or
AUTOLOADed, and some other package's ``m`` is not it (the analyzer records
no perl inheritance to walk). Both edges carry ``call_construct="function"``:
the call is made on the module itself (ADR-0059). Like the function-call
arm, a class-method call outside any sub is attributed to the file.

Before this, the arm looked ``m`` up by short name: an undeclared ``m``
emitted no edge at all (``File::Spec->catfile``), and a declared one bound
whatever package declared it (git: ``SVN::Pool->new`` -> ``Git::new``;
postgresql: ``IO::Socket::INET->new`` -> ``LdapServer::new``).

Calls on a value (WI-mugir)
---------------------------
An invocant that is an EXPRESSION (``$ua->get``, ``shift->m``,
``Pkg->new->get``) is a value, and its package is the value's type. The type is
taken only where the file states it: ``Pkg->new`` (the constructor; another
class method returns whatever it returns), a scalar assigned one, and the
method invocant -- ``$self`` / ``$this`` / ``$class`` taken by ``shift`` or as
the first of ``@_``, or a bare ``shift->m`` outside ``main`` -- which is the
enclosing package. A typed call resolves like a class-method call, stamped
``call_construct="method"``; an untyped one gets the honest
``perl:external:0-0:<m>:unresolved`` method edge. It used to bind any
package's ``m`` by short name, so a repo ``get`` captured
``HTTP::Tiny->new->get($u)``. Outside any sub the call is attributed to the
file, as the other two call arms are.

The invocant is typed by NAME, not by shape, because the shape does not say a
sub is a method (see ``_INVOCANT_NAMES``). A dynamic (``$o->$m``) or qualified
(``$o->Other::m``) method name keeps the qualified lookup.
"""

from __future__ import annotations

import re
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
    make_unresolved_edge,
    node_text,
    SymbolsAt,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("perl")


def find_perl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Perl files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


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
    """The package in force at ``node``: the block-form package enclosing it
    (``package B { ... }``, Perl 5.14+), else the most recent statement-form
    ``package X;`` before it in an enclosing scope, else ``"main"``.

    A block-form package scopes only its block. Reading it as a statement
    named every sub in a later ``package B { }`` block after the package
    before it (``A::go``), and a sub after the block after the block's own
    package; the twins then tied, and deleting the first block renamed the
    second's sub (WI-himah; INV-punig's sweep).
    """
    current = node.parent
    while current:
        if current.type == "package_statement" and _is_block_package(current):
            return _package_name(current, source) or "main"
        found_self = False
        for sibling in reversed(current.children):
            if sibling is node or (sibling.end_byte <= node.start_byte):
                found_self = True
            if (found_self and sibling.type == "package_statement"
                    and not _is_block_package(sibling)):
                name = _package_name(sibling, source)
                if name:
                    return name
        current = current.parent
    return "main"


def _is_block_package(statement: "tree_sitter.Node") -> bool:
    """Whether a ``package_statement`` is the block form (``package B { }``)."""
    return find_child_by_type(statement, "block") is not None


def _package_name(statement: "tree_sitter.Node", source: bytes) -> str:
    """The name a ``package_statement`` declares, or ``""`` without one."""
    package_nodes = _find_children_by_type(statement, "package")
    return node_text(package_nodes[1], source) if len(package_nodes) >= 2 else ""


def _find_enclosing_function_perl(
    node: "tree_sitter.Node",
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The subroutine whose declaration contains ``node``.

    Keyed by the declaration's POSITION, not its name (INV-midag, WI-mapor).
    A file holding two packages has one package name on record, the last, so
    ``sub run`` in package A and in package B both qualified to ``B::run``.
    """
    current = node.parent
    while current:
        if current.type == "subroutine_declaration_statement":
            sym = symbol_declared_by(current, decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - defensive


# A method name the class-method path resolves: a plain identifier. A
# dynamic name (``Pkg->$m``) names no sub; a qualified one (``Pkg->Other::m``,
# ``SUPER::m``) starts the search in another package and keeps the
# short-name path.
_PERL_METHOD_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def _invocant_package(
    call: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """The package a ``->`` call is made on, or None when the invocant is an expression.

    A bareword invocant is the package as written, minus a root ``::`` and the
    trailing ``::`` Perl allows to force a package reading (``Foo::->new``).
    ``__PACKAGE__`` is the enclosing package.
    """
    invocant = call.children[0]
    if invocant.type == "bareword":
        return node_text(invocant, source).strip(":") or None
    if (
        invocant.type == "func0op_call_expression"
        and node_text(invocant, source) == "__PACKAGE__"
    ):
        return _get_current_package(call, source)
    return None


def _constructed_package(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """``Pkg`` for ``Pkg->new(...)``, the constructor call, else None (WI-mugir).

    Only ``new``: another class method (``DBI->connect``) returns whatever it
    returns -- a ``DBI::db`` handle, not a ``DBI`` -- so naming the class as
    the value's type there would put a present-but-wrong owner in the slot.
    """
    if node.type != "method_call_expression":
        return None
    method = find_child_by_type(node, "method")
    if method is None or node_text(method, source) != "new":
        return None
    return _invocant_package(node, source)


def _is_method_invocant(node: "tree_sitter.Node", source: bytes) -> bool:
    """Whether ``node`` takes the invocant: ``shift`` / ``shift @_`` (WI-mugir)."""
    if node.type != "func1op_call_expression":
        return False
    spelled = "".join(node_text(node, source).split()).replace("(", "").replace(")", "")
    return spelled in ("shift", "shift@_")


#: The names Perl's method idiom gives the invocant (``my $self = shift``).
#: The invocant is typed by NAME because the shape alone does not say a sub is
#: a method: measured on postgresql and virtualbox, ``my ($node, ...) = @_`` /
#: ``my $proxy = shift`` in a plain function, a second ``shift`` after
#: ``$self``'s, and a ``$SIG{__DIE__}`` handler's ``my ($e) = @_`` were each
#: typed as the enclosing package and moved hundreds of correct edges onto it.
_INVOCANT_NAMES = frozenset({"self", "this", "class"})


def _in_anonymous_sub(node: "tree_sitter.Node") -> bool:
    """Whether ``node``'s nearest enclosing sub is an anonymous one, whose
    ``@_`` is its own caller's arguments, not the method's."""
    current = node.parent
    while current is not None:
        if current.type == "anonymous_subroutine_expression":
            return True
        if current.type == "subroutine_declaration_statement":
            return False
        current = current.parent
    return False  # pragma: no cover - asked only of a node inside a named sub


def _scalar_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """``x`` for a ``$x`` node, else None."""
    if node.type != "scalar":
        return None
    varname = find_child_by_type(node, "varname")
    return node_text(varname, source) if varname is not None else None


def _package_unresolved_edge(
    caller_id: str,
    package: str,
    name: str,
    line: int,
    run_id: str,
    *,
    call_construct: Optional[str] = None,
) -> Edge:
    """The unresolved edge for ``name`` in ``package``, the package as written.

    Shared by ``Pkg::name()`` and ``Pkg->name()``: one spelling,
    ``perl:<Pkg>:0-0:<name>:unresolved``, with a structured ``dst_ref`` keyed
    by the package (WI-nigah, ADR-0051's owner path).
    """
    return make_unresolved_edge(
        "perl", caller_id, name, line, PASS_ID, run_id,
        module_hint=package,
        dst_ref=ExternalRef(lang="perl", module_path=package, name=name),
        call_construct=call_construct,
    )


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

    Overrides ``register_symbol`` to register by the qualified name
    (Package::sub) only; the ``NameResolver`` suffix index handles
    unqualified (sub) lookups for cross-file resolution.

    Pass 2 finds a call's enclosing sub by the declaration's position
    (INV-midag), so it needs no per-file package state. It kept the file's
    LAST package name for that, which qualified every sub of a two-package
    file into the last package.
    """

    lang = "perl"
    file_patterns: ClassVar[list[str]] = ["*.pl", "*.pm", "*.t"]
    language_pack_name = "perl"
    # INV-kokaj: extract_symbols_from_file now emits the canonical file
    # pseudo-node explicitly (with the file's actual end_line) so the
    # base-class auto-emitted Symbol (span=1-1) would duplicate it under
    # the same id.
    create_file_symbols = False

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

        # INV-kokaj: emit the file pseudo-node as kind="file" with the
        # canonical file-id shape so the orchestrator file-symbol synthesizer
        # dedups against it (existing_ids check). Distinct from the
        # ``package_statement`` Symbols below — those keep kind="module"
        # because Perl packages are real namespacing constructs (not
        # file pseudo-nodes). Only the per-file anchor changes shape.
        end_line = tree.root_node.end_point[0] + 1
        module_symbol = Symbol(
            id=make_file_id("perl", rel_path),
            name=rel_path,
            kind="file",
            language="perl",
            path=rel_path,
            span=Span(start_line=1, end_line=end_line, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        )
        analysis.symbols.append(module_symbol)

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
                    analysis.node_for_symbol[symbol.id] = node
                    analysis.symbol_by_name[package_name] = symbol

            elif node.type == "subroutine_declaration_statement":
                bareword = find_child_by_type(node, "bareword")
                if bareword:
                    sub_name = node_text(bareword, source)
                    current_pkg = _get_current_package(node, source)
                    qualified_name = (
                        f"{current_pkg}::{sub_name}"
                        if current_pkg != "main"
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
                        stable_id=self.compute_stable_id(
                            node, kind="function", name=sub_name,
                            qualified_name=qualified_name,
                            file_stable_id=self._file_anchor(rel_path),
                        ),
                        cyclomatic_complexity=compute_cyclomatic_complexity(node, "perl"),
                        line_span=end_line - start_line + 1,
                    )
                    analysis.symbols.append(symbol)
                    analysis.node_for_symbol[symbol.id] = node
                    # Store both qualified and unqualified for local lookup
                    analysis.symbol_by_name[qualified_name] = symbol
                    if "::" in qualified_name:
                        unqualified = qualified_name.rsplit("::", 1)[-1]
                        analysis.symbol_by_name[unqualified] = symbol
        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups
        across ``::`` separators.
        """
        global_symbols[symbol.name] = symbol

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
        _caller_path = str(file_path)
        file_id = make_file_id("perl", rel_path)
        run_id = run.execution_id
        # Every declaration of this file, by position (INV-midag).
        decl_index = symbols_at(self.file_symbols(local_symbols))

        # INV-kokaj: look up the file pseudo-node by its new canonical
        # name (the rel_path the Pass 1 emitter stamped) for top-level
        # call attribution.
        module_symbol: Symbol | None = global_symbols.get(rel_path)
        # WI-mugir: the package a scalar holds, per (enclosing sub id or ""
        # for the file, name). ``None`` records an assignment of something
        # untyped, so a sub's own rebinding is not read through to a
        # same-named file lexical.
        scalar_types: dict[tuple[str, str], Optional[str]] = {}

        def _invocant_type(invocant: "tree_sitter.Node", sub: Optional[Symbol]) -> Optional[str]:
            """The package an expression invocant's value is, when the file says."""
            if _is_method_invocant(invocant, source):
                # A bare ``shift->m`` is the method idiom outside ``main``;
                # in a script's plain function it is the first argument.
                package = _get_current_package(invocant, source)
                return package if (
                    sub and package != "main" and not _in_anonymous_sub(invocant)
                ) else None
            constructed = _constructed_package(invocant, source)
            if constructed is not None:
                return constructed
            name = _scalar_name(invocant, source)
            if name is None:
                return None
            key = (sub.id if sub else "", name)
            return scalar_types[key] if key in scalar_types else scalar_types.get(("", name))

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
                            node, decl_index,
                        ) or module_symbol
                        if caller:
                            lookup_result = resolver.lookup(func_name, caller_path=_caller_path)
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
                                    meta={"call_construct": "function"},
                                )
                                edges.append(edge)
                            else:
                                # WI-nigah Tier 2: Perl package-qualified
                                # calls use ``::`` (e.g., ``Foo::bar()``).
                                # Split at the rightmost ``::`` so the
                                # unresolved edge carries structured
                                # ``dst_ref=ExternalRef(...)`` keyed by
                                # package name. Bare calls (no ``::``) have
                                # no module signal — dst_ref stays None.
                                if "::" in func_name:
                                    pkg, _, bare = func_name.rpartition("::")
                                    edges.append(_package_unresolved_edge(
                                        caller.id, pkg, bare,
                                        node.start_point[0] + 1, run_id,
                                    ))
                                else:
                                    edges.append(make_unresolved_edge(
                                        "perl", caller.id, func_name,
                                        node.start_point[0] + 1, PASS_ID, run_id,
                                    ))

            # WI-mugir: ``my $ua = Pkg->new``, ``my $self = shift``,
            # ``my ($self, ...) = @_`` type the scalar; any other assignment
            # to it untypes it.
            elif node.type == "assignment_expression" and node.children:
                lhs, rhs = node.children[0], node.children[-1]
                scalars = (
                    [c for c in lhs.children if c.type == "scalar"]
                    if lhs.type == "variable_declaration" else [lhs]
                )
                is_list = any(c.type == "(" for c in lhs.children)
                sub = _find_enclosing_function_perl(node, decl_index)
                first = _scalar_name(scalars[0], source) if scalars else None
                takes_invocant = (
                    sub is not None and first in _INVOCANT_NAMES
                    and not _in_anonymous_sub(node)
                    and (node_text(rhs, source).replace(" ", "") == "@_" if is_list
                         else _is_method_invocant(rhs, source))
                )
                typed: Optional[str] = (
                    _get_current_package(node, source) if takes_invocant
                    else None if is_list else _constructed_package(rhs, source)
                )
                for index, scalar in enumerate(scalars):
                    name = _scalar_name(scalar, source)
                    if name is not None:
                        scalar_types[(sub.id if sub else "", name)] = (
                            typed if index == 0 else None)

            # Handle method calls (arrow operator)
            elif node.type == "method_call_expression":
                method_node = find_child_by_type(node, "method")
                if method_node is None:  # pragma: no cover - grammar always has one
                    continue
                method_name = node_text(method_node, source)
                package = _invocant_package(node, source)
                if package is not None and _PERL_METHOD_IDENTIFIER.fullmatch(
                    method_name,
                ):
                    # WI-mahik: the invocant names the owner (module docstring).
                    caller = _find_enclosing_function_perl(
                        node, decl_index,
                    ) or module_symbol
                    if caller:
                        edges.append(self._class_method_edge(
                            caller.id, package, method_name,
                            node.start_point[0] + 1, global_symbols, run_id,
                        ))
                elif _PERL_METHOD_IDENTIFIER.fullmatch(method_name):
                    # WI-mugir: an EXPRESSION invocant resolves on its type,
                    # never on the short name across packages (module docstring).
                    sub = _find_enclosing_function_perl(node, decl_index)
                    caller = sub or module_symbol
                    owner = _invocant_type(node.children[0], sub)
                    if caller and owner is not None:
                        edges.append(self._class_method_edge(
                            caller.id, owner, method_name,
                            node.start_point[0] + 1, global_symbols, run_id,
                            call_construct="method",
                        ))
                    elif caller:
                        edges.append(make_unresolved_edge(
                            "perl", caller.id, method_name,
                            node.start_point[0] + 1, PASS_ID, run_id,
                            call_construct="method",
                        ))
                else:
                    # A dynamic (``$o->$m``) or qualified (``$o->Other::m``,
                    # ``SUPER::m``) method name: the qualified name is looked
                    # up as written.
                    caller = _find_enclosing_function_perl(
                        node, decl_index,
                    )
                    if caller:
                        lookup_result = resolver.lookup(method_name, caller_path=_caller_path)
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
                                meta={"call_construct": "method"},
                            )
                            edges.append(edge)

        return edges

    @staticmethod
    def _class_method_edge(
        caller_id: str,
        package: str,
        method_name: str,
        line: int,
        global_symbols: dict[str, Symbol],
        run_id: str,
        *,
        call_construct: str = "function",
    ) -> Edge:
        """The edge for ``package->method_name``: ``package``'s own sub, or its external edge.

        ``call_construct`` is ``function`` for a call on the package itself
        (``Pkg->m``) and ``method`` for one on a value of it (``$obj->m``,
        WI-mugir), per ADR-0059.

        The registry is keyed by qualified name (``register_symbol``), and a
        ``main`` sub by its bare name, so the exact key is the whole lookup:
        no suffix match, which is the short-name binding this path replaces.
        """
        qualified = (
            method_name if package == "main" else f"{package}::{method_name}"
        )
        callee = global_symbols.get(qualified)
        if callee is not None and callee.kind == "function":
            return Edge.create(
                src=caller_id,
                dst=callee.id,
                edge_type="calls",
                line=line,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="ast_call",
                confidence=0.85,
                meta={"call_construct": call_construct},
            )
        return _package_unresolved_edge(
            caller_id, package, method_name, line, run_id,
            call_construct=call_construct,
        )


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
