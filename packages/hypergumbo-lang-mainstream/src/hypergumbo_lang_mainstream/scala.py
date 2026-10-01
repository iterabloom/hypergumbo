# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scala analysis pass using tree-sitter-scala.

This analyzer uses tree-sitter to parse Scala files and extract:
- Function definitions (def)
- Class definitions (class)
- Object definitions (object)
- Trait definitions (trait)
- Method definitions (inside classes/objects/traits)
- Secondary constructors (def this(...), kind=constructor)
- Scala 3 enums (kind=enum) with one ``field`` symbol per case, and named
  ``given`` instances (kind=instance), so their bodies' val/var members have
  an owner (a ``def`` in an enum or given body is not owned by it)
- val/var declarations: ``field`` inside a class/object/trait/enum/given
  body, ``variable`` at top level (local bindings are skipped)
- Function call relationships
- Import statements
- Annotations/decorators (into symbol meta["decorators"], for functions, methods, classes, and val/var fields)
- Inheritance: extends/with base classes and traits (into symbol meta["base_classes"], for classes and traits)

Modifiers (access/abstract/final/sealed/override/implicit/lazy/case) are
captured on Symbol.modifiers, and parameter/variable types -- declared, or
inferred from a declared return type (WI-gokop) -- are tracked to
disambiguate type-qualified method calls.

If tree-sitter with Scala support is not installed, the analyzer warns and
returns a skipped result (``skip_reason_code=DEPENDENCY_UNAVAILABLE``).

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, classes, objects, traits, enums, givens and
   val/var members, with signatures. Each ``def``'s declared return type, and
   each typed member ``val`` or class parameter, feeds the base return-type
   registry (``_extract_scala_return_type_name``), qualified through the
   declaring file's imports; the element of a container it returns
   (``Seq[File]`` -> ``File``) feeds a scala-local element registry.
2. Pass 2: Extract call edges, import edges, and eta-expansion references edges using NameResolver
   - A method call's receiver is typed by :class:`_ScalaReceiverTyper`: the
     nearest lexical binder when its type can be said, else the file-wide
     declared map as before; an inferred ``val``, a call result, a chained
     receiver, a parameterless member, ``this`` and ``asInstanceOf[T]`` are
     typed through the registry, and a ``for`` generator, a one-parameter
     lambda / ``case`` / ``_`` of an element-passing call and a ``.get`` /
     ``.head`` projection through the element registry. A qualified project
     type binds only where its path allows (the import check below).
   - An explicit import outranks a same-named project symbol in another
     package: the bind is refused when no reading of the import (absolute
     or relative to a package or object in scope) can name that symbol.
     Imports inside a block or body apply only within it.
   - A method call not resolved in-file becomes an unresolved edge rather
     than a short-name guess; the ``ExternalRef`` module slot carries the
     receiver's import-qualified type, or the imported owner of a static
     call such as ``Files.readAllBytes``. One- and two-letter callee
     names (usually lambda parameters) take a confidence penalty on
     short-name binds.
   - A bare call that matches a DIFFERENT class's method only by short name
     is not bound: it becomes an unresolved edge stamped with the enclosing
     class (``defer_bare_method_call``), which the ``inherited_calls`` linker
     resolves when the method is inherited (INV-fahub).
   - A call in no function is anchored on the class/object/trait/enum whose
     body holds it (``_TYPE_BODY_NODES``), else on the file (INV-bamij).

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides the Scala-specific extraction
logic, plus a per-run file-to-package map (reset at the start of each
``analyze()``, since the analyzer is a module singleton) that the import
check above reads.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-scala package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency
"""
from __future__ import annotations

from collections.abc import Container
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Iterator, NamedTuple, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    find_child_by_type,
    iter_tree,
    file_anchor_symbol,
    enclosing_declared_symbol,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    make_variable_stable_id,
    node_text,
    symbol_declared_by,
    symbols_at,
    visibility_from_modifiers,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_lang_mainstream.jvm_implicit_imports import (
    SCALA_SHADOWED_JAVA_LANG,
    inline_qualified_owner,
    static_owner_module,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("scala")

#: INV-bamij: templates whose BODY anchors a call that sits in no function
#: (every ``val`` in an ``object`` is an initialiser).
_TYPE_BODY_NODES: frozenset[str] = frozenset({
    "object_definition", "class_definition", "trait_definition", "enum_definition",
})


def _short_name_penalty(name: str) -> float:
    """Confidence penalty for short callee names in Scala call resolution.

    Single-letter names (f, g, x, n) are almost always lambda parameters
    or local defs in Scala FP code, not cross-file calls. Two-letter names
    (fn, xs) are also often parameters. Applying a penalty makes false
    positive edges easily filterable by downstream consumers.
    """
    n = len(name)
    if n <= 1:
        return 0.15
    if n == 2:
        return 0.50
    return 1.0


def find_scala_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Scala files in the repository."""
    yield from find_files(repo_root, ["*.scala"])


def _extract_extends_clause(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base class/trait names from extends clause.

    Handles:
    - extends BaseClass
    - extends BaseClass with Trait1 with Trait2
    - extends GenericClass[T]

    Args:
        node: class_definition or trait_definition node
        source: Source code bytes

    Returns:
        List of base class/trait names (without generic type params)
    """
    base_classes: list[str] = []

    extends_clause = find_child_by_type(node, "extends_clause")
    if extends_clause is None:
        return base_classes

    for child in extends_clause.children:
        if child.type == "type_identifier":
            base_classes.append(node_text(child, source))
        elif child.type == "generic_type":
            type_id = find_child_by_type(child, "type_identifier")
            if type_id:
                base_classes.append(node_text(type_id, source))

    return base_classes


def _extract_import_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import statements for disambiguation.

    In Scala:
        import package.ClassName -> ClassName maps to package.ClassName
        import package.{A, B} -> A, B map to their full paths
        import package.{A => Alias} -> Alias maps to package.A

    Returns a dict mapping short names to full qualified paths.
    """
    hints: dict[str, str] = {}
    for node in iter_tree(tree.root_node):
        if node.type == "import_declaration":
            hints.update(_import_declaration_hints(node, source))
    return hints


def _import_declaration_hints(node: "tree_sitter.Node", source: bytes) -> dict[str, str]:
    """The short-name -> path hints ONE ``import_declaration`` makes."""
    hints: dict[str, str] = {}
    identifiers: list[str] = []
    has_selectors = False

    for child in node.children:
        if child.type == "identifier":
            identifiers.append(node_text(child, source))
        elif child.type == "namespace_selectors":
            has_selectors = True
            base_path = ".".join(identifiers)
            for selector in child.children:
                if selector.type == "arrow_renamed_identifier":
                    names = [sub for sub in selector.children if sub.type == "identifier"]
                    if len(names) >= 2:
                        original = node_text(names[0], source)
                        alias = node_text(names[-1], source)
                        full_path = f"{base_path}.{original}"
                        hints[alias] = full_path
                elif selector.type == "identifier":
                    name = node_text(selector, source)
                    full_path = f"{base_path}.{name}"
                    hints[name] = full_path

    if identifiers and not has_selectors:
        full_path = ".".join(identifiers)
        short_name = identifiers[-1]
        hints[short_name] = full_path

    return hints


#: Where a TOP-LEVEL import sits: the file itself, or a braced package's body.
_TOP_LEVEL_IMPORT_PARENTS = ("compilation_unit",)


def _block_scoped_imports(
    root: "tree_sitter.Node", source: bytes,
) -> "dict[str, list[tuple[int, int]]]":
    """Names imported ONLY inside a block, class or object body, mapped to the
    byte ranges where each such import applies: from the end of the import to
    the end of its enclosing body.

    ``_extract_import_hints`` reads every import as file-wide. That is harmless
    for a module-slot hint, but an import used to REFUSE a binding (WI-tipoh)
    must apply to the call. On zio, a ``scala.concurrent.Promise`` imported
    inside one test refused ``zio.Promise`` calls elsewhere in the file. A name
    also imported at top level is left out: the file-wide reading stands for it.
    """
    top: set[str] = set()
    scoped: dict[str, list[tuple[int, int]]] = {}
    for node in iter_tree(root):
        if node.type != "import_declaration" or node.parent is None:
            continue
        parent = node.parent
        is_top = parent.type in _TOP_LEVEL_IMPORT_PARENTS or (
            parent.type == "template_body" and parent.parent is not None
            and parent.parent.type == "package_clause")
        for name in _import_declaration_hints(node, source):
            if is_top:
                top.add(name)
            else:
                scoped.setdefault(name, []).append((node.end_byte, parent.end_byte))
    return {name: spans for name, spans in scoped.items() if name not in top}


def _extract_annotation_info(
    annotation_node: "tree_sitter.Node", source: bytes,
) -> dict[str, object]:
    """Extract annotation name, args, and kwargs from a Scala annotation node.

    Scala annotations have two forms:
    1. Simple: ``annotation → @ + type_identifier`` (e.g., ``@Inject``)
    2. With args: ``annotation → @ + type_identifier + arguments`` (e.g., ``@deprecated("old", "2.0")``)
    """
    name = ""
    args: list[object] = []
    kwargs: dict[str, object] = {}

    for child in annotation_node.children:
        if child.type == "type_identifier":
            name = node_text(child, source)
        elif child.type == "arguments":
            for arg_child in child.children:
                if arg_child.type == "string":
                    # Scala string node text includes quotes: strip them
                    raw = node_text(arg_child, source)
                    args.append(raw.strip('"'))
                elif arg_child.type == "identifier":
                    args.append(node_text(arg_child, source))
                elif arg_child.type == "assignment_expression":
                    # key = value (e.g., @Table(name = "users"))
                    parts = [c for c in arg_child.children if c.type != "="]
                    if len(parts) >= 2:
                        key = node_text(parts[0], source)
                        val_node = parts[1]
                        val = (
                            node_text(val_node, source).strip('"')
                            if val_node.type == "string"
                            else node_text(val_node, source)
                        )
                        kwargs[key] = val

    return {"name": name, "args": args, "kwargs": kwargs}


#: Types Scala imports IMPLICITLY -- ``scala.Predef._``, ``scala._``,
#: ``java.lang._`` -- which therefore appear in NO import line.
#:
#: WHY A GENERIC BASE IS DENIED WHEN IT IS ONE OF THESE, measured rather than
#: argued. Widening type extraction to ``generic_type`` makes ``val m:
#: Map[String, X]`` contribute the base ``Map``. That name cannot qualify --
#: there is no import to look it up in -- so it adds NOTHING to the module slot.
#: What it does instead is reach the bare short-name bind, which is the funnel
#: this file's own external branch refuses for untyped receivers ("confidently
#: bind to an arbitrary same-named internal def"). On sbt that produced 44
#: bindings of a standard-library ``Map[K,V]`` to the PROJECT's
#: ``sbt/SessionVar.scala`` ``Map.get`` -- 24% of every edge the change newly
#: resolved. lila showed 0, because lila happens to define no such collider;
#: that is luck, not safety.
#:
#: SCOPED TO THE GENERIC BASE, DELIBERATELY. An explicitly ANNOTATED bare
#: ``val x: Map = ...`` is untouched: the programmer wrote that name, it is the
#: pre-existing INV-fahub / WI-bihit population, and narrowing it is a separate
#: change with its own measurement. This denies only names this change would
#: newly INVENT from a type argument.
_SCALA_IMPLICIT_IMPORT_TYPES = frozenset({
    "Map", "List", "Set", "Seq", "Vector", "Array", "Option", "Some", "Either",
    "Left", "Right", "Iterable", "Iterator", "Stream", "Range", "String",
    "Int", "Long", "Boolean", "Double", "Float", "Char", "Byte", "Short",
    "Unit", "Any", "AnyRef", "AnyVal", "Nothing", "Null", "Throwable",
    "Exception", "Error", "Thread", "Class", "Object", "Tuple2", "Function1",
})

#: The type-node kinds a declared Scala type can take. ``type_identifier`` is
#: the bare one every extraction site used to look for exclusively; the other
#: two were invisible, which cost the hint entirely (WI-pokam).
_SCALA_TYPE_NODES = ("type_identifier", "stable_type_identifier", "generic_type")


class _ScalaFile(NamedTuple):
    """What an import check needs to know about one analysed file (WI-tipoh)."""

    #: Its package: ``""`` for the root package, ``None`` when unknown.
    package: "str | None"
    #: The simple names of the classes, objects and traits it declares.
    types: "frozenset[str]"


def _is_project_type(
    name: str,
    global_symbols: dict[str, Symbol],
    import_aliases: "dict[str, str] | None" = None,
    file_packages: "dict[str, _ScalaFile] | None" = None,
    project_packages: "frozenset[str]" = frozenset(),
) -> bool:
    """Whether ``name`` is a project class, object or trait for this file.

    A same-package type shadows a default import in scala, so a project
    ``object System`` must not be read as ``java.lang.System`` (WI-kilap). An
    EXPLICIT import of a path OUTSIDE the project outranks a same-named project
    type (WI-tipoh): ``import scala.sys.process.Process`` then
    ``Process.apply(c)`` is the stdlib's, whatever the project names
    ``Process``. An import of a PROJECT path keeps the name a project type even
    when the registry holds a different same-named one. On spark, 1217
    ``Utils.x`` calls on the imported ``org.apache.spark.util.Utils`` would
    otherwise have lost the placeholder slot the Tier-2 linkers resolve from.
    """
    sym = global_symbols.get(name)
    if sym is None or sym.kind not in ("class", "object", "trait"):
        return False
    imported = (import_aliases or {}).get(name)
    if imported is None or not _import_names_elsewhere(sym, imported, file_packages, import_aliases):
        return True
    return _import_names_project_path(imported, project_packages)


def _package_readings(file_packages: "dict[str, _ScalaFile]") -> "frozenset[str]":
    """Every package the project declares, and every trailing run of its segments,
    which is what a RELATIVE import may start with."""
    readings: set[str] = set()
    for package, _types in file_packages.values():
        if package:
            segments = package.split(".")
            readings.update(".".join(segments[i:]) for i in range(len(segments)))
    return frozenset(readings)


def _import_names_project_path(imported: str, project_packages: "frozenset[str]") -> bool:
    """Whether some reading of the import's package part lies in a package the
    project declares. ``project_packages`` is :func:`_package_readings`."""
    parts = imported.removeprefix("_root_.").split(".")[:-1]
    return any(".".join(parts[:k]) in project_packages for k in range(1, len(parts) + 1))


def _scala_file_package(root: "tree_sitter.Node", source: bytes) -> "str | None":
    """The package a file's top-level definitions live in: ``""`` for the root
    package, ``None`` when one name cannot say.

    Chained header clauses make one package (``package org.apache.spark`` then
    ``package sql`` is ``org.apache.spark.sql``). A braced ``package a { ... }``
    counts only when it is the file's sole definition, since otherwise the
    file holds definitions in more than one package.
    """
    parts: list[str] = []
    braced = 0
    others = 0
    for child in root.children:
        if child.type == "package_clause":
            ident = find_child_by_type(child, "package_identifier")
            if ident is None:  # pragma: no cover - the grammar always names it
                return None
            parts.append(node_text(ident, source))
            if find_child_by_type(child, "template_body") is not None:
                braced += 1
        elif child.type not in ("import_declaration", "comment", "block_comment"):
            others += 1
    if braced and (braced > 1 or others):
        return None
    return ".".join(parts)


def _import_names_elsewhere(
    sym: Symbol,
    imported: "str | None",
    file_packages: "dict[str, _ScalaFile] | None",
    import_aliases: "dict[str, str] | None" = None,
) -> bool:
    """Whether the file's explicit import of a name cannot be the project symbol
    ``sym`` the resolver returned (WI-tipoh).

    An explicit import outranks a same-named project symbol in another package:
    ``import scala.sys.process.Process`` then ``Process(cmd)`` bound to spark's
    unrelated test ``case class Process``. That is the edge this refuses.

    WHY NOT AN EXACT QUALIFIED NAME, AS KOTLIN USES. Measured on the scala
    cohort, an exact ``<package>.<name>`` against the import withheld correct
    edges from three causes:
    - a scala import may be RELATIVE, to an enclosing package, to a wildcard
      import, or to ``scala._`` (``import pekko.util.OptionVal`` inside
      ``org.apache.pekko``; ``collection.concurrent.TrieMap``);
    - a nested type's symbol name omits its enclosing object
      (``DataTypeMismatch`` in ``object TypeCheckResult``);
    - a package-object member's package omits the object (``truncatedString`` in
      ``package object util``).
    So the import CONTRADICTS the symbol only when no reading of it fits. It fits
    when it is a segment-aligned suffix of ``<package>.<name>``, or when it begins
    with some trailing run of the symbol's package segments. The first covers a
    path relative to a type in scope; the second covers the absolute path and
    every relative spelling of it, with nested types and package objects below.
    ``_root_.`` makes an import absolute. What this still refuses is a different
    package tree: ``scala.sys.process`` against ``build``,
    ``catalyst.expressions`` against ``sql.internal``, ``scaladsl`` against
    ``javadsl``. What it keeps, without deciding, is anything a relative reading
    could reach.

    A third reading covers an import relative to an OBJECT in scope, whose nested
    member's name omits the object; see the comment at the end.

    An import whose first segment is a name the file itself imported is read
    through that import first: ``import scala.collection.{mutable => cm}`` then
    ``import cm.{AnyRefMap}`` names ``scala.collection.mutable.AnyRefMap``.

    Nothing to contradict (no import, an import with no dot, a file whose package
    is unknown) is False, the old behaviour.
    """
    if not imported or "." not in imported or file_packages is None:
        return False
    head, _, rest = imported.partition(".")
    through = (import_aliases or {}).get(head)
    if through and "." in through and not imported.startswith("_root_."):
        imported = f"{through}.{rest}"
    info = file_packages.get(sym.path)
    if info is None or info.package is None:
        return False
    package = info.package
    absolute = imported.startswith("_root_.")
    if absolute:
        imported = imported[len("_root_."):]
    qualified = f"{package}.{sym.name}" if package else sym.name
    if qualified == imported or (not absolute and ("." + qualified).endswith("." + imported)):
        return False
    segments = package.split(".") if package else []
    tails = [segments] if absolute else [segments[i:] for i in range(len(segments))]
    if any(tail and imported.startswith(".".join(tail) + ".") for tail in tails):
        return False
    # Relative to an object in scope: ``import SymDenotations.SymDenotation``
    # names a class nested in ``object SymDenotations``, and the nested class's
    # symbol name omits the object. The import fits when its leaf is the symbol
    # or a type enclosing it (a call on a value of the imported type returns a
    # member: ``SymDenotation.is``), and it passes through a type the symbol's
    # own file declares. Measured: 91 withholds on scala3 and 39 on pekko were
    # this shape.
    parts = imported.split(".")
    return not (parts[-1] in sym.name.split(".")
                and any(part in info.types for part in parts[:-1]))


#: The roots a fully-qualified inline call is taken at its word for (INV-vokut):
#: the Scala standard library and the JDK, the packages scala.yaml and the JVM
#: rows it reaches describe. A root outside this set is left alone.
_INLINE_QUALIFIED_ROOTS: Final = frozenset({"scala", "java", "javax"})


def _inline_path(value: "tree_sitter.Node", source: bytes) -> "str | None":
    """The dotted text of a receiver built only of identifiers, else ``None``.

    ``scala.io.StdIn`` parses as nested ``field_expression`` nodes with an
    identifier at every level. A call, an index or a literal anywhere in the
    chain means the receiver is a value, not a path, and returns ``None``.
    """
    parts: list[str] = []
    node = value
    while node.type == "field_expression":
        field = node.child_by_field_name("field")
        inner = node.child_by_field_name("value")
        if field is None or inner is None or field.type != "identifier":
            return None
        parts.append(node_text(field, source))
        node = inner
    if node.type != "identifier":
        return None
    parts.append(node_text(node, source))
    return ".".join(reversed(parts))


def _inline_qualified_owner(
    path: str, callee_name: str, bound: Container[str],
) -> "str | None":
    """The module slot a fully-qualified inline call states, or ``None`` (INV-vokut).

    scala's roots applied to the shared JVM rule
    (:func:`jvm_implicit_imports.inline_qualified_owner`), which kotlin uses
    with its own roots (INV-dupol).
    """
    return inline_qualified_owner(path, callee_name, bound, roots=_INLINE_QUALIFIED_ROOTS)


def _qualify_scala_receiver(
    receiver_type: str,
    import_aliases: "dict[str, str]",
) -> "str | None":
    """The module-slot path for a receiver type, or ``None`` to keep the sentinel.

    WI-sigog's discipline, unchanged: the slot only ever carries a path THE FILE
    ITSELF DECLARES. A bare name is looked up in the file's imports and left
    alone when it misses, because a simple name in the module slot asserts a
    module that does not exist and can collide with a catalogued entry of the
    same short name (INV-fazim).

    WI-pokam adds ONE case to "declares": an INLINE QUALIFICATION. ``val b:
    java.io.File`` states the path at the use site, which is the same fact
    ``import java.io.File`` states at the top of the file — and it is precisely
    the case where there is NO import line to consult, so the alternative is not
    a safer answer but no answer. Nothing is invented and nothing is looked up:
    the name qualifies to itself.

    THE KNOWN IMPRECISION, named rather than discovered later. Scala also writes
    a PATH-DEPENDENT type in type position (``val x: someObj.Inner``), which
    parses to the same ``stable_type_identifier`` node. That would put
    ``someObj.Inner`` in the slot, which is not a module. It is bounded rather
    than unbounded: a dotted name is far less collision-prone than the bare name
    INV-fazim refused, and a path that names nothing simply matches no catalogue
    row. The corpus share of the shape is measured rather than assumed — see
    ``~/hypergumbo_lab_notebook/pokam_scala_09102026/``.
    """
    imported: "str | None" = import_aliases.get(receiver_type)
    if imported:
        return imported
    return receiver_type if "." in receiver_type else None


def _declared_type_name(parent: "tree_sitter.Node", source: bytes) -> "str | None":
    """The NAME of the type declared as a direct child of ``parent``, or ``None``.

    THE ONE PLACE A DECLARED TYPE IS NAMED. Every site that wanted a type used
    to call ``find_child_by_type(parent, "type_identifier")`` itself, which named
    ONLY a bare type; a qualified type (``java.io.File``) parses to
    ``stable_type_identifier`` and a generic applied type (``Buffer[String]``) to
    ``generic_type``, so four sites independently returned ``None`` for both.
    Sharing the PREDICATE is not enough when callers can still walk different
    populations (INV-motos), so this shares the WALK as well.

    WHAT EACH SHAPE CONTRIBUTES, and the two are deliberately different:

    * ``stable_type_identifier`` contributes the FULL DOTTED NAME. An inline
      qualification IS the static owner path (ADR-0050/0051), spelled by the file
      at the use site, and it is the one case where there is no import line to
      consult — so nothing is invented and nothing needs looking up.
    * ``generic_type`` contributes its BASE, recursively: the receiver's methods
      live on ``Buffer``, not on ``Buffer[String]``, and the type argument is not
      the receiver. ``java.util.List[String]`` therefore yields
      ``java.util.List`` and qualifies by the clause above.

    A tuple type, a function type and a wildcard yield ``None`` — deliberately,
    not by omission: none of them names a single owner a catalogue row could be
    keyed to, and INV-fazim's rule is that an unqualifiable type is left alone
    rather than written in bare.
    """
    for child in parent.children:
        if child.type in _SCALA_TYPE_NODES:
            return _type_node_name(child, source)
    return None


def _type_node_name(node: "tree_sitter.Node", source: bytes) -> "str | None":
    """The NAME one type node contributes, by :func:`_declared_type_name`'s rules.

    Split out so a type reached through a FIELD (a ``def``'s ``return_type``, a
    ``typed_pattern``'s type) is named by the same rules as one found among a
    declaration's children, rather than by a second copy of them.
    """
    if node.type in ("type_identifier", "stable_type_identifier"):
        return node_text(node, source)
    if node.type == "generic_type":
        base = _declared_type_name(node, source)
        # A base this change INVENTED from a type argument is refused when
        # it is an implicitly-imported name: it cannot qualify, so its only
        # reachable effect is a short-name mis-bind. See
        # :data:`_SCALA_IMPLICIT_IMPORT_TYPES` for the measurement.
        if base is not None and base not in _SCALA_IMPLICIT_IMPORT_TYPES:
            return base
    return None


def _scala_type_parameter_names(node: "tree_sitter.Node", source: bytes) -> "set[str]":
    """The type parameters in scope at a member: its own and its enclosing type's.

    ``def get: T`` in ``class Box[T]`` names no type a caller could look up.
    """
    names: set[str] = set()
    current: "tree_sitter.Node | None" = node
    while current is not None:
        params = find_child_by_type(current, "type_parameters")
        if params is not None:
            names.update(node_text(c, source) for c in params.children if c.type == "identifier")
        if current.type in ("class_definition", "trait_definition"):
            break
        current = current.parent
    return names


def _registrable_type(name: "str | None", member: "tree_sitter.Node", source: bytes) -> "str | None":
    """``name`` as a return-type registry value, or ``None`` to register nothing.

    Two refusals, both about types the registry would INVENT for a caller that
    wrote no type at all. An implicitly-imported name (``String``, ``Int``,
    ``List``, ``Option``) cannot qualify, so its only reachable effect is the
    short-name mis-bind WI-pokam measured (:data:`_SCALA_IMPLICIT_IMPORT_TYPES`).
    A type parameter names nothing outside the declaration.
    """
    if name is None or name in _SCALA_IMPLICIT_IMPORT_TYPES:
        return None
    if name in _scala_type_parameter_names(member, source):
        return None
    return name


def _extract_scala_return_type_name(node: "tree_sitter.Node", source: bytes) -> "str | None":
    """The type a ``def``'s DECLARED return type names, for the registry (WI-gokop).

    Read off the ``return_type`` field and named by :func:`_type_node_name`, so a
    qualified type keeps its path and a generic one contributes its base
    (``Buffer[String]`` -> ``Buffer``), exactly as an annotation would. Then:

    * ``this.type`` -- the fluent-builder return -- names the enclosing type, the
      same resolution swift gives ``-> Self``;
    * a WRAPPER is NOT unwrapped. ``Future[T]`` and ``Try[T]`` register as
      ``Future`` / ``Try``, and ``Option[T]`` / ``Either[L, R]`` register nothing
      (implicit-import names), rather than any of them reading as ``T``: a
      receiver bound to ``opt()`` or ``fut()`` calls the WRAPPER's methods
      (``getOrElse``, ``map``), and typing it ``T`` would hand them to ``T``. Rust
      unwraps in its registry because ``?`` and ``.unwrap()`` project the value at
      nearly every use; Scala's projections -- ``for``, ``map``, ``case Some(x)``
      -- bind a NEW name, which is an element type, not this registry's answer;
    * a tuple, function, compound or other structural type names no single owner
      and registers nothing.

    Implicits are not resolved (ADR-0006 leaves them future work).
    """
    rt = node.child_by_field_name("return_type")
    if rt is None:
        return None
    if rt.type == "singleton_type":
        ident = find_child_by_type(rt, "identifier")
        if ident is not None and node_text(ident, source) == "this":
            return _get_enclosing_type(node, source)
        return None
    return _registrable_type(_type_node_name(rt, source), node, source)



def _class_parameter_member(node: "tree_sitter.Node", source: bytes) -> "str | None":
    """``Owner.param`` for a ``class_parameter``, or ``None`` when either is unnamed."""
    owner = node.parent.parent if node.parent is not None else None
    owner_name = find_child_by_type(owner, "identifier") if owner is not None else None
    param_name = find_child_by_type(node, "identifier")
    if owner_name is None or param_name is None:  # pragma: no cover - the grammar names both
        return None
    return f"{node_text(owner_name, source)}.{node_text(param_name, source)}"


def _extract_annotations_scala(
    node: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """Extract annotations from a Scala declaration node.

    In Scala, annotations are direct children of the declaration, not
    wrapped in a ``modifiers`` node (unlike Java/Kotlin/Groovy).
    """
    decorators: list[dict[str, object]] = []
    for child in node.children:
        if child.type == "annotation":
            dec_info = _extract_annotation_info(child, source)
            if dec_info["name"]:
                decorators.append(dec_info)
    return decorators


def _get_enclosing_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/object/trait name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_definition", "object_definition", "trait_definition"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The function or method whose declaration contains ``node``.

    Keyed by the declaration's POSITION, not its name (INV-midag). The short
    name is shared by the ``apply`` of every companion in a file, and by
    ``OneOf.show`` / ``WebSocketBodyOutput.show`` in tapir's EndpointIO.scala.
    The lookup returned whichever registered last, so 964 of 16,531 scala call
    edges on a 26-repo run named a src that does not contain the call. A
    ``function_definition`` with no symbol (a local ``def``) is walked past.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            sym = symbol_declared_by(current, decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None


# WI-jusus (emission-parity F5): scope discrimination for a val/var, so only a
# class/object/trait/enum/given-body val becomes a ``field`` and only a
# top-level val a ``variable``. Any LOCAL binding is NOT an API surface —
# emitting one would repeat the swift INV-lanaz / go INV-sidab function-local
# leak regressions. The local set must catch every non-body scope a val can sit
# directly under: a block/function/lambda, a ``case_clause`` (a braceless
# ``case _ => val w`` in a partial-function literal / match / try-catch — the
# *initializer* of a field or top-level val, which would otherwise climb to the
# body and leak; covers both Scala-2 ``case_block`` and Scala-3 ``indented_cases``
# chains since the val sits directly under ``case_clause``), and a Scala-3
# ``indented_block`` (a braceless nested initializer block).
_SCALA_LOCAL_SCOPE_TYPES = frozenset({
    "block", "function_definition", "function_declaration", "lambda_expression",
    "case_clause", "indented_block",
})
# Body nodes whose (named) owner makes a directly-contained val a field.
_SCALA_FIELD_BODY_TYPES = frozenset({
    "template_body", "enum_body", "with_template_body",
})
_SCALA_TYPE_DEF_TYPES = frozenset({
    "class_definition", "object_definition", "trait_definition",
    "enum_definition", "given_definition",
})


def _scala_property_scope(
    node: "tree_sitter.Node", source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """Classify a val/var by its nearest scope-defining ancestor (WI-jusus).

    Returns ``("field", owner)`` for a val/var directly in a NAMED
    class/object/trait/enum/given body, ``("variable", None)`` for a top-level
    val/var (reaches ``compilation_unit`` first), or ``(None, None)`` for a local
    binding (block/function/lambda/case/indented) OR an anonymous
    ``new Foo { val x = ... }`` / anonymous-given member (a body whose parent is
    not a NAMED type_definition — e.g. an ``instance_expression``). The NEAREST
    scope wins; the owner is the body's parent identifier (NOT
    ``_get_enclosing_type``, which would walk past an anonymous body to the outer
    type and mis-attribute the member).
    """
    current = node.parent
    while current is not None:
        if current.type in _SCALA_LOCAL_SCOPE_TYPES:
            return (None, None)
        if current.type in _SCALA_FIELD_BODY_TYPES:
            parent = current.parent
            if (
                parent is not None
                and parent.type in _SCALA_TYPE_DEF_TYPES
                and (nm := find_child_by_type(parent, "identifier")) is not None
            ):
                return ("field", node_text(nm, source))
            return (None, None)
        if current.type == "compilation_unit":
            return ("variable", None)
        current = current.parent  # pragma: no cover - a val's immediate parent is always a scope node in the bundled grammar
    return (None, None)  # pragma: no cover - every node is under compilation_unit


def _extract_scala_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Scala function definition.

    Returns signature like:
    - "(x: Int, y: Int): Int" for regular functions
    - "(message: String)" for Unit functions (Unit omitted)
    """
    params: list[str] = []
    return_type = None
    found_params = False

    for child in node.children:
        if child.type == "parameters":
            found_params = True
            for subchild in child.children:
                if subchild.type == "parameter":
                    param_name = None
                    param_type = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = node_text(pc, source)
                        elif pc.type in ("type_identifier", "generic_type", "tuple_type",
                                         "function_type", "infix_type"):
                            param_type = node_text(pc, source)
                    if param_name and param_type:
                        params.append(f"{param_name}: {param_type}")
        elif found_params and child.type in ("type_identifier", "generic_type",
                                              "tuple_type", "function_type", "infix_type"):
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "Unit":
        signature += f": {return_type}"

    return signature


def normalize_scala_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Scala signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(signature, type_params, return_sep=":")


# Scala modifier keywords extractable from the AST.
# tree-sitter-scala wraps access modifiers in ``modifiers`` → ``access_modifier``
# whose children are the keywords (private, protected, etc.).
SCALA_MODIFIER_KEYWORDS = {
    "private", "protected",
    "abstract", "final", "sealed",
    "override", "implicit", "lazy",
    "case",
}


def _extract_modifiers_scala(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a Scala declaration node.

    Scala tree-sitter groups modifiers under a ``modifiers`` container.
    Access modifiers appear as ``access_modifier`` children wrapping
    the keyword (``private``, ``protected``).  Other modifiers like
    ``abstract``, ``sealed`` appear as direct keyword children inside
    ``modifiers``.  The ``case`` keyword is a direct child of the
    declaration node (not inside ``modifiers``).

    Returns a list of modifier strings like ``["private", "case"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for mod_node in child.children:
                if mod_node.type == "access_modifier":
                    for kw in mod_node.children:
                        if kw.type in SCALA_MODIFIER_KEYWORDS:
                            modifiers.append(kw.type)
                elif mod_node.type in SCALA_MODIFIER_KEYWORDS:
                    modifiers.append(mod_node.type)
        # ``case`` is a direct child, not inside modifiers
        elif child.type == "case":
            modifiers.append("case")
    return modifiers


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    element_types: "dict[str, str] | None" = None,
) -> FileAnalysis:
    """Extract symbols from a single Scala file.

    ``element_types`` (WI-gokop), when given, receives ``<Owner>.<member>`` ->
    the ELEMENT type of a container the member returns (``def files:
    Seq[File]`` -> ``File``), beside the return types this records in
    ``FileAnalysis.method_return_types``. First writer wins, as in the base
    aggregation of the latter.
    """
    analysis = FileAnalysis()
    # WI-gokop: a registered type is written as the DECLARING file names it, so a
    # library type is qualified through THIS file's imports (``FileWriter`` ->
    # ``java.io.FileWriter``) and a caller that never imports it -- inference
    # needs no import -- still gets the path. java's registry does the same
    # (WI-gajuh). Pass 2 keeps the path, which tells same-named project types
    # apart, and looks a member up by the simple name
    # (:meth:`_ScalaReceiverTyper.symbol_type_name`).
    import_hints = _extract_import_hints(tree, source)

    def _qualified(name: str) -> str:
        return name if "." in name else import_hints.get(name, name)

    def _register_type(key: str, name: "str | None") -> None:
        if name is not None:
            analysis.method_return_types.setdefault(key, _qualified(name))

    def _register_element(key: str, type_holder: "tree_sitter.Node | None") -> None:
        name = _declared_element(type_holder, source)
        if name is not None and element_types is not None:
            element_types.setdefault(key, _qualified(name))

    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_path`` is
    # the repo-relative path (the extract override passes ``rel_path``). Folded into
    # make_typed_stable_id's containing slot so same-name functions/methods in
    # different files hash distinctly.
    file_stable_id = make_file_stable_id("scala", normalize_path(file_path))

    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                func_name = node_text(name_node, source)
                enclosing_type = _get_enclosing_type(node, source)
                # WI-rupum: Scala secondary constructors are parsed as
                # ``function_definition`` with identifier text "this" —
                # ``def this(arg) = this(...)``. These are not methods;
                # they're constructors, invoked by ``new ClassName(arg)``.
                # Without this special case, the WI-tubot prospector
                # surfaced them (e.g. CachedPartition.this, KafkaConfig.this)
                # as top-ranked dead-code candidates, because the static
                # call graph never reaches them.
                is_secondary_ctor = (
                    func_name == "this" and enclosing_type is not None
                )
                if is_secondary_ctor:
                    full_name = f"{enclosing_type}.this"
                    kind = "constructor"
                elif enclosing_type:
                    full_name = f"{enclosing_type}.{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                signature = _extract_scala_signature(node, source)
                modifiers = _extract_modifiers_scala(node)
                annotations = _extract_annotations_scala(node, source)
                meta = {"decorators": annotations} if annotations else None

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_scala_signature(signature)
                stable_id = make_typed_stable_id(
                    kind, norm_sig, visibility_from_modifiers(modifiers),
                    name=func_name, qualified_name=full_name,
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                # WI-rupum: secondary constructors are inherently part
                # of the public API of their enclosing class (something
                # calls them via ``new``) — mark is_exported=True so
                # dead-code-maybe's --seeds exports mode treats them
                # as reachable. The constructor kind ALSO excludes them
                # from the dead-code candidate list at the kind filter.
                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                    meta=meta,
                    is_exported=is_secondary_ctor,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "scala"),
                    line_span=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol
                if not is_secondary_ctor:
                    _register_type(full_name, _extract_scala_return_type_name(node, source))
                    _register_element(full_name, node.child_by_field_name("return_type"))

        elif node.type == "function_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                func_name = node_text(name_node, source)
                enclosing_type = _get_enclosing_type(node, source)
                if enclosing_type:
                    full_name = f"{enclosing_type}.{func_name}"
                else:
                    full_name = func_name  # pragma: no cover - abstract methods are in traits

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                signature = _extract_scala_signature(node, source)
                modifiers = _extract_modifiers_scala(node)
                annotations = _extract_annotations_scala(node, source)
                meta = {"decorators": annotations} if annotations else None

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_scala_signature(signature)
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                    name=func_name, qualified_name=full_name,
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                    meta=meta,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "scala"),
                    line_span=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[func_name] = symbol
                analysis.symbol_by_name[full_name] = symbol
                _register_type(full_name, _extract_scala_return_type_name(node, source))
                _register_element(full_name, node.child_by_field_name("return_type"))

        elif node.type == "class_parameter":
            # WI-gokop: a class parameter is a member its owner's callers can
            # reach (always for a case class or a ``val`` parameter), and Scala
            # reads a member exactly as it calls a parameterless ``def``, so it
            # registers like one: ``box.boxed.write(..)`` types ``boxed``.
            member = _class_parameter_member(node, source)
            if member is not None:
                _register_type(
                    member, _registrable_type(_declared_type_name(node, source), node, source))
                _register_element(member, node)

        elif node.type == "class_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                base_classes = _extract_extends_clause(node, source)
                annotations = _extract_annotations_scala(node, source)
                # Renamed from `meta` — a different construct's meta dict than
                # the earlier `meta` in this method (fixes mypy [no-redef]). Typed
                # non-Optional and built up as a dict, then coerced to None only at
                # the Symbol via `or None`, so indexed assignment type-checks
                # instead of tripping [index] on the `| None` arm (WI-hokag).
                class_meta: dict[str, object] = {}
                if base_classes:
                    class_meta["base_classes"] = base_classes
                if annotations:
                    class_meta["decorators"] = annotations

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, type_name, "class"),
                    name=type_name,
                    kind="class",
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=class_meta or None,
                    modifiers=_extract_modifiers_scala(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        elif node.type == "object_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, type_name, "object"),
                    name=type_name,
                    kind="object",
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    modifiers=_extract_modifiers_scala(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        elif node.type == "trait_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                base_classes = _extract_extends_clause(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, type_name, "trait"),
                    name=type_name,
                    kind="trait",
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    modifiers=_extract_modifiers_scala(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        elif node.type == "enum_definition":
            # WI-pujiz: emit the Scala 3 enum owner (kind="enum", in
            # CONTAINER_KINDS) so the containment linker roots the enum body's
            # val/var fields (Color.rgb -> Color). The `given` owner is emitted
            # below.
            #
            # WI-dorop: the enum's CASES are now emitted too — they were the
            # "remain out of scope" this comment used to record. Without them a
            # reverse slice from the enum returned the container alone, which a
            # consumer reads as "this enum is dead". Same defect WI-duguk
            # drained for the eight analyzers the G2 parity matrix gates; scala
            # is outside that matrix, so nothing caught it.
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, type_name, "enum"),
                    name=type_name,
                    kind="enum",
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    modifiers=_extract_modifiers_scala(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

                # WI-dorop: one kind="field" per enum CASE, named
                # `Color.Red` — the `.` separator scala already uses for its
                # fields (`f"{owner}.{prop_name}"`) and methods, and one the
                # containment linker splits on.
                #
                # TWO member node types, not one: `simple_enum_case` (`case
                # Red`) and `full_enum_case` (`case Node(v: Int)`). And a
                # single `case Green, Blue` parses as ONE
                # `enum_case_definitions` holding TWO `simple_enum_case`
                # siblings, so the walk iterates CASES rather than
                # case-definition groups — a per-group loop would silently drop
                # every case after the first comma.
                #
                # Modifiers come from the ENUM, not the case: the `case`
                # keyword is a sibling token of the case node rather than a
                # child, so `_extract_modifiers_scala(case_node)` returns []
                # and a case has no visibility of its own to read.
                enum_modifiers = _extract_modifiers_scala(node)
                enum_body = find_child_by_type(node, "enum_body")
                for group in enum_body.children if enum_body else ():
                    if group.type != "enum_case_definitions":
                        continue
                    for case_node in group.children:
                        if case_node.type not in (
                            "simple_enum_case", "full_enum_case",
                        ):
                            continue
                        case_name_node = find_child_by_type(
                            case_node, "identifier",
                        )
                        if case_name_node is None:  # pragma: no cover - a case always names
                            continue
                        case_name = node_text(case_name_node, source)
                        case_full = f"{type_name}.{case_name}"
                        c_start = case_node.start_point[0] + 1
                        c_end = case_node.end_point[0] + 1
                        case_sym = Symbol(
                            id=make_symbol_id(
                                "scala", str(file_path), c_start, c_end,
                                case_full, "field",
                            ),
                            name=case_full,
                            kind="field",
                            language="scala",
                            path=str(file_path),
                            span=Span(
                                start_line=c_start,
                                end_line=c_end,
                                start_col=case_node.start_point[1],
                                end_col=case_node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            modifiers=enum_modifiers,
                            stable_id=make_typed_stable_id(
                                "field", "",
                                visibility_from_modifiers(enum_modifiers),
                                name=case_name, qualified_name=case_full,
                                file_stable_id=file_stable_id,
                            ),
                            line_span=c_end - c_start + 1,
                            # A case is as reachable as its enum; Scala has no
                            # per-case visibility modifier.
                            is_exported=not any(
                                m in enum_modifiers
                                for m in ("private", "protected")
                            ),
                        )
                        analysis.symbols.append(case_sym)
                        analysis.node_for_symbol[case_sym.id] = case_node
                        # Qualified name only — scala.py deliberately does not
                        # register short names (see the note on the member
                        # branches below).
                        analysis.symbol_by_name[case_full] = case_sym

        elif node.type == "given_definition":
            # WI-pujiz (REUSE-INSTANCE — 3-lens ADR/spec/spirit audit): a Scala 3
            # `given` is a typeclass / interface INSTANCE, the same construct
            # Haskell/Lean/PureScript already emit as kind="instance" (the
            # cross-language canonical). Per ADR-0027 a distinct source keyword
            # does NOT earn a new kind when a canonical role already fits (that
            # would fragment the canonical — Cluster-27C apex/peer), so the NAMED
            # given owner is emitted as kind="instance"; `instance` is in
            # CONTAINER_KINDS, so the given body's val/var fields
            # (intOrd.cached -> intOrd) root under it. Anonymous givens have no
            # identifier child -> skipped (matches _scala_property_scope).
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, type_name, "instance"),
                    name=type_name,
                    kind="instance",
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    modifiers=_extract_modifiers_scala(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[type_name] = symbol

        elif node.type in (
            "val_definition", "var_definition",
            "val_declaration", "var_declaration",
        ):
            # WI-jusus (emission-parity F5): emit a kind="field" Symbol for a
            # class/object/trait/enum/given-body val/var and a kind="variable"
            # Symbol for a top-level val/var. A local binding / anonymous-object
            # member is skipped (see _scala_property_scope). Documented fails-safe
            # deferrals (miss the symbol, never emit a WRONG one): a tuple-pattern
            # ``val (a, b) = t`` and a multi-name ``val a, b = 0`` (the name is an
            # ``identifiers`` container, no direct ``identifier`` child -> skipped);
            # constructor ``val``/``var`` params (``class C(val x: Int)`` — an
            # ``class_parameter``, not a val_definition); and package-object members.
            scope, owner = _scala_property_scope(node, source)
            name_node = find_child_by_type(node, "identifier")
            if scope is not None and name_node is not None:
                prop_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                type_node = find_child_by_type(node, "type_identifier")
                prop_type = (
                    node_text(type_node, source) if type_node is not None else None
                )
                modifiers = _extract_modifiers_scala(node)
                annotations = _extract_annotations_scala(node, source)
                meta = {"decorators": annotations} if annotations else None

                if scope == "field":
                    full_name = f"{owner}.{prop_name}"
                    stable_id = make_typed_stable_id(
                        "field", prop_type or "",
                        visibility_from_modifiers(modifiers),
                        name=prop_name, qualified_name=full_name,
                        file_stable_id=file_stable_id,
                    )
                else:
                    full_name = prop_name
                    stable_id = make_variable_stable_id(
                        "scala", str(file_path), prop_name
                    )

                symbol = Symbol(
                    id=make_symbol_id("scala", str(file_path), start_line, end_line, full_name, scope),
                    name=full_name,
                    kind=scope,
                    language="scala",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    stable_id=stable_id,
                    signature=prop_type,
                    modifiers=modifiers,
                    meta=meta,
                    is_exported=not any(
                        m in modifiers for m in ("private", "protected")
                    ),
                    line_span=end_line - start_line + 1,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                # Register a FIELD only under its qualified name (never its bare
                # short name): symbol_by_name is the edge pass's local_symbols
                # resolution index, and a short-name field/variable would shadow a
                # same-named callable (a call mis-resolving to a field, or a field
                # returned as an enclosing "function"). This mirrors commit
                # 67b2e14788, which stripped short-name registration from 11 langs
                # for exactly this call-graph-integrity reason; short-name lookups
                # go through the NameResolver suffix index. A variable is never a
                # call target, so it is not registered here at all.
                if scope == "field":
                    analysis.symbol_by_name[full_name] = symbol
                    # WI-gokop: a typed (or constructed) member is read like a
                    # parameterless ``def``, so it registers like one.
                    _inst = find_child_by_type(node, "instance_expression")
                    _holder = _inst if _inst is not None else node
                    _register_type(full_name, _registrable_type(
                        _declared_type_name(_holder, source), node, source))
                    _register_element(full_name, _holder)

    return analysis


def _extract_param_types_scala(
    node: "tree_sitter.Node", source: bytes,
) -> dict[str, str]:
    """Extract parameter name → type mapping from a Scala function definition.

    Enables type inference for method calls on typed parameters, e.g.:
        def process(client: Client) = { client.send() }
    resolves client.send() → Client.send.

    Scala parameters use ``parameter`` nodes inside ``parameters`` with
    identifier (name) then type_identifier (type).
    """
    param_types: dict[str, str] = {}
    for child in node.children:
        if child.type == "parameters":
            for subchild in child.children:
                if subchild.type == "parameter":
                    param_name = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = node_text(pc, source)
                    param_type = _declared_type_name(subchild, source)
                    if param_name and param_type:
                        param_types[param_name] = param_type
    return param_types


#: WI-gokop. The scopes whose DIRECT children may declare a ``val`` / ``var`` that
#: a later expression reads. In a BLOCK-like scope a declaration is visible only
#: after it; in a TEMPLATE-like scope every member is visible everywhere in it.
_SCALA_BLOCK_SCOPES: Final = frozenset({"block", "indented_block", "case_clause"})
_SCALA_TEMPLATE_SCOPES: Final = frozenset({
    "template_body", "with_template_body", "enum_body", "compilation_unit",
})
_SCALA_VALUE_DEFS: Final = frozenset({
    "val_definition", "var_definition", "val_declaration", "var_declaration",
})
#: The types whose methods a ``this`` / bare call inside them belongs to.
_SCALA_OWNER_DEFS: Final = frozenset({"class_definition", "object_definition", "trait_definition"})


#: WI-gokop. Generic types whose ONE type argument is the element a ``for``
#: generator, an element-passing call's lambda, or a projection (``.get``,
#: ``.head``) hands out. ``Either`` is right-biased: its element is ``R``. A
#: ``Map`` hands out pairs, so it is not here.
_SCALA_ELEMENT_CONTAINERS: Final = frozenset({
    "List", "Seq", "IndexedSeq", "LinearSeq", "Vector", "Set", "SortedSet",
    "Iterable", "Iterator", "Array", "ArraySeq", "Stream", "LazyList", "Buffer",
    "ArrayBuffer", "ListBuffer", "Queue", "Option", "Some", "Future", "Try",
    "Success", "Either", "NonEmptyList",
})
#: Methods whose function argument receives ONE element of the receiver.
_SCALA_ELEMENT_PASSING: Final = frozenset({
    "map", "flatMap", "foreach", "filter", "filterNot", "withFilter", "exists",
    "forall", "find", "count", "takeWhile", "dropWhile", "partition", "span",
    "groupBy", "sortBy", "maxBy", "minBy", "maxByOption", "minByOption",
    "distinctBy", "tapEach", "collect", "collectFirst", "indexWhere",
})
#: Methods that return a container of the receiver's OWN elements.
_SCALA_ELEMENT_PRESERVING: Final = frozenset({
    "filter", "filterNot", "withFilter", "take", "drop", "takeRight", "dropRight",
    "takeWhile", "dropWhile", "distinct", "distinctBy", "reverse", "sorted",
    "sortBy", "sortWith", "tail", "init", "slice", "toList", "toSeq", "toVector",
    "toSet", "toIndexedSeq", "toArray", "toIterable", "iterator", "view",
    "headOption", "lastOption", "find",
})
#: Methods that return ONE element of the receiver -- the call-site projection
#: Rust's ``?`` / ``.unwrap()`` is, which is where Scala's wrapper is unwrapped.
_SCALA_ELEMENT_PROJECTING: Final = frozenset({
    "get", "head", "last", "getOrElse", "orNull", "next",
})


def _generic_element(generic: "tree_sitter.Node", source: bytes) -> "tree_sitter.Node | None":
    """The type node a container's element is, or ``None`` for any other type."""
    base = next((c for c in generic.children
                 if c.type in ("type_identifier", "stable_type_identifier")), None)
    targs = find_child_by_type(generic, "type_arguments")
    if base is None or targs is None:  # pragma: no cover - a generic type has both
        return None
    container = node_text(base, source).rsplit(".", 1)[-1]
    args = [c for c in targs.children if c.is_named]
    if container not in _SCALA_ELEMENT_CONTAINERS:
        return None
    if container == "Either":
        return args[-1] if len(args) == 2 else None
    return args[0] if len(args) == 1 else None


def _declared_element(node: "tree_sitter.Node | None", source: bytes) -> "str | None":
    """The element type a declaration's type states (``xs: Seq[File]`` -> ``File``).

    ``node`` is the declaration (its first type child is read, as
    :func:`_declared_type_name` reads it) or a type node itself. Refused like an
    inferred type (:func:`_registrable_type`): the element of ``Seq[String]``
    can qualify to nothing, and only invites the short-name mis-bind.
    """
    if node is None:
        return None
    if node.type not in _SCALA_TYPE_NODES:
        node = next((c for c in node.children if c.type in _SCALA_TYPE_NODES), None)
    if node is None or node.type != "generic_type":
        return None
    element = _generic_element(node, source)
    if element is None:
        return None
    return _registrable_type(_type_node_name(element, source), node, source)


def _pattern_binds(pattern: "tree_sitter.Node", name: str, source: bytes) -> "_Binder | None":
    """The binder a PATTERN makes for ``name``, or ``None`` when it binds none.

    A lowercase identifier in a Scala pattern is a binder; a capitalised one is a
    stable identifier the pattern COMPARES against (``case Foo =>``), so it binds
    nothing. ``case q: FileWriter`` (a ``typed_pattern``) declares the type;
    every other binder -- ``case Some(x)``, ``case h :: t``, a tuple -- binds a
    name whose type the pattern does not state.
    """
    if pattern.type == "identifier":
        text = node_text(pattern, source)
        return _Binder() if text == name and not text[:1].isupper() else None
    if pattern.type == "typed_pattern":
        bound = find_child_by_type(pattern, "identifier")
        if bound is not None and node_text(bound, source) == name:
            return _Binder(declared=pattern)
    for child in pattern.children:
        if child.type in _SCALA_TYPE_NODES:
            continue
        found = _pattern_binds(child, name, source)
        if found is not None:
            return found
    return None


class _Binder(NamedTuple):
    """What the binder of a name says about its value (WI-gokop).

    At most one field is set; none set is a binder of UNKNOWN type, which still
    shadows every same-named binding outside it.
    """

    #: A declaration whose own children carry the declared type: a parameter, a
    #: class parameter, a typed lambda binding, a ``typed_pattern``.
    declared: "tree_sitter.Node | None" = None
    #: A ``val`` / ``var`` definition: annotation, ``new``, else its initialiser.
    value_def: "tree_sitter.Node | None" = None
    #: An expression whose TYPE the name takes (``for { y = expr }``).
    expr: "tree_sitter.Node | None" = None
    #: A container expression whose ELEMENT the name takes: a ``for`` generator,
    #: or the receiver of an element-passing call (``xs.map(x => ..)``).
    element_of: "tree_sitter.Node | None" = None


def _element_passing_receiver(
    call_arg: "tree_sitter.Node", source: bytes,
) -> "tree_sitter.Node | None":
    """The receiver whose elements ``call_arg`` -- a function argument -- is passed.

    ``call_arg`` sits under ``arguments`` (``xs.map(x => ..)``) or is itself the
    block / case block argument (``xs.map { x => .. }``). The call must be
    ``recv.m`` with ``m`` in :data:`_SCALA_ELEMENT_PASSING`.
    """
    holder = call_arg.parent
    if holder is not None and holder.type == "arguments":
        holder = holder.parent
    elif holder is not None and holder.type == "block":
        holder = holder.parent
    if holder is None or holder.type != "call_expression":
        return None
    function = holder.child_by_field_name("function")
    if function is None or function.type != "field_expression":
        return None
    field = function.child_by_field_name("field")
    if field is None or node_text(field, source) not in _SCALA_ELEMENT_PASSING:
        return None
    return function.child_by_field_name("value")


class _ScalaReceiverTyper:
    """The type of a receiver EXPRESSION, for one file of Pass 2 (WI-gokop).

    Before this, ``scala.py`` typed a receiver from one file-wide ``var_types``
    map filled by DECLARATIONS only, which left every inferred binding untyped:
    ``val x = mk()``, ``mk().foo()``, ``a.b.foo()``, ``this.foo()``,
    ``for (y <- ys)``, ``xs.map(z => z.foo())``.

    TWO SOURCES, IN THIS ORDER.

    1. A LEXICAL walk from the expression outwards finds the NEAREST binder of
       a name (:class:`_Binder`): a lambda parameter, a ``case`` pattern, a
       ``for`` enumerator, a parameter, a class parameter, or a ``val`` /
       ``var`` of an enclosing block or template. When that binder's type can
       be said, it is the answer, and it beats the file-wide map. The walk
       stops at the nearest binder either way, so a typed ``val`` OUTSIDE a
       lambda never types the lambda's same-named parameter.
    2. Otherwise the file-wide map answers, exactly as before this class
       existed. That is a measured choice, not an oversight. Letting an untyped
       binder SHADOW the map (the stricter reading) dropped 264 hints on sbt
       and 236 on lila, and a sample of them read against the source was
       mostly right by naming convention: ``{ s => s.get(k) }`` where every
       ``s`` in sbt is a ``State``, ``threads.forEach { thread => .. }``. The
       map's file-wide leak (ADR-0006 "Scope Handling") is pre-existing and
       is neither widened nor narrowed here: an INFERRED binding is never
       written to it.

    TYPES. An INFERRED binding takes the type of its right-hand side, through the
    return-type registry the base analyzer aggregates from every file's
    ``method_return_types``. A call looks its callee up as ``<Owner>.<name>``:
    the receiver's type for ``r.m()``, the enclosing types (and an anonymous
    class's parent) for a bare ``m()``, then a top-level ``m``; each owner's
    base classes are walked when it does not declare the member. A case-class
    ``Foo(..)`` with no registered ``apply`` is a ``Foo``;
    ``x.asInstanceOf[T]`` is a ``T``.

    ELEMENTS, the same walk one level down. A container's element type comes from
    a declaration (``xs: Seq[File]``) or from the element registry the analyzer
    fills beside the return-type one (``def files: Seq[File]``), and is carried
    through the calls that keep it (``xs.filter(..)``, ``.toList``). It types a
    ``for`` generator's binder, the parameter of a one-parameter lambda or
    ``case`` passed to an element-passing call (``map``, ``foreach``, ...), a
    placeholder ``_`` there, and a projection (``opt.get``, ``xs.head``). That is
    where a wrapper is unwrapped -- never for the wrapper itself.

    WHAT THE REGISTRY HOLDS: a type as its DECLARING file names it, qualified
    through that file's imports -- a library type and an imported project type
    alike. The qualified path is KEPT, because it is the only thing that tells
    two same-named project types apart: 74 lila files declare a class ``Env``,
    and ``env.tournament.version(id)`` bound to ``lila.challenge.Env.version``
    until the call site checked the bind against ``lila.tournament.Env``
    (:func:`_import_names_elsewhere`, the WI-tipoh check). The simple name is
    derived where a symbol is looked up (:meth:`symbol_type_name`).

    KNOWN IMPRECISION, shared with every registry in the tree: keys are simple
    owner names, first writer wins, so two same-named types in different
    packages share one entry; a companion object and its class share one
    namespace. Implicit conversions, extension methods and type aliases are not
    resolved.
    """

    def __init__(
        self,
        source: bytes,
        registry: "dict[str, str]",
        elements: "dict[str, str]",
        var_types: "dict[str, str]",
        global_symbols: "dict[str, Symbol]",
        import_aliases: "dict[str, str]",
        file_packages: "dict[str, _ScalaFile] | None",
        project_packages: "frozenset[str]",
    ) -> None:
        self._source = source
        self._registry = registry
        self._elements = elements
        self._var_types = var_types
        self._global_symbols = global_symbols
        self._import_aliases = import_aliases
        self._file_packages = file_packages
        self._project_packages = project_packages
        self._scopes: "dict[int, dict[str, list[tree_sitter.Node]]]" = {}
        self._memo: "dict[tuple[int, bool], str | None]" = {}
        self._active: "set[tuple[int, bool]]" = set()

    # -- names ---------------------------------------------------------------

    def receiver_type(self, name: str, at: "tree_sitter.Node") -> "str | None":
        """The type of the value ``name`` denotes at ``at``."""
        if name == "this":
            return self._this_type(at)
        binder = self._lexical(name, at)
        found = self._binder_type(binder) if binder is not None else None
        return found if found is not None else self._var_types.get(name)

    def _binder_type(self, binder: _Binder) -> "str | None":
        if binder.declared is not None:
            return _declared_type_name(binder.declared, self._source)
        if binder.value_def is not None:
            return self._value_def(binder.value_def, element=False)
        if binder.expr is not None:
            return self.expr_type(binder.expr)
        if binder.element_of is not None:
            return self.element_type(binder.element_of)
        return None

    def _binder_element(self, binder: _Binder) -> "str | None":
        if binder.declared is not None:
            return _declared_element(binder.declared, self._source)
        if binder.value_def is not None:
            return self._value_def(binder.value_def, element=True)
        if binder.expr is not None:
            return self.element_type(binder.expr)
        return None  # an element's own element is not tracked

    def _lexical(self, name: str, at: "tree_sitter.Node") -> "_Binder | None":
        cur = at.parent
        while cur is not None:
            kind = cur.type
            hit: "_Binder | None" = None
            if kind == "lambda_expression":
                hit = self._lambda_binds(cur, name)
            elif kind == "case_clause":
                hit = self._case_binds(cur, name)
            elif kind == "for_expression":
                hit = self._for_binds(cur, name, at)
            elif kind in ("function_definition", "function_declaration"):
                hit = self._params_bind(cur, "parameters", "parameter", name)
            elif kind == "class_definition":
                hit = self._params_bind(cur, "class_parameters", "class_parameter", name)
            if hit is not None:
                return hit
            if kind in _SCALA_BLOCK_SCOPES or kind in _SCALA_TEMPLATE_SCOPES:
                found = self._scope_value_def(cur, name, at)
                if found is not None:
                    pattern = found.child_by_field_name("pattern")
                    # A destructuring pattern states no per-name type.
                    if pattern is not None and pattern.type == "identifier":
                        return _Binder(value_def=found)
                    return _Binder()
            cur = cur.parent
        return None

    def _lambda_binds(self, lam: "tree_sitter.Node", name: str) -> "_Binder | None":
        # The parameters are what precedes ``=>``; ``x => x`` must not read the
        # BODY ``x`` as a parameter. Only a ONE-parameter lambda takes an element.
        params: "list[tuple[str, tree_sitter.Node | None]]" = []
        for child in lam.children:
            if child.type == "=>":
                break
            if child.type == "identifier":
                params.append((node_text(child, self._source), None))
            elif child.type == "bindings":
                for bound in child.children:
                    ident = find_child_by_type(bound, "identifier")
                    if ident is not None:
                        params.append((node_text(ident, self._source), bound))
        for pname, binding in params:
            if pname != name:
                continue
            if binding is not None and any(c.type in _SCALA_TYPE_NODES for c in binding.children):
                return _Binder(declared=binding)
            if len(params) == 1:
                return _Binder(element_of=_element_passing_receiver(lam, self._source))
            return _Binder()
        return None

    def _case_binds(self, clause: "tree_sitter.Node", name: str) -> "_Binder | None":
        pattern = clause.child_by_field_name("pattern")
        hit = _pattern_binds(pattern, name, self._source) if pattern is not None else None
        if hit is None or hit != _Binder() or pattern is None or pattern.type != "identifier":
            return hit
        # ``xs.foreach { case x => .. }``: a WHOLE-pattern binder in a case block
        # passed to an element-passing call takes the element. A Scala 3
        # ``indented_cases`` / ``match`` is no element-passing call.
        block = clause.parent
        if block is None or block.type != "case_block":
            return hit
        return _Binder(element_of=_element_passing_receiver(block, self._source))

    def _for_binds(
        self, loop: "tree_sitter.Node", name: str, at: "tree_sitter.Node",
    ) -> "_Binder | None":
        # Enumerators bind in order, each visible to the ones after it and to the
        # body. A generator ``y <- ys`` binds an ELEMENT of ``ys``; a value
        # definition ``y = expr`` binds ``expr``'s type.
        hit: "_Binder | None" = None
        enumerators = find_child_by_type(loop, "enumerators")
        for enum in enumerators.children if enumerators is not None else ():
            if enum.type != "enumerator":
                continue
            if enum.start_byte <= at.start_byte < enum.end_byte:
                break
            named = [c for c in enum.children if c.is_named and c.type != "guard"]
            if not named:  # pragma: no cover - an enumerator always binds a pattern
                continue
            found = _pattern_binds(named[0], name, self._source)
            if found is None:
                continue
            hit = found
            if found == _Binder() and named[0].type == "identifier" and len(named) > 1:
                generator = any(c.type == "<-" for c in enum.children)
                hit = (_Binder(element_of=named[-1]) if generator
                       else _Binder(expr=named[-1]))
        return hit

    def _params_bind(
        self, owner: "tree_sitter.Node", list_type: str, param_type: str, name: str,
    ) -> "_Binder | None":
        for plist in owner.children:
            if plist.type != list_type:
                continue
            for param in plist.children:
                if param.type != param_type:
                    continue
                ident = find_child_by_type(param, "identifier")
                if ident is not None and node_text(ident, self._source) == name:
                    return _Binder(declared=param)
        return None

    def _scope_value_def(
        self, scope: "tree_sitter.Node", name: str, at: "tree_sitter.Node",
    ) -> "tree_sitter.Node | None":
        index = self._scopes.get(scope.id)
        if index is None:
            index = {}
            for child in scope.children:
                if child.type not in _SCALA_VALUE_DEFS:
                    continue
                pattern = child.child_by_field_name("pattern")
                for bound in _pattern_names(pattern, self._source) if pattern is not None else ():
                    index.setdefault(bound, []).append(child)
            self._scopes[scope.id] = index
        before_only = scope.type in _SCALA_BLOCK_SCOPES
        chosen: "tree_sitter.Node | None" = None
        for defn in index.get(name, ()):
            if defn.start_byte <= at.start_byte < defn.end_byte:
                continue  # its own initialiser
            if before_only and defn.end_byte > at.start_byte:
                break
            chosen = defn
            if not before_only:
                break
        return chosen

    def _value_def(self, defn: "tree_sitter.Node", *, element: bool) -> "str | None":
        """A ``val`` / ``var``'s type (or element type), memoised, cycle-safe."""
        key = (defn.id, element)
        if key in self._memo:
            return self._memo[key]
        if key in self._active:
            return None  # ``val a = b; val b = a``
        self._active.add(key)
        try:
            inst = find_child_by_type(defn, "instance_expression")
            if element:
                found = _declared_element(inst if inst is not None else defn, self._source)
            else:
                found = _declared_type_name(inst if inst is not None else defn, self._source)
            if found is None and inst is None and defn.child_by_field_name("type") is None:
                value = defn.child_by_field_name("value")
                if value is not None:
                    found = self.element_type(value) if element else self.expr_type(value)
        finally:
            self._active.discard(key)
        self._memo[key] = found
        return found

    def _this_type(self, at: "tree_sitter.Node") -> "str | None":
        cur = at.parent
        while cur is not None:
            if cur.type in _SCALA_OWNER_DEFS:
                ident = find_child_by_type(cur, "identifier")
                return node_text(ident, self._source) if ident is not None else None
            if cur.type in ("enum_definition", "given_definition") or (
                cur.type == "instance_expression"
                and find_child_by_type(cur, "template_body") is not None
            ):
                return None  # ``this`` is an enum, a given or an anonymous class
            cur = cur.parent
        return None

    # -- expressions -------------------------------------------------------------

    def expr_type(self, expr: "tree_sitter.Node") -> "str | None":
        """The type of a receiver expression, or ``None`` when it cannot be said."""
        kind = expr.type
        if kind == "identifier":
            return self.receiver_type(node_text(expr, self._source), expr)
        if kind == "wildcard":
            # A placeholder ``_`` used as a receiver is the parameter of the
            # function argument it sits in (``xs.map(_.foo())``).
            arg = self._placeholder_argument(expr)
            receiver = _element_passing_receiver(arg, self._source) if arg is not None else None
            return self.element_type(receiver) if receiver is not None else None
        if kind == "parenthesized_expression":
            inner = next((c for c in expr.children if c.is_named), None)
            return self.expr_type(inner) if inner is not None else None
        if kind == "instance_expression":
            return _declared_type_name(expr, self._source)
        if kind == "generic_function":
            return self._cast_type(expr)
        if kind in ("field_expression", "call_expression"):
            target = self._member_access(expr)
            if target is None:
                return None
            receiver, member = target
            if receiver is None:
                return self._bare_call_type(expr, member, element=False)
            owner = self._owner(receiver)
            found = self._member(owner, member, self._registry) if owner is not None else None
            if found is None and member in _SCALA_ELEMENT_PROJECTING:
                found = self.element_type(receiver)
            return found
        return None

    def element_type(self, expr: "tree_sitter.Node") -> "str | None":
        """The element type of a container expression, or ``None``."""
        kind = expr.type
        if kind == "identifier":
            name = node_text(expr, self._source)
            binder = self._lexical(name, expr) if name != "this" else None
            return self._binder_element(binder) if binder is not None else None
        if kind == "parenthesized_expression":
            inner = next((c for c in expr.children if c.is_named), None)
            return self.element_type(inner) if inner is not None else None
        if kind in ("field_expression", "call_expression"):
            target = self._member_access(expr)
            if target is None:
                return None
            receiver, member = target
            if receiver is None:
                return self._bare_call_type(expr, member, element=True)
            owner = self._owner(receiver)
            found = self._member(owner, member, self._elements) if owner is not None else None
            if found is None and member in _SCALA_ELEMENT_PRESERVING:
                found = self.element_type(receiver)
            return found
        return None

    def _member_access(
        self, expr: "tree_sitter.Node",
    ) -> "tuple[tree_sitter.Node | None, str] | None":
        """``(receiver, member)`` for ``r.m`` / ``r.m(..)``, ``(None, f)`` for a
        bare ``f(..)``, else ``None``. A type argument (``f[T](..)``) is skipped."""
        function: "tree_sitter.Node | None" = expr
        if expr.type == "call_expression":
            function = expr.child_by_field_name("function")
            if function is not None and function.type == "generic_function":
                function = function.child_by_field_name("function")
            if function is not None and function.type == "identifier":
                return None, node_text(function, self._source)
        if function is None or function.type != "field_expression":
            return None
        value = function.child_by_field_name("value")
        field = function.child_by_field_name("field")
        if value is None or field is None:  # pragma: no cover - the grammar sets both
            return None
        return value, node_text(field, self._source)

    def _placeholder_argument(self, wildcard: "tree_sitter.Node") -> "tree_sitter.Node | None":
        """The function argument a placeholder ``_`` receiver expands in.

        Up through the chain it is the receiver of (``_.a().b``) to the first
        node that is an argument; anything else ends the walk.
        """
        cur = wildcard
        while cur.parent is not None:
            parent = cur.parent
            if parent.type in ("arguments", "block"):
                return cur
            receiver_of = (
                parent.child_by_field_name("value") if parent.type == "field_expression"
                else parent.child_by_field_name("function")
                if parent.type in ("call_expression", "generic_function") else None)
            if receiver_of != cur:
                return None
            cur = parent
        return None  # pragma: no cover - a placeholder always sits in an expression

    def _cast_type(self, generic: "tree_sitter.Node") -> "str | None":
        function = generic.child_by_field_name("function")
        targs = generic.child_by_field_name("type_arguments")
        if (
            function is not None and targs is not None
            and function.type == "field_expression"
            and (field := function.child_by_field_name("field")) is not None
            and node_text(field, self._source) == "asInstanceOf"
        ):
            return _declared_type_name(targs, self._source)
        return None

    def _bare_call_type(
        self, call: "tree_sitter.Node", name: str, *, element: bool,
    ) -> "str | None":
        registry = self._elements if element else self._registry
        if self._lexical(name, call) is not None or name in self._var_types:
            return None  # a call of a VALUE (a function, an ``apply``)
        for owner in self._enclosing_owners(call):
            found = self._member(owner, name, registry)
            if found is not None:
                return found
        top = registry.get(name)
        if top is not None:
            return top
        # An implicitly-imported name (``Map(..)``, ``List(..)``) is the
        # standard library's, whatever the project calls a type: measured on
        # sbt, ``Map(pairs*)`` read as the project's ``SessionVar.Map`` bound two
        # ``.get`` calls to it -- the collider WI-pokam measured, reached a new
        # way.
        if name[:1].isupper() and name not in _SCALA_IMPLICIT_IMPORT_TYPES:
            found = self._member(name, "apply", registry)
            if found is not None or element:
                return found
            sym = self._global_symbols.get(name)
            if sym is not None and sym.kind == "class" and _is_project_type(
                name, self._global_symbols, self._import_aliases,
                self._file_packages, self._project_packages,
            ):
                return name  # a case class's synthesised ``apply``
        return None

    def _owner(self, value: "tree_sitter.Node") -> "str | None":
        """The type a member access on ``value`` looks in: its value's type, or an
        object / type named bare when no binder in scope takes the name."""
        typed = self.expr_type(value)
        if typed is not None:
            return typed
        if value.type == "identifier":
            name = node_text(value, self._source)
            if (
                name[:1].isupper() and name not in _SCALA_IMPLICIT_IMPORT_TYPES
                and self._lexical(name, value) is None and name not in self._var_types
            ):
                return name
        return None

    def _enclosing_owners(self, at: "tree_sitter.Node") -> "list[str]":
        owners: list[str] = []
        cur = at.parent
        while cur is not None:
            if cur.type in _SCALA_OWNER_DEFS:
                ident = find_child_by_type(cur, "identifier")
                if ident is not None:
                    owners.append(node_text(ident, self._source))
            elif cur.type == "instance_expression" and find_child_by_type(
                cur, "template_body",
            ) is not None:
                parent = _declared_type_name(cur, self._source)
                if parent is not None:
                    owners.append(parent)
            cur = cur.parent
        return owners

    def _member(self, owner: str, member: str, registry: "dict[str, str]") -> "str | None":
        """``owner.member``'s registered entry, through ``owner``'s base classes."""
        queue = [owner.rsplit(".", 1)[-1]]
        seen: set[str] = set()
        while queue and len(seen) < 32:
            cls = queue.pop(0)
            if cls in seen:
                continue
            seen.add(cls)
            found = registry.get(f"{cls}.{member}")
            if found is not None:
                return found
            sym = self._global_symbols.get(cls)
            queue.extend((sym.meta or {}).get("base_classes", []) if sym is not None else [])
        return None

    def symbol_type_name(self, type_name: str) -> str:
        """The name a type's MEMBERS are registered and resolved under: a
        qualified PROJECT type by its simple name, anything else unchanged.

        A project symbol is named ``<Type>.<member>`` with the type's simple name,
        and the ``inherited_calls`` linker looks a ``receiver_type_hint`` up by
        it; the qualified path still decides WHICH same-named type a bind may
        land on (see the call site) and fills the module slot.
        """
        if "." not in type_name:
            return type_name
        simple = type_name.rsplit(".", 1)[1]
        if not self._is_project_type_name(simple):
            return type_name
        # A project PACKAGE path (``lila.user.UserApi``), or a type member of a
        # project type or object (``NetworkClient.Arguments``, sbt: the declared
        # return of ``parseArgs``). Either way the leaf is the type.
        if _import_names_project_path(type_name, self._project_packages) or (
            self._is_project_type_name(type_name.split(".", 1)[0])
        ):
            return simple
        return type_name

    def _is_project_type_name(self, name: str) -> bool:
        sym = self._global_symbols.get(name)
        return sym is not None and sym.kind in ("class", "object", "trait")


def _pattern_names(pattern: "tree_sitter.Node", source: bytes) -> "list[str]":
    """Every name a value-definition PATTERN binds (``val (a, b) = ..`` binds two)."""
    if pattern.type == "identifier":
        return [node_text(pattern, source)]
    names: list[str] = []
    for child in pattern.children:
        if child.type not in _SCALA_TYPE_NODES:
            names.extend(_pattern_names(child, source))
    return names


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    import_aliases: dict[str, str],
    file_symbols: "list[Symbol] | None" = None,
    file_packages: "dict[str, _ScalaFile] | None" = None,
    project_packages: "frozenset[str]" = frozenset(),
    method_return_type_registry: "dict[str, str] | None" = None,
    element_registry: "dict[str, str] | None" = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Types a method call's receiver to disambiguate ``x.bar()``: from declared
    types (parameters, annotations, ``new``) and, through
    ``method_return_type_registry``, from inferred ones -- a call result, a
    chained receiver, a parameterless member, ``this`` (WI-gokop,
    :class:`_ScalaReceiverTyper`).

    ``file_packages`` maps each analysed file to its package and the types it
    declares (:class:`_ScalaFile`). It lets a symbol
    the resolver returns be checked against the file's explicit import, which
    outranks a same-named project symbol in another package (WI-tipoh).
    ``project_packages`` (:func:`_package_readings`) says whether an import names
    a project path at all.
    """
    _caller_path = str(file_path)
    # Every declaration of this file, by position (INV-midag): ``local_symbols``
    # keeps ONE symbol per name.
    decl_index = symbols_at(
        file_symbols if file_symbols is not None
        else list({s.id: s for s in local_symbols.values()}.values()))
    edges: list[Edge] = []
    file_id = make_file_id("scala", str(file_path))
    file_anchor = file_anchor_symbol("scala", str(file_path), PASS_ID, run_id)
    var_types: dict[str, str] = {}
    _scoped_imports = _block_scoped_imports(tree.root_node, source)
    typer = _ScalaReceiverTyper(
        source, method_return_type_registry or {}, element_registry or {}, var_types,
        global_symbols, import_aliases, file_packages, project_packages,
    )

    def _applies(name: str, at: "tree_sitter.Node") -> bool:
        """Whether the file's import of ``name`` is in force at ``at``: always
        for a top-level import; for a block-scoped one, only inside its block
        and after it."""
        spans = _scoped_imports.get(name)
        return spans is None or any(lo <= at.start_byte < hi for lo, hi in spans)

    for node in iter_tree(tree.root_node):
        if node.type == "import_declaration":
            identifiers = [child for child in node.children if child.type == "identifier"]
            if identifiers:
                import_path = ".".join(node_text(id_node, source) for id_node in identifiers)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"scala:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Track param types from function definitions
        elif node.type in ("function_definition", "function_declaration"):
            param_types = _extract_param_types_scala(node, source)
            for pname, ptype in param_types.items():
                var_types[pname] = ptype

        # Track val receiver types: `val repo = new UserRepository()`
        # (constructor) and `val repo: UserRepository = f()` (annotation).
        # INV-fahub / WI-bihit: threading the annotation-typed val — previously
        # dropped — lets a receiver typed only by annotation resolve via the
        # type-qualified path instead of misbinding to an arbitrary same-named
        # def (recall recovery for the receiver gate below).
        elif node.type == "val_definition":
            var_node = find_child_by_type(node, "identifier")
            if var_node:
                inst_node = find_child_by_type(node, "instance_expression")
                # WI-pokam: BOTH paths through ``_declared_type_name``, because a
                # qualified or generic type was invisible to BOTH. The `new`
                # branch is listed in WI-pokam among the shapes that work; it
                # loses ``new java.io.File("p")`` exactly as the annotation
                # branch loses ``val b: java.io.File``.
                type_name = _declared_type_name(
                    inst_node if inst_node is not None else node, source,
                )
                if type_name is not None:
                    var_types[node_text(var_node, source)] = type_name

        # Track class-constructor parameter types: `class C(val svc: Service)`.
        # A constructor-param receiver (`svc.process()`) is typed and must
        # resolve, not misbind (INV-fahub / WI-bihit recall recovery). The
        # `class_parameter` node is visited before the class body's calls
        # (pre-order DFS), so var_types is populated in time.
        elif node.type == "class_parameter":
            pname_node = find_child_by_type(node, "identifier")
            ptype_name = _declared_type_name(node, source)
            if pname_node is not None and ptype_name is not None:
                var_types[node_text(pname_node, source)] = ptype_name

        elif node.type == "call_expression":
            # INV-bamij: a call in no function is anchored on the template that
            # holds it (an ``object``'s ``val`` initialiser), else the file.
            current_function: Optional[Symbol] = (
                _get_enclosing_function(node, source, decl_index)
                or enclosing_declared_symbol(node, decl_index, _TYPE_BODY_NODES)
                or file_anchor
            )
            if current_function is not None:
                # INV-fahub Site-1: the enclosing class short name for a bare /
                # implicit-``this`` call, so a deferred bare→method call can be
                # recovered by the inherited_calls MRO walker when the method is
                # on the enclosing class's linearization (inherited), and left
                # external when it is a cross-class magnet. ``None`` for a
                # top-level def (no owning class → not an implicit-``this`` call).
                enclosing_type = (
                    current_function.name.split(".")[-2]
                    if "." in current_function.name else None
                )
                callee_node = find_child_by_type(node, "identifier")
                receiver_name = None
                # INV-pirot: DOES THIS CALL HAVE A RECEIVER, separately from
                # whether the receiver can be NAMED. A ``field_expression``
                # callee is ``<something>.<name>`` -- a method call by
                # construction -- but only a bare-identifier receiver yields a
                # ``receiver_name``. ``new File(x).createNewFile()``,
                # ``get().createNewFile()`` and
                # ``o.asInstanceOf[File].createNewFile()`` all land in the
                # one-identifier arm below, where the receiver is real and
                # nameless. Asking ``receiver_name`` "was there a receiver"
                # answered "no" for all three (LIVE.md rule 7: one variable,
                # two questions).
                has_receiver = False
                inline_path: "str | None" = None
                receiver_type: "str | None" = None
                if not callee_node:
                    field_node = find_child_by_type(node, "field_expression")
                    if field_node:
                        has_receiver = True
                        ids = [c for c in field_node.children if c.type == "identifier"]
                        if len(ids) >= 2:
                            receiver_name = node_text(ids[0], source)
                            callee_node = ids[-1]
                            receiver_type = typer.receiver_type(receiver_name, node)
                        elif ids:
                            # The receiver is an EXPRESSION, so it contributed
                            # no identifier of its own and the single id is the
                            # method. Marked ``defensive`` and no-cover until
                            # 2026-08-25; it is in fact the production path for
                            # every complex-receiver call in Scala.
                            callee_node = ids[0]
                            _value = field_node.child_by_field_name("value")
                            if _value is not None:
                                inline_path = _inline_path(_value, source)
                                # WI-gokop: a call result, a member, a cast.
                                receiver_type = typer.expr_type(_value)

                if callee_node:
                    callee_name = node_text(callee_node, source)

                    # Type-qualified resolution: receiver.method() → Type.method
                    edge_added = False
                    if receiver_type:
                        # WI-gokop: a QUALIFIED project type (from the registry,
                        # or written inline) looks its member up by the simple
                        # name, and the qualified path must not contradict the
                        # symbol found -- the same WI-tipoh check an explicit
                        # import gets. Without it a same-named type in another
                        # package took the bind (lila: 74 ``Env`` classes).
                        type_name = typer.symbol_type_name(receiver_type)
                        stated = receiver_type if type_name != receiver_type else None
                        qualified = f"{type_name}.{callee_name}"
                        target = local_symbols.get(qualified)
                        if target is None:
                            lookup = resolver.lookup(
                                qualified,
                                path_hint=import_aliases.get(type_name),
                                caller_path=_caller_path,
                            )
                            if lookup.found and lookup.symbol is not None and not (
                                stated is None and _applies(type_name, node)
                                and _import_names_elsewhere(
                                    lookup.symbol, import_aliases.get(type_name), file_packages,
                                    import_aliases)
                            ):
                                target = lookup.symbol
                        if target is not None and stated is not None and _import_names_elsewhere(
                            target, stated, file_packages, import_aliases,
                        ):
                            target = None
                        if target is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                            edge_added = True

                    if not edge_added and has_receiver:
                        # INV-fahub (WI-bihit): a method call `recv.m()` whose
                        # receiver type could not be resolved in-file MUST NOT
                        # fall through to the bare short-name binds below and
                        # confidently bind to an arbitrary same-named internal
                        # def (the copy/setTo @0.68 funnel). Emit an honest
                        # unresolved external edge instead, mirroring py.py's
                        # unknown-receiver branch: `calls` / external-unresolved
                        # dst / `is_resolved=False` / `evidence_type="ast_call"`
                        # (→ 0.40) / `call_construct="method"`. When the
                        # receiver's TYPE is known (its method just wasn't found
                        # here), stamp `receiver_type_hint` so the shared
                        # inherited_calls linker can recover the edge (Site-2
                        # Step-1); an untyped/duck receiver gets no hint (bias to
                        # unresolved). The linker is the sole minter of the
                        # resolved edge (INV-nilud; taint-safe by construction).
                        # INV-pirot widened the guard above from "the
                        # receiver has a NAME" to "there is a receiver", so a
                        # nameless receiver now reaches this branch too. That is
                        # the branch's own stated purpose -- an unresolvable
                        # receiver MUST NOT fall through to the bare short-name
                        # binds below and bind an arbitrary same-named internal
                        # def -- and it is MORE true of a nameless receiver, not
                        # less: ``new Untyped(x).createNewFile()`` cannot be a
                        # call on the enclosing class under any reading.
                        gate_meta: dict = {"call_construct": "method"}
                        if receiver_type:
                            gate_meta["receiver_type_hint"] = typer.symbol_type_name(receiver_type)
                        # WI-sigog / INV-linub L3: the receiver TYPE this branch
                        # has just inferred must also reach the MODULE SLOT, not
                        # only ``meta``. The two slots answer different questions
                        # and both need an answer: ``receiver_type_hint`` is read
                        # by the Tier-2 ``inherited_calls`` linker, which resolves
                        # PROJECT-INTERNAL symbols, while the module segment of
                        # the dst is what ``_lookup_named_entry`` /
                        # ``gate_named_entry`` read on the EXTERNAL surface.
                        # Hardcoding ``external`` here left the second one
                        # permanently unanswered, and that is fatal rather than
                        # lossy: the F3 gate opens with
                        # ``if call_construct == "method": return None``, so with
                        # no module hint a method call matches NOTHING — not a
                        # method-kind entry, not even a function-kind one.
                        # Measured 2026-09-09 on sbt + lila: 0 of 40,970 external
                        # method-call edges carried a receiver type and 0 of 164
                        # method-kind catalogue rows were reached through Scala's
                        # own edges. Scala emitted every one of those calls and
                        # stamped ``call_construct`` correctly; only the slot was
                        # a constant.
                        #
                        # THIS IS VERBATIM THE JAVA DEFECT PR #227 FIXED, one
                        # language over (:func:`java._qualify_receiver_type`);
                        # java measures 65.0% typed on killbill since. The
                        # discipline that PR established is kept here: the slot
                        # only ever carries a path THE FILE ITSELF DECLARES. An
                        # unqualifiable type is left alone rather than written in
                        # bare, because a simple name in the module slot asserts a
                        # module that does not exist and can collide with a
                        # catalogued entry of the same short name (INV-fazim).
                        # A project class needs no special case: it reaches
                        # ``import_aliases`` only when the file imports it by
                        # path, and that path IS its static owner path
                        # (ADR-0050/0051); a bare one misses and keeps the
                        # sentinel.
                        #
                        # ``call_construct="method"`` above is NOT decoration and
                        # must not be dropped now that the slot is filled.
                        # ``_register_sanitizer_callers`` refuses an unresolved
                        # call unless it carries receiver evidence and reads that
                        # evidence from THIS slot -- its docstring notes the
                        # placeholder "still yields no module, and is still
                        # refused" -- so filling it is precisely what makes
                        # barrier registration reachable for Scala for the first
                        # time. A registered barrier earns ``sanitized`` and DROPS
                        # the flow (#214), so the flag is what stops a first-party
                        # ``doFinal`` from binding ``javax.crypto.Cipher.doFinal``
                        # and deleting a real one.
                        #
                        # WILDCARDS ARE OUT OF SCOPE HERE, NOT OVERLOOKED.
                        # ``import java.io._`` parses to ``namespace_wildcard``,
                        # which ``_extract_import_hints`` does not record as a
                        # type hint, so such a receiver keeps the sentinel. Java
                        # writes the comma-joined disjunction of its wildcard
                        # packages into the slot; that is not free, because an
                        # UNENUMERATED disjunct withholds every verdict under
                        # INV-zimud's ALL-gate. Measured on the same corpus, the
                        # shape is also rare in Scala -- 199/4,314 import lines in
                        # sbt (4.6%) and 5/6,674 in lila (0.07%) -- so the
                        # explicit-import path below carries the population.
                        typed_module = (
                            _qualify_scala_receiver(receiver_type, import_aliases)
                            if receiver_type
                            # WI-kilap: an untyped receiver spelled as a TYPE
                            # (``Files.readAllBytes(p)``, ``System.getenv(k)``,
                            # ``Properties.envOrElse(...)``) is a static / object
                            # call, and its owner is the module. Only the file's
                            # import or java.lang's closed list names one, so
                            # WI-sigog's "a path the file declares" still holds.
                            else static_owner_module(
                                receiver_name or "", import_aliases,
                                shadowed=SCALA_SHADOWED_JAVA_LANG,
                                is_project_type=_is_project_type(
                                    receiver_name or "", global_symbols,
                                    import_aliases if _applies(receiver_name or "", node) else None,
                                    file_packages, project_packages),
                            )
                        )
                        if typed_module is None and inline_path is not None:
                            typed_module = _inline_qualified_owner(
                                inline_path, callee_name,
                                bound=set(var_types) | set(import_aliases),
                            )
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=(
                                f"scala:{typed_module or 'external'}"
                                f":0-0:{callee_name}:unresolved"
                            ),
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            is_resolved=False,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            dst_ref=(
                                ExternalRef(
                                    lang="scala",
                                    module_path=typed_module,
                                    name=callee_name,
                                )
                                if typed_module else None
                            ),
                            meta=gate_meta,
                        ))
                    elif not edge_added and callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        # INV-fahub: a bare same-file hit binds directly only to a
                        # same-enclosing-class method (implicit ``this``) or a
                        # non-method (free def / object); a DIFFERENT class's
                        # method is a magnet — defer to the inherited_calls Site-1
                        # walker (shared ``defer_bare_method_call`` decision;
                        # "suffix" flags the weak short-name evidence of a bare
                        # ``local_symbols`` hit).
                        if defer_bare_method_call(
                            callee.kind, callee.name, "suffix", enclosing_type,
                        ):
                            edges.append(make_unresolved_edge(
                                "scala", current_function.id, callee_name,
                                node.start_point[0] + 1, PASS_ID, run_id,
                                enclosing_class=enclosing_type,
                            ))
                        else:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                    elif not edge_added:
                        path_hint = import_aliases.get(callee_name)
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint, caller_path=_caller_path)
                        # WI-jusus: a call must never resolve to a field/variable
                        # — the resolver's suffix index now contains the newly
                        # emitted field/variable symbols, and a same-short-name
                        # field would otherwise become a confidently-wrong call
                        # target (a call-graph corruption). Fall through to the
                        # honest unresolved edge instead.
                        # INV-fahub (real-repro re-scope 2026-07-18, WI-bihit
                        # reopened): the DOMINANT Scala funnel is a BARE call —
                        # implicit-``this`` (case-class ``copy``) or a chained
                        # receiver whose receiver token was dropped — that
                        # suffix-matches an unrelated class's ``method`` @0.68
                        # (magnet: dozens of files → one arbitrary
                        # ``FileCopyTask.copy`` / ``ColumnOps.setTo`` / ``.map``).
                        # A class-member method needs a receiver/scope; a weak
                        # short-name *suffix* guess is not resolution evidence, so
                        # withhold it → honest unresolved edge (INV-nogof
                        # withhold-not-pick-first). Exact / path-hint matches and
                        # free-function / object targets are unaffected.
                        _sym = lookup_result.symbol
                        _defer = _sym is not None and defer_bare_method_call(
                            _sym.kind, _sym.name,
                            lookup_result.match_type, enclosing_type,
                        )
                        if (
                            lookup_result.found
                            and _sym is not None
                            and _sym.kind not in ("field", "variable")
                            and not _defer
                            # WI-tipoh: the file's explicit import outranks a
                            # same-named project symbol in another package.
                            and not (_applies(callee_name, node) and _import_names_elsewhere(
                                _sym, path_hint, file_packages, import_aliases))
                        ):
                            conf = 0.80 * lookup_result.confidence * _short_name_penalty(callee_name)
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                confidence=conf,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                        else:
                            # INV-fahub: stamp the enclosing class so the
                            # inherited_calls Site-1 walker can recover a bare
                            # *inherited* implicit-``this`` call (the ~30% solo
                            # tail of the withheld suffix-method magnet), while a
                            # true cross-class magnet stays external (its method
                            # is not on the enclosing class's MRO).
                            edges.append(make_unresolved_edge(
                                "scala", current_function.id, callee_name,
                                node.start_point[0] + 1, PASS_ID, run_id,
                                module_hint=path_hint or "external",
                                dst_ref=(
                                    ExternalRef(lang="scala", module_path=path_hint, name=callee_name)
                                    if path_hint else None
                                ),
                                enclosing_class=enclosing_type,
                            ))

        # Scala eta-expansion: ``transform _`` produces a postfix_expression
        # whose second child is identifier("_").  This is a first-class
        # reference to the function, not a call.
        elif node.type == "postfix_expression":
            children = node.named_children
            if (
                len(children) == 2
                and children[1].type == "identifier"
                and node_text(children[1], source) == "_"
                and children[0].type == "identifier"
            ):
                ref_name = node_text(children[0], source)
                current_function = _get_enclosing_function(
                    node, source, decl_index,
                )
                if current_function is not None:
                    target = local_symbols.get(ref_name)
                    if target is None:  # pragma: no cover — cross-file
                        lookup = resolver.lookup(ref_name, caller_path=_caller_path)
                        if lookup.found and lookup.symbol is not None:
                            target = lookup.symbol
                    if (
                        target is not None
                        and target.kind in ("function", "method")
                        and target.id != current_function.id
                    ):
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            evidence_type="eta_expansion",
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

    return edges


class ScalaAnalyzer(TreeSitterAnalyzer):
    """Scala language analyzer using tree-sitter-scala."""

    lang = "scala"
    file_patterns: ClassVar[list[str]] = ["*.scala"]
    grammar_module = "tree_sitter_scala"
    #: Each analysed file's package, for this run only (WI-tipoh). Reset by
    #: :meth:`analyze`; ``None`` outside a run.
    _file_packages: "Optional[dict[str, _ScalaFile]]" = None
    #: :func:`_package_readings` of ``_file_packages``, built on the first file of
    #: Pass 2, when every package is known.
    _project_packages: "Optional[frozenset[str]]" = None
    #: ``<Owner>.<member>`` -> the element type of the container it returns, for
    #: this run only (WI-gokop). Reset by :meth:`analyze`.
    _element_types: "Optional[dict[str, str]]" = None

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, classes, objects, traits from a Scala file."""
        analysis = _extract_symbols_from_file(
            tree, source, rel_path, run.execution_id, element_types=self._element_types)
        if self._file_packages is not None:
            self._file_packages[rel_path] = _ScalaFile(
                _scala_file_package(tree.root_node, source),
                frozenset(s.name.split(".")[-1] for s in analysis.symbols
                          if s.kind in ("class", "object", "trait")),
            )
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Scala import hints for disambiguation."""
        return _extract_import_hints(tree, source)

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups.
        """
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a Scala file."""
        return _extract_edges_from_file(
            tree, source, rel_path,
            local_symbols, global_symbols,
            run.execution_id, resolver, import_aliases,
            file_symbols=self.file_symbols(local_symbols),
            file_packages=self._file_packages,
            project_packages=self._project_package_readings(),
            method_return_type_registry=self._method_return_type_registry,
            element_registry=self._element_types,
        )

    def _project_package_readings(self) -> "frozenset[str]":
        if self._project_packages is None:
            self._project_packages = _package_readings(self._file_packages or {})
        return self._project_packages

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """The base two-pass analysis, with the per-run package map reset first.

        ``_analyzer`` is a module singleton, so a map kept from an earlier run
        would answer for a same-named path in this one.
        """
        self._file_packages = {}
        self._project_packages = None
        self._element_types = {}
        return super().analyze(repo_root, max_files)


_analyzer = ScalaAnalyzer()


def is_scala_tree_sitter_available() -> bool:
    """Check if tree-sitter with Scala grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("scala")
def analyze_scala(repo_root: Path) -> AnalysisResult:
    """Analyze Scala files in a repository."""
    return _analyzer.analyze(repo_root)
