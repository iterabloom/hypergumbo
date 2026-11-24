# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tcl language analyzer using tree-sitter.

This module provides static analysis for Tcl source code, extracting symbols
(procedures, namespaces) and edges (calls).

Tcl (Tool Command Language) is a dynamic scripting language commonly used for
rapid prototyping, scripted applications, GUIs, and testing. It's particularly
prevalent in EDA (Electronic Design Automation) tools.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for Tcl grammar
- Handles Tcl-specific constructs like proc, namespace eval, and command substitution
- stable_ids hash every enclosing ``namespace eval`` (INV-fabih); a proc's id
  name slot and ``meta.namespace`` carry every one too (``a::inner::go``,
  Tcl's own name), where they kept the innermost only (WI-tusad scheme bump).
  The hashed name stays ``inner::go`` beside the chain, so stable_ids did
  not move.
- Names (ADR-0036 Ruling 1, ADR-0032): ``Symbol.name`` is a proc's or
  namespace's last word, ``qualified_name`` its absolute name without the
  leading ``::`` (:func:`_declared_names`), and the id's name slot is the
  name. The slot used to carry the enclosing namespaces (``util..helper``)
  and, for ``proc ::top`` or ``namespace eval ::glob``, the ``::`` itself
  (``..top``), while the name held the word as declared. The stable_id's
  inputs (the word, the innermost namespace, the chain) are unchanged.
- The stable id hashes the kind the Symbol reports: a proc hashes
  ``function``, where it hashed the role word ``proc`` until stable-id scheme
  v19 (WI-lilab); a namespace already hashed ``namespace``.
- A call resolves the way Tcl resolves a command name
  (:func:`_resolve_callee`): ``::a::b`` from the global namespace, any other
  name first in the namespace the calling proc runs in (a command
  ``namespace import`` brought there included) and then in the global one.
  Each proc is registered under its absolute name for this
  (``TclAnalyzer.register_symbol``). The lookup used to be the call word in
  a registry keyed by each proc's word as declared, so ``util::helper``
  missed the ``proc helper`` of ``namespace eval util`` and ended on a
  stand-in, and a bare word reached whichever proc of that word was
  registered last. A name these rules do not find in the tree still falls
  back to that registry.

Key constructs extracted:
- procedure: proc name {args} {body}
- namespace: namespace eval name {body}
- command / command_substitution: a bare `command args` invocation or a bracketed `[command args]` substitution (both produce call edges)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.ir import Edge, NodeId, ExternalRef, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    ancestor_labels,
    make_symbol_id,
    make_unresolved_edge,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import nested_member_qualified_name
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("tcl")


def find_tcl_files(root: Path) -> Iterator[Path]:
    """Find all Tcl files in the given directory.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(root)



def _get_proc_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the procedure name from a procedure node."""
    for child in node.children:
        if child.type == "simple_word":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_proc_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a procedure's arguments."""
    params = []
    for child in node.children:
        if child.type == "arguments":
            for arg_child in child.children:
                if arg_child.type == "argument":
                    for inner in arg_child.children:
                        if inner.type == "simple_word":
                            params.append(_get_node_text(inner))
    return params


def _get_namespace_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the namespace name from a namespace node."""
    for child in node.children:
        if child.type == "word_list":
            found_eval = False
            for word_child in child.children:
                if word_child.type == "simple_word":
                    text = _get_node_text(word_child)
                    if text == "eval":
                        found_eval = True
                    elif found_eval:
                        return text
    return None  # pragma: no cover


def _namespace_path(node: "tree_sitter.Node") -> tuple[str, ...]:
    """Every ``namespace eval`` enclosing ``node``, outermost first: the
    namespace a proc's id and ``meta.namespace`` carry (``a::inner``)."""
    return tuple(ancestor_labels(
        node, lambda n: _get_namespace_name(n) if n.type == "namespace" else None,
    ))


def _count_namespace_procs(node: "tree_sitter.Node") -> int:
    """Count procedures in a namespace."""
    count = 0
    for child in node.children:
        if child.type == "word_list":
            for word_child in child.children:
                if word_child.type == "braced_word":
                    for inner in word_child.children:
                        if inner.type == "procedure":
                            count += 1
    return count


def _get_command_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the command name from a command or command_substitution node."""
    if node.type == "command_substitution":
        for child in node.children:
            if child.type == "command":
                return _get_command_name(child)
    elif node.type == "command":
        for child in node.children:
            if child.type == "simple_word":
                return _get_node_text(child)
    return None  # pragma: no cover


def _callee_word(word: Optional[str]) -> Optional[str]:
    """The command name a call names, or ``None`` when it names no proc.

    A leading ``::`` is the global namespace, so ``::baz`` is the proc
    ``baz`` and ``::foo::bar`` is ``foo::bar`` (both are written to reach
    past a namespace's own command of that name); the name is resolved,
    checked against the built-ins, and split for the stand-in without it. A
    word whose namespace part or proc part is then empty names no proc --
    ``::`` alone, or ``ns::`` -- and makes no edge: it would be an id with an
    empty path or name slot (``tcl::0-0::unresolved``, git-gui's
    ``regsub -all {::} ...``, whose braced literal the grammar parses as a
    command; WI-zolat).
    """
    if word is None:
        return None
    if word.startswith("::"):
        word = word[2:]
    if not word or word.endswith("::") or word.startswith("::"):
        return None
    return word


def _qualified(scope: str, word: str) -> str:
    """The absolute name, without the leading ``::``, of ``word`` read in the
    namespace ``scope`` (itself absolute, ``""`` for the global namespace).

    A word that opens with ``::`` is absolute already; any other is a child
    of ``scope``. ``scope`` may be a chain of ``namespace eval`` words joined
    by ``::`` in which a word was itself absolute (``a::::x`` for
    ``namespace eval ::x`` inside ``namespace eval a``): what follows the
    last such word is the name.
    """
    name = word if word.startswith("::") or not scope else f"{scope}::{word}"
    name = name.rsplit("::::", 1)[-1]
    return name[2:] if name.startswith("::") else name


def _declared_names(ns_path: tuple[str, ...], word: str) -> tuple[str, str]:
    """``(name, qualified_name)`` of a proc or namespace declared as ``word``
    inside the ``namespace eval`` words ``ns_path`` (outermost first).

    ``qualified_name`` is the absolute name without the leading ``::``
    (:func:`_qualified`: ``proc ::top`` is ``top``, ``proc helper`` in
    ``namespace eval ::glob`` is ``glob::helper``, ``proc util::other`` at
    the top level is ``util::other``); ``name`` is its last word. The id's
    name slot is ``name`` (ADR-0036 Ruling 1), so the slot holds no ``::``
    to sanitize; the scope lives in ``qualified_name`` (ADR-0032, Tcl's
    separator ``::``).
    """
    qualified = _qualified(_qualified("", "::".join(ns_path)), word)
    return qualified.rpartition("::")[2], qualified


#: The registry key of a proc's absolute name (``::util::helper``). The
#: ``::`` keeps these keys apart from the base class's keys, which are each
#: proc's own word as declared (``helper``, ``util::other``).
_ABSOLUTE = "::"


def _namespace_imports(
    root: "tree_sitter.Node",
) -> tuple[dict[tuple[str, str], str], dict[str, list[str]]]:
    """What ``namespace import`` brings into each namespace of one file.

    ``({(namespace, command): absolute name}, {namespace: [namespaces whose
    every exported command it imports]})`` -- the first for ``namespace
    import ::util::helper``, the second for ``::util::*``. A pattern is read
    in the namespace the import runs in; an option (``-force``) is skipped.
    Only this file's imports are read: an import in another file that a
    script sources is not seen.
    """
    named: dict[tuple[str, str], str] = {}
    wildcard: dict[str, list[str]] = {}
    for node in _iter_nodes(root):
        if node.type != "namespace":
            continue
        words = [
            _get_node_text(w) for c in node.children if c.type == "word_list"
            for w in c.children if w.type == "simple_word"
        ]
        if not words or words[0] != "import":
            continue
        scope = _qualified("", "::".join(_namespace_path(node)))
        for pattern in words[1:]:
            if pattern.startswith("-"):
                continue
            target = _qualified(scope, pattern)
            if target.endswith("::*"):
                wildcard.setdefault(scope, []).append(target[:-3])
            else:
                named[(scope, target.rpartition("::")[2])] = target
    return named, wildcard


def _iter_nodes(root: "tree_sitter.Node") -> Iterator["tree_sitter.Node"]:
    """Every node under ``root``, root first."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _resolve_callee(
    word: str, scope: str, global_symbols: dict[str, Symbol],
    imports: tuple[dict[tuple[str, str], str], dict[str, list[str]]],
) -> Optional[Symbol]:
    """The proc a call of ``word`` from a proc in namespace ``scope`` runs.

    Tcl's order: a name first in the caller's namespace (where an imported
    command also sits), then in the global one. :func:`_qualified` reads an
    absolute name (``::util::helper``) the same in both, so it needs no case
    of its own. A name none of these finds in the tree falls back to the
    registry key of the word as written (without a leading ``::``), the
    analyzer's lookup before namespaces were read, which an import in
    another file, ``namespace path`` or ``interp alias`` can make right.
    """
    named, wildcard = imports
    candidates = [_qualified(scope, word)]
    if "::" not in word:
        if (scope, word) in named:
            candidates.append(named[(scope, word)])
        candidates.extend(f"{ns}::{word}" for ns in wildcard.get(scope, ()))
    candidates.append(_qualified("", word))
    for name in candidates:
        found = global_symbols.get(_ABSOLUTE + name)
        if found is not None:
            return found
    return global_symbols.get(_qualified("", word))


# Tcl built-in commands that should not generate call edges
_BUILTINS = frozenset({
    "puts", "set", "expr", "return", "if", "else", "while",
    "for", "foreach", "switch", "catch", "try", "proc",
    "namespace", "package", "source", "uplevel", "upvar",
    "global", "variable", "incr", "append", "lappend",
    "list", "lindex", "llength", "lsort", "lsearch",
    "string", "regexp", "regsub", "split", "join", "format",
    "scan", "open", "close", "read", "gets", "eof", "flush",
    "seek", "tell", "file", "glob", "cd", "pwd", "exec",
    "eval", "after", "update", "vwait", "info", "array",
    "dict", "clock", "time", "pid", "error", "break",
    "continue", "rename", "unset", "trace", "interp",
})


def _find_enclosing_proc(
    node: "tree_sitter.Node",
) -> Optional[tuple["tree_sitter.Node", str]]:
    """The innermost enclosing procedure that has a name: its node and the
    word it is declared by."""
    current = node.parent
    while current is not None:
        if current.type == "procedure":
            proc_name = _get_proc_name(current)
            if proc_name:
                return current, proc_name
        current = current.parent
    return None


def _proc_id(
    proc_node: "tree_sitter.Node", proc_name: str, path: Path, repo_root: Path,
) -> NodeId:
    """The id :meth:`TclAnalyzer.extract_symbols` gives this procedure.

    One function returning the factory's value, so the static id check can
    read an edge source built from it.
    """
    name, _qualified = _declared_names(_namespace_path(proc_node), proc_name)
    return make_symbol_id(
        "tcl", str(path.relative_to(repo_root)),
        proc_node.start_point[0] + 1, proc_node.end_point[0] + 1, name, "function",
    )


def _proc_scope(proc_node: "tree_sitter.Node", proc_name: str) -> str:
    """The namespace a procedure's body runs in: its absolute name
    (:func:`_qualified`) minus its last word. ``proc util::other`` declared
    at the top level runs in ``util``."""
    _name, qualified_name = _declared_names(_namespace_path(proc_node), proc_name)
    return qualified_name.rpartition("::")[0]


class TclAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Tcl source files using TreeSitterAnalyzer base class."""

    lang = "tcl"
    file_patterns: ClassVar[list[str]] = ["*.tcl", "*.tk"]
    language_pack_name = "tcl"

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Register a proc under its name and under its absolute name.

        The base class keys a symbol by its name, the proc's last word
        (``helper`` for ``proc helper`` in ``namespace eval util``). A call
        names it ``util::helper`` or ``::util::helper``, so each proc is also
        keyed by its ``qualified_name`` (``::util::helper``, :data:`_ABSOLUTE`),
        what :func:`_resolve_callee` looks up.
        """
        super().register_symbol(symbol, global_symbols)
        if symbol.kind == "function" and symbol.qualified_name is not None:
            global_symbols[_ABSOLUTE + symbol.qualified_name] = symbol

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract procedure and namespace symbols from a Tcl file."""
        analysis = FileAnalysis()
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        # WI-bokab (v7): file-identity anchor for this file's symbols. ``rel_path``
        # is the repo-relative path (the base class passes it). Folded into
        # compute_stable_id's containing slot so same-name procs/namespaces in
        # different files hash distinctly (ADR-0035 §1/§4).
        file_anchor = self._file_anchor(rel_path)

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run, analysis, None,
            file_anchor, (),
        )
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
        namespace: Optional[str], file_anchor: str, ns_path: tuple[str, ...],
    ) -> None:
        """Extract symbols from a syntax tree node recursively.

        ``namespace`` is the innermost enclosing ``namespace eval``, the
        prefix of the name the stable_id hashes; ``ns_path`` is every
        enclosing one, outermost first, which the stable_id hashes beside it
        (INV-fabih: ``a::inner::go`` and ``b::inner::go`` both hashed
        ``inner::go``, tied, and deleting the first moved the second) and the
        node id and ``meta.namespace`` carry.
        """
        if node.type == "procedure":
            name = _get_proc_name(node)
            if name:
                params = _get_proc_params(node)
                signature = f"proc {name} {{{', '.join(params)}}}"

                hashed_name = f"{namespace}::{name}" if namespace else name
                short_name, qualified_name = _declared_names(ns_path, name)

                sym = Symbol(
                    id=make_symbol_id("tcl", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, short_name, "function"),
                    stable_id=self.compute_stable_id(
                        node, kind="function", name=hashed_name,
                        qualified_name=nested_member_qualified_name(ns_path, hashed_name, "::"),
                        file_stable_id=file_anchor,
                    ),
                    name=short_name,
                    qualified_name=qualified_name,
                    kind="function",
                    language="tcl",
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
                    meta={"namespace": "::".join(ns_path)} if ns_path else None,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "tcl"),
                    line_span=node.end_point[0] - node.start_point[0] + 1,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[short_name] = sym

        elif node.type == "namespace":
            name = _get_namespace_name(node)
            if name:
                proc_count = _count_namespace_procs(node)
                short_name, qualified_name = _declared_names(ns_path, name)

                sym = Symbol(
                    id=make_symbol_id(
                        "tcl", rel_path, node.start_point[0] + 1, node.end_point[0] + 1,
                        short_name, "namespace",
                    ),
                    stable_id=self.compute_stable_id(
                        node, kind="namespace", name=name,
                        qualified_name=nested_member_qualified_name(ns_path, name, "::"),
                        file_stable_id=file_anchor,
                    ),
                    name=short_name,
                    qualified_name=qualified_name,
                    kind="namespace",
                    language="tcl",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"proc_count": proc_count},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node

                # Extract procedures within the namespace
                for child in node.children:
                    if child.type == "word_list":
                        for word_child in child.children:
                            if word_child.type == "braced_word":
                                for inner in word_child.children:
                                    self._extract_symbols_recursive(
                                        inner, path, repo_root, rel_path,
                                        run, analysis, namespace=name,
                                        file_anchor=file_anchor,
                                        ns_path=(*ns_path, name),
                                    )
                return  # Don't recursively process namespace children again

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis, namespace,
                file_anchor, ns_path,
            )

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a Tcl file."""
        edges: list[Edge] = []
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_edges_recursive(
            tree.root_node, file_path, repo_root, global_symbols, run, edges,
            _namespace_imports(tree.root_node),
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        global_symbols: dict[str, Symbol], run: "AnalysisRun", edges: list[Edge],
        imports: tuple[dict[tuple[str, str], str], dict[str, list[str]]],
    ) -> None:
        """Extract edges from a syntax tree node recursively.

        ``imports`` is the file's :func:`_namespace_imports`.
        """
        if node.type in ("command_substitution", "command"):
            caller = _find_enclosing_proc(node)
            if caller:
                proc_node, proc_name = caller
                caller_id = _proc_id(proc_node, proc_name, path, repo_root)
                scope = _proc_scope(proc_node, proc_name)
                word = _get_command_name(node)
                callee_name = _callee_word(word)

                if word and callee_name and callee_name not in _BUILTINS:
                    callee_sym = _resolve_callee(word, scope, global_symbols, imports)
                    if callee_sym is not None:
                        edge = Edge.create(
                            src=caller_id,
                            dst=callee_sym.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_call_direct",
                            evidence_lang="tcl",
                        )
                    else:
                        # WI-nigah Tier 2: split namespace-qualified callee at
                        # the rightmost ``::`` so the unresolved edge carries
                        # a structured ``dst_ref=ExternalRef(...)`` mirroring
                        # the legacy colon-separated dst shape. Bare callees
                        # (no ``::``) keep the pre-WI-nigah behavior.
                        if "::" in callee_name:
                            ns, _, bare = callee_name.rpartition("::")
                            edge = make_unresolved_edge(
                                "tcl", caller_id, bare,
                                node.start_point[0] + 1,
                                PASS_ID, run.execution_id,
                                module_hint=ns,
                                dst_ref=ExternalRef(
                                    lang="tcl", module_path=ns, name=bare,
                                ),
                            )
                        else:
                            edge = make_unresolved_edge(
                                "tcl", caller_id, callee_name,
                                node.start_point[0] + 1,
                                PASS_ID, run.execution_id,
                            )
                    edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, global_symbols, run, edges, imports,
            )


_analyzer = TclAnalyzer()


def is_tcl_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Tcl support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("tcl", language_state="no_taxonomy_spec")  # WI-futin
def analyze_tcl(repo_root: Path) -> AnalysisResult:
    """Analyze Tcl source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
