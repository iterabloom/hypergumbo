# SPDX-License-Identifier: AGPL-3.0-or-later
"""PureScript language analyzer using tree-sitter.

This module provides static analysis for PureScript source code, extracting symbols
(modules, functions, data types, type aliases, classes, instances) and call
edges.

PureScript is a strongly-typed functional programming language that compiles to
JavaScript. It features a powerful type system inspired by Haskell, with support
for type classes, row polymorphism, and algebraic data types.

Implementation approach:
- Uses tree-sitter-language-pack for PureScript grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles modules, functions, data types, type aliases, and type classes

Key constructs extracted:
- module Name where - module definition
- function name :: Type - function with type signature
- data Name = ... - algebraic data types
- type Name = ... - type aliases
- class Name ... where - type classes
- instance ... - class instances

Symbol names are module-qualified (``Module.name``) when the file declares a
module. Traversal does not descend into function bodies, so local ``where``
and ``let`` bindings are not extracted. In pass 2 each ``exp_apply`` inside a
function becomes a ``calls`` edge unless its head is on a small builtins
skip-list (``pure``, ``map``, ``show``, ``liftEffect``, ...). The callee is
looked up by the bare name, then qualified with the current module: a hit is
confidence 1.0, a miss goes to ``purescript:unresolved:<name>`` at 0.6.

One function, one symbol (WI-tusad scheme bump). A function is written as a
type signature and one ``function`` node per equation (``fact 0 = 1`` /
``fact n = ...``). Each used to become its own symbol, all with one name and
one hash, so the per-file split pass numbered them and deleting the signature
or an earlier equation renumbered the rest. Now the equations of a name in one
declaration block (the module, a class, an instance) fold into one symbol at
the first equation, spanning to the last (the Haskell analyzer's shape), with
the signature as its ``signature`` and the cyclomatic complexity summed over
the equations; calls in any equation come from it. A signature with no
equations beside it (a class member) is still a symbol of its own.

Identity: the module-qualified name plus, for a class member or an instance
method, the class name or instance name (an unnamed instance's head, ``Shape
Boolean``) as scope: ``Demo.shapeInt.area``, hashed only (the emitted name
stays ``Demo.area``), so ``area`` in two instances no longer ties. A top-level
function's value is unchanged.
"""

import time
from pathlib import Path
from typing import Iterator, Optional, ClassVar, TYPE_CHECKING

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, _get_python_toolchain, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, TreeSitterAnalyzer, make_symbol_id, populate_docstrings_from_tree
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.analyze.base import node_own_text as _get_node_text
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("purescript")



def find_purescript_files(root: Path) -> Iterator[Path]:
    """Find all PureScript files in the given directory."""
    for path in find_files(root, ["*.purs"]):
        if path.is_file():
            yield path



def _get_module_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the module name from a purescript or qualified_module node."""
    for child in node.children:
        if child.type == "qualified_module":
            return _get_node_text(child)
        elif child.type == "module" and child.parent and child.parent.type == "qualified_module":
            return _get_node_text(child)  # pragma: no cover
    return None  # pragma: no cover


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a function or signature node."""
    for child in node.children:
        if child.type == "variable":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_data_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type name from a data or type_alias declaration."""
    # Skip the keyword child (first "type" or "data" in type_alias/data nodes)
    # and find the actual type name
    for child in node.children:
        if child.type in ("type", "data"):
            text = _get_node_text(child)
            if text in ("type", "data"):
                # This is the keyword, skip it
                continue
            return text
    return None  # pragma: no cover


def _get_class_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the class name from a class_declaration."""
    for child in node.children:
        if child.type == "class_head":
            # First word in class_head is the class name
            text = _get_node_text(child)
            return text.split()[0] if text else None
    return None  # pragma: no cover


def _get_type_signature(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type signature from a signature node."""
    for child in node.children:
        if child.type in ("type_infix", "type_apply", "type"):
            return _get_node_text(child)
    return None


def _get_instance_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the instance name from a class_instance node (None when unnamed)."""
    for child in node.children:
        if child.type == "instance_name":
            return _get_node_text(child)
    return None


def _equations(node: "tree_sitter.Node", name: str) -> list["tree_sitter.Node"]:
    """Every ``function`` node named ``name`` in ``node``'s declaration block
    (its parent), in source order: the equations of one function."""
    siblings = node.parent.children if node.parent is not None else [node]
    return [c for c in siblings if c.type == "function" and _get_function_name(c) == name]


def _has_equations(node: "tree_sitter.Node", name: str) -> bool:
    """Whether ``node``'s declaration block implements ``name``."""
    return bool(_equations(node, name))


def _declared_signature(node: "tree_sitter.Node", name: str) -> Optional[str]:
    """The type of the ``signature`` declaring ``name`` beside ``node``, if any."""
    siblings = node.parent.children if node.parent is not None else []
    for sibling in siblings:
        if sibling.type == "signature" and _get_function_name(sibling) == name:
            return _get_type_signature(sibling)
    return None


def _instance_label(node: "tree_sitter.Node") -> str:
    """An instance's scope label: its name, or for an unnamed instance
    (``instance Shape Boolean where``) its head, which tells it from another
    instance of the class."""
    name = _get_instance_name(node)
    if name:
        return name
    for child in node.children:
        if child.type == "instance_head":
            return " ".join(_get_node_text(child).split())
    return ""  # pragma: no cover - the grammar always gives a name or a head


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from an exp_apply node."""
    for child in node.children:
        if child.type == "variable":
            return _get_node_text(child)  # pragma: no cover
        elif child.type == "exp_name":
            # Qualified name like Module.function
            return _get_node_text(child)
    return None  # pragma: no cover


class _PureScriptExtractor:
    """Analyzer for PureScript source files."""

    def __init__(self, repo_root: Path, analyzer: "TreeSitterAnalyzer"):
        self.repo_root = repo_root
        self._ts_analyzer = analyzer
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_module: Optional[str] = None
        # The enclosing class / instance labels of the declaration being
        # extracted (the stable_id scope chain), and, per (path, start byte)
        # of every equation, the id of the function symbol it folded into.
        self._scope: list[str] = []
        self._equation_owner: dict[tuple[str, int], str] = {}

    def analyze(self) -> AnalysisResult:
        """Analyze all PureScript files in the repository."""
        if not is_purescript_tree_sitter_available():  # pragma: no cover
            # Defense-in-depth: PureScriptAnalyzer.analyze() gates on
            # _check_grammar_available() before constructing this extractor,
            # so this branch is unreachable through the public API.
            import warnings
            warnings.warn(
                "PureScript analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("purescript")
        ps_files = list(find_purescript_files(self.repo_root))

        if not ps_files:
            return AnalysisResult()

        # Pass 1: Collect all symbols
        for path in ps_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_module = None
                before = len(self.symbols)
                self._extract_symbols(tree.root_node, path)
                populate_docstrings_from_tree(tree.root_node, content, self.symbols[before:])
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in ps_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_module = None
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain=_get_python_toolchain(),
            duration_ms=int(elapsed * 1000),
        )

        return AnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "purescript":
            # Module declaration
            mod_name = _get_module_name(node)
            if mod_name:
                self._current_module = mod_name
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=make_symbol_id("purescript", rel_path, node.start_point[0]+1, node.end_point[0]+1, mod_name, "module"),
                    stable_id=self._ts_analyzer.compute_stable_id(node, kind="module", name=mod_name, qualified_name=mod_name, file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
                    name=mod_name,
                    kind="module",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                self.symbols.append(sym)

        elif node.type == "function":
            # Function definition: one symbol per name per declaration block,
            # from its first equation (see the module docstring).
            name = _get_function_name(node)
            if name:
                equations = _equations(node, name)
                if equations[0].start_byte == node.start_byte:
                    self._add_function(node, name, equations, path)
            return  # Don't recurse into function bodies

        elif node.type == "signature":
            # A type signature carries a symbol only when nothing in its
            # declaration block implements it (a class member, a standalone
            # declaration); otherwise the function's symbol takes it.
            name = _get_function_name(node)
            type_sig = _get_type_signature(node)
            if name and type_sig and not _has_equations(node, name):
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name
                sym = Symbol(
                    id=make_symbol_id("purescript", rel_path, node.start_point[0]+1, node.end_point[0]+1, qualified_name, "function"),
                    stable_id=self._ts_analyzer.compute_stable_id(node, kind="function", name=name, qualified_name=self._scoped(name), file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
                    name=qualified_name,
                    kind="function",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=type_sig,
                    meta={"module": self._current_module},
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "purescript"),
                    line_span=node.end_point[0] - node.start_point[0] + 1,
                )
                self.symbols.append(sym)

        elif node.type == "data":
            # Data type definition
            name = _get_data_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                # Count constructors
                constructors = [c for c in node.children if c.type == "constructor"]

                sym = Symbol(
                    id=make_symbol_id("purescript", rel_path, node.start_point[0]+1, node.end_point[0]+1, qualified_name, "type"),
                    stable_id=self._ts_analyzer.compute_stable_id(node, kind="type", name=name, qualified_name=qualified_name, file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
                    name=qualified_name,
                    kind="type",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"constructor_count": len(constructors), "module": self._current_module},
                )
                self.symbols.append(sym)

        elif node.type == "type_alias":
            # Type alias
            name = _get_data_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=make_symbol_id("purescript", rel_path, node.start_point[0]+1, node.end_point[0]+1, qualified_name, "type_alias"),
                    stable_id=self._ts_analyzer.compute_stable_id(node, kind="type_alias", name=name, qualified_name=qualified_name, file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
                    name=qualified_name,
                    kind="type_alias",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)

        elif node.type == "class_declaration":
            # Type class
            name = _get_class_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=make_symbol_id("purescript", rel_path, node.start_point[0]+1, node.end_point[0]+1, qualified_name, "class"),
                    stable_id=self._ts_analyzer.compute_stable_id(node, kind="class", name=name, qualified_name=qualified_name, file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
                    name=qualified_name,
                    kind="class",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)
                self._extract_scoped_children(node, path, name)
                return

        elif node.type == "class_instance":
            # Instance declaration
            name = _get_instance_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=make_symbol_id("purescript", rel_path, node.start_point[0]+1, node.end_point[0]+1, qualified_name, "instance"),
                    stable_id=self._ts_analyzer.compute_stable_id(node, kind="instance", name=name, qualified_name=qualified_name, file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
                    name=qualified_name,
                    kind="instance",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)
            self._extract_scoped_children(node, path, _instance_label(node))
            return

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _scoped(self, name: str) -> str:
        """The qualified name a member's stable_id hashes: module, then the
        enclosing class or instance, then the name (``Demo.shapeInt.area``).
        The emitted name stays ``Demo.area``: calls resolve by it."""
        return ".".join(p for p in (self._current_module, *self._scope, name) if p)

    def _extract_scoped_children(
        self, node: "tree_sitter.Node", path: Path, label: str,
    ) -> None:
        """Extract a class's or instance's members with ``label`` as their scope."""
        self._scope.append(label)
        try:
            for child in node.children:
                self._extract_symbols(child, path)
        finally:
            self._scope.pop()

    def _add_function(
        self, node: "tree_sitter.Node", name: str,
        equations: list["tree_sitter.Node"], path: Path,
    ) -> None:
        """Emit ONE function symbol for ``equations`` (all of one name in one
        declaration block), spanning the first to the last, with the type
        signature declared beside them."""
        rel_path = str(path.relative_to(self.repo_root))
        qualified_name = f"{self._current_module}.{name}" if self._current_module else name
        last = equations[-1]
        start_line, end_line = node.start_point[0] + 1, last.end_point[0] + 1
        sym = Symbol(
            id=make_symbol_id("purescript", rel_path, start_line, end_line, qualified_name, "function"),
            stable_id=self._ts_analyzer.compute_stable_id(node, kind="function", name=name, qualified_name=self._scoped(name), file_stable_id=self._ts_analyzer._file_anchor(rel_path)),
            name=qualified_name,
            kind="function",
            language="purescript",
            path=rel_path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=node.start_point[1],
                end_col=last.end_point[1],
            ),
            origin=PASS_ID,
            signature=_declared_signature(node, name),
            meta={"module": self._current_module},
            # Each equation is one alternative of the function's pattern match.
            cyclomatic_complexity=sum(
                compute_cyclomatic_complexity(eq, "purescript") or 0 for eq in equations
            ),
            line_span=end_line - start_line + 1,
        )
        self.symbols.append(sym)
        for eq in equations:
            self._equation_owner[(rel_path, eq.start_byte)] = sym.id

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "purescript":
            mod_name = _get_module_name(node)
            if mod_name:
                self._current_module = mod_name

        elif node.type == "exp_apply":
            # Function application
            call_name = _get_call_name(node)
            if call_name:
                # Skip built-in functions
                builtins = {
                    # Common functions
                    "log", "pure", "bind", "map", "apply",
                    "show", "eq", "compare", "append",
                    # Effect
                    "liftEffect", "runEffect",
                    # Common operators used as functions
                    "add", "sub", "mul", "div", "mod",
                    "negate", "not", "and", "or",
                }

                if call_name not in builtins:
                    caller_id = self._find_enclosing_function(node, path)
                    if caller_id:
                        # Try qualified name first
                        callee_id = self._symbol_registry.get(call_name)
                        if callee_id is None and self._current_module:
                            qualified = f"{self._current_module}.{call_name}"
                            callee_id = self._symbol_registry.get(qualified)

                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"purescript:unresolved:{call_name}"

                        line = node.start_point[0] + 1
                        edge = Edge.create(
                            src=caller_id,
                            dst=callee_id,
                            edge_type="calls",
                            line=line,
                            origin=PASS_ID,
                            origin_run_id=self._run_id,
                            evidence_type="ast_call_direct",
                            confidence=confidence,
                            evidence_lang="purescript",
                        )
                        self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _find_enclosing_function(
        self, node: "tree_sitter.Node", path: Path
    ) -> Optional[str]:
        """Find the enclosing function for a node."""
        current = node.parent
        while current is not None:
            if current.type == "function":
                name = _get_function_name(current)
                if name:
                    rel_path = str(path.relative_to(self.repo_root))
                    owner = self._equation_owner.get((rel_path, current.start_byte))
                    if owner is not None:
                        return owner
                    # A ``where`` / ``let`` local function: no symbol of its
                    # own, so the id is minted from its node as before.
                    qualified = f"{self._current_module}.{name}" if self._current_module else name
                    return make_symbol_id("purescript", rel_path,
                        current.start_point[0]+1, current.end_point[0]+1, qualified, "function")
            current = current.parent
        return None  # pragma: no cover


class PureScriptAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based analyzer for PureScript source files.

    Extracts modules, functions, data types, type aliases, classes, instances,
    and call edges. Uses language_pack_name for the purescript grammar.
    """

    lang = "purescript"
    file_patterns: ClassVar[list[str]] = ["**/*.purs"]
    language_pack_name = "purescript"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run PureScript analysis with cross-file symbol registry."""
        if not self._check_grammar_available():
            import warnings
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        extractor = _PureScriptExtractor(repo_root, self)
        return extractor.analyze()


_analyzer = PureScriptAnalyzer()


def is_purescript_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with PureScript support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("purescript")
def analyze_purescript(repo_root: Path) -> AnalysisResult:
    """Analyze PureScript source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
