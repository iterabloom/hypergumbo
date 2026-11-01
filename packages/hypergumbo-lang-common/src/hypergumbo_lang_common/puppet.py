# SPDX-License-Identifier: AGPL-3.0-or-later
"""Puppet manifest analyzer using tree-sitter.

Puppet is a configuration management tool that uses a declarative language
to describe system configuration. Understanding Puppet manifests helps with
infrastructure-as-code analysis and system configuration auditing.

How It Works
------------
The analyzer runs on ``TreeSitterAnalyzer``'s two-pass pipeline (the
tree-sitter-puppet grammar from tree-sitter-language-pack):

1. Pass 1 (``extract_symbols_from_file``) declares every manifest's classes,
   defined types, resources and nodes, and emits no edge.
2. The base registers the symbols of ALL files before any edge is made;
   ``register_symbol`` keeps only classes, the one thing an ``include`` binds.
3. Pass 2 (``extract_edges_from_file``) emits include, ``require``-function,
   ``inherits`` and resource relationship edges.

An include therefore binds to a class declared lower in the same file or in a
file that sorts later just as it does to one declared earlier: resolution
does not depend on declaration or file order (WI-vahun). It used to, because
one sequential walk both filled the class registry and read it, leaving a
forward include dangling with ``is_resolved=False``. A relationship edge
leaves the resource that declares it, found by its declaration's position
(``symbols_at``), not re-derived.

Names
-----
A class is named by its full ``::``-joined name (``class apache::params``
is ``apache::params``: the grammar spells it as one ``class_identifier``
over the segments), and a leading ``::`` is dropped, since ``::apache`` and
``apache`` name the same top-scope class. Every reference reads every name
the same way (``_class_names``): ``include a::b, c`` names both, wherever
in the list; ``include ::beta`` (whose ``::`` the grammar parses as an
ERROR before the name) names ``beta``; ``include "c"`` / ``'c'`` names
``c``. A ``$variable`` or an interpolated ``"${x}::y"`` names no class
statically and gets no edge (WI-hozot). The grammar mis-shapes two lists,
and both are read back: it ends a ``require`` statement at its first name
and leaves ``, b, c`` as following siblings, and it can fold
``beta, ::gamma`` into one ``class_identifier`` whose segments a comma
ERROR divides. Two constructs are out of reach because this grammar has no
node for them: ``contain x`` parses as two bare identifiers and the
resource-like ``class { 'x': }`` as an ERROR plus a hash, so neither gives
an edge; ``include [a, b]`` (an array argument) is an ERROR too.

Symbols Extracted
-----------------
- **Classes**: Puppet class definitions, namespaced ones included
- **Defined types**: Custom resource types (define)
- **Resources**: Resource declarations (package, service, file, etc.)
- **Nodes**: Node definitions

Include statements emit only edges, not Symbols (per audit-findings
0010 Cluster E sub-case (b) fold).

Edges Extracted
---------------
- **includes**: Links the manifest file to each class an ``include``
  (evidence_type ``include``) or the ``require`` function (``require
  delta``, evidence_type ``require``: it includes the class and orders it
  first) names, wherever in the repository it is declared
  (``is_resolved=True``); meta ``class_name`` and ref_construct
  ``puppet_class``. A class declared nowhere gives a dangling
  ``puppet:class:<name>:1-1:<name>:class`` dst with ``is_resolved=False``,
  minted by ``make_symbol_id`` so a namespaced name's ``::`` is folded to
  ``..`` in the name slot and the id still parses. When a class name is
  declared twice, the last declaration (by file sort order, then position)
  is the target: the base registry is last-writer.
- **inherits**: Links a class to the parent its ``inherits`` clause names
  (evidence_type ``ast_extends``, meta ``class_name``), bound like an
  include.
- **depends_on**: Resource relationships from ``require`` (evidence_type
  ``require``, meta ref_construct ``puppet_require``) and ``notify``
  (evidence_type ``notify``, meta ref_construct ``puppet_notify`` and
  ``refresh: True``) attributes

Identity
--------
Every symbol mints ``make_doc_symbol_ids`` with the scopes enclosing it: a
class, defined type or node definition by its name, a conditional branch
(``if $x``, ``elsif``, ``else``, ``unless``, a case option) by its prelude.
``file { '/tmp/x': }`` in ``class a`` and in ``class b`` are two identities,
and deleting the first class moves nothing (INV-punig; ADR-0035 §1, scheme
v9). A top-level declaration hashes no scope and keeps its value.

Why This Design
---------------
- Puppet is widely used for infrastructure management
- Class/define patterns show reusable configuration components
- Resource relationships reveal deployment dependencies
- Node definitions map infrastructure topology
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult, FileAnalysis, TreeSitterAnalyzer, ancestor_labels, block_prelude,
    iter_tree, make_doc_symbol_ids, make_file_id, make_symbol_id, symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter

    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("puppet")

def _class_names(node: "tree_sitter.Node") -> list[str]:
    """The class names a name node spells: none for one that names no class
    statically (a ``$variable``, an interpolated ``"${x}::y"``, ``''``), one
    for ``alpha``, ``apache::params`` or ``'alpha'``.

    The full ``::``-joined name is the name, and a leading ``::`` is dropped:
    ``::apache`` and ``apache`` name the same top-scope class. This grammar
    parses that leading ``::`` as an ERROR sibling before the name, so the
    name node itself never carries it; the strip covers a quoted
    ``'::apache'``. A namespaced name is rebuilt from its segments, because
    the grammar folds ``require ::a, beta, ::gamma``'s tail into ONE
    ``class_identifier`` whose segments a comma ERROR divides
    (``beta, ::gamma``): each comma ends a name.
    """
    if node.type == "class_identifier":
        groups: list[list[str]] = [[]]
        for child in node.children:
            if child.type == "identifier":
                groups[-1].append(_get_node_text(child))
            elif child.type == "ERROR" and any(c.type == "," for c in child.children):
                groups.append([])
        return ["::".join(segments) for segments in groups if segments]
    if node.type == "string":
        parts = [child for child in node.children if child.type not in ("'", '"')]
        if len(parts) != 1 or parts[0].type != "string_content":
            return []  # empty, or interpolated: no static class name
        text = _get_node_text(parts[0]).lstrip(":")
    elif node.type == "identifier":
        text = _get_node_text(node)
    else:
        return []
    return [text] if text else []


def _definition_name(node: "tree_sitter.Node") -> Optional[str]:
    """A class or defined type's own name: its first name child (so its
    parameters and ``inherits`` parent are never read as the name)."""
    for child in node.children:
        names = _class_names(child)
        if names:
            return names[0]
    return None  # pragma: no cover - a definition always has a name


def _node_name(node: "tree_sitter.Node") -> Optional[str]:
    """A node definition's name, its quotes stripped."""
    for child in node.children:
        if child.type == "node_name":
            return _get_node_text(child).strip("'\"")
    return None  # pragma: no cover - a node definition always has a name


#: Definitions that open a NAMED scope, and the reader of each one's name.
_NAMED_SCOPES = {
    "class_definition": _definition_name,
    "defined_resource_type": _definition_name,
    "node_definition": _node_name,
}


def _scope_label(node: "tree_sitter.Node") -> Optional[str]:
    """The scope an ancestor opens: a class, defined type or node definition
    by its name (so its parameters and ``inherits`` stay out of the chain); a
    conditional branch (``if $x``, ``elsif``, ``else``, ``unless``, a case
    option) by what precedes its block."""
    name_of = _NAMED_SCOPES.get(node.type)
    if name_of is not None:
        return name_of(node)
    return block_prelude(node)


def _scope(node: "tree_sitter.Node") -> list[str]:
    """The definitions and branches enclosing ``node``, outermost first."""
    return ancestor_labels(node, _scope_label)




def find_puppet_files(repo_root: Path) -> list[Path]:
    """Find all Puppet manifest files in the repository."""
    files: list[Path] = []
    files.extend(find_files(repo_root, ["*.pp"]))
    return sorted(set(files))



def _span(node: "tree_sitter.Node") -> Span:
    return Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )


def _extract_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a parameter list."""
    params: list[str] = []
    for child in node.children:
        if child.type == "parameter":
            for param_child in child.children:
                if param_child.type == "variable":
                    var_name = _get_node_text(param_child)
                    # Remove $ prefix for cleaner display
                    if var_name.startswith("$"):
                        var_name = var_name[1:]
                    params.append(var_name)
                    break
    return params


def _resource_parts(node: "tree_sitter.Node") -> tuple[str, str, dict[str, str]]:
    """A resource declaration's type, title and ``key => value`` attributes."""
    resource_type = ""
    resource_title = ""
    attributes: dict[str, str] = {}

    for child in node.children:
        if child.type == "identifier":
            resource_type = _get_node_text(child)
        elif child.type == "class_identifier":
            resource_type = _get_node_text(child)
        elif child.type == "string":
            if not resource_title:
                resource_title = _get_node_text(child).strip("'\"")
        elif child.type == "attribute":
            attr_text = _get_node_text(child)
            if "=>" in attr_text:
                parts = attr_text.split("=>", 1)
                key = parts[0].strip()
                value = parts[1].strip() if len(parts) > 1 else ""
                attributes[key] = value
    return resource_type, resource_title, attributes


def _class_symbol(node: "tree_sitter.Node", rel_path: str) -> Optional[Symbol]:
    """A class definition's symbol, named by its full ``::``-joined name."""
    class_name = _definition_name(node)
    if class_name is None:
        return None  # pragma: no cover - a definition always has a name
    params: list[str] = []
    for child in node.children:
        if child.type == "parameter_list":
            params = _extract_params(child)

    span = _span(node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah);
    # node.id stays byte-identical to the old raw composite.
    symbol_id, stable_id = make_doc_symbol_ids(
        "puppet", rel_path, "class", class_name, span.start_line, span.end_line,
        _scope(node),
    )

    param_str = ", ".join(params) if params else ""
    signature = f"class {class_name}({param_str})" if params else f"class {class_name}"

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=class_name,
        kind="class",
        language="puppet",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta={
            "params": params,
            "param_count": len(params),
        },
    )


def _defined_type_symbol(node: "tree_sitter.Node", rel_path: str) -> Optional[Symbol]:
    """A defined resource type's symbol."""
    type_name = _definition_name(node)
    if type_name is None:
        return None  # pragma: no cover - a definition always has a name
    params: list[str] = []
    for child in node.children:
        if child.type == "parameter_list":
            params = _extract_params(child)

    span = _span(node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah);
    # node.id stays byte-identical to the old raw composite.
    symbol_id, stable_id = make_doc_symbol_ids(
        "puppet", rel_path, "defined_type", type_name, span.start_line, span.end_line,
        _scope(node),
    )

    param_str = ", ".join(params) if params else ""
    signature = f"define {type_name}({param_str})" if params else f"define {type_name}"

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=type_name,
        kind="defined_type",
        language="puppet",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta={
            "params": params,
            "param_count": len(params),
        },
    )


def _resource_symbol(node: "tree_sitter.Node", rel_path: str) -> Optional[Symbol]:
    """A resource declaration's symbol."""
    resource_type, resource_title, attributes = _resource_parts(node)

    if not resource_type:
        return None  # pragma: no cover

    name = f"{resource_type}[{resource_title}]" if resource_title else resource_type
    span = _span(node)

    # Both ids minted together by make_doc_symbol_ids (INV-dulah);
    # node.id stays byte-identical to the old raw composite.
    symbol_id, stable_id = make_doc_symbol_ids(
        "puppet", rel_path, "resource", name, span.start_line, span.end_line,
        _scope(node),
    )

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=name,
        kind="resource",
        language="puppet",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        signature=f"{resource_type} {{ '{resource_title}': ... }}",
        meta={
            "resource_type": resource_type,
            "title": resource_title,
            "ensure": attributes.get("ensure", ""),
        },
    )


def _node_symbol(node: "tree_sitter.Node", rel_path: str) -> Optional[Symbol]:
    """A node definition's symbol."""
    node_name = ""

    for child in node.children:
        if child.type == "node_name":
            # Get the string content
            for name_child in child.children:
                if name_child.type == "string":
                    node_name = _get_node_text(name_child).strip("'\"")
                    break
            if not node_name:  # pragma: no cover
                node_name = _get_node_text(child).strip("'\"")

    if not node_name:
        return None  # pragma: no cover

    span = _span(node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah);
    # node.id stays byte-identical to the old raw composite.
    symbol_id, stable_id = make_doc_symbol_ids(
        "puppet", rel_path, "node", node_name, span.start_line, span.end_line,
        _scope(node),
    )

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=node_name,
        kind="node",
        language="puppet",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        signature=f"node '{node_name}'",
        meta={},
    )


#: Pass 1: the declaration node types and the builder of each one's symbol.
_SYMBOL_BUILDERS = {
    "class_definition": _class_symbol,
    "defined_resource_type": _defined_type_symbol,
    "resource_declaration": _resource_symbol,
    "node_definition": _node_symbol,
}

def _relationship_edges(
    node: "tree_sitter.Node", resource: Symbol, run_id: str,
) -> list[Edge]:
    """``depends_on`` edges for a resource's ``require`` / ``notify``
    attributes (``notify`` carries meta ``refresh=True``)."""
    _, _, attributes = _resource_parts(node)
    line = node.start_point[0] + 1
    edges: list[Edge] = []
    # The evidence type is the attribute's name. Each meta is a literal here,
    # not a shared table entry, so the meta-key coherence scan can read its
    # keys (a copied table value is a write it cannot enumerate).
    for attribute in ("require", "notify"):
        if attribute not in attributes:
            continue
        if attribute == "notify":
            meta: dict[str, object] = {
                "ref_construct": "puppet_notify", "refresh": True,
            }
        else:
            meta = {"ref_construct": "puppet_require"}
        edges.append(Edge.create(
            src=resource.id,
            # Raw reference text, e.g. ``Package['nginx']`` (WI-tuniz).
            dst=f"puppet:resource:{attributes[attribute]}",
            edge_type="depends_on",
            line=line,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type=attribute,
            meta=meta,
        ))
    return edges


def _named_classes(node: "tree_sitter.Node") -> list[str]:
    """Every class an ``include`` / ``require`` statement names, in order.

    ``include a::b, c`` keeps its whole list as children; a leading ``::``
    (``include ::beta, gamma``) is an ERROR child before its name, skipped
    here. The grammar ends a ``require`` statement at its FIRST name and
    leaves ``, b, c`` as following siblings (an ERROR holding the comma, and
    any ``::``, then the name), so for a ``require_statement`` those siblings
    are read too, for as long as the comma-separated list goes on.
    """
    names = [name for child in node.children for name in _class_names(child)]
    if node.type == "require_statement":
        sibling = node.next_sibling
        while (
            sibling is not None and sibling.type == "ERROR"
            and {c.type for c in sibling.children} in ({","}, {",", "::"})
            and sibling.next_sibling is not None
        ):
            more = _class_names(sibling.next_sibling)
            if not more:
                break
            names.extend(more)
            sibling = sibling.next_sibling.next_sibling
    return names


def _class_dst(class_name: str, classes: dict[str, Symbol]) -> tuple[str, bool]:
    """The dst a reference to ``class_name`` binds: the class of that name
    wherever in the repository it is declared, else a dangling class id.

    The dangling id is minted by ``make_symbol_id``, which folds the ``::``
    of a namespaced name out of the name slot (ADR-0036 Ruling 1) and keeps
    it verbatim in the path slot, so ``puppet:class:a::b:1-1:a..b:class``
    still parses right-anchored."""
    target = classes.get(class_name)
    if target is not None:
        return target.id, True
    return make_symbol_id("puppet", f"class:{class_name}", 1, 1, class_name, "class"), False


def _include_edges(
    node: "tree_sitter.Node", rel_path: str,
    classes: dict[str, Symbol], run_id: str,
) -> list[Edge]:
    """One ``includes`` edge per class an ``include`` statement or the
    ``require`` function names (evidence_type ``include`` / ``require``), each
    bound to the class of that name wherever in the repository it is declared."""
    evidence = "require" if node.type == "require_statement" else "include"
    edges: list[Edge] = []
    for class_name in _named_classes(node):
        # Cluster E sub-case (b) per audit-findings 0010: per-include Symbol
        # was redundant. Edge src is the manifest file id; the edge is
        # emitted unconditionally — when no class of that name exists, dst
        # is a dangling class id that the IR's boundary materialization
        # handles (so unresolved-include cases keep their representation in
        # the graph instead of disappearing with the dropped Symbol).
        dst, resolved = _class_dst(class_name, classes)
        edges.append(Edge.create(
            src=make_file_id("puppet", rel_path),
            dst=dst,
            edge_type="includes",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type=evidence,
            # ADR-0039: the include is certain, so it derives the base;
            # a target declared nowhere in the repository is unresolved (ADR-0037).
            is_resolved=resolved,
            meta={"class_name": class_name, "ref_construct": "puppet_class"},
        ))
    return edges


def _inherits_edge(
    node: "tree_sitter.Node", child: Symbol,
    classes: dict[str, Symbol], run_id: str,
) -> Optional[Edge]:
    """The ``inherits`` edge from a class to the parent its ``inherits``
    clause names, bound like an include."""
    for part in node.children:
        if part.type != "class_inherits":
            continue
        for name_node in part.children:
            parents = _class_names(name_node)
            if not parents:
                continue
            parent = parents[0]
            dst, resolved = _class_dst(parent, classes)
            return Edge.create(
                src=child.id,
                dst=dst,
                edge_type="inherits",
                line=part.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="ast_extends",
                is_resolved=resolved,
                meta={"class_name": parent},
            )
    return None


class PuppetAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based analyzer for Puppet manifest files.

    Extracts classes, defined types, resources, nodes, includes, and
    relationship edges on the base class's two-pass pipeline: Pass 1
    (``extract_symbols_from_file``) declares every file's symbols, the base
    registers them repo-wide (classes only, see ``register_symbol``), and
    Pass 2 (``extract_edges_from_file``) resolves against that registry.
    Uses language_pack_name for the puppet grammar.
    """

    lang = "puppet"
    file_patterns: ClassVar[list[str]] = ["**/*.pp"]
    language_pack_name = "puppet"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """The manifests, in the sorted order ``find_puppet_files`` gives."""
        yield from find_puppet_files(repo_root)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: AnalysisRun,
    ) -> FileAnalysis:
        """Pass 1: the classes, defined types, resources and nodes a file declares."""
        analysis = FileAnalysis()
        for node in iter_tree(tree.root_node):
            builder = _SYMBOL_BUILDERS.get(node.type)
            if builder is None:
                continue
            sym = builder(node, rel_path)
            if sym is not None:
                analysis.symbols.append(sym)
                analysis.symbol_by_name[sym.name] = sym
        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Register CLASSES only: the registry is what an ``include`` binds
        against, and a node or defined type sharing a class's name is not an
        include target."""
        if symbol.kind == "class":
            global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: AnalysisRun, import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Pass 2: include edges (against every class in the repository) and
        resource relationship edges (from the resource declaring them)."""
        declared = symbols_at(self.file_symbols(local_symbols))
        edges: list[Edge] = []
        for node in iter_tree(tree.root_node):
            if node.type in ("include_statement", "require_statement"):
                edges.extend(_include_edges(node, rel_path, global_symbols, run.execution_id))
            elif node.type == "class_definition":
                child = symbol_declared_by(node, declared)
                if child is not None:
                    edge = _inherits_edge(node, child, global_symbols, run.execution_id)
                    if edge is not None:
                        edges.append(edge)
            elif node.type == "resource_declaration":
                resource = symbol_declared_by(node, declared)
                if resource is not None:
                    edges.extend(_relationship_edges(node, resource, run.execution_id))
        return edges


_analyzer = PuppetAnalyzer()


def is_puppet_tree_sitter_available() -> bool:
    """Check if tree-sitter-puppet is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("puppet", language_state="no_taxonomy_spec")  # WI-futin
def analyze_puppet(repo_root: Path) -> AnalysisResult:
    """Analyze Puppet manifest files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
