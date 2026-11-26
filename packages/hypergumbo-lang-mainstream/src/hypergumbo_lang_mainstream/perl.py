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

1. extract_symbols_from_file: extracts the file pseudo-node, packages and subroutines,
   and what each package exports (``@EXPORT``) and which packages each file is in
2. register_symbol: every declaration by its full name (``Package::sub``; a ``main`` sub
   by its bare name), all of them: two files may declare one name
3. extract_edges_from_file: the file's import table, use/require, function calls, method calls
4. A call's enclosing sub is found by the declaration's position (INV-midag)

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

The calls (ADR-0064)
--------------------
The analyzer binds a call itself only where the call site writes the package
in full: Perl package names are absolute (``Foo::Bar`` is the same package
written in any package), so ``Pkg::f()``, ``Pkg->m()`` (``__PACKAGE__->m()``:
the enclosing package), ``$o->Pkg::m()`` and a call on a value whose package
the file states name the declaration
``Pkg::m`` exactly, with no lookup rule in between (``_declared``). A value's
package is taken only where the file states it: ``Pkg->new`` (the
constructor; ``DBI->connect`` returns a ``DBI::db``, not a ``DBI``), a scalar
assigned one, and the method invocant (``$self`` / ``$this`` / ``$class``
taken by ``shift`` or as the first of ``@_``, a bare ``shift->m`` outside
``main``), which is the enclosing package. The invocant is typed by NAME,
not by shape, because the shape does not say a sub is a method (see
``_INVOCANT_NAMES``).

``Pkg::m`` declared in ONE file is that declaration (the last in the file: a
later ``sub`` of one name replaces the earlier, Perl's rule). Declared in
several files, the caller's own file's declaration is the one meant (the
shared rule for a bare call, ``inherited_calls._declaration_named``: a file
is loaded with the file it is in); else nobody can tell which file was
loaded last, and the call is a stub. Before ADR-0064 the last declaration
registered in the run won.

Every other call goes out as a stub with what the call site says, and the
shared linkers decide through the file's import table (``_perl_import_scope``):

- ``f()`` / ``&f`` (a bare name): no fact. The declared-name rule
  (``type_visibility``) finds ``f`` in the caller's own package, or through
  the file's ``use`` lines: ``use Util qw(f)`` names ``Util::f``; ``use Util;``
  names what ``Util``'s ``@EXPORT`` lists, read from the tree.
- ``Pkg::f()``, ``Pkg->m()``, ``$o->Pkg::m()`` not bound above: ``qualifier``
  ``Pkg``, with ``Pkg`` in the module slot (ADR-0051's owner path), so a
  catalogue or overlay row for ``HTTP::Tiny`` / ``IO::File`` classifies it.
  The method may be inherited or AUTOLOADed: some other package's ``m`` is not
  it.
- ``$o->m()`` with ``$o`` from ``Pkg->new``: ``receiver_type_hint`` ``Pkg``
  (and ``Pkg`` in the module slot); with ``$self`` / ``shift``:
  ``enclosing_class`` (the enclosing package); ``$self->SUPER::m()``:
  ``enclosing_class`` and ``super_receiver``.
- ``$o->m()`` with ``$o`` untyped: no fact.
- ``$o->$m()``: nothing (the method's name is a value).

Perl inheritance (``use parent``, ``use base``, ``@ISA``) is not read: no
``extends`` edge is emitted, so no linker walks a Perl package's parents.

Before WI-mahik / WI-mugir the method arms looked ``m`` up by its short name
among every package (git: ``SVN::Pool->new`` -> ``Git::new``; a repository
``get`` captured ``HTTP::Tiny->new->get($u)``); before ADR-0064 a bare ``f()``
was bound by the run-wide name lookup (the exact key, else the one key ending
in ``f``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ClassVar, Iterator, Optional

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
from hypergumbo_core.ir import AnalysisRun, Edge, NodeId, ExternalRef, Span, Symbol, make_pass_id
from hypergumbo_core import resolved_by
from hypergumbo_core.import_scope import ImportScope
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


# A method's own name: a plain identifier. A dynamic name (``Pkg->$m``) names
# no sub and is not reported; a qualified one (``Pkg->Other::m``,
# ``SUPER::m``) is this identifier after the package the search starts in.
_PERL_METHOD_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")

# A sub a function call can name: ``f``, ``Pkg::f``. A code reference called
# with ``&`` (``&$code(..)``, ``&{$h{x}}``) names none, and is not reported.
_SUB_PATH = re.compile(r"[A-Za-z_]\w*(?:::\w+)*")


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
    caller_id: NodeId,
    package: str,
    name: str,
    line: int,
    run_id: str,
    *,
    call_construct: Optional[str] = None,
    qualifier: Optional[str] = None,
    receiver_type_hint: Optional[str] = None,
    enclosing_class: Optional[str] = None,
) -> Edge:
    """The unresolved edge for ``name`` in ``package``, the package as written.

    Shared by ``Pkg::name()``, ``Pkg->name()`` and a call on a value of
    ``Pkg``: one spelling, ``perl:<Pkg>:0-0:<name>:unresolved``, with a
    structured ``dst_ref`` keyed by the package (WI-nigah, ADR-0051's owner
    path), and the call fact that says how the call site named ``Pkg``
    (ADR-0064): ``qualifier`` (written), ``receiver_type_hint`` (a value
    constructed as ``Pkg``) or ``enclosing_class`` (the invocant).
    """
    return make_unresolved_edge(
        "perl", caller_id, name, line, PASS_ID, run_id,
        module_hint=package,
        dst_ref=ExternalRef(lang="perl", module_path=package, name=name),
        call_construct=call_construct,
        qualifier=qualifier,
        receiver_type_hint=receiver_type_hint,
        enclosing_class=enclosing_class,
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

# Modules whose ``use`` line imports no sub of the module named: ``parent`` /
# ``base`` name parent classes, ``lib`` directories, ``if`` a condition and a
# module to load (``use if $c, Foo => qw(f)``, not read).
_LOADS_NO_SUB = frozenset({"parent", "base", "lib", "if"})

# A sub's name as an import or export list writes it: ``f`` or ``&f``. Anything
# else in such a list is not a sub: ``$v`` / ``@a`` / ``%h`` (variables),
# ``:tag`` (a group, ``%EXPORT_TAGS``), ``!f`` / ``/re/`` (Exporter's negations
# and patterns), ``-norequire`` (a module's own option).
_IMPORTED_SUB = re.compile(r"&?([A-Za-z_]\w*)")


def _literal_words(nodes: "list[tree_sitter.Node]", source: bytes) -> list[str]:
    """The words the literal elements of a Perl list spell (``qw(a b)``,
    ``'a', "b"``, ``('a')``, ``&f``). Any other element (a variable, a call,
    an interpolating string) spells nothing that can be read here and is
    skipped: ``(@Test::More::EXPORT, qw(setup run))`` reads as ``setup run``,
    each of which the list does hold."""
    words: list[str] = []
    for node in nodes:
        if node.type == "quoted_word_list":
            content = find_child_by_type(node, "string_content")
            words += node_text(content, source).split() if content is not None else []
        elif node.type in ("string_literal", "interpolated_string_literal"):
            if all(c.type in ("string_content", '"', "'") for c in node.children):
                content = find_child_by_type(node, "string_content")
                words.append(node_text(content, source) if content is not None else "")
        elif node.type == "list_expression":
            words += _literal_words(node.children, source)
        elif node.type == "function_call_expression" and node.child_count == 1:
            words.append(node_text(node, source))  # ``&f`` in a list
    return words


def _use_import_list(node: "tree_sitter.Node", source: bytes) -> Optional[list[str]]:
    """What a ``use Module LIST;`` imports, as written: ``None`` when it writes
    no list (``use Module;`` imports the module's ``@EXPORT``), ``[]`` for
    ``use Module ();`` (nothing), else the words. A version (``use Module
    1.2 qw(f)``) is not part of the list."""
    items = [c for c in node.children[2:] if c.type not in (";", "number", "version")]
    return _literal_words(items, source) if items else None


def _package_exports(tree: "tree_sitter.Tree", source: bytes) -> dict[str, tuple[str, ...]]:
    """Each package's ``@EXPORT`` in this file: ``our @EXPORT = qw(..)``,
    ``@EXPORT = (..)``, ``push @EXPORT, ..``, the literal words of every one
    in force in that package added together (``_literal_words``: a name the
    list takes from elsewhere is not known here, and is not bound).
    ``@EXPORT_OK`` is not read: it says what a ``use`` may name, and the
    ``use`` line names it."""
    found: dict[str, list[str]] = {}
    for node in iter_tree(tree.root_node):
        if node.type == "assignment_expression" and node.children:
            target = node.children[0]
            array = (find_child_by_type(target, "array")
                     if target.type == "variable_declaration" else target)
            values = node.children[2:]
        elif node.type == "ambiguous_function_call_expression":
            function = find_child_by_type(node, "function")
            if function is None or node_text(function, source) != "push":
                continue
            array = find_child_by_type(node, "array")
            values = [c for c in node.children if c is not array and c.type != "function"]
        else:
            continue
        varname = find_child_by_type(array, "varname") if array is not None else None
        if array is None or array.type != "array" or varname is None or (
            node_text(varname, source) != "EXPORT"
        ):
            continue
        package = _get_current_package(node, source)
        found.setdefault(package, []).extend(
            sub.group(1) for sub in map(_IMPORTED_SUB.fullmatch, _literal_words(values, source))
            if sub is not None
        )
    return {package: tuple(words) for package, words in found.items()}


def _perl_import_scope(
    tree: "tree_sitter.Tree",
    source: bytes,
    own_namespace: Optional[str],
    exports_of: Callable[[str], Optional[tuple[str, ...]]],
) -> ImportScope:
    """The file's import table for the shared call resolution (ADR-0064), in
    the declared-name form: a Perl package is a namespace, found by its full
    name, and packages do not nest (``Foo::Bar`` is not inside ``Foo``).

    ``use Util qw(f g)`` / ``use Util 'f'`` binds ``f`` to ``Util::f``
    (``add_named``), whether or not the repository declares ``Util``: a
    library's name then means the library's sub, never a namesake of the
    repository. ``use Util;`` and ``use Util qw(:DEFAULT)`` bind the subs
    ``Util``'s ``@EXPORT`` lists where the tree declares it (``exports_of``,
    ``add_wildcard``); a library's binds nothing.
    ``use Util ();`` binds nothing. Pragmas, ``use parent`` / ``use base`` /
    ``use lib`` / ``use if``, ``no Module`` and ``require`` bind no sub.

    ``own_namespace`` is the one package of the file (``""`` for ``main``,
    whose subs are named bare), or ``None`` for a file of several packages.
    ``library_names_screened`` stays False: a Perl sub is kind ``function``
    whatever calls it, and the single-holder name step binds a call on a
    receiver only to a ``method``, a bare call in a language of declared
    names never, so it has nothing to bind for Perl (measured: no edge moves
    with it on).
    """
    scope = ImportScope(own_namespace=own_namespace)
    for node in iter_tree(tree.root_node):
        if node.type != "use_statement" or node.children[0].type == "no":
            continue  # ``no Module LIST`` takes names away
        package_node = find_child_by_type(node, "package")
        module = node_text(package_node, source) if package_node is not None else ""
        if not module or module in _PERL_PRAGMAS or module in _LOADS_NO_SUB:
            continue
        written = _use_import_list(node, source)
        for word in [":DEFAULT"] if written is None else written:
            imported = _IMPORTED_SUB.fullmatch(word)
            if word == ":DEFAULT":
                scope.add_wildcard(module, exports_of(module) or ())
            elif imported is not None:
                scope.add_named(module, imported.group(1))
    return scope


class PerlAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Perl analyzer.

    Uses tree-sitter-perl from the language-pack to extract packages,
    subroutines, and cross-file call/import edges.

    ``register_symbol`` keeps EVERY declaration of a full name
    (``Package::sub``, a ``main`` sub bare): two files may declare one name,
    and which of them a call means is decided by ``_declared``, not by the
    order the files were read in.

    Pass 2 finds a call's enclosing sub by the declaration's position
    (INV-midag). It kept the file's LAST package name for that, which
    qualified every sub of a two-package file into the last package.

    Run state, reset when a new run's first file is read: each package's
    ``@EXPORT`` per file (pass 1, read by every file's table in pass 2), the
    packages each file's subs are in, and each file's table
    (``AnalysisResult.import_scopes``, ``analyze``).
    """

    lang = "perl"
    file_patterns: ClassVar[list[str]] = ["*.pl", "*.pm", "*.t"]
    language_pack_name = "perl"
    # INV-kokaj: extract_symbols_from_file now emits the canonical file
    # pseudo-node explicitly (with the file's actual end_line) so the
    # base-class auto-emitted Symbol (span=1-1) would duplicate it under
    # the same id.
    create_file_symbols = False
    _run_id: Optional[str] = None
    _exports: "dict[str, dict[str, tuple[str, ...]]]"
    _file_packages: "dict[str, set[str]]"
    _import_scopes: "dict[str, ImportScope]"

    def __init__(self) -> None:
        super().__init__()
        self._exports, self._file_packages, self._import_scopes = {}, {}, {}

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """The base two-pass analysis, with every file's import table in the
        result: a file with no ``use`` has an empty one, which still tells the
        linkers that this file's calls are reported for them to resolve."""
        result = super().analyze(repo_root, max_files)
        result.import_scopes = dict(self._import_scopes)
        return result

    def _exports_of(self, package: str) -> Optional[tuple[str, ...]]:
        """``package``'s ``@EXPORT`` as the tree declares it: one list, or
        the same list in every file that declares one; ``None`` when no file
        does or two files disagree."""
        lists = set(self._exports.get(package, {}).values())
        return next(iter(lists)) if len(lists) == 1 else None

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
        packages get qualified names (Package::sub). A symbol's
        ``qualified_name`` is the full name its stable id already hashes: the
        package's name, ``Package::sub``, a ``main`` sub's bare name.
        """
        if self._run_id != run.execution_id:
            self._run_id = run.execution_id
            self._exports, self._file_packages, self._import_scopes = {}, {}, {}
        for package, exported in _package_exports(tree, source).items():
            self._exports.setdefault(package, {})[rel_path] = exported
        packages = self._file_packages.setdefault(rel_path, set())
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
                    packages.add(package_name)
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
                        qualified_name=package_name,
                    )
                    analysis.symbols.append(symbol)
                    analysis.node_for_symbol[symbol.id] = node
                    analysis.symbol_by_name[package_name] = symbol

            elif node.type == "subroutine_declaration_statement":
                bareword = find_child_by_type(node, "bareword")
                if bareword:
                    sub_name = node_text(bareword, source)
                    current_pkg = _get_current_package(node, source)
                    packages.add(current_pkg)
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
                    # ADR-0036 Ruling 1: the slot is the sanitized name,
                    # ``Pkg::sub`` -> ``Pkg..sub`` (it held the bare sub).
                    sym_id = make_symbol_id(
                        "perl", rel_path, start_line, end_line,
                        qualified_name, "function",
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
                        qualified_name=qualified_name,
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
        """Every declaration of a full name, in the order read (``_declared``
        decides among them)."""
        global_symbols.setdefault(symbol.name, []).append(symbol)

    def _own_namespace(self, rel_path: str) -> Optional[str]:
        """The file's one package for its table (``""``: ``main``, whose subs
        are named bare), or ``None`` when its package statements and subs are
        in several."""
        packages = {"" if p == "main" else p for p in self._file_packages.get(rel_path, ())}
        return next(iter(packages)) if len(packages) == 1 else (None if packages else "")

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
        """Extract call and import edges from a Perl file, and keep its
        import table (``_perl_import_scope``) for the run's result.

        Detects use_statement, require_expression, function calls
        (ambiguous/func0op/func1op), and method calls (arrow operator); the
        module docstring says which calls are bound here and which are
        reported.
        """
        edges: list[Edge] = []
        file_id = make_file_id("perl", rel_path)
        run_id = run.execution_id
        self._import_scopes[rel_path] = _perl_import_scope(
            tree, source, self._own_namespace(rel_path), self._exports_of,
        )
        file_symbols = self.file_symbols(local_symbols)
        # Every declaration of this file, by position (INV-midag).
        decl_index = symbols_at(file_symbols)

        # INV-kokaj: the file pseudo-node is the caller of a top-level call.
        module_symbol = next((s for s in file_symbols if s.kind == "file"), None)
        # WI-mugir: the package a scalar holds, per (enclosing sub id or ""
        # for the file, name), and whether it is the invocant (the enclosing
        # package) rather than a constructed value. ``None`` records an
        # assignment of something untyped, so a sub's own rebinding is not
        # read through to a same-named file lexical.
        scalar_types: dict[tuple[str, str], Optional[tuple[str, bool]]] = {}

        def _invocant_type(
            invocant: "tree_sitter.Node", sub: Optional[Symbol],
        ) -> Optional[tuple[str, bool]]:
            """The package an expression invocant's value is, when the file
            says, and whether that is because it is the invocant."""
            if _is_method_invocant(invocant, source):
                # A bare ``shift->m`` is the method idiom outside ``main``;
                # in a script's plain function it is the first argument.
                package = _get_current_package(invocant, source)
                return (package, True) if (
                    sub and package != "main" and not _in_anonymous_sub(invocant)
                ) else None
            constructed = _constructed_package(invocant, source)
            if constructed is not None:
                return constructed, False
            name = _scalar_name(invocant, source)
            if name is None:
                return None
            key = (sub.id if sub else "", name)
            return scalar_types[key] if key in scalar_types else scalar_types.get(("", name))

        def bind_or_report(
            caller: Symbol, call: "tree_sitter.Node", package: str, name: str,
            by: str, call_construct: Optional[str], **facts: Optional[str],
        ) -> None:
            """``package``'s own ``name`` (``_declared``), else its stub."""
            line = call.start_point[0] + 1
            callee = _declared(global_symbols, package, name, rel_path)
            if callee is not None:
                edges.append(Edge.create(
                    src=caller.id, dst=callee.id, edge_type="calls", line=line,
                    origin=PASS_ID, origin_run_id=run_id, evidence_type="ast_call",
                    confidence=0.85,
                    meta={"call_construct": call_construct or "function"},
                    resolved_by=by,
                ))
                return
            edges.append(_package_unresolved_edge(
                caller.id, package, name, line, run_id,
                call_construct=call_construct, **facts,
            ))

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
                if func_node is None:
                    continue
                func_name = _written_sub(node_text(func_node, source))
                caller = _find_enclosing_function_perl(node, decl_index) or module_symbol
                if (
                    func_name in _PERL_BUILTINS or caller is None
                    or not _SUB_PATH.fullmatch(func_name)
                ):
                    continue  # a builtin; ``&$code(..)`` names no sub
                if "::" in func_name:
                    # ``Pkg::f()`` names its package in full (WI-nigah: the
                    # stub keeps the package in its module slot).
                    pkg, _, bare = func_name.rpartition("::")
                    bind_or_report(caller, node, pkg, bare, resolved_by.QUALIFIED,
                                   None, qualifier=pkg)
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
                constructed = None if is_list else _constructed_package(rhs, source)
                typed: Optional[tuple[str, bool]] = (
                    (_get_current_package(node, source), True) if takes_invocant
                    else (constructed, False) if constructed is not None else None
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
                sub = _find_enclosing_function_perl(node, decl_index)
                caller = sub or module_symbol
                if caller is None:  # pragma: no cover - every file has its file symbol
                    continue
                package = _invocant_package(node, source)
                written_owner, _, written_method = method_name.rpartition("::")
                if not _PERL_METHOD_IDENTIFIER.fullmatch(written_method):
                    continue  # ``$o->$m()``: the method's name is a value
                if written_owner == "SUPER":
                    # The search starts at the parents of the package the
                    # sub is compiled in, whatever the invocant.
                    edges.append(make_unresolved_edge(
                        "perl", caller.id, written_method,
                        node.start_point[0] + 1, PASS_ID, run_id,
                        call_construct="method", super_receiver=True,
                        enclosing_class=_get_current_package(node, source),
                    ))
                elif written_owner:
                    # ``$o->Other::m()``: the search starts at ``Other``.
                    bind_or_report(caller, node, written_owner.strip(":") or "main",
                                   written_method, resolved_by.QUALIFIED, "method",
                                   qualifier=written_owner.strip(":") or "main")
                elif package is not None:
                    # WI-mahik: the invocant names the owner (module docstring).
                    bind_or_report(caller, node, package, method_name,
                                   resolved_by.QUALIFIED, "function", qualifier=package)
                else:
                    # WI-mugir: an EXPRESSION invocant resolves on its type,
                    # never on the short name across packages (module docstring).
                    owner = _invocant_type(node.children[0], sub)
                    if owner is None:
                        edges.append(make_unresolved_edge(
                            "perl", caller.id, method_name,
                            node.start_point[0] + 1, PASS_ID, run_id,
                            call_construct="method",
                        ))
                        continue
                    owner_package, is_invocant = owner
                    # ``shift->m`` is the invocant itself; a scalar or a
                    # ``Pkg->new`` carries the type it was given.
                    direct = _is_method_invocant(node.children[0], source)
                    bind_or_report(
                        caller, node, owner_package, method_name,
                        resolved_by.OWN_TYPE if direct else resolved_by.RECEIVER_TYPE,
                        "method",
                        **({"enclosing_class": owner_package} if is_invocant
                           else {"receiver_type_hint": owner_package}),
                    )

        return edges


def _written_sub(written: str) -> str:
    """The sub a function call names, as Perl reads the text: ``&f`` is ``f``
    (the sigil), ``::f`` and ``main::f`` are ``main``'s ``f`` (named bare),
    and ``main::Pkg::f`` is ``Pkg::f`` (``main::`` is the root of every
    package name)."""
    name = written.lstrip("&")
    while name.startswith(("::", "main::")):
        name = name[2:] if name.startswith("::") else name[len("main::"):]
    return name


def _declared(
    global_symbols: "dict[str, list[Symbol]]", package: str, name: str, caller_path: str,
) -> Optional[Symbol]:
    """The sub ``package::name`` (``main``'s by its bare name), as the call
    site writes it in full: declared in ONE file, the last declaration in it
    (a later ``sub`` of one name replaces the earlier); in several files, the
    caller's own file's, else ``None`` (which file was loaded last cannot be
    told). No suffix, no other package: the owner is written."""
    key = name if package == "main" else f"{package}::{name}"
    found = [s for s in global_symbols.get(key, ()) if s.kind == "function"]
    files = {s.path for s in found}
    if len(files) > 1:
        found = [s for s in found if s.path == caller_path]
    return max(found, key=lambda s: s.span.start_line) if found else None


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
