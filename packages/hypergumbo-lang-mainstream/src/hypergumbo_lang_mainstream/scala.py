"""Scala analysis pass using tree-sitter-scala.

This analyzer uses tree-sitter to parse Scala files and extract:
- Function definitions (def)
- Class definitions (class)
- Object definitions (object)
- Trait definitions (trait)
- Method definitions (inside classes/objects/traits)
- Function call relationships
- Import statements

If tree-sitter with Scala support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, classes, objects, traits with signatures
2. Pass 2: Extract call edges and import edges using NameResolver

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
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("scala")


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

    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                func_name = node_text(name_node, source)
                enclosing_type = _get_enclosing_type(node, source)
                if enclosing_type:
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
                ) if norm_sig else None

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
                )
                analysis.symbols.append(symbol)
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
                )
                analysis.symbols.append(symbol)
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
                meta: dict[str, object] | None = {}
                if base_classes:
                    meta["base_classes"] = base_classes
                if annotations:
                    meta["decorators"] = annotations
                if not meta:
                    meta = None

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
                    meta=meta,
                    modifiers=_extract_modifiers_scala(node),
                )
                analysis.symbols.append(symbol)
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
                analysis.symbol_by_name[type_name] = symbol

    return analysis


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
    """Extract call and import edges from a file."""
    edges: list[Edge] = []
    file_id = make_file_id("scala", str(file_path))

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
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                callee_node = find_child_by_type(node, "identifier")
                if not callee_node:
                    field_node = find_child_by_type(node, "field_expression")
                    if field_node:
                        ids = [c for c in field_node.children if c.type == "identifier"]
                        if len(ids) >= 2:
                            callee_node = ids[-1]
                        elif ids:  # pragma: no cover - defensive
                            callee_node = ids[0]

                if callee_node:
                    callee_name = node_text(callee_node, source)

                    if callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="function_call",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))
                    else:
                        path_hint = import_aliases.get(callee_name)
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run_id,
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
                        lookup = resolver.lookup(ref_name)
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
                            confidence=0.85,
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
        """Register symbol globally, including short name for cross-file resolution."""
        global_symbols[symbol.name] = symbol
        short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
        global_symbols[short_name] = symbol

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
