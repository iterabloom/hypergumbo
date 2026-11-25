# SPDX-License-Identifier: AGPL-3.0-or-later
"""PHP analysis pass using tree-sitter-php.

This analyzer uses tree-sitter-php to parse PHP files and extract:
- Function declarations (symbols). A function declared inside another
  function or a method is the namespace's function (``qualified_name``),
  and its stable_id hashes its holders too (``App\\A::f::h``,
  ``_php_holders``), so the ``h`` of two holders do not tie (they did until
  scheme v19, WI-finam)
- Class declarations (symbols)
- Interface, trait and enum declarations (container symbols, so in-file
  interfaces take part in dispatch instead of surfacing as placeholders)
- Inheritance on class, interface and enum symbols (traits have none):
  ``meta.base_classes`` lists the ``extends`` / ``implements`` names as
  written, and ``meta.base_class_imports`` maps each bare base to the
  qualified name of the ``use`` it came through, so the inheritance linker
  checks that name rather than any in-repo type with the same short name
- Method declarations (symbols)
- The signature of a function and a method (``_extract_php_signature``),
  which is also the text hashed into the typed stable_id
  (``normalize_php_signature``): every parameter node, a variadic
  ``int ...$xs`` and a constructor's promoted ``private int $x`` included,
  as ``[type ][&][...]$name`` with its type's source text whatever the
  node kind (``A&B``, ``(A&B)|null``), the return type the same way
  (``never`` included; ``void`` omitted), and a leading ``&`` for
  ``function &f()``. Before, only ``simple_parameter`` and four type
  kinds were written: the rest of a parameter list or the return type was
  dropped, and two declarations differing only there shared one id. A
  promoted parameter's visibility and ``readonly`` are not written (they
  declare a property, not the parameter's type); neither are attributes
  and default values. Property and class-constant types are not hashed
  (their stable_id has an empty signature slot).
- Properties and class constants (``field``, whose stable_id is the ADR-0035
  producer formula, ``make_typed_stable_id`` over the namespace/type chain;
  WI-tusad: they rode the backstop floor key) and global constants
  (``variable``)
- The namespace of each declaration, read per declaration
  (``_php_namespace_resolver``): braced ``namespace A { }`` blocks and
  repeated ``namespace A;`` statements. It was read once from the first
  definition, so a second namespace's declarations carried the first one's
  name in ``qualified_name`` and stable_id and tied with it (INV-fabih
  sweep). Classes, interfaces, traits and enums mint
  ``overload_arity.scoped_table_stable_id`` scoped by that namespace (they
  had the backstop's bare-name table key)
- A per-file ``kind="file"`` pseudo-node that anchors file-level edges
- Laravel route definitions (Route::get, Route::post, etc.)
- Call and instantiation edges (``_extract_edges``): bound here only on
  PHP's own evidence (``$this->m()`` / ``self::m()`` / ``static::m()`` on the
  enclosing type's own method, found by position); every other call is an
  unresolved stub carrying the facts its site has, which the shared linkers
  resolve through the file's import table (ADR-0064)
- One import table per file (``_php_uses``), handed over in
  ``AnalysisResult.import_scopes``
- Import relationships (edges): one ``imports`` edge per ungrouped ``use``
  clause that names a qualified path (``use A\\B, C\\D;`` gives two),
  carrying the full namespace so framework detection can match it; grouped
  ``use A\\{B, C}`` and a bare ``use Foo;`` emit none

If tree-sitter-php is not installed, the analyzer gracefully degrades:
it emits a warning and returns a skipped result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-php are available
2. If not available, warn and return a result with ``skipped=True`` and
   ``skip_reason_code=DEPENDENCY_UNAVAILABLE`` (no PHP analysis)
3. Find files with ``find_php_files``, which is also the profile's PHP
   count: every .php file except those ``classify_dot_php_file`` reads as
   Hack (a ``<?hh`` header), which the Hack analyzer takes instead
4. Three-pass analysis:
   - Pass 1: Parse all files, read each file's ``use`` declarations once,
     extract its symbols
   - Pass 2: Detect each file's calls against its own declarations (the
     caller and the enclosing type's members, by position) and the types its
     declarations write (``_PhpTypes``), then apply the ADR-0015 automatic
     dataflow annotation to the edges
   - Pass 3: Extract usage contexts and emit Laravel route Symbols

Why This Design
---------------
- Optional dependency keeps base install lightweight
- PHP support is separate from JS/TS to keep modules focused
- Multi-pass: every symbol exists before any call is read; a call to
  another file's declaration is resolved by the shared linkers, not here
- Same pattern as JS/TS analyzer for consistency

Population of ``is_exported`` follows PHP's default-public rule: top-level
functions and classes are exported; class members are exported unless the
declaration carries ``private`` or ``protected``. Class constants follow
the same rule (their modifier is read like a property's); in a constant's
stable_id only ``private`` / ``protected`` fill the visibility slot, and an
explicit ``public`` hashes the same empty slot as an unmodified constant
(``_php_constant_id_visibility``).
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import classify_dot_php_file, find_files
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, StableId, Symbol, UsageContext, make_pass_id
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core import resolved_by
from hypergumbo_core.analyze.base import (
    find_matching_paren,
    record_partial_parse,
    AnalysisResult,
    TreeSitterAnalyzer,
    ancestor_labels,
    enclosing_declared_symbol,
    symbol_declared_by,
    symbols_at,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_route_symbol,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    populate_docstrings_from_tree,
    visibility_from_modifiers,
)
from hypergumbo_core.overload_arity import scoped_table_stable_id
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)
from hypergumbo_core.dataflow import annotate_dataflow, get_dataflow_config
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("php")

# ADR-0015: Dataflow config for PHP
_df_config = get_dataflow_config("php")

# Laravel HTTP route methods - used by _extract_laravel_routes
LARAVEL_HTTP_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
}

# Laravel resource-route action set. Order matters for stable output.
# (http_method, path_suffix, action_name)
LARAVEL_RESOURCE_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("GET", "", "index"),
    ("GET", "/create", "create"),
    ("POST", "", "store"),
    ("GET", "/{id}", "show"),
    ("GET", "/{id}/edit", "edit"),
    ("PUT", "/{id}", "update"),
    ("DELETE", "/{id}", "destroy"),
)

# `Route::apiResource()` excludes the two HTML-form routes: `create` and
# `edit`. Treating `apiResource` identically to `resource` produces phantom
# GET /create and GET /{id}/edit endpoints that don't exist (WI-jorim).
LARAVEL_API_RESOURCE_EXCLUDED_ACTIONS: frozenset[str] = frozenset(
    {"create", "edit"}
)


def find_php_files(repo_root: Path) -> Iterator[Path]:
    """Yield the repository's .php files, except Hack's (a ``<?hh`` header)."""
    for path in find_files(repo_root, ["*.php"]):
        if classify_dot_php_file(path) == "php":
            yield path


def _is_php_tree_sitter_available_legacy() -> bool:  # pragma: no cover - replaced by TreeSitterAnalyzer
    """Legacy availability check -- replaced by TreeSitterAnalyzer._check_grammar_available."""
    pass


def _extract_use_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    r"""The file's ``use`` aliases: short name to full path
    (:func:`_php_uses`)."""
    return _php_uses(tree, source).aliases


@dataclass
class _PhpUses:
    r"""What one file's ``use`` declarations say, read in one walk.

    ``aliases``: local short name to the full path as written, every kind
    together (``use A\B;``, ``use A\B as C;``, ``use function A\f;``, ``use
    const A\K;``, grouped forms). It is what the symbol pass reads for a base
    class's import (``meta["base_class_imports"]``) and what an unresolved
    bare call names as its module (the external identity a catalogue row
    matches). A bare ``use Foo;`` is not in it.

    ``scope``: the file's import table for the shared call resolution
    (ADR-0064), in the declared-name form (``ImportScope``, namespaces): a
    class import and a function import are ``add_named(module, name,
    alias)``, ``use Foo;`` is ``add_named("", "Foo")``. A ``use const`` is
    left out: nothing calls a constant. PHP's own global functions and
    classes are not recorded: nothing in a repository is declared there.
    The file's own namespace needs no record: the caller's qualified name
    carries it.

    ``library_names_screened`` stays False, so the single-holder name linker
    does not run for PHP, although this analyzer marks a call whose name PHP's
    own classes define (:data:`_PHP_LIBRARY_METHOD_NAMES`). Measured with it
    True on two trees (grpc's PHP, koel), read in the source: of 15 calls the
    name step bound that nothing bound before, 2 were right; of 15 it bound to
    the target the old by-name lookup had chosen, 9. The wrong ones are calls
    on a value a LIBRARY call returned (Laravel's ``$table->foreign('x')
    ->references('id')->on('users')``, ``Storage::disk('s3')->put(..)``,
    ``$response->json()``), bound to the repository's one ``on`` / ``put`` /
    ``json``: no list of PHP's own names holds a framework's, and no stub fact
    says that a receiver is the result of a call.
    """

    aliases: dict[str, str]
    scope: ImportScope


def _php_uses(tree: "tree_sitter.Tree", source: bytes) -> _PhpUses:
    r"""Read every ``namespace_use_declaration`` of the file (:class:`_PhpUses`).

    THE GROUPED FORM (PHP 7.0+) IS ITS OWN SHAPE, and it registered nothing
    until INV-nuvug. tree-sitter puts the shared prefix on the declaration as a
    ``namespace_name`` and the clauses one level down, in a
    ``namespace_use_group``; each clause holds a bare ``name`` (``A``) or a
    qualified tail (``Sub\D``) RELATIVE to that prefix. A scan of the
    declaration's direct children for a clause with a ``qualified_name`` found
    neither, so a call through a grouped import fell to the ``external`` slot
    while the single-import spelling of the same call resolved. INV-zuvib's
    shape in rust.py.

    The kind (``function``, ``const``) is the clause's ``type`` field, or the
    declaration's for a group written ``use function A\{f, g};``.
    """
    aliases: dict[str, str] = {}
    scope = ImportScope()

    for node in iter_tree(tree.root_node):
        if node.type != "namespace_use_declaration":
            continue

        prefix = ""
        group = None
        for child in node.children:
            if child.type == "namespace_name":
                prefix = node_text(child, source)
            elif child.type == "namespace_use_group":
                group = child
        declared_kind = node.child_by_field_name("type")

        clauses = group.children if group is not None else node.children
        for clause in clauses:
            if clause.type != "namespace_use_clause":
                continue
            kind = clause.child_by_field_name("type") or declared_kind
            path, alias_name = _use_clause_path(clause, source, grouped=group is not None)
            if path is None:
                bare = clause.named_children[0] if clause.named_children else None
                if bare is not None and bare.type == "name" and kind is None:
                    scope.add_named(module="", name=node_text(bare, source), alias=alias_name)
                continue
            full_path = f"{prefix}\\{path}" if prefix else path
            module, _, name = full_path.rpartition("\\")
            short_name = alias_name or name
            aliases[short_name] = full_path
            if kind is None or kind.type == "function":
                scope.add_named(module=module, name=name, alias=alias_name)

    return _PhpUses(aliases=aliases, scope=scope)


def _use_clause_path(
    clause: "tree_sitter.Node", source: bytes, *, grouped: bool,
) -> tuple[Optional[str], Optional[str]]:
    """``(path, alias)`` of one ``namespace_use_clause``.

    The path is the ``qualified_name`` child, or, inside a group only, the bare
    ``name`` before any ``as``. The alias is the ``name`` after ``as``.
    """
    path: Optional[str] = None
    alias_name: Optional[str] = None
    has_as = False
    for sub in clause.children:
        if sub.type == "qualified_name":
            path = node_text(sub, source)
        elif sub.type == "as":
            has_as = True
        elif sub.type == "name":
            if has_as:
                alias_name = node_text(sub, source)
            elif grouped and path is None:
                path = node_text(sub, source)
    return path, alias_name


def _find_name_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "name":
            return node_text(child, source)
    return None


def _extract_base_classes_php(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base class and interface names from class declaration.

    Handles:
    - class Foo extends Bar
    - class Foo implements IBar
    - class Foo extends Bar implements IBaz, IQux

    Args:
        node: class_declaration node
        source: Source code bytes

    Returns:
        List of base class/interface names
    """
    base_classes: list[str] = []

    for child in node.children:
        # extends clause: class Foo extends Bar
        if child.type == "base_clause":
            for sub in child.children:
                if sub.type == "name":
                    base_classes.append(node_text(sub, source))
                elif sub.type == "qualified_name":
                    # Fully qualified: extends \Namespace\Class
                    base_classes.append(node_text(sub, source))
        # implements clause: class Foo implements IBar, IBaz
        elif child.type == "class_interface_clause":
            for sub in child.children:
                if sub.type == "name":
                    base_classes.append(node_text(sub, source))
                elif sub.type == "qualified_name":
                    base_classes.append(node_text(sub, source))

    return base_classes


# WI-fosuh (WI-jusus): PHP type-declaration containers whose bodies hold the
# ``field``-kind members (properties, constants) emitted below. Constants are
# also legal at file scope (a global constant → a ``variable``).
_PHP_TYPE_CONTAINERS = (
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
)

# INV-tihim: the symbol walk emitted ONLY `class_declaration`, so three of
# PHP's four type containers were invisible — an in-file `interface` surfaced
# as an `external_symbol` placeholder and PHP produced no interface dispatch
# at all. One branch keyed off this map rather than four copies of it, so a
# fifth container cannot be added to _PHP_TYPE_CONTAINERS and silently skipped.
_PHP_CONTAINER_KINDS: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}


def _php_holders(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """The names of the type declarations, methods and functions enclosing
    ``node``, outermost first: what a nested function's stable_id hashes
    beside the namespace (WI-finam).

    PHP declares ``function h() {}`` written inside a function or a method
    in the global (namespace) scope when its holder runs, so its
    ``qualified_name`` is the namespace's ``h`` whatever holds it, and its id
    hashed that name alone: the ``h`` of ``A::f``, of ``A::g`` and of
    ``B::f`` were one id. A closure has no name and adds nothing. PHP has no
    overloading, so a name tells a holder from its siblings.
    """
    def label(n: "tree_sitter.Node") -> Optional[str]:
        if n.type not in _PHP_HOLDER_NODES:
            return None
        name_node = n.child_by_field_name("name")
        return node_text(name_node, source) if name_node is not None else None
    return ancestor_labels(node, label)


#: The declarations whose name labels a nested function's holder chain.
_PHP_HOLDER_NODES = frozenset({*_PHP_CONTAINER_KINDS, "function_definition", "method_declaration"})


def _enclosing_type_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up to the enclosing type declaration (class/interface/trait/enum)."""
    current = node.parent
    while current is not None:
        if current.type in _PHP_TYPE_CONTAINERS:
            name = _find_name_in_children(current, source)
            if name:
                return name
        current = current.parent
    return None


def _php_property_element_name(
    el: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """The property name (without the ``$``) from a ``property_element``."""
    for vn in el.children:
        if vn.type == "variable_name":
            for nm in vn.children:
                if nm.type == "name":
                    return node_text(nm, source)
    return None  # pragma: no cover - defensive: a property always names a var


def _php_const_element_name(
    el: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """The constant name from a ``const_element``."""
    for nm in el.children:
        if nm.type == "name":
            return node_text(nm, source)
    return None  # pragma: no cover - defensive: a const_element always names


def _php_member_symbol(
    file_path: Path,
    el: "tree_sitter.Node",
    full_name: str,
    kind: str,
    qualified_name: Optional[str],
    modifiers: list[str],
    exported: bool,
    run: AnalysisRun,
    stable_id: Optional[StableId] = None,
) -> Symbol:
    """Build a ``field`` / ``variable`` Symbol for a PHP property or constant,
    spanning the individual property/const ``element`` node (so ``$a, $b`` in one
    declaration emit two distinctly-spanned fields)."""
    span = Span(
        start_line=el.start_point[0] + 1,
        end_line=el.end_point[0] + 1,
        start_col=el.start_point[1],
        end_col=el.end_point[1],
    )
    return Symbol(
        id=make_symbol_id(
            "php", str(file_path), span.start_line, span.end_line, full_name, kind
        ),
        name=full_name,
        kind=kind,
        language="php",
        path=str(file_path),
        span=span,
        origin=PASS_ID,
        origin_run_id=run.execution_id,
        stable_id=stable_id,
        modifiers=modifiers,
        line_span=span.end_line - span.start_line + 1,
        is_exported=exported,
        qualified_name=qualified_name,
    )


def _php_namespace_name(
    definition: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """The name a ``namespace_definition`` declares; None for ``namespace {``
    (the global namespace)."""
    for sub in definition.children:
        if sub.type in ("namespace_name", "qualified_name"):
            return node_text(sub, source)
    return None


def _php_namespace_resolver(
    root: "tree_sitter.Node", source: bytes
) -> "Callable[[tree_sitter.Node], Optional[str]]":
    """A function giving the namespace each node of the file is declared in.

    A file may declare several namespaces: braced (``namespace A { ... }``,
    whose declarations are its descendants) or statement-form (``namespace
    A;``, which holds until the next one, its declarations being SIBLINGS at
    the top level). The namespace used to be read once, from the FIRST
    definition, so every declaration of a later namespace carried the first
    one's name in its ``qualified_name`` and stable_id: ``A\\Inner`` and
    ``B\\Inner`` were both ``A\\Inner``, tied, and deleting the first moved
    the second's id (INV-fabih sweep). None is the global namespace.
    """
    statements = [
        (child.start_byte, _php_namespace_name(child, source))
        for child in root.children
        if child.type == "namespace_definition"
        and not any(c.type == "compound_statement" for c in child.children)
    ]

    def namespace_of(node: "tree_sitter.Node") -> Optional[str]:
        current = node.parent
        while current is not None:
            if current.type == "namespace_definition":
                return _php_namespace_name(current, source)
            current = current.parent
        before = [name for start, name in statements if start < node.start_byte]
        return before[-1] if before else None
    return namespace_of


def _make_php_qualified_name(
    namespace: Optional[str], enclosing_class: Optional[str], name: str
) -> str:
    """Build a PHP qualified name.

    PHP namespace separator is ``\\``; the class-to-method boundary uses
    ``::`` (PHP's scope-resolution operator). Examples:

        - top-level function in App\\Service: ``App\\Service\\helperFunc``
        - method on class in App\\Service: ``App\\Service\\HelloService::method``
        - method on a class in the global namespace: ``HelloService::method``
    """
    ns_sep = separator_for_language("php")  # "\\"
    parts: list[str] = []
    if namespace:
        parts.append(namespace)
    if enclosing_class:
        parts.append(enclosing_class)
        # Namespace + class joined by "\\"; method appended via "::".
        return ns_sep.join(parts) + "::" + name
    parts.append(name)
    return ns_sep.join(parts)


# The node kinds of a PHP parameter (``formal_parameters`` children). A
# ``variadic_parameter`` (``int ...$xs``) and a ``property_promotion_parameter``
# (``private int $x`` in a constructor) were dropped whole: only
# ``simple_parameter`` had a branch.
_PHP_PARAMETER_NODE_TYPES: frozenset[str] = frozenset({
    "simple_parameter", "variadic_parameter", "property_promotion_parameter",
})


def _php_type_text(type_node: "tree_sitter.Node", source: bytes) -> str:
    """The source text of a type node with each comment in it written as one
    space (WI-radim).

    A comment is not an id input. The shared ``without_comments`` (applied
    when the signature is normalized and hashed) knows ``//`` and ``/* */``
    but not PHP's ``#`` line comment, which other languages' texts use for
    code (``#if``, ``#[...]``), so ``int|# c\n string`` hashed the comment:
    the grammar's ``comment`` nodes are blanked here instead.
    """
    start = type_node.start_byte
    parts: list[str] = []
    for comment in _php_comments(type_node):
        parts.append(source[start:comment.start_byte].decode("utf-8", "replace"))
        parts.append(" ")
        start = comment.end_byte
    parts.append(source[start:type_node.end_byte].decode("utf-8", "replace"))
    return "".join(parts)


def _php_comments(node: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """The ``comment`` nodes inside ``node``, in source order."""
    found: list["tree_sitter.Node"] = []
    for child in node.children:
        if child.type == "comment":
            found.append(child)
        else:
            found.extend(_php_comments(child))
    return found


def _php_parameter_text(param: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """One parameter as ``[type ][&][...]$name``, or None if it names nothing.

    The type is the text of the ``type`` field whatever its node kind (an
    intersection ``A&B`` and a DNF ``(A&B)|null`` were dropped). A
    by-reference ``&`` and a variadic ``...`` are written before the name: a
    simple or variadic parameter has its ``&`` in a ``reference_modifier``
    field, a promoted one inside its ``name`` field (``by_ref``), whose text is
    then ``&$x``. A promoted parameter's visibility and ``readonly`` are not
    written: they declare the property, not the parameter's type. Attributes
    and the default value are not written either.
    """
    name_node = param.child_by_field_name("name")
    if name_node is None:  # pragma: no cover - defensive: a parameter names a variable
        return None
    marks = "&" if param.child_by_field_name("reference_modifier") is not None else ""
    if param.type == "variadic_parameter":
        marks += "..."
    name = marks + node_text(name_node, source)
    type_node = param.child_by_field_name("type")
    if type_node is None:
        return name
    return f"{_php_type_text(type_node, source)} {name}"


def _extract_php_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a PHP function or method declaration.

    Returns signature like:
    - "(int $x, int $y): int" for typed functions
    - "(string $msg)" for void functions
    - "&(array &$a, int ...$xs): A&B" for a function returning by reference

    Every parameter kind (``_PHP_PARAMETER_NODE_TYPES``) is written by
    ``_php_parameter_text``, and the return type is the text of the
    ``return_type`` field whatever its kind (``never`` is a ``bottom_type``;
    ``A&B`` and ``(A&B)|null`` were dropped too). ``void`` is omitted. A
    function or method declared ``function &f()`` returns by reference: its
    signature starts with ``&``, which ``normalize_php_signature`` keeps.

    Args:
        node: The function_definition or method_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    params_node = node.child_by_field_name("parameters")
    if params_node is not None:
        for child in params_node.children:
            if child.type in _PHP_PARAMETER_NODE_TYPES:
                text = _php_parameter_text(child, source)
                if text:
                    params.append(text)

    signature = f"({', '.join(params)})"

    return_node = node.child_by_field_name("return_type")
    if return_node is not None:
        return_type = _php_type_text(return_node, source)
        if return_type != "void":
            signature += f": {return_type}"

    if any(child.type == "reference_modifier" for child in node.children):
        signature = "&" + signature
    return signature


def _normalize_php_parameter(param: str, type_params: list[str] | None) -> str:
    """One parameter by the PHP rule, with a variadic ``...`` kept.

    The rule hashes what stands before the ``$`` (the type, with a ``&``) and
    runs it through the shared FQN strip, which cuts a type at its last dot:
    ``int ...`` would hash as the empty string. A variadic parameter is
    therefore normalized without its ``...``, which is put back after the
    normalized type (``int ...$xs`` -> ``int...``, ``&...$xs`` -> ``&...``,
    an untyped ``...$xs`` -> ``...``).
    """
    from hypergumbo_core.analyze.base import normalize_signature_php
    variadic = "...$" in param
    if variadic:
        param = param.replace("...$", "$", 1)
        if param.startswith("$"):
            return "..."
    normalized = normalize_signature_php(f"({param})", type_params)
    assert normalized is not None  # a parenthesized text always normalizes
    inner = normalized[1:-1]
    return inner + "..." if variadic else inner


def normalize_php_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a PHP signature for typed stable_id (spec §6 typed tier).

    The PHP rule in ``analyze/base.py`` (``normalize_signature_php``: the type
    before each ``$``, names of typed parameters dropped, ``void`` omitted,
    ``(Type,Type)Return``) applied to one parameter at a time
    (``_normalize_php_parameter``) and to the return type, which writes the
    same text as the rule does for the whole of a signature with no variadic
    parameter, and keeps the variadic ``...`` its FQN strip would lose. A
    leading by-reference ``&`` (``function &f()``) is kept in front:
    ``&(int $x): int`` -> ``&(int)int``.
    """
    if not signature:
        return None
    by_ref = ""
    if signature.startswith("&"):
        by_ref, signature = "&", signature[1:]
    close = find_matching_paren(signature, 0) if signature.startswith("(") else -1
    if close < 0:
        return None
    from hypergumbo_core.analyze.base import normalize_signature_php, split_params_top_level
    params = [
        _normalize_php_parameter(p, type_params)
        for p in split_params_top_level(signature[1:close]) if p
    ]
    returned = normalize_signature_php("()" + signature[close + 1:], type_params)
    assert returned is not None  # "()" always closes
    return f"{by_ref}({','.join(params)}){returned[2:]}"


# PHP's simple backslash escapes. A single-quoted literal has only two
# (backslash-quote and backslash-backslash); the grammar emits an
# ``escape_sequence`` node only for a sequence the literal's quote style really
# decodes, so a single-quoted backslash-n stays inside one ``string_content``
# run and is never decoded here.
_PHP_SIMPLE_ESCAPES: dict[str, str] = {
    "\\\\": "\\", "\\'": "'", '\\"': '"', "\\$": "$", "\\n": "\n",
    "\\t": "\t", "\\r": "\r", "\\v": "\v", "\\e": "\x1b", "\\f": "\f",
}

# A literal's own delimiter tokens; any other child of an ``encapsed_string``
# (``variable_name``, ``{``, ``member_access_expression``, ...) is interpolation.
_PHP_LITERAL_DELIMITERS = frozenset({"'", '"'})


def _php_escape_value(text: str) -> str | None:
    """Decode one ``escape_sequence`` node's text, or ``None`` if invalid.

    Covers the simple escapes, octal ``\\101``, hex ``\\x41`` and Unicode
    ``\\u{42}``. A codepoint past U+10FFFF is a PHP compile error, so it is
    refused rather than guessed.
    """
    simple = _PHP_SIMPLE_ESCAPES.get(text)
    if simple is not None:
        return simple
    try:
        if text.startswith("\\u{"):
            return chr(int(text[3:-1], 16))
        if text.startswith("\\x"):
            return chr(int(text[2:], 16))
        return chr(int(text[1:], 8) & 0xFF)
    except ValueError:
        return None


def _php_string_value(
    string_node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Return the value of a PLAIN PHP string literal, else ``None``.

    The one literal reader for the Laravel route walk (WI-fadip). A
    single-quoted literal is a ``string`` node and a double-quoted one an
    ``encapsed_string`` node; both are accepted, which is the whole point:
    testing only for ``string`` silently dropped every double-quoted route.

    The value is the WHOLE literal: every ``string_content`` run joined with
    its decoded ``escape_sequence`` nodes, so ``'it\\'s'`` reads ``it's``
    rather than its first run ``it``. An empty literal returns ``""``.

    ``None`` means "not a plain literal", never "empty": any other node type
    (a variable, a constant, a call) and any INTERPOLATED double-quoted
    string (``"/u/{$x}"``, ``"/u/$x"``). Reading an interpolated string as
    its leading run would emit the wrong value (the route ``/u/``), so the
    caller must refuse it instead. Laravel's ``"/u/{id}"`` is not
    interpolation (no ``$``); the grammar keeps it inside ``string_content``.
    """
    if string_node.type not in ("string", "encapsed_string"):
        return None
    parts: list[str] = []
    for child in string_node.children:
        if child.type == "string_content":
            parts.append(node_text(child, source))
        elif child.type == "escape_sequence":
            decoded = _php_escape_value(node_text(child, source))
            if decoded is None:
                return None
            parts.append(decoded)
        elif child.type not in _PHP_LITERAL_DELIMITERS:
            return None
    return "".join(parts)


def _laravel_match_methods(
    node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Return the comma-joined verb list from ``Route::match([...], ...)``.

    Laravel's ``match`` declares its verbs as the FIRST argument
    (``Route::match(['get','post'], '/path', ...)``), which the generic
    Route:: handler previously discarded in favour of the lossy ``MATCH``
    aggregate. Returns e.g. ``"GET,POST"`` — the comma-joined shape
    ``routes.method_matches`` understands — or ``None`` when the verb list
    is absent or built dynamically (a variable), so the caller can fall back
    to the aggregate rather than inventing verbs (WI-zunal).
    """
    args_node = node.child_by_field_name("arguments")
    if args_node is None:  # pragma: no cover - defensive
        return None
    for child in args_node.children:
        if child.type != "argument":
            continue
        methods: list[str] = []
        for arg_child in child.children:
            if arg_child.type != "array_creation_expression":
                continue
            for arr_child in arg_child.children:
                if arr_child.type != "array_element_initializer":
                    continue
                for element in arr_child.children:
                    # Either quote style; an interpolated or empty verb
                    # contributes nothing rather than a wrong verb (WI-fadip).
                    value = _php_string_value(element, source)
                    if value:
                        methods.append(value.strip().upper())
        return ",".join(methods) if methods else None
    return None  # pragma: no cover - defensive


def _extract_controller_action(
    args_node: "tree_sitter.Node", source: bytes, *, arg_offset: int = 0
) -> str | None:
    """Extract controller@action from a Laravel route's controller argument.

    Supports two syntaxes:
    - Array: [Controller::class, 'action']
    - String: 'Controller@action'

    *arg_offset* shifts which positional argument holds the path, for the
    ``Route::match`` shape whose args[0] is the verb array (WI-zunal).

    Returns:
        String like 'UserController@index' or None if not extractable.
    """
    arg_index = 0
    for child in args_node.children:
        if child.type != "argument":
            continue

        if arg_index <= arg_offset:
            # Positional args up to and including the path — skip.
            arg_index += 1
            continue

        if arg_index == arg_offset + 1:
            # Second arg is controller reference
            for arg_child in child.children:
                # Array syntax: [Controller::class, 'action']
                if arg_child.type == "array_creation_expression":
                    controller = None
                    action = None
                    for arr_child in arg_child.children:
                        if arr_child.type == "array_element_initializer":
                            for elem in arr_child.children:
                                if elem.type == "class_constant_access_expression":
                                    # Controller::class - first name child is the class
                                    for cc in elem.children:
                                        if cc.type == "name":
                                            controller = node_text(cc, source)
                                            break
                                elif elem.type in ("string", "encapsed_string"):
                                    # 'action' or "action"; an interpolated
                                    # action is refused, not truncated.
                                    action = _php_string_value(elem, source)
                    if controller and action:
                        return f"{controller}@{action}"

                # String syntax: 'Controller@action'
                elif arg_child.type in ("string", "encapsed_string"):
                    text = _php_string_value(arg_child, source)
                    if text and "@" in text:
                        return text
            break

        arg_index += 1  # pragma: no cover - loop breaks at arg_index == 1

    return None


def _laravel_resource_actions_for(
    http_method: str,
    call_node: "tree_sitter.Node",
    source: bytes,
) -> set[str]:
    """Compute the set of REST actions a `Route::resource(...)` call emits.

    Inputs:
        http_method:  ``"RESOURCE"`` (full 7-action set) or ``"API_RESOURCE"``
                      (5 actions — drops `create` and `edit`, the HTML-form
                      routes that don't make sense for an API).
        call_node:    the ``scoped_call_expression`` for the ``Route::xxx``
                      call. Used to detect chained ``->except([...])`` /
                      ``->only([...])`` modifiers, walking up through
                      ``member_call_expression`` parents.
        source:       file source bytes, for ``node_text``.

    Behavior:
        Start with the 7-action default, drop ``{create, edit}`` for
        API_RESOURCE, then apply ``except`` (drop) and ``only`` (intersect)
        modifiers walking up the parent chain. Multiple modifiers compose
        in source-order — the first ``only`` restricts the set, subsequent
        ``except``/``only`` further refine.

    Why walk parents: Laravel resource registration commonly chains
    ``Route::apiResource('posts', PostController::class)->except(['destroy'])``
    where the ``->except`` lives in a ``member_call_expression`` whose
    ``object`` is the ``scoped_call_expression`` we're processing.
    """
    base_actions: set[str] = {action for _, _, action in LARAVEL_RESOURCE_ACTIONS}
    if http_method == "API_RESOURCE":
        base_actions -= LARAVEL_API_RESOURCE_EXCLUDED_ACTIONS

    # Walk up through member_call_expression parents collecting modifiers.
    # PHP tree-sitter chains `expr->method(args)` as
    #   member_call_expression { object: <expr>, name: method, arguments: ... }
    # so a chain like A()->b()->c() nests as:
    #   member_call_expression(name=c, object=
    #     member_call_expression(name=b, object=A()))
    cursor = call_node.parent
    while cursor is not None and cursor.type == "member_call_expression":
        name_node = cursor.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            break
        modifier_name = node_text(name_node, source)
        if modifier_name in ("except", "only"):
            actions = _laravel_collect_string_args(cursor, source)
            if modifier_name == "except":
                base_actions -= actions
            else:  # only
                base_actions &= actions
        cursor = cursor.parent

    return base_actions


def _laravel_collect_string_args(
    member_call: "tree_sitter.Node",
    source: bytes,
) -> set[str]:
    """Collect string-literal action names from a ``->except(...)`` / ``->only(...)`` call.

    Accepts both forms Laravel allows:
        ->except('create', 'edit')                 # variadic strings
        ->except(['create', 'edit'])               # array literal

    Non-string arguments (variables, function calls, interpolated strings)
    are silently skipped — we can't statically resolve them and a partial
    parse is more useful than refusing to apply any restriction.
    """
    args_node = member_call.child_by_field_name("arguments")
    if args_node is None:  # pragma: no cover - defensive
        return set()

    actions: set[str] = set()
    for child in args_node.children:
        if child.type != "argument":
            continue
        for arg_child in child.children:
            actions.update(_laravel_extract_strings(arg_child, source))
    return actions


def _laravel_extract_strings(
    node: "tree_sitter.Node", source: bytes,
) -> set[str]:
    """Extract every string-literal value reachable in a Laravel modifier arg.

    Handles single-quoted ``string`` nodes (`'foo'`), double-quoted
    ``encapsed_string`` nodes (`"foo"`), and ``array_creation_expression``
    nodes containing either form. Recursive over array elements so nested
    arrays — uncommon but legal — also yield their leaves.
    """
    out: set[str] = set()
    if node.type in ("string", "encapsed_string"):
        # The whole literal, one value; an interpolated one is skipped
        # rather than read as its leading run (WI-fadip).
        value = _php_string_value(node, source)
        if value:
            out.add(value)
        return out
    if node.type == "array_creation_expression":
        for elem in node.children:
            if elem.type == "array_element_initializer":
                for ec in elem.children:
                    out |= _laravel_extract_strings(ec, source)
    return out


def _extract_laravel_routes(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Laravel Route facade calls.

    Detects patterns like:
    - Route::get('/users', [Controller::class, 'action'])
    - Route::post('/login', 'Controller@action')
    - Route::resource('photos', Controller::class)
    - Route::apiResource('posts', Controller::class)

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols are route markers (kind="function" +
        meta.framework_role="route"), which enables route-handler linking.
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        if node.type != "scoped_call_expression":
            continue

        scope_node = node.child_by_field_name("scope")
        name_node = node.child_by_field_name("name")

        if not scope_node or not name_node:  # pragma: no cover - defensive
            continue

        # Check if this is Route::method()
        scope_text = node_text(scope_node, source)
        if scope_text != "Route":
            continue

        method_name = node_text(name_node, source).lower()

        # HTTP method routes
        if method_name in LARAVEL_HTTP_METHODS:
            http_method = LARAVEL_HTTP_METHODS[method_name]
        elif method_name == "resource":
            http_method = "RESOURCE"
        elif method_name == "apiresource":
            # WI-jorim: differentiate from `resource` so route expansion
            # below can drop the HTML-form `create` and `edit` actions.
            http_method = "API_RESOURCE"
        elif method_name == "match":
            # `Route::match(['get','post'], '/path', ...)` — the verbs are
            # args[0] and the path is args[1]. Read the real verb list rather
            # than stamping the lossy `MATCH` aggregate: the comma-joined form
            # is what `routes.method_matches` understands, so a matched route
            # links to an OpenAPI GET *and* POST operation (WI-zunal).
            http_method = _laravel_match_methods(node, source) or "MATCH"
        elif method_name == "any":
            http_method = "ANY"
        else:  # pragma: no cover - unknown Route:: method
            continue

        # `Route::match` shifts every positional argument right by one: its
        # args[0] is the verb array, so the path is args[1] and the controller
        # args[2]. Reading the path from args[0] unconditionally is why every
        # `Route::match` route was silently DROPPED (WI-zunal).
        arg_offset = 1 if method_name == "match" else 0

        route_path = None
        controller_action = None
        args_node = node.child_by_field_name("arguments")
        if args_node:
            arg_index = 0
            for child in args_node.children:
                if child.type != "argument":
                    continue
                if arg_index == arg_offset:
                    # Either quote style (WI-fadip: a double-quoted path is an
                    # `encapsed_string`, and testing only `string` dropped every
                    # such route). An interpolated path reads as None and the
                    # route is refused below, never truncated to its prefix.
                    for arg_child in child.children:
                        if arg_child.type in ("string", "encapsed_string"):
                            route_path = _php_string_value(arg_child, source)
                            break
                    break
                arg_index += 1

            controller_action = _extract_controller_action(
                args_node, source, arg_offset=arg_offset,
            )

        if not route_path:
            continue

        # Normalize route path
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # Build metadata
        metadata: dict[str, str] = {
            "route_path": normalized_path,
            "http_method": http_method,
        }
        if controller_action:
            metadata["controller_action"] = controller_action

        # Create UsageContext
        span = Span(
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route Symbol(s) - enables route-handler linking
        if http_method in ("RESOURCE", "API_RESOURCE"):
            # Laravel resource() creates 7 RESTful routes; apiResource()
            # creates 5 (excludes the HTML-form `create` + `edit`).
            # `.except()` / `.only()` modifiers further restrict the set.
            controller = None
            if args_node:
                arg_index = 0
                for child in args_node.children:
                    if child.type != "argument":
                        continue
                    if arg_index == 1:
                        for arg_child in child.children:
                            if arg_child.type == "class_constant_access_expression":
                                # Controller::class - first name child is the controller
                                for cc in arg_child.children:
                                    if cc.type == "name":
                                        controller = node_text(cc, source)
                                        break
                        break
                    arg_index += 1

            if controller:
                # Determine which actions to emit (WI-jorim).
                allowed_actions = _laravel_resource_actions_for(
                    http_method, node, source,
                )
                restful_routes = [
                    (m, f"{normalized_path}{suffix}", action)
                    for (m, suffix, action) in LARAVEL_RESOURCE_ACTIONS
                    if action in allowed_actions
                ]
                for http_meth, route_pth, action in restful_routes:
                    route_symbols.append(make_route_symbol(
                        language="php",
                        path=str(file_path),
                        span=span,
                        method=http_meth,
                        route_path=route_pth,
                        origin=run.pass_id,
                        origin_run_id=run.execution_id,
                        extra_meta={
                            "controller_action": f"{controller}@{action}",
                        },
                        is_exported=True,
                    ))
        else:
            # Single HTTP method route
            route_extra: dict[str, str] = {}
            if controller_action:
                route_extra["controller_action"] = controller_action
            route_symbols.append(make_route_symbol(
                language="php",
                path=str(file_path),
                span=span,
                method=http_method,
                route_path=normalized_path,
                origin=run.pass_id,
                origin_run_id=run.execution_id,
                extra_meta=route_extra,
                is_exported=True,
            ))

    return contexts, route_symbols


def _get_php_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for PHP."""
    try:
        import tree_sitter
        import tree_sitter_php
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    # PHP has two grammars: php and php_only. We use php which includes HTML.
    lang_ptr = tree_sitter_php.language_php()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis: the file's ``use``
    declarations (read once) and its own symbols (the callers and the
    members a call is bound to by position)."""

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes
    uses: _PhpUses
    symbols: list[Symbol]


def _extract_modifiers_php(node: "tree_sitter.Node") -> list[str]:
    """Extract visibility and other modifiers from a PHP declaration node.

    PHP tree-sitter uses ``visibility_modifier`` and ``static_modifier`` as
    direct children of method/property declarations.  Classes may have
    ``abstract`` or ``final`` modifier nodes.

    Returns e.g. ``["public", "static"]`` or ``["private"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "visibility_modifier":
            for kw in child.children:
                if kw.type in ("public", "private", "protected"):
                    modifiers.append(kw.type)
        elif child.type == "static_modifier":
            modifiers.append("static")
        elif child.type in ("abstract_modifier", "final_modifier", "readonly_modifier"):
            # e.g. abstract_modifier wraps "abstract" keyword
            keyword = child.type.removesuffix("_modifier")
            modifiers.append(keyword)
    return modifiers


def _php_member_exported(modifiers: list[str]) -> bool:
    """PHP's default-public rule for a class member (method, property or
    class constant): exported unless declared ``private`` or ``protected``."""
    return "private" not in modifiers and "protected" not in modifiers


def _php_constant_id_visibility(modifiers: list[str]) -> str:
    """The stable_id visibility slot of a class constant.

    ``private`` / ``protected`` are hashed; ``public`` and no modifier both
    hash the empty slot. A constant with no modifier is public, so the explicit
    keyword adds no information, and before the modifier was read every
    constant hashed the empty slot: hashing ``public`` would have moved the id
    of every explicitly public constant for nothing (methods and properties,
    whose modifiers were always read, keep ``public`` in the slot)."""
    visibility = visibility_from_modifiers(modifiers)
    return "" if visibility == "public" else visibility


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    repo_root: Optional[Path] = None,
    use_aliases: Optional[dict[str, str]] = None,
) -> list[Symbol]:
    """Extract symbols from a parsed PHP tree (pass 1).

    ``use_aliases``: the file's ``use`` aliases when the caller has read them
    (:func:`_php_uses`); read here otherwise.

    When ``repo_root`` is provided, the file pseudo-node Symbol's ``name``
    is the repo-relative path (mirror of INV-vaguj's name-normalisation
    for the analyzer-side producer). Otherwise ``name`` falls back to
    ``str(file_path)`` for tests that call this helper directly.
    """
    symbols: list[Symbol] = []
    # ADR-0032 Phase 4 PR4: the namespace for qualified_name population, per
    # declaration (a file may declare several; INV-fabih sweep).
    namespace_of = _php_namespace_resolver(tree.root_node, source)

    # INV-kokaj: emit the file pseudo-node as kind="file" with the
    # canonical file-id shape so the orchestrator file-symbol synthesizer
    # dedups against it (existing_ids check). Before this fix, every PHP
    # file emitted both a kind="module" Symbol (here) and a kind="file"
    # Symbol (from the synthesizer when edges targeted the file id).
    end_line = tree.root_node.end_point[0] + 1
    file_name = str(file_path)
    if repo_root is not None:
        try:
            file_name = str(file_path.relative_to(repo_root))
        except ValueError:  # pragma: no cover - defensive
            pass

    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_path`` is
    # ABSOLUTE here (the analyze() override discovers files via find_php_files(repo_root),
    # which yields absolute paths), so we anchor on the repo-relative ``file_name`` computed
    # above — folding an absolute path would make the stable_id location-dependent (a
    # regression). Folded into make_typed_stable_id's containing slot so same-name
    # functions/methods in different files hash distinctly. Byte-matches the file node's own
    # stable_id (make_file_stable_id over the normalized repo-relative path). For the legacy
    # repo_root-less test path, ``file_name`` degrades to ``str(file_path)`` (same fallback
    # the file pseudo-node uses).
    file_stable_id = make_file_stable_id("php", normalize_path(file_name))
    module_symbol = Symbol(
        id=make_file_id("php", str(file_path)),
        name=file_name,
        kind="file",
        language="php",
        path=str(file_path),
        span=Span(start_line=1, end_line=end_line, start_col=0, end_col=0),
        origin=PASS_ID,
        origin_run_id=run.execution_id,
        line_span=end_line - 1 + 1,
    )
    symbols.append(module_symbol)
    if use_aliases is None:
        use_aliases = _extract_use_aliases(tree, source)

    for node in iter_tree(tree.root_node):
        # Function declarations
        if node.type == "function_definition":
            name = _find_name_in_children(node, source)
            namespace_name = namespace_of(node)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                signature = _extract_php_signature(node, source)
                modifiers = _extract_modifiers_php(node)

                # Typed stable_id (spec §6 typed tier). A function nested
                # in another or in a method hashes its holders
                # (``App\\A::f::h``, WI-finam); a top-level one, none.
                norm_sig = normalize_php_signature(signature)
                holders = _php_holders(node, source)
                stable_id = make_typed_stable_id(
                    "function", norm_sig, visibility_from_modifiers(modifiers),
                    name=name,
                    qualified_name=_make_php_qualified_name(
                        namespace_name, "::".join(holders) or None, name),
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("php", str(file_path), span.start_line, span.end_line, name, "function"),
                    name=name,
                    kind="function",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "php"),
                    modifiers=modifiers,
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    is_exported=True,
                    qualified_name=_make_php_qualified_name(namespace_name, None, name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "php"),
                )
                symbols.append(symbol)

        # Type-container declarations (class / interface / trait / enum)
        elif node.type in _PHP_CONTAINER_KINDS:
            name = _find_name_in_children(node, source)
            namespace_name = namespace_of(node)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )

                # Extract base classes and interfaces
                base_classes = _extract_base_classes_php(node, source)
                meta: dict[str, object] | None = (
                    {"base_classes": base_classes} if base_classes else None
                )
                # WI-pivap: which ``use`` each BARE base came through, so the
                # inheritance linker checks it as that qualified name rather
                # than binding any in-repo class of the same short name.
                base_imports = {
                    b: use_aliases[b] for b in base_classes
                    if "\\" not in b and b in use_aliases
                }
                if meta is not None and base_imports:
                    meta["base_class_imports"] = base_imports

                container_kind = _PHP_CONTAINER_KINDS[node.type]
                symbol = Symbol(
                    id=make_symbol_id("php", str(file_path), span.start_line, span.end_line, name, container_kind),
                    name=name,
                    kind=container_kind,
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # INV-fabih: the table formula scoped by the namespace (a
                    # file may declare two ``Inner`` in two namespaces).
                    stable_id=scoped_table_stable_id(
                        kind=container_kind, lang="php", path=file_name, name=name,
                        scope=[namespace_name] if namespace_name else [],
                    ),
                    meta=meta,
                    modifiers=_extract_modifiers_php(node),
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    is_exported=True,
                    qualified_name=_make_php_qualified_name(namespace_name, None, name),
                )
                symbols.append(symbol)

        # Method declarations (inside classes)
        elif node.type == "method_declaration":
            name = _find_name_in_children(node, source)
            namespace_name = namespace_of(node)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # _enclosing_type_name walks EVERY container. A class-only walk
                # emitted interface and trait members BARE, and as the caller
                # lookup it made no trait or enum method a caller (WI-fonaz).
                enclosing_class = _enclosing_type_name(node, source)
                full_name = f"{enclosing_class}.{name}" if enclosing_class else name
                signature = _extract_php_signature(node, source)
                modifiers = _extract_modifiers_php(node)

                # Typed stable_id (spec §6 typed tier)
                norm_sig = normalize_php_signature(signature)
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                    name=name,
                    qualified_name=_make_php_qualified_name(namespace_name, enclosing_class, name),
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("php", str(file_path), span.start_line, span.end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="php",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, "php"),
                    modifiers=modifiers,
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    is_exported=_php_member_exported(modifiers),
                    qualified_name=_make_php_qualified_name(namespace_name, enclosing_class, name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "php"),
                )
                symbols.append(symbol)

        # Class/trait properties -> kind="field" anchored to the type
        # (WI-fosuh / WI-jusus). property_declaration only occurs in a type body,
        # so a method-body local (an assignment_expression) can never reach here.
        elif node.type == "property_declaration":
            enclosing = _enclosing_type_name(node, source)
            namespace_name = namespace_of(node)
            if enclosing is not None:
                modifiers = _extract_modifiers_php(node)
                exported = _php_member_exported(modifiers)
                for el in node.children:
                    if el.type != "property_element":
                        continue
                    pname = _php_property_element_name(el, source)
                    if pname is None:  # pragma: no cover - defensive
                        continue
                    qualified = _make_php_qualified_name(namespace_name, enclosing, pname)
                    symbols.append(_php_member_symbol(
                        file_path, el, f"{enclosing}.{pname}", "field",
                        qualified, modifiers, exported, run,
                        stable_id=make_typed_stable_id(
                            "field", "", visibility_from_modifiers(modifiers),
                            name=pname, qualified_name=qualified,
                            file_stable_id=file_stable_id,
                        ),
                    ))

        # Constants: a class/interface/trait/enum constant is a ``field``; a
        # top-level (global) constant is a ``variable``. A class constant's
        # modifiers (``private`` / ``protected`` / ``public`` / ``final``) are
        # children of the const_declaration and are read like a property's:
        # they were not read, so a private constant came out public and
        # exported (WI-dajur).
        elif node.type == "const_declaration":
            enclosing = _enclosing_type_name(node, source)
            namespace_name = namespace_of(node)
            modifiers = _extract_modifiers_php(node)
            for el in node.children:
                if el.type != "const_element":
                    continue
                cname = _php_const_element_name(el, source)
                if cname is None:  # pragma: no cover - defensive
                    continue
                if enclosing is not None:
                    qualified = _make_php_qualified_name(namespace_name, enclosing, cname)
                    symbols.append(_php_member_symbol(
                        file_path, el, f"{enclosing}.{cname}", "field",
                        qualified, modifiers, _php_member_exported(modifiers), run,
                        stable_id=make_typed_stable_id(
                            "field", "", _php_constant_id_visibility(modifiers),
                            name=cname, qualified_name=qualified,
                            file_stable_id=file_stable_id,
                        ),
                    ))
                else:
                    # A top-level ``const`` has no modifiers, so this is ``[]``
                    # there; an anonymous class's constant (``new class {
                    # private const X = 1; }``) has no enclosing type name and
                    # lands here too, and keeps its declared visibility.
                    symbols.append(_php_member_symbol(
                        file_path, el, cname, "variable",
                        _make_php_qualified_name(namespace_name, None, cname),
                        modifiers, _php_member_exported(modifiers), run,
                    ))

    # INV-jahiv Phase 4 PR3: post-pass for symbols (e.g. classes) whose
    # emit site does not call extract_preceding_doc_comment inline. PHP
    # overrides analyze() and bypasses the base run_pipeline path, so
    # we re-implement the same post-pass here.
    populate_docstrings_from_tree(tree.root_node, source, symbols)

    return symbols


#: Method names PHP's own classes and interfaces define, lowercased (PHP method
#: names are case-insensitive). A call ``$x->m()`` on a receiver of unknown
#: type whose name is here goes out marked ``stdlib_member_name``: the library
#: is then a second holder of the name, and the single-holder name linker does
#: not bind it to the repository's one ``m`` (ADR-0064; that linker does not
#: run for PHP yet, :class:`_PhpUses` says why). Sourced from the PHP
#: manual's class references (php.net/manual/en/book.*.php), not from any
#: repository; the groups are the manual's pages:
_PHP_LIBRARY_METHOD_NAMES: frozenset[str] = frozenset(name.lower() for name in (
    # Throwable, Exception, Error (language.exceptions; class.throwable)
    "getMessage", "getCode", "getFile", "getLine", "getTrace", "getTraceAsString",
    "getPrevious", "__toString",
    # DateTimeInterface, DateTime, DateTimeImmutable (book.datetime)
    "format", "getTimezone", "getOffset", "getTimestamp", "getMicrosecond", "diff",
    "modify", "add", "sub", "setDate", "setISODate", "setTime", "setTimestamp",
    "setMicrosecond", "setTimezone", "createFromFormat", "createFromInterface",
    "createFromImmutable", "createFromMutable", "createFromTimestamp", "getLastErrors",
    # DateTimeZone, DateInterval, DatePeriod (book.datetime)
    "getName", "getLocation", "getTransitions", "listAbbreviations", "listIdentifiers",
    "createFromDateString", "getStartDate", "getEndDate", "getDateInterval",
    "getRecurrences",
    # PDO, PDOStatement (book.pdo)
    "beginTransaction", "commit", "errorCode", "errorInfo", "exec", "getAttribute",
    "getAvailableDrivers", "inTransaction", "lastInsertId", "prepare", "query", "quote",
    "rollBack", "setAttribute", "bindColumn", "bindParam", "bindValue", "closeCursor",
    "columnCount", "debugDumpParams", "execute", "fetch", "fetchAll", "fetchColumn",
    "fetchObject", "getColumnMeta", "nextRowset", "rowCount", "setFetchMode",
    # Closure (class.closure)
    "bind", "bindTo", "call", "fromCallable", "__invoke",
    # ArrayAccess, Countable, IteratorAggregate, Iterator, Traversable's
    # SeekableIterator / OuterIterator / RecursiveIterator, JsonSerializable,
    # Serializable (reserved.interfaces; spl.interfaces)
    "offsetExists", "offsetGet", "offsetSet", "offsetUnset", "count", "getIterator",
    "current", "key", "next", "rewind", "valid", "seek", "getInnerIterator",
    "hasChildren", "getChildren", "jsonSerialize", "serialize", "unserialize",
    # Generator (class.generator)
    "getReturn", "send", "throw",
    # SPL data structures (spl.datastructures): SplDoublyLinkedList, SplStack,
    # SplQueue, SplHeap, SplPriorityQueue, SplFixedArray, SplObjectStorage,
    # ArrayObject (and ArrayIterator, the same names)
    "bottom", "isEmpty", "pop", "push", "shift", "top", "unshift", "setIteratorMode",
    "getIteratorMode", "enqueue", "dequeue", "compare", "extract", "insert",
    "isCorrupted", "recoverFromCorruption", "setExtractFlags", "getExtractFlags",
    "fromArray", "getSize", "setSize", "toArray", "addAll", "attach", "contains",
    "detach", "getHash", "getInfo", "removeAll", "removeAllExcept", "setInfo", "append",
    "asort", "ksort", "exchangeArray", "getArrayCopy", "getFlags", "setFlags",
    "natcasesort", "natsort", "uasort", "uksort", "getIteratorClass", "setIteratorClass",
    # SPL iterators (spl.iterators): FilterIterator, CachingIterator,
    # LimitIterator, RecursiveIteratorIterator, AppendIterator, MultipleIterator
    "accept", "hasNext", "getCache", "getPosition", "getDepth", "getSubIterator",
    "beginChildren", "endChildren", "callHasChildren", "callGetChildren", "getMaxDepth",
    "setMaxDepth", "getIteratorIndex", "getArrayIterator", "attachIterator",
    "detachIterator", "containsIterator", "countIterators",
))

#: The nodes a PHP variable is scoped to. A closure (``function () use ($x)``)
#: sees an outer variable only through its ``use`` clause; an arrow function
#: (``fn``) sees every variable of the scope around it.
_PHP_VARIABLE_SCOPES = frozenset({
    "function_definition", "method_declaration", "anonymous_function", "arrow_function",
})

#: Type names PHP writes as a ``named_type`` that name no class.
_PHP_NO_CLASS_TYPES = frozenset({"parent", "mixed", "iterable", "object", "callable"})


def _enclosing_container(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The type declaration (class / interface / trait / enum, or an anonymous
    ``new class``) ``node`` is written in, found by position, or None. An
    anonymous class is the object ``$this`` means inside it, although its
    methods' symbols carry the enclosing named type's name."""
    current = node.parent
    while current is not None:
        if current.type in _PHP_TYPE_CONTAINERS or current.type == "anonymous_class":
            return current
        current = current.parent
    return None


def _container_name(container: Optional["tree_sitter.Node"], source: bytes) -> Optional[str]:
    """A type declaration's name; None for none and for an anonymous class."""
    name = container.child_by_field_name("name") if container is not None else None
    return node_text(name, source) if name is not None else None


def _container_members(
    container: "tree_sitter.Node", kind: str,
) -> Iterator["tree_sitter.Node"]:
    """The direct members of a type declaration of one node kind."""
    body = container.child_by_field_name("body")
    for child in body.children if body is not None else ():
        if child.type == kind:
            yield child


def _own_method(
    container: "tree_sitter.Node", name: str, source: bytes,
) -> Optional["tree_sitter.Node"]:
    """The method ``name`` the type declaration itself declares (PHP method
    names are case-insensitive), or None."""
    for method in _container_members(container, "method_declaration"):
        declared = method.child_by_field_name("name")
        if declared is not None and node_text(declared, source).lower() == name.lower():
            return method
    return None


#: The calls and the member reads ``_PhpTypes.of`` types when they are
#: written on ``$this``, ``self`` or ``static``.
_PHP_MEMBER_CALLS = ("member_call_expression", "nullsafe_member_call_expression",
                     "scoped_call_expression")
_PHP_MEMBER_ACCESSES = ("member_access_expression", "nullsafe_member_access_expression",
                        "scoped_property_access_expression")


def _new_type_written(creation: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The type a ``new`` writes (``Conn``, ``\\Lib\\Conn``, ``self``), or None
    (``new $cls``, an anonymous class)."""
    for child in creation.children:
        if child.type in ("name", "qualified_name"):
            return node_text(child, source)
    return None


#: One write of a variable: where it ends, and either the value assigned (its
#: type is the value's) or the declared type node (a parameter, a ``catch``);
#: both None for a write that writes no type.
_Write = tuple[int, Optional["tree_sitter.Node"], Optional["tree_sitter.Node"]]


class _PhpTypes:
    r"""What type a PHP expression has, as the file writes it: THE RULE, in
    one place for every call site.

    PHP records no type on a variable. "A variable's type is the type its
    declaration writes", where PHP writes one:

    - a parameter's declared type (a promoted constructor parameter too);
    - ``$x = new Name(...)``: ``Name`` as written;
    - ``$x = f(...)``, ``f`` a function of this file: its declared return
      type; ``$x = $this->m()`` (and ``self::`` / ``static::``), ``m`` the
      enclosing type's own method: its declared return type;
    - ``catch (Name $e)``: ``Name`` (one type caught);
    - ``$this->p`` / ``self::$p``: the declared type of the enclosing type's
      property ``p`` (a typed property or a promoted constructor parameter).

    A variable's type is the one its LAST write before the use gives, in the
    variable's scope (a function, a method, a closure, an arrow function; a
    closure looks outward only for a variable of its ``use`` clause, an arrow
    function for any). A write that writes no type (``$x = g()`` of a function
    this file does not declare, ``foreach``, ``list()``, ``&``, ``global``,
    ``static``) makes the variable untyped from there on. A declared type is
    a class name only: ``?T`` and ``T|null`` are ``T``; a union of two
    classes, an intersection and a scalar are no type; ``self`` / ``static``
    are the enclosing type. A ``@var`` docblock is not read.
    """

    def __init__(self, tree: "tree_sitter.Tree", source: bytes) -> None:
        self.source = source
        self.functions: dict[str, list["tree_sitter.Node"]] = {}
        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                name = node.child_by_field_name("name")
                if name is not None:
                    self.functions.setdefault(node_text(name, source).lower(), []).append(node)
        self._writes: dict[tuple[int, str], list[_Write]] = {}

    def declared(self, type_node: Optional["tree_sitter.Node"]) -> Optional[str]:
        """The class a declared type names, as written (see the class docstring)."""
        if type_node is None:
            return None
        if type_node.type == "optional_type":
            return self.declared(type_node.named_children[-1])
        if type_node.type in ("union_type", "type_list"):
            named = [
                c for c in type_node.named_children
                if not (c.type == "primitive_type" and node_text(c, self.source) == "null")
            ]
            return self.declared(named[0]) if len(named) == 1 else None
        if type_node.type != "named_type":
            return None
        written = node_text(type_node, self.source)
        if written.lower() in ("self", "static"):
            return _container_name(_enclosing_container(type_node), self.source)
        return None if written.lower() in _PHP_NO_CLASS_TYPES else written

    def returned(self, declaration: "tree_sitter.Node") -> Optional[str]:
        """A function's or method's declared return type."""
        return self.declared(declaration.child_by_field_name("return_type"))

    def of(self, expr: "tree_sitter.Node") -> Optional[str]:
        """The type of an expression, or None when the file does not write one."""
        kind = expr.type
        if kind == "parenthesized_expression" and expr.named_children:
            return self.of(expr.named_children[0])
        if kind == "object_creation_expression":
            return self.constructed(expr)
        if kind == "variable_name":
            return self.variable(expr)
        if kind == "function_call_expression":
            function = expr.child_by_field_name("function")
            if function is None or function.type != "name":
                return None
            declared = self.functions.get(node_text(function, self.source).lower(), [])
            types = {self.returned(d) for d in declared}
            return types.pop() if len(types) == 1 else None
        if kind not in _PHP_MEMBER_CALLS and kind not in _PHP_MEMBER_ACCESSES:
            return None
        container = _enclosing_container(expr)
        member = expr.child_by_field_name("name")
        if container is None or member is None or not _on_the_enclosing_object(expr, self.source):
            return None
        if kind in _PHP_MEMBER_CALLS:
            method = _own_method(container, node_text(member, self.source), self.source)
            return self.returned(method) if method is not None else None
        return self.property(container, node_text(member, self.source).lstrip("$"))

    def constructed(self, creation: "tree_sitter.Node") -> Optional[str]:
        """The type ``new`` names, as written; ``new self`` / ``new static``
        the enclosing type. None for ``new parent``, ``new $cls`` and an
        anonymous class."""
        written = _new_type_written(creation, self.source)
        if written is None or written.lower() == "parent":
            return None
        if written.lower() in ("self", "static"):
            return _container_name(_enclosing_container(creation), self.source)
        return written

    def property(self, container: "tree_sitter.Node", name: str) -> Optional[str]:
        """The declared type of the type declaration's own property ``name``:
        a typed property, or a promoted constructor parameter."""
        for declaration in _container_members(container, "property_declaration"):
            for element in declaration.children:
                if (element.type == "property_element"
                        and _php_property_element_name(element, self.source) == name):
                    return self.declared(declaration.child_by_field_name("type"))
        constructor = _own_method(container, "__construct", self.source)
        parameters = constructor.child_by_field_name("parameters") if constructor else None
        for parameter in parameters.children if parameters is not None else ():
            declared = parameter.child_by_field_name("name")
            if (parameter.type == "property_promotion_parameter" and declared is not None
                    and node_text(declared, self.source).lstrip("&$") == name):
                return self.declared(parameter.child_by_field_name("type"))
        return None

    def variable(self, variable: "tree_sitter.Node") -> Optional[str]:
        """The type of a variable where it is read (the class docstring). A
        variable of the file's top level has none: a call there has no caller
        and is not reported, so nothing asks."""
        name = node_text(variable, self.source)
        at = variable.start_byte
        scope = _variable_scope(variable)
        while scope is not None:
            earlier = [w for w in self._writes_in(scope, name) if w[0] <= at]
            if earlier:
                _, value, typed_by = earlier[-1]
                return self.of(value) if value is not None else self.declared(typed_by)
            at = scope.start_byte
            scope = _variable_scope(scope) if _sees_outer(scope, name, self.source) else None
        return None

    def _writes_in(self, scope: "tree_sitter.Node", name: str) -> list["_Write"]:
        """Every write of ``name`` in ``scope``, in source order
        (:data:`_Write`): its parameters, then its body, nested scopes left
        out. The type of a write is computed only for the write a read
        selects: a value names other variables, whose earlier writes are read
        in turn."""
        key = (scope.id, name)
        if key in self._writes:
            return self._writes[key]
        writes: list[_Write] = []
        parameters = scope.child_by_field_name("parameters")
        for parameter in parameters.children if parameters is not None else ():
            declared = parameter.child_by_field_name("name")
            if (parameter.type in _PHP_PARAMETER_NODE_TYPES and declared is not None
                    and node_text(declared, self.source).lstrip("&") == name):
                typed = (None if parameter.type == "variadic_parameter"
                         else parameter.child_by_field_name("type"))
                writes.append((parameter.end_byte, None, typed))
        for node in _walk_scope(scope.child_by_field_name("body")):
            written = _write_of(node, name, self.source)
            if written is not None:
                writes.append(written)
        writes.sort(key=lambda w: w[0])
        self._writes[key] = writes
        return writes


def _variable_scope(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The function, method, closure or arrow function ``node`` is in."""
    scope = node.parent
    while scope is not None and scope.type not in _PHP_VARIABLE_SCOPES:
        scope = scope.parent
    return scope


def _walk_scope(root: Optional["tree_sitter.Node"]) -> Iterator["tree_sitter.Node"]:
    """The nodes of one variable scope, nested scopes left out."""
    stack = [root] if root is not None else []
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in reversed(node.children) if c.type not in _PHP_VARIABLE_SCOPES)


def _sees_outer(scope: "tree_sitter.Node", name: str, source: bytes) -> bool:
    """Whether a variable ``name`` not written in ``scope`` is the enclosing
    scope's: always in an arrow function, through ``use`` in a closure."""
    if scope.type == "arrow_function":
        return True
    if scope.type != "anonymous_function":
        return False
    return any(
        variable.type == "variable_name" and node_text(variable, source) == name
        for clause in scope.children if clause.type == "anonymous_function_use_clause"
        for variable in clause.children
    )


def _write_of(node: "tree_sitter.Node", name: str, source: bytes) -> Optional[_Write]:
    """The write of the variable ``name`` that ``node`` is, or None."""
    def named(child: Optional["tree_sitter.Node"]) -> bool:
        return (child is not None and child.type == "variable_name"
                and node_text(child, source) == name)

    if node.type == "assignment_expression" and named(node.child_by_field_name("left")):
        return (node.end_byte, node.child_by_field_name("right"), None)
    if node.type == "catch_clause" and named(node.child_by_field_name("name")):
        return (node.child_by_field_name("name").end_byte, None,  # type: ignore[union-attr]
                node.child_by_field_name("type"))
    if node.type in ("reference_assignment_expression", "augmented_assignment_expression"):
        return (node.end_byte, None, None) if named(node.child_by_field_name("left")) else None
    if node.type in ("list_literal", "global_declaration", "static_variable_declaration"):
        return (node.end_byte, None, None) if any(named(c) for c in node.children) else None
    if node.type == "foreach_statement":
        after_as = False
        for child in node.children:
            after_as = after_as or child.type == "as"
            targets = child.children if child.type == "pair" else [child]
            if after_as and any(named(t) for t in targets):
                return (child.end_byte, None, None)
    return None


def _on_the_enclosing_object(expr: "tree_sitter.Node", source: bytes) -> bool:
    """Whether a member access or call is written on ``$this``, ``self`` or
    ``static``: the enclosing type's own."""
    owner = expr.child_by_field_name("object") or expr.child_by_field_name("scope")
    text = node_text(owner, source) if owner is not None else ""
    if owner is not None and owner.type == "variable_name":
        return text == "$this"
    return owner is not None and owner.type == "relative_scope" and text.lower() in ("self", "static")


#: The declarations a PHP call's caller can be: a closure is no caller of its
#: own, a call in one is the enclosing function's.
_PHP_CALLER_NODES = frozenset({"function_definition", "method_declaration"})


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    file_symbols: list[Symbol],
    use_aliases: dict[str, str],
) -> list[Edge]:
    r"""Extract the call, construction and import edges of one parsed PHP file
    (pass 2).

    THE CALLS (ADR-0064). The analyzer binds a call itself only where PHP's
    own evidence is in hand: ``$this->m()``, ``self::m()`` and ``static::m()``
    whose ``m`` the enclosing type declares, the type and the method both
    found by position in this file (``own_type``). Every other call goes out
    as an unresolved stub carrying what the call site says, and the shared
    linkers decide through the file's import table (:class:`_PhpUses`):

    - ``f()``, ``\A\f()``, ``A\f()``: the callee as written, no fact (the
      function the name means from the caller's namespace and imports; the
      module slot keeps the ``use`` path, the external identity);
    - ``$x->m()`` / ``$x?->m()``: ``receiver_type_hint`` when the file writes
      the receiver's type (:class:`_PhpTypes`), else no type and
      ``stdlib_member_name`` when PHP's own classes define ``m``;
    - ``$this->m()`` / ``self::m()`` / ``static::m()`` that the enclosing type
      does not declare: ``enclosing_class`` (its ancestors are walked);
      ``parent::m()``: ``enclosing_class`` and ``super_receiver``;
    - ``Name::m()``: ``qualifier`` as written;
    - ``new Name()``: an ``instantiates`` stub of the type as written.

    Every stub of a call on a receiver or an owner is ``call_construct=
    "method"``, which keeps the linker from reading its name as a function's.
    The caller is the function or method found by position
    (``enclosing_declared_symbol``); a bare call outside any is the file's.
    """
    declared_at = symbols_at([s for s in file_symbols if s.kind in ("function", "method")])
    module_symbol = next((s for s in file_symbols if s.kind == "file"), None)
    types = _PhpTypes(tree, source)
    edges: list[Edge] = []

    # INV-naguv: emit an `imports` edge per `use Namespace\Class;` statement.
    # php.py previously emitted zero imports edges, so PHP framework namespaces
    # (`Illuminate\...`, `Symfony\...`) never reached profile.refine_frameworks'
    # import-promote phase and a manifest-less PHP app surfaced no framework.
    # The dst carries the full backslash namespace path, which _module_match_kind
    # now prefix-matches against the PHP_FRAMEWORKS namespace patterns.
    _file_id = make_file_id("php", str(file_path))
    for _use in iter_tree(tree.root_node):
        if _use.type != "namespace_use_declaration":
            continue
        for _clause in _use.children:
            if _clause.type != "namespace_use_clause":
                continue
            for _sub in _clause.children:
                if _sub.type == "qualified_name":
                    _module_path = node_text(_sub, source)
                    if _module_path:
                        edges.append(Edge.create(
                            src=_file_id,
                            dst=f"php:{_module_path}:0-0:package:package",
                            edge_type="imports",
                            line=_use.start_point[0] + 1,
                            evidence_type="import_statement",
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

    def report(caller: Symbol, call: "tree_sitter.Node", callee: str, **facts: object) -> None:
        edges.append(make_unresolved_edge(
            "php", caller.id, callee, call.start_point[0] + 1, PASS_ID, run.execution_id,
            **facts,  # type: ignore[arg-type]
        ))

    def own_member(
        caller: Symbol, call: "tree_sitter.Node", container: "tree_sitter.Node",
        method: str, on_this: bool,
    ) -> None:
        """``$this->m()`` (``on_this``) / ``self::m()`` / ``static::m()``: the
        enclosing type's own ``m`` of this file, else a stub for the hierarchy
        walk."""
        declared = _own_method(container, method, source)
        target = symbol_declared_by(declared, declared_at) if declared is not None else None
        if target is not None:
            edges.append(Edge.create(
                src=caller.id, dst=target.id, edge_type="calls",
                line=call.start_point[0] + 1, resolution_score=1.0,
                origin=PASS_ID, origin_run_id=run.execution_id,
                evidence_type="ast_method_this" if on_this else "ast_static_call",
                resolved_by=resolved_by.OWN_TYPE,
            ))
            return
        report(caller, call, method, call_construct="method",
               enclosing_class=_container_name(container, source))

    def on_a_value(caller: Symbol, call: "tree_sitter.Node", receiver: "tree_sitter.Node", method: str) -> None:
        """A call on a value: its type, if the file writes one; else the name."""
        typed = types.of(receiver)
        if typed is not None:
            report(caller, call, method, call_construct="method", receiver_type_hint=typed)
        else:
            report(caller, call, method, call_construct="method",
                   stdlib_member_name=method.lower() in _PHP_LIBRARY_METHOD_NAMES)

    for node in iter_tree(tree.root_node):
        if node.type == "function_call_expression":
            function = node.child_by_field_name("function")
            caller = enclosing_declared_symbol(node, declared_at, _PHP_CALLER_NODES) or module_symbol
            if function is None or function.type not in ("name", "qualified_name") or caller is None:
                continue
            callee = node_text(function, source)
            module = use_aliases.get(callee) if function.type == "name" else None
            report(caller, node, callee, module_hint=module or "external",
                   dst_ref=ExternalRef(lang="php", module_path=module, name=callee) if module else None)

        elif node.type in ("member_call_expression", "nullsafe_member_call_expression"):
            caller = enclosing_declared_symbol(node, declared_at, _PHP_CALLER_NODES)
            name_node = node.child_by_field_name("name")
            receiver = node.child_by_field_name("object")
            if caller is None or name_node is None or receiver is None:
                continue
            if name_node.type != "name":
                continue  # ``$x->$m()``: the method's name is a value
            method = node_text(name_node, source)
            container = _enclosing_container(node)
            if receiver.type == "variable_name" and node_text(receiver, source) == "$this" and container:
                own_member(caller, node, container, method, on_this=True)
            else:
                on_a_value(caller, node, receiver, method)

        elif node.type == "scoped_call_expression":
            caller = enclosing_declared_symbol(node, declared_at, _PHP_CALLER_NODES)
            scope_node = node.child_by_field_name("scope")
            name_node = node.child_by_field_name("name")
            if caller is None or scope_node is None or name_node is None:
                continue
            method = node_text(name_node, source)
            container = _enclosing_container(node)
            written = node_text(scope_node, source)
            if scope_node.type == "relative_scope" and container is not None:
                if written.lower() == "parent":
                    report(caller, node, method, call_construct="method", super_receiver=True,
                           enclosing_class=_container_name(container, source))
                else:
                    own_member(caller, node, container, method, on_this=False)
            elif scope_node.type in ("name", "qualified_name"):
                report(caller, node, method, call_construct="method", qualifier=written)
            else:
                on_a_value(caller, node, scope_node, method)

        elif node.type == "object_creation_expression":
            caller = enclosing_declared_symbol(node, declared_at, _PHP_CALLER_NODES)
            written_type = _new_type_written(node, source)
            if caller is not None and written_type and written_type.lower() not in ("self", "static", "parent"):
                report(caller, node, written_type, instantiates=True)

    return edges


def _analyze_php_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single PHP file (legacy single-pass, used for testing).

    Returns (symbols, edges, success).
    """
    parser = _get_php_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
        run.record_failed_file(str(file_path), f"{type(e).__name__}: {e}")
        return [], [], False

    uses = _php_uses(tree, source)
    symbols = _extract_symbols(tree, source, file_path, run, use_aliases=uses.aliases)
    edges = _extract_edges(tree, source, file_path, run, symbols, uses.aliases)
    return symbols, edges, True


class PHPAnalyzer(TreeSitterAnalyzer):
    """PHP analyzer using tree-sitter-php.

    Overrides ``analyze()`` because PHP uses a custom ``_get_php_parser()``
    helper (which selects the ``language_php`` sub-grammar), a three-pass
    structure (symbols, edges, usage contexts), and a per-file import table
    (``_php_uses``).
    """

    lang = "php"
    file_patterns: ClassVar[list[str]] = ["*.php"]
    grammar_module = "tree_sitter_php"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run PHP analysis with three-pass symbol/edge/route extraction."""
        start_time = time.time()

        # Create analysis run for provenance
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        # Check for tree-sitter-php availability
        if not self._check_grammar_available():
            skip_reason = "PHP analysis skipped: requires tree-sitter-php (pip install tree-sitter-php)"
            warnings.warn(skip_reason, stacklevel=2)
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=skip_reason,
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        parser = _get_php_parser()
        if parser is None:
            skip_reason = "PHP analysis skipped: requires tree-sitter-php (pip install tree-sitter-php)"
            warnings.warn(skip_reason, stacklevel=2)
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=skip_reason,
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        # Pass 1: Parse all files and extract symbols
        parsed_files: list[_ParsedFile] = []
        all_symbols: list[Symbol] = []
        files_analyzed = 0
        files_skipped = 0

        for file_path in find_php_files(repo_root):
            try:
                source = file_path.read_bytes()
                tree = parser.parse(source)
                record_partial_parse(run, str(file_path.relative_to(repo_root)), tree)  # WI-bulaz
                uses = _php_uses(tree, source)
                symbols = _extract_symbols(
                    tree, source, file_path, run, repo_root=repo_root, use_aliases=uses.aliases,
                )
                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source, uses=uses, symbols=symbols,
                ))
                all_symbols.extend(symbols)
                files_analyzed += 1
            except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
                files_skipped += 1
                run.record_failed_file(
                    str(file_path.relative_to(repo_root)),
                    f"{type(e).__name__}: {e}",
                )

        # Pass 2: Extract edges, each file with its own declarations and imports
        all_edges: list[Edge] = []
        for pf in parsed_files:
            edges = _extract_edges(pf.tree, pf.source, pf.path, run, pf.symbols, pf.uses.aliases)
            # ADR-0015 Tier 1: automatic dataflow annotation
            if _df_config is not None:
                annotate_dataflow(edges, pf.tree, pf.source, _df_config)
            all_edges.extend(edges)

        # Pass 3: Extract UsageContexts and route symbols for framework pattern matching
        all_usage_contexts: list[UsageContext] = []
        for pf in parsed_files:
            contexts, route_symbols = _extract_laravel_routes(pf.tree, pf.source, pf.path, run)
            all_usage_contexts.extend(contexts)
            all_symbols.extend(route_symbols)

        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            usage_contexts=all_usage_contexts,
            run=run,
            # ADR-0064: each file's import table, keyed like ``Symbol.path``.
            import_scopes={str(pf.path): pf.uses.scope for pf in parsed_files},
        )


_analyzer = PHPAnalyzer()


def is_php_tree_sitter_available() -> bool:
    """Check if tree-sitter with PHP grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("php", find_files=find_php_files)
def analyze_php(repo_root: Path) -> AnalysisResult:
    """Analyze all PHP files in a repository."""
    return _analyzer.analyze(repo_root)
