# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scala analysis pass using tree-sitter-scala.

This analyzer uses tree-sitter to parse Scala files and extract:
- Function definitions (def)
- Class definitions (class)
- Object definitions (object)
- Trait definitions (trait)
- Method definitions (inside classes/objects/traits)
- Secondary constructors (def this(...), kind=constructor)
- Function call relationships
- Import statements
- Annotations/decorators (into symbol meta["decorators"], for functions, methods, and classes)
- Inheritance: extends/with base classes and traits (into symbol meta["base_classes"], for classes and traits)

Modifiers (access/abstract/sealed/case) are captured on Symbol.modifiers,
and parameter/variable types are tracked to disambiguate type-qualified
method calls.

If tree-sitter with Scala support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, classes, objects, traits with signatures
2. Pass 2: Extract call edges, import edges, and eta-expansion references edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Scala-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-scala package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    make_variable_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("scala")


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
        if node.type != "import_declaration":
            continue

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
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


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
) -> FileAnalysis:
    """Extract symbols from a single Scala file."""
    analysis = FileAnalysis()
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
                    param_type = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = node_text(pc, source)
                        elif pc.type == "type_identifier" and param_type is None:
                            param_type = node_text(pc, source)
                    if param_name and param_type:
                        param_types[param_name] = param_type
    return param_types


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: "NameResolver",
    import_aliases: dict[str, str],
) -> list[Edge]:
    """Extract call and import edges from a file.

    Tracks variable types from function parameters and constructor assignments
    (``val x = new Foo()``) to disambiguate method calls like ``x.bar()``.
    """
    _caller_path = str(file_path)
    edges: list[Edge] = []
    file_id = make_file_id("scala", str(file_path))
    var_types: dict[str, str] = {}

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
                if inst_node is not None:
                    type_node = find_child_by_type(inst_node, "type_identifier")
                else:
                    # Annotated val: the type_identifier is a direct child
                    # (`val f: Foo = …` → val_definition > type_identifier).
                    type_node = find_child_by_type(node, "type_identifier")
                if type_node is not None:
                    var_types[node_text(var_node, source)] = node_text(
                        type_node, source,
                    )

        # Track class-constructor parameter types: `class C(val svc: Service)`.
        # A constructor-param receiver (`svc.process()`) is typed and must
        # resolve, not misbind (INV-fahub / WI-bihit recall recovery). The
        # `class_parameter` node is visited before the class body's calls
        # (pre-order DFS), so var_types is populated in time.
        elif node.type == "class_parameter":
            pname_node = find_child_by_type(node, "identifier")
            ptype_node = find_child_by_type(node, "type_identifier")
            if pname_node is not None and ptype_node is not None:
                var_types[node_text(pname_node, source)] = node_text(
                    ptype_node, source,
                )

        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
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
                if not callee_node:
                    field_node = find_child_by_type(node, "field_expression")
                    if field_node:
                        has_receiver = True
                        ids = [c for c in field_node.children if c.type == "identifier"]
                        if len(ids) >= 2:
                            receiver_name = node_text(ids[0], source)
                            callee_node = ids[-1]
                        elif ids:
                            # The receiver is an EXPRESSION, so it contributed
                            # no identifier of its own and the single id is the
                            # method. Marked ``defensive`` and no-cover until
                            # 2026-08-25; it is in fact the production path for
                            # every complex-receiver call in Scala.
                            callee_node = ids[0]

                if callee_node:
                    callee_name = node_text(callee_node, source)

                    # Type-qualified resolution: receiver.method() → Type.method
                    edge_added = False
                    if receiver_name and receiver_name in var_types:
                        type_name = var_types[receiver_name]
                        qualified = f"{type_name}.{callee_name}"
                        target = local_symbols.get(qualified)
                        if target is None:
                            lookup = resolver.lookup(
                                qualified,
                                path_hint=import_aliases.get(type_name),
                                caller_path=_caller_path,
                            )
                            if lookup.found and lookup.symbol is not None:
                                target = lookup.symbol
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
                        receiver_type = (
                            var_types.get(receiver_name) if receiver_name else None
                        )
                        if receiver_type:
                            gate_meta["receiver_type_hint"] = receiver_type
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=f"scala:external:0-0:{callee_name}:unresolved",
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            is_resolved=False,
                            origin=PASS_ID,
                            origin_run_id=run_id,
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
                    node, source, local_symbols,
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

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, classes, objects, traits from a Scala file."""
        return _extract_symbols_from_file(tree, source, rel_path, run.execution_id)

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
        )


_analyzer = ScalaAnalyzer()


def is_scala_tree_sitter_available() -> bool:
    """Check if tree-sitter with Scala grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("scala")
def analyze_scala(repo_root: Path) -> AnalysisResult:
    """Analyze Scala files in a repository."""
    return _analyzer.analyze(repo_root)
