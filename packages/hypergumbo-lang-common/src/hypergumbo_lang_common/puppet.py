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
3. Pass 2 (``extract_edges_from_file``) emits include and resource
   relationship edges.

An include therefore binds to a class declared lower in the same file or in a
file that sorts later just as it does to one declared earlier: resolution
does not depend on declaration or file order (WI-vahun). It used to, because
one sequential walk both filled the class registry and read it, leaving a
forward include dangling with ``is_resolved=False``. A relationship edge
leaves the resource that declares it, found by its declaration's position
(``symbols_at``), not re-derived.

Symbols Extracted
-----------------
- **Classes**: Puppet class definitions
- **Defined types**: Custom resource types (define)
- **Resources**: Resource declarations (package, service, file, etc.)
- **Nodes**: Node definitions

Include statements emit only edges, not Symbols (per audit-findings
0010 Cluster E sub-case (b) fold).

Edges Extracted
---------------
- **includes**: Links the manifest file to the included class, wherever in
  the repository it is declared (``is_resolved=True``); a class declared
  nowhere gives a dangling ``puppet:class:<name>:1-1:<name>:class`` dst with
  ``is_resolved=False``. When a class name is declared twice, the last
  declaration (by file sort order, then position) is the target: the base
  registry is last-writer.
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
    iter_tree, make_doc_symbol_ids, make_file_id, symbol_declared_by, symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter

    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("puppet")

#: Definitions that open a NAMED scope, and the child naming each.
_NAMED_SCOPES = {
    "class_definition": ("identifier",),
    "defined_resource_type": ("class_identifier", "identifier"),
    "node_definition": ("node_name",),
}


def _scope_label(node: "tree_sitter.Node") -> Optional[str]:
    """The scope an ancestor opens: a class, defined type or node definition
    by its name (so its parameters and ``inherits`` stay out of the chain); a
    conditional branch (``if $x``, ``elsif``, ``else``, ``unless``, a case
    option) by what precedes its block."""
    name_types = _NAMED_SCOPES.get(node.type)
    if name_types is not None:
        for child in node.children:
            if child.type in name_types:
                return _get_node_text(child).strip("'\"")
        return None  # pragma: no cover - a definition always has a name
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
    """A class definition's symbol."""
    class_name = ""
    params: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            class_name = _get_node_text(child)
        elif child.type == "parameter_list":
            params = _extract_params(child)

    if not class_name:
        return None  # pragma: no cover

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
    type_name = ""
    params: list[str] = []

    for child in node.children:
        if child.type == "class_identifier":
            type_name = _get_node_text(child)
        elif child.type == "identifier" and not type_name:  # pragma: no cover
            type_name = _get_node_text(child)
        elif child.type == "parameter_list":
            params = _extract_params(child)

    if not type_name:
        return None  # pragma: no cover

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


def _include_edge(
    node: "tree_sitter.Node", rel_path: str,
    classes: dict[str, Symbol], run_id: str,
) -> Optional[Edge]:
    """The ``includes`` edge for an include statement, bound to the class of
    that name wherever in the repository it is declared."""
    class_name = ""

    for child in node.children:
        if child.type == "identifier":
            class_name = _get_node_text(child)
        elif child.type == "class_identifier":  # pragma: no cover
            class_name = _get_node_text(child)

    if not class_name:
        return None  # pragma: no cover

    # Cluster E sub-case (b) per audit-findings 0010: per-include Symbol
    # was redundant. Edge src is now the manifest file id; edge is
    # emitted unconditionally — when no class of that name exists, dst
    # is a 5-part dangling id that the IR's boundary materialization
    # handles (so unresolved-include cases keep their representation in
    # the graph instead of disappearing with the dropped Symbol).
    target = classes.get(class_name)
    return Edge.create(
        src=make_file_id("puppet", rel_path),
        dst=target.id if target is not None else f"puppet:class:{class_name}:1-1:{class_name}:class",
        edge_type="includes",
        line=node.start_point[0] + 1,
        origin=PASS_ID,
        origin_run_id=run_id,
        evidence_type="include",
        # ADR-0039: the include is certain, so it derives the base;
        # a target declared nowhere in the repository is unresolved (ADR-0037).
        is_resolved=target is not None,
        meta={"class_name": class_name, "ref_construct": "puppet_class"},
    )


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
            if node.type == "include_statement":
                edge = _include_edge(node, rel_path, global_symbols, run.execution_id)
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
