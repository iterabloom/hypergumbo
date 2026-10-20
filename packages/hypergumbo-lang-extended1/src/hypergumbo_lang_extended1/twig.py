# SPDX-License-Identifier: AGPL-3.0-or-later
"""Twig template analyzer using tree-sitter.

Twig is a modern template engine for PHP, used extensively in Symfony
and other PHP frameworks. Understanding Twig structure helps with
template architecture and component relationships.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1 (``extract_symbols_from_file``): Extract blocks, macros and
   control structures as symbols
2. Pass 2 (``extract_edges_from_file``): Re-walk the AST to emit the
   template inheritance and composition edges (``extends`` / ``includes``);
   they need no cross-file resolution, so the walk is repeated rather than
   resolved against a registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Twig-specific extraction
logic.

Symbols Extracted
-----------------
- **Blocks**: Block definitions ({% block name %})
- **Macros**: Reusable template functions ({% macro name() %})
- **For loops**: Iteration structures ({% for item in items %})
- **Conditionals**: ``{% if a %}`` and ``{% elseif b %}`` (named ``if a`` /
  ``elseif b``); ``{% else %}`` tests nothing and is not a symbol
- **Call sites**: each ``{{ func() }}`` output call other than ``include()``
  becomes a symbol of kind ``call_site`` (signature ``{{ func() }}``, meta
  ``arg_count`` and ``call_kind="function"``); no call edge is emitted

Edges Extracted
---------------
- **extends**: template file -> ``twig:template:<name>`` for
  {% extends "base.twig" %}
- **includes**: template file -> ``twig:template:<name>`` for
  {% include "partial.twig" %} and {{ include() }}; the function form
  (template name taken from its first argument) is marked
  ``meta.form="function"``

Spans and identity
------------------
tree-sitter-twig attaches the whitespace before a directive to its opening
``{%`` / ``{{`` token, so a directive's node starts where the previous one
ended; ``_directive_span`` starts every symbol (and the line of every edge)
at the delimiter itself. Before the WI-tusad scheme bump a macro on line 2
reported line 1. Each symbol's stable_id (``make_doc_symbol_ids``) hashes the
chain of ``block`` / ``macro`` / ``for`` / ``if`` directives open around it
(``_Scopes``: the grammar keeps a directive's body as its siblings, so the walk
tracks open and end tags itself), so the same ``{{ greet(user) }}`` in two
blocks is two identities and deleting one moves nothing; two identical ones in
one scope are a same-scope tie the per-file split pass orders (ADR-0035 §1).

Why This Design
---------------
- Twig is the dominant PHP template engine
- Block/extends patterns reveal template hierarchy
- Include patterns show template composition
- Macros indicate reusable template logic
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_doc_symbol_ids,
    make_file_id,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("twig")


def find_twig_files(repo_root: Path) -> list[Path]:
    """Find all Twig template files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    return sorted(_analyzer._find_source_files(repo_root))


def is_twig_tree_sitter_available() -> bool:
    """Check if tree-sitter-twig is available."""
    return _analyzer._check_grammar_available()



def _directive_span(node: "tree_sitter.Node") -> Span:
    """The span of a ``{% ... %}`` / ``{{ ... }}`` directive, from its delimiter.

    tree-sitter-twig folds the whitespace BEFORE a directive into its opening
    ``{%`` / ``{{`` token, so the directive node starts where the previous one
    ended: a macro on line 2 reported line 1, and every directive opening a
    line reported the line above. The real start is where the delimiter text
    begins, on the token's last row (the delimiter holds no newline).
    """
    start_row, start_col = node.start_point
    opener = node.children[0] if node.children else None
    if opener is not None and opener.text:
        text = opener.text
        delimiter = text.lstrip()
        if len(delimiter) < len(text):
            start_row = opener.end_point[0]
            start_col = opener.end_point[1] - len(delimiter)
    return Span(
        start_line=start_row + 1,
        start_col=start_col,
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )


@dataclass
class _Scopes:
    """The open ``block`` / ``macro`` / ``for`` / ``if`` directives, outermost
    first: the scope chain each symbol's stable_id hashes (WI-tusad).

    The grammar does not nest a directive's body under it (``{% block a %}``,
    its content and ``{% endblock %}`` are siblings), so the walk keeps the
    chain itself: an opening tag pushes ``(kind, label)``, its end tag pops
    back to the nearest open one of that kind (a stray end tag pops nothing).
    ``elseif`` / ``else`` stay inside the ``if`` / ``for`` they continue.
    """

    open: list[tuple[str, str]] = field(default_factory=list)

    def chain(self) -> list[str]:
        """The labels of the open directives, outermost first."""
        return [label for _, label in self.open]

    def push(self, kind: str, label: str) -> None:
        """Open a directive of ``kind``."""
        self.open.append((kind, label))

    def close(self, kind: str) -> None:
        """Close the innermost open directive of ``kind``."""
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == kind:
                del self.open[index:]
                return


#: End tag -> the directive kind it closes.
_END_TAGS = {"endblock": "block", "endmacro": "macro", "endfor": "for", "endif": "if"}


def _create_extends_edges(
    rel_path: str, node: "tree_sitter.Node",
    template_name: str, run_id: str,
) -> list[Edge]:
    """Create edge for extends statement.

    Cluster E sub-case (b) per audit-findings 0010: per-extends Symbol
    was redundant. Edge src is the using template file id; dst is the
    parent template's dangling id (handled by IR boundary materialization).
    """
    line = _directive_span(node).start_line
    edges: list[Edge] = []

    if template_name:
        edge = Edge.create(
            src=make_file_id("twig", str(rel_path)),
            dst=f"twig:template:{template_name}",
            edge_type="extends",
            line=line,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="extends",
            meta={"template": template_name, "ref_construct": "template"},
        )
        edges.append(edge)

    return edges


def _create_block_symbol(
    rel_path: str, node: "tree_sitter.Node", block_name: str, scope: list[str],
) -> Symbol:
    """Create a symbol for block definition."""
    span = _directive_span(node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah). node.id is
    # byte-identical to the old f"twig:{path}:{kind}:{line}:{name}" form.
    symbol_id, stable_id = make_doc_symbol_ids(
        "twig", str(rel_path), "block", block_name,
        span.start_line, span.end_line, scope=scope,
    )

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=block_name,
        kind="block",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% block {block_name} %}}",
        meta={},
    )


def _create_include_edges(
    rel_path: str, node: "tree_sitter.Node",
    template_name: str, run_id: str,
) -> list[Edge]:
    """Create edge for {% include %} statement.

    Cluster E sub-case (b) per audit-findings 0010: per-include Symbol
    was redundant. Edge src is the including template file id; dst is
    the included template's dangling id (handled by IR boundary
    materialization).
    """
    line = _directive_span(node).start_line
    edges: list[Edge] = []

    if template_name:
        edge = Edge.create(
            src=make_file_id("twig", str(rel_path)),
            dst=f"twig:template:{template_name}",
            edge_type="includes",
            line=line,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="include",
            meta={"template": template_name, "ref_construct": "template"},
        )
        edges.append(edge)

    return edges


def _create_macro_symbol(
    rel_path: str, node: "tree_sitter.Node", macro_name: str, scope: list[str],
) -> Symbol:
    """Create a symbol for macro definition."""
    span = _directive_span(node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah). node.id is
    # byte-identical to the old f"twig:{path}:{kind}:{line}:{name}" form.
    symbol_id, stable_id = make_doc_symbol_ids(
        "twig", str(rel_path), "macro", macro_name,
        span.start_line, span.end_line, scope=scope,
    )

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=macro_name,
        kind="macro",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% macro {macro_name}() %}}",
        meta={},
    )


def _create_include_function_edges(
    rel_path: str, node: "tree_sitter.Node",
    template_name: str, run_id: str,
) -> list[Edge]:
    """Create edge for {{ include() }} function call.

    Cluster E sub-case (b) per audit-findings 0010: per-include Symbol
    was redundant. Edge src is the including template file id; dst is
    the included template's dangling id.
    """
    line = _directive_span(node).start_line
    edge = Edge.create(
        src=make_file_id("twig", str(rel_path)),
        dst=f"twig:template:{template_name}",
        edge_type="includes",
        line=line,
        origin=PASS_ID,
        origin_run_id=run_id,
        evidence_type="include",
        meta={"template": template_name, "form": "function", "ref_construct": "template"},
    )
    return [edge]


def _extract_tag_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge], scopes: _Scopes,
) -> None:
    """Extract tag statements like extends, block, include, and the end tags.

    A block's name is the FIRST variable after ``block``. Anything after it
    is the shorthand ``{% block title "Hi" %}`` / ``{% block t page_title %}``,
    which has no ``endblock`` and so opens no scope (the last argument was
    read as the name until the WI-tusad scheme bump: ``Hi``, ``page_title``).
    """
    tag_name = ""
    arg_value = ""
    block_name = ""
    arguments = 0

    for child in node.children:
        if child.type in ("tag", "repeat", "conditional"):
            tag_name = _get_node_text(child)
        elif child.type == "variable":
            arg_value = _get_node_text(child)
            block_name = block_name or arg_value
            arguments += 1
        elif child.type in ("interpolated_string", "string"):
            arguments += 1
            text = _get_node_text(child)
            if text.startswith('"') and text.endswith('"'):
                arg_value = text[1:-1]
            elif text.startswith("'") and text.endswith("'"):
                arg_value = text[1:-1]
            else:  # pragma: no cover
                arg_value = text

    if tag_name == "extends":
        edges_out.extend(_create_extends_edges(rel_path, parent_node, arg_value, run_id))
    elif tag_name == "block" and block_name:
        symbols_out.append(_create_block_symbol(rel_path, parent_node, block_name, scopes.chain()))
        if arguments == 1:
            scopes.push("block", f"block {block_name}")
    elif tag_name == "include":
        edges_out.extend(_create_include_edges(rel_path, parent_node, arg_value, run_id))
    elif tag_name in _END_TAGS:
        scopes.close(_END_TAGS[tag_name])


def _extract_macro_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, symbols_out: list[Symbol], scopes: _Scopes,
) -> None:
    """Extract macro definition from macro_statement node."""
    macro_name = ""

    for child in node.children:
        if child.type == "method":
            macro_name = _get_node_text(child)

    if macro_name:
        sym = _create_macro_symbol(rel_path, parent_node, macro_name, scopes.chain())
        symbols_out.append(sym)
        scopes.push("macro", f"macro {macro_name}")


def _extract_for_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, symbols_out: list[Symbol], scopes: _Scopes,
) -> None:
    """Extract for loop statement."""
    loop_var = ""
    iterable = ""

    for child in node.children:
        if child.type == "variable":
            if not loop_var:
                loop_var = _get_node_text(child)
            else:
                iterable = _get_node_text(child)

    name = f"for {loop_var} in {iterable}" if iterable else f"for {loop_var}"
    span = _directive_span(parent_node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah). node.id is
    # byte-identical to the old f"twig:{path}:{kind}:{line}:{name}" form.
    symbol_id, stable_id = make_doc_symbol_ids(
        "twig", str(rel_path), "for_loop", name,
        span.start_line, span.end_line, scope=scopes.chain(),
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=name,
        kind="for_loop",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% {name} %}}",
        meta={"loop_variable": loop_var, "iterable": iterable},
    )
    symbols_out.append(symbol)
    scopes.push("for", name)


def _extract_if_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, symbols_out: list[Symbol], scopes: _Scopes,
) -> None:
    """Extract a conditional: ``if a`` and ``elseif b`` each yield one, named by
    their own keyword; only ``if`` opens a scope. ``else`` tests nothing and
    yields none: it was emitted as a symbol named ``"if "``, and ``elseif b``
    as ``if b``, indistinguishable from an ``if``."""
    condition = ""
    keyword = ""

    for child in node.children:
        if child.type == "conditional":
            keyword = _get_node_text(child)
        elif child.type == "variable":
            condition = _get_node_text(child)
            break

    if keyword == "else":
        return
    name = f"{keyword} {condition}"
    span = _directive_span(parent_node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah). node.id is
    # byte-identical to the old f"twig:{path}:{kind}:{line}:{name}" form.
    symbol_id, stable_id = make_doc_symbol_ids(
        "twig", str(rel_path), "conditional", name,
        span.start_line, span.end_line, scope=scopes.chain(),
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=name,
        kind="conditional",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% {name} %}}",
        meta={"condition": condition},
    )
    symbols_out.append(symbol)
    if keyword == "if":
        scopes.push("if", name)


def _extract_statement(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge], scopes: _Scopes,
) -> None:
    """Extract statement directives ({% ... %})."""
    for child in node.children:
        if child.type == "tag_statement":
            _extract_tag_statement(child, node, rel_path, run_id, symbols_out, edges_out, scopes)
        elif child.type == "macro_statement":
            _extract_macro_statement(child, node, rel_path, symbols_out, scopes)
        elif child.type == "for_statement":
            _extract_for_statement(child, node, rel_path, symbols_out, scopes)
        elif child.type == "if_statement":
            _extract_if_statement(child, node, rel_path, symbols_out, scopes)


def _extract_function_call(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge], scopes: _Scopes,
) -> None:
    """Extract function calls in output directives."""
    func_name = ""
    args: list[str] = []

    for child in node.children:
        if child.type == "function_identifier":
            func_name = _get_node_text(child)
        elif child.type == "arguments":
            for arg_child in child.children:
                if arg_child.type == "argument":
                    args.append(_get_node_text(arg_child))

    if not func_name:
        return  # pragma: no cover

    # Handle include() function calls
    if func_name == "include" and args:
        template_name = args[0].strip("'\"")
        edges_out.extend(_create_include_function_edges(
            rel_path, parent_node, template_name, run_id,
        ))
        return

    span = _directive_span(parent_node)
    # Both ids minted together by make_doc_symbol_ids (INV-dulah). node.id is
    # byte-identical to the old f"twig:{path}:{kind}:{line}:{name}" form.
    symbol_id, stable_id = make_doc_symbol_ids(
        "twig", str(rel_path), "function_call", func_name,
        span.start_line, span.end_line, scope=scopes.chain(),
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=func_name,
        kind="call_site",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{{{ {func_name}() }}}}",
        meta={"arg_count": len(args), "call_kind": "function"},
    )
    symbols_out.append(symbol)


def _extract_output(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge], scopes: _Scopes,
) -> None:
    """Extract output directives ({{ ... }}) - specifically function calls."""
    for child in node.children:
        if child.type == "function_call":
            _extract_function_call(child, node, rel_path, run_id, symbols_out, edges_out, scopes)


def _extract_twig_symbols(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge], scopes: _Scopes,
) -> None:
    """Extract symbols from a syntax tree node, in document order (the order
    ``scopes`` needs to see opening and end tags)."""
    if node.type == "statement_directive":
        _extract_statement(node, rel_path, run_id, symbols_out, edges_out, scopes)
    elif node.type == "output_directive":
        _extract_output(node, rel_path, run_id, symbols_out, edges_out, scopes)

    for child in node.children:
        _extract_twig_symbols(child, rel_path, run_id, symbols_out, edges_out, scopes)


class TwigAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Twig template files using TreeSitterAnalyzer base class."""

    lang = "twig"
    file_patterns: ClassVar[list[str]] = ["*.twig", "*.html.twig"]
    language_pack_name = "twig"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Twig template symbols and edges from a Twig file.

        Note: the shared walker also collects the ``extends`` / ``includes``
        edges here and serialises them into import_aliases under
        ``__edge_<i>__`` keys, but nothing reads those entries back; the
        emitted edges come from ``extract_edges_from_file`` re-walking the
        AST in Pass 2.
        """
        analysis = FileAnalysis()
        # Collect edges during symbol extraction; will be added in post_process
        edges: list[Edge] = []
        _extract_twig_symbols(
            tree.root_node, rel_path, run.execution_id,
            analysis.symbols, edges, _Scopes(),
        )
        # Encode edge count so we can retrieve edges later
        # Store edges in import_aliases using JSON-serializable representation
        for i, edge in enumerate(edges):
            analysis.import_aliases[f"__edge_{i}__"] = (
                f"{edge.src}|{edge.dst}|{edge.edge_type}|{edge.line}|"
                f"{edge.origin}|{edge.origin_run_id}|{edge.evidence_type}|{edge.confidence}"
            )
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Re-extract edges from Twig file during Pass 2.

        Since Twig edges don't need cross-file resolution, we simply
        re-extract them from the AST.
        """
        edges: list[Edge] = []
        symbols: list[Symbol] = []  # discarded
        _extract_twig_symbols(
            tree.root_node, rel_path, run.execution_id,
            symbols, edges, _Scopes(),
        )
        return edges


_analyzer = TwigAnalyzer()


@register_analyzer("twig", language_state="no_taxonomy_spec")  # WI-futin
def analyze_twig(repo_root: Path) -> AnalysisResult:
    """Analyze Twig template files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
