# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lean 4 analysis pass using tree-sitter-lean.

This analyzer uses tree-sitter to parse Lean 4 files and extract:
- Definition declarations (def, abbrev)
- Theorem and lemma declarations (kind ``theorem``; lemmas carry meta
  ``is_lemma``)
- Structure definitions (kind ``struct``)
- Inductive type definitions
- Class and instance definitions
- Import statements

Lean 4 is an interactive theorem prover and programming language.
Unlike typical programming languages, "calls" are less meaningful than
"references" (dependencies between theorems/lemmas). Dependencies are
``references`` edges (confidence 0.80) from each declaration to identifiers
in its signature/body; ``imports`` edges are emitted only for import
statements.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect imports and references

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Lean-specific extraction
logic. ``LeanAnalyzer`` sets ``create_file_symbols = True`` so every
analyzed file gets a file node for import edges to land on.

Details of each pass:

- Symbols: a declaration inside ``namespace A`` ... ``end A`` is named with
  the namespace, as Lean names it (``A.run``; nested and dotted namespaces
  chain, ``def B.run`` inside ``namespace A`` is ``A.B.run``, and a
  ``_root_.`` prefix opts out). Sections and ``mutual`` blocks add nothing.
  Structures and classes keep the name as written: their ids come from the
  table formula over the name, so qualifying them would change ids already
  computed (ADR-0035 §6). An unnamed ``instance : Add Nat where ...`` is named
  after its typeclass (``Add``). Every declaration is emitted; before
  (INV-kagib) only the first symbol of a given name in a file was kept, so
  ``run`` in ``namespace B`` was dropped when ``namespace A`` declared one.
- Identity (WI-tusad): functions (def, abbrev), theorems, inductives and
  instances mint the ADR-0035 producer stable_id through
  ``overload_arity.declared_arity_stable_id`` over the namespace-qualified
  name; a def/abbrev/theorem records its explicit binders in
  ``meta["parameters"]`` (a binder with ``:=`` is a default). An unnamed
  instance's scope chain carries its whole type application (``instance Add
  Nat``), so two unnamed instances of one class stay apart.
- Imports: ``_module_name_to_file_path`` maps ``A.B.C`` to ``A/B/C.lean``;
  when that path is in ``known_file_paths`` (paths of ``.lean`` files seen
  among the global symbols) the edge targets the file node id, otherwise it
  falls back to ``lean:<Module>:0-0:module:module``.
- References: all eight declaration kinds (def, theorem, lemma, instance,
  structure, class, inductive, abbrev) have their signature and body scanned
  for identifiers. The source of an edge is the declaration's own symbol,
  found by its position (two ``run``s in two namespaces share a bare name). A ``references`` edge is kept only when the target's
  file is the current file or one it imports (in-repo imports resolved as
  above), which drops same-named symbols in unrelated files. Each
  (declaration, target) pair is emitted once.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Built from source since not on PyPI
- Uses experimental tree-sitter-lean grammar
- Two-pass allows cross-file resolution
- References model fits proof languages better than calls

Lean 4 Considerations
--------------------
- Lean 4 has namespaces and modules
- Definitions can have type signatures inline or separate
- Inductive types have constructors as separate entries
- Structures are special cases of inductive types
- Classes are used for type class polymorphism
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING, ClassVar, Iterator, Optional

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
    node_text,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("lean")


def find_lean_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Lean files in the repository."""
    yield from find_files(repo_root, ["*.lean"])


def _make_module_id(module_name: str) -> str:
    """Generate ID for a Lean module (used as import edge target)."""
    return f"lean:{module_name}:0-0:module:module"


def _get_identifier_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract the identifier text from a node or its children."""
    if node.type == "identifier":
        return node_text(node, source).strip()
    # Look for identifier child
    id_node = find_child_by_type(node, "identifier")  # pragma: no cover
    if id_node:  # pragma: no cover
        return node_text(id_node, source).strip()  # pragma: no cover
    return ""  # pragma: no cover


def _extract_lean_signature(
    decl_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function/theorem signature from a Lean declaration node.

    Lean declarations look like:
        def double (x : Nat) : Nat := ...
        theorem add_comm (a b : Nat) : a + b = b + a := ...

    The node contains:
    - identifier (name)
    - binders (parameters like (x : Nat))
    - : followed by return type
    - := followed by body

    Returns signature like "(x : Nat) : Nat".
    """
    parts: list[str] = []
    found_name = False
    found_return_colon = False

    for child in decl_node.children:
        # Skip the keyword (def, theorem, etc.) and name
        if child.type in ("def", "theorem", "lemma", "abbrev"):
            continue
        if child.type == "identifier" and not found_name:
            found_name = True
            continue

        # Collect binders (parameters)
        if child.type == "binders":
            binders_text = node_text(child, source).strip()
            if binders_text:
                parts.append(binders_text)

        # Collect return type after :
        if child.type == ":":
            found_return_colon = True
            parts.append(":")
            continue

        # Stop at := (start of body)
        if child.type == ":=":
            break

        # Collect the return type expression
        if found_return_colon and child.type not in (":=", "tactics"):
            type_text = node_text(child, source).strip()
            if type_text:
                parts.append(type_text)

    if parts:
        return " ".join(parts)
    return None


#: Kinds whose ids come from the table formula over the bare name; they keep
#: the name as written (see the module docstring).
_LEAN_TABLE_KINDS = frozenset({"struct", "class"})


class _NamespaceStack:
    """The namespaces open at a point of a document-order walk.

    ``namespace A.B`` pushes ``A.B``; a named ``section S`` pushes a marker that
    adds nothing to names; ``end X`` pops back to (and including) the entry
    named ``X``. A plain ``end`` closes an anonymous section or a ``mutual``
    block, neither of which adds to names, so it is ignored, as is an ``end``
    naming nothing that is open (a file that does not parse cleanly).
    """

    def __init__(self) -> None:
        self._entries: list[tuple[str, bool]] = []  # (name, is_namespace)

    def visit(self, node: "tree_sitter.Node", source: bytes) -> None:
        """Update the stack for a ``namespace`` / ``section`` / ``end`` node."""
        id_node = find_child_by_type(node, "identifier")
        if id_node is None:
            return  # a keyword token, an anonymous section, a plain ``end``
        name = node_text(id_node, source).strip()
        if node.type in ("namespace", "section"):
            self._entries.append((name, node.type == "namespace"))
            return
        names = [entry[0] for entry in self._entries]
        if name in names:
            del self._entries[len(names) - 1 - names[::-1].index(name):]

    def qualify(self, name: str) -> str:
        """``name`` as declared here: ``A.B.name``, or ``x`` for ``_root_.x``."""
        if name.startswith("_root_."):
            return name[len("_root_."):]
        return ".".join([n for n, is_ns in self._entries if is_ns] + [name])


def _lean_parameters(
    decl_node: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """``meta["parameters"]`` of a def/abbrev/theorem: its explicit binders.

    ``(x y : Nat)`` is two parameters; ``(z : Nat := 1)`` is a default.
    Implicit ``{x}``, strict-implicit and instance ``[inst]`` binders are filled
    in by elaboration, not passed, and are left out.
    """
    entries: list[dict[str, object]] = []
    binders = find_child_by_type(decl_node, "binders")
    if binders is None:
        return entries
    for binder in binders.children:
        if binder.type == "identifier":
            entries.append(parameter_entry(node_text(binder, source).strip()))
        elif binder.type == "explicit_binder":
            has_default = any(c.type == ":=" for c in binder.children)
            for child in binder.children:
                if child.type == ":":
                    break
                if child.type == "identifier":
                    entries.append(parameter_entry(
                        node_text(child, source).strip(), default=has_default,
                    ))
    return entries


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Lean file.

    Detects:
    - function: def, abbrev declarations
    - theorem: theorem, lemma declarations
    - structure: structure declarations
    - inductive: inductive type definitions
    - class: class declarations
    - instance: instance declarations
    """
    symbols: list[Symbol] = []
    namespaces = _NamespaceStack()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        meta: dict[str, Any] | None = None,
        signature: Optional[str] = None,
        complexity: bool = False,
        chain_tail: Optional[str] = None,
    ) -> None:
        """Add one symbol for the declaration ``node`` (see the module docstring).

        ``complexity=True`` (callable kinds — function / theorem) populates
        cyclomatic_complexity + line_span per INV-loguk. Gated so
        non-callable kinds (structure/inductive/class/instance) that share this
        helper don't aggregate their whole subtree. ``chain_tail`` replaces the
        name in the scope chain (an unnamed instance's type application).
        """
        stable_id: Optional[str] = None
        if kind not in _LEAN_TABLE_KINDS:
            chain = namespaces.qualify(chain_tail or name)
            if chain_tail is None:
                name = chain
            stable_id = declared_arity_stable_id(
                kind=kind, lang="lean", path=file_path,
                name=name.rsplit(".", 1)[-1], qualified_name=chain,
                arity=declared_arity(meta),
            )

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )
        sym_id = make_symbol_id("lean", file_path, start_line, end_line, name, kind)
        sym = Symbol(
            id=sym_id,
            stable_id=stable_id,
            name=name,
            kind=kind,
            language="lean",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            cyclomatic_complexity=(
                compute_cyclomatic_complexity(node, "lean") if complexity else None
            ),
            line_span=(end_line - start_line + 1) if complexity else None,
            meta=meta or None,
        )
        symbols.append(sym)

    for node in iter_tree(tree.root_node):
        if node.type in ("namespace", "section", "end"):
            namespaces.visit(node, source)
        elif node.type == "declaration":
            # Check what kind of declaration this is
            for child in node.children:
                if child.type == "def":
                    # def name ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(
                                child, name, "function",
                                {PARAMETERS_KEY: _lean_parameters(child, source)},
                                signature=sig, complexity=True,
                            )

                elif child.type == "theorem":
                    # theorem name ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(
                                child, name, "theorem",
                                {PARAMETERS_KEY: _lean_parameters(child, source)},
                                signature=sig, complexity=True,
                            )

                elif child.type == "lemma":  # pragma: no cover - similar to theorem
                    # lemma name ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(
                                child, name, "theorem",
                                {"is_lemma": True,
                                 PARAMETERS_KEY: _lean_parameters(child, source)},
                                signature=sig, complexity=True,
                            )

                elif child.type == "structure":
                    # structure name where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            # WI-zipis / audit-0015 fold: Lean `structure` is a
                            # struct (record/product type) -> canonical `struct`.
                            add_symbol(child, name, "struct")

                elif child.type == "inductive":
                    # inductive name where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "inductive")

                elif child.type == "class":  # pragma: no cover - class detection
                    # class name where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "class")

                elif child.type == "instance":
                    # Named: instance myAdd : Add Nat where ...
                    # Unnamed: instance : Add Nat where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "instance")
                    else:
                        # Unnamed instance — use typeclass name from the
                        # type application. Older grammar revisions named
                        # this node "apply"; current main (post-rebase
                        # 2026-05) uses "app".
                        apply_node = find_child_by_type(child, "app") or find_child_by_type(child, "apply")
                        if apply_node:
                            tc_id = find_child_by_type(apply_node, "identifier")
                            if tc_id:
                                tc_name = node_text(tc_id, source).strip()
                                if tc_name:
                                    applied = " ".join(
                                        node_text(apply_node, source).split()
                                    )
                                    add_symbol(
                                        child, tc_name, "instance",
                                        chain_tail=f"instance {applied}",
                                    )

                elif child.type == "abbrev":  # pragma: no cover - abbrev detection
                    # abbrev name ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(
                                child, name, "function",
                                {"is_abbrev": True,
                                 PARAMETERS_KEY: _lean_parameters(child, source)},
                                signature=sig, complexity=True,
                            )

    return symbols


def _module_name_to_file_path(module_name: str) -> str:
    """Convert a Lean module name to a relative file path.

    Lean modules use dot-separated names that map to directory structure:
    ``ArkLib.AGM.Basic`` → ``ArkLib/AGM/Basic.lean``.
    """
    return module_name.replace(".", "/") + ".lean"


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
    known_file_paths: frozenset[str] = frozenset(),
) -> list[Edge]:
    """Extract import edges from a parsed Lean file.

    Detects:
    - import: Import statements (import Module.Name)

    When ``known_file_paths`` is provided, intra-repo imports are resolved
    to file node IDs (``lean:path.lean:1-1:file:file``) instead of module
    IDs (``lean:Module.Name:0-0:module:module``).  This ensures import
    edges connect to actual file nodes in the graph, fixing hundreds of
    dangling edges in repos like ArkLib.
    """
    edges: list[Edge] = []
    file_id = make_file_id("lean", file_path)

    # Track which file paths this file imports so we can filter reference
    # edges to only symbols reachable through the import chain.  The
    # current file is always included (same-file references are valid).
    imported_file_paths: set[str] = {file_path}

    for node in iter_tree(tree.root_node):
        # Look for import statements at module level
        # Lean imports look like: import Mathlib.Data.Nat.Basic
        # In the tree: import node with identifier child containing the dotted path
        if node.type == "import":
            # Find the identifier child which contains the module path
            id_node = find_child_by_type(node, "identifier")
            if id_node:
                # The identifier contains the full dotted path
                module_name = node_text(id_node, source).strip()
                if module_name:
                    # Resolve to file ID if the target file exists in-repo;
                    # otherwise keep module ID for external deps.
                    expected_path = _module_name_to_file_path(module_name)
                    if expected_path in known_file_paths:
                        target_id = make_file_id("lean", expected_path)
                        imported_file_paths.add(expected_path)
                    else:
                        target_id = _make_module_id(module_name)
                    edge = Edge.create(
                        src=file_id,
                        dst=target_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="import",
                    )
                    edges.append(edge)

    # Reference edges: scan all declaration types for identifier references
    # to defined symbols (WI-juviz).  Originally only def/theorem/lemma were
    # scanned, leaving instance/structure/class/inductive/abbrev symbols as
    # orphans.  In the clean repo this caused 204 Lean orphans (133 functions,
    # 26 theorems, 23 instances, 22 structures).
    _REF_SCAN_TYPES = (
        "def", "theorem", "lemma",
        "instance", "structure", "class", "inductive", "abbrev",
    )
    seen_ref_pairs: set[tuple[str, str]] = set()
    decl_index = symbols_at(file_symbols)
    for node in iter_tree(tree.root_node):
        if node.type not in _REF_SCAN_TYPES:
            continue
        # Find the declaration name
        decl_name_node = find_child_by_type(node, "identifier")
        if not decl_name_node:
            continue  # pragma: no cover
        decl_name = _get_identifier_text(decl_name_node, source)
        if not decl_name:
            continue  # pragma: no cover

        # The declaration's own symbol, by position (INV-kagib): a bare-name
        # lookup cannot tell ``A.run`` from ``B.run``.
        decl_symbol = symbol_declared_by(node, decl_index)
        if decl_symbol is None:
            continue  # pragma: no cover - every scanned declaration is emitted

        # Collect both type signature nodes (before :=) and body nodes
        # (after :=).  Skip the keyword and declaration name.
        scan_nodes: list = []
        past_name = False
        for child in node.children:
            if child.type == "identifier" and not past_name:
                past_name = True
                continue
            if child.type in ("def", "theorem", "lemma"):
                continue
            if past_name:
                scan_nodes.append(child)

        # Scan all collected nodes for identifier references
        for scan_node in scan_nodes:
            for id_node in iter_tree(scan_node):
                if id_node.type != "identifier":
                    continue
                ref_name = node_text(id_node, source).strip()
                if not ref_name or ref_name == decl_name:
                    continue  # pragma: no cover — skip self-ref

                ref_result = resolver.lookup(ref_name)
                if ref_result.symbol:
                    # Filter by import chain: only create cross-file reference
                    # edges when the referenced symbol's file is imported by
                    # this file.  This prevents false positives where a bare
                    # name like ``pSpec`` resolves to a same-named symbol in
                    # an unrelated file (ArkLib had 119 such false edges).
                    ref_path = getattr(ref_result.symbol, "path", "")
                    if ref_path and ref_path not in imported_file_paths:
                        continue
                    pair = (decl_symbol.id, ref_result.symbol.id)
                    if pair not in seen_ref_pairs:
                        seen_ref_pairs.add(pair)
                        edge = Edge.create(
                            src=decl_symbol.id,
                            dst=ref_result.symbol.id,
                            edge_type="references",
                            evidence_type="reference",
                            line=id_node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            confidence=0.80,
                        )
                        edges.append(edge)

    return edges


class LeanAnalyzer(TreeSitterAnalyzer):
    """Lean 4 language analyzer using tree-sitter-lean."""

    lang = "lean"
    file_patterns: ClassVar[list[str]] = ["*.lean"]
    grammar_module = "tree_sitter_lean"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract definition, theorem, structure, and inductive symbols."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import edges from a Lean file."""
        # Build set of known file paths from global symbols so intra-repo
        # imports resolve to file node IDs instead of dangling module IDs.
        # Keyed by run ID to avoid stale data across analyze() calls.
        run_id = run.execution_id
        if getattr(self, "_known_paths_run_id", None) != run_id:
            paths: set[str] = set()
            for sym in global_symbols.values():
                if hasattr(sym, "path") and sym.path.endswith(".lean"):
                    paths.add(sym.path)
            self._known_lean_paths: frozenset[str] = frozenset(paths)
            self._known_paths_run_id = run_id
        return _extract_edges_from_file(
            tree, source, rel_path, self.file_symbols(local_symbols),
            resolver, run.execution_id,
            known_file_paths=self._known_lean_paths,
        )


_analyzer = LeanAnalyzer()


def is_lean_tree_sitter_available() -> bool:
    """Check if tree-sitter with Lean grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("lean")
def analyze_lean(repo_root: Path) -> AnalysisResult:
    """Analyze Lean files in a repository."""
    return _analyzer.analyze(repo_root)
