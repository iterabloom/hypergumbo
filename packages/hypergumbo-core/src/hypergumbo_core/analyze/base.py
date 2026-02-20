"""Base classes and utilities for language analyzers.

This module provides shared infrastructure for all language analyzers,
eliminating duplication across 65+ analyzer files.

Shared Components
-----------------
- **AnalysisResult**: Universal result type returned by all analyzers
- **FileAnalysis**: Intermediate per-file analysis result
- **Tree-sitter helpers**: node_text, find_child_by_type, find_child_by_field
- **ID generation**: make_symbol_id, make_file_id
- **Availability checking**: is_grammar_available

Why This Design
---------------
Previously, each analyzer duplicated these components. This led to:
- 65+ copies of identical dataclasses
- Inconsistent helper implementations
- High maintenance burden when adding new analyzers

Now, analyzers import from this module and focus only on
language-specific parsing logic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ClassVar, Iterator, Optional

from ..discovery import find_files
from ..ir import PASS_VERSION, AnalysisRun, Edge, Span, Symbol, UsageContext, make_pass_id
from ..symbol_resolution import NameResolver

if TYPE_CHECKING:
    import tree_sitter


@dataclass
class AnalysisResult:
    """Universal result type for all language analyzers.

    This replaces the per-language XxxAnalysisResult dataclasses
    (GoAnalysisResult, RustAnalysisResult, etc.) which were all identical.

    Attributes:
        symbols: List of detected symbols (functions, classes, etc.)
        edges: List of relationships between symbols (calls, imports, etc.)
        usage_contexts: List of usage contexts for call-based pattern matching (v1.1.x)
        run: Provenance tracking for the analysis pass
        skipped: Whether the analysis was skipped (e.g., missing dependency)
        skip_reason: Human-readable reason for skipping
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single source file.

    Used during two-pass analysis: first pass collects symbols,
    second pass resolves cross-file references using the symbol registry.

    Attributes:
        symbols: Symbols detected in this file
        symbol_by_name: Quick lookup by symbol name for edge resolution
        import_aliases: Mapping of import alias → import path (Go, etc.)
        node_for_symbol: Mapping of symbol ID → tree-sitter node for
            automatic shape_id computation (ADR-0014 §1). Analyzers that
            populate this get shape_id computed by the base class.
    """

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    import_aliases: dict[str, str] = field(default_factory=dict)
    node_for_symbol: dict[str, "tree_sitter.Node"] = field(default_factory=dict)


@dataclass(frozen=True)
class ArityFlags:
    """Parameter arity classification for stable_id computation (ADR-0014 §2).

    Captures the structural shape of a function's parameter list without
    recording names or types.  Two functions with the same ArityFlags and
    kind will produce the same untyped-tier stable_id (by design — they
    have the same *interface shape*).

    Attributes:
        param_count: Number of regular parameters (excludes self/cls/receiver).
        has_defaults: Whether any parameter has a default value.
        has_varargs: Whether variadic positional args exist (``*args``, ``...``).
        has_kwargs: Whether variadic keyword args exist (``**kwargs``).
    """

    param_count: int
    has_defaults: bool
    has_varargs: bool
    has_kwargs: bool

    def as_flags_str(self) -> str:
        """Return the canonical string form used in stable_id hashing."""
        return f"{self.has_defaults},{self.has_varargs},{self.has_kwargs}"


# ---------------------------------------------------------------------------
# Tree-sitter helper functions
# ---------------------------------------------------------------------------


def node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text content for a tree-sitter node.

    Args:
        node: A tree-sitter node
        source: Source file bytes

    Returns:
        The text content of the node, decoded as UTF-8.
    """
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find the first child node of a given type.

    Args:
        node: Parent tree-sitter node
        type_name: The node type to search for

    Returns:
        The first matching child, or None if not found.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def find_child_by_field(
    node: "tree_sitter.Node", field_name: str
) -> Optional["tree_sitter.Node"]:
    """Find a child node by field name.

    Args:
        node: Parent tree-sitter node
        field_name: The field name to look up

    Returns:
        The child at that field, or None if not found.
    """
    return node.child_by_field_name(field_name)


# ---------------------------------------------------------------------------
# ID generation helpers
# ---------------------------------------------------------------------------


def make_symbol_id(
    lang: str, path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate a location-based symbol ID.

    Format: {lang}:{path}:{start}-{end}:{name}:{kind}

    Args:
        lang: Language identifier (e.g., "go", "rust", "python")
        path: File path
        start_line: Starting line number
        end_line: Ending line number
        name: Symbol name
        kind: Symbol kind (function, class, etc.)

    Returns:
        A unique, location-based symbol ID.
    """
    return f"{lang}:{path}:{start_line}-{end_line}:{name}:{kind}"


def make_file_id(lang: str, path: str) -> str:
    """Generate an ID for a file node (used as import edge source).

    Args:
        lang: Language identifier
        path: File path

    Returns:
        A file-level symbol ID.
    """
    return f"{lang}:{path}:1-1:file:file"


def make_route_stable_id(method: str, path: str) -> str:
    """Compute a collision-free stable_id for route symbols.

    Uses sha256("route:{method}:{path}") per ADR-0014 §4.  The previous
    approach set stable_id to bare HTTP methods (e.g. "GET"), causing every
    same-method route to collide.

    Args:
        method: HTTP method (e.g. "GET", "POST", "ANY"). Case-insensitive —
            the value is upper-cased internally for consistency.
        path: Route path (e.g. "/users", "/posts/:id").

    Returns:
        A hex-digest string that uniquely identifies the (method, path) pair.
    """
    key = f"route:{method.upper()}:{path}"
    return hashlib.sha256(key.encode()).hexdigest()


def make_entry_stable_id(entry_type: str, name: str) -> str:
    """Compute a collision-free stable_id for entry-point symbols.

    Uses sha256("entry:{entry_type}:{name}") per ADR-0014 §4.  Used for
    symbols like WGSL shader stages (@vertex, @fragment, @compute) where the
    previous approach set stable_id to the bare entry type string, causing
    same-type entry points to collide.

    Args:
        entry_type: Entry point category (e.g. "vertex", "fragment", "compute").
        name: Symbol name (e.g. the function name).

    Returns:
        A hex-digest string that uniquely identifies the (entry_type, name) pair.
    """
    key = f"entry:{entry_type}:{name}"
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Grammar availability checking
# ---------------------------------------------------------------------------


def is_grammar_available(grammar_module: str) -> bool:
    """Check if a tree-sitter grammar is available.

    Args:
        grammar_module: The grammar module name (e.g., "tree_sitter_go")

    Returns:
        True if both tree_sitter and the grammar module are importable.
    """
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec(grammar_module) is None:
        return False
    return True


# ---------------------------------------------------------------------------
# Iterative tree traversal (avoids RecursionError on deeply nested code)
# ---------------------------------------------------------------------------


def iter_tree(root: "tree_sitter.Node") -> Iterator["tree_sitter.Node"]:
    """Iterate over all nodes in a tree-sitter tree without recursion.

    Uses an explicit stack to avoid RecursionError on deeply nested code
    (e.g., TensorFlow has files exceeding Python's 1000-level limit).

    Args:
        root: The root node of the tree to traverse

    Yields:
        Each node in depth-first order.

    Example:
        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                # process function...
    """
    stack: list["tree_sitter.Node"] = [root]
    while stack:
        node = stack.pop()
        yield node
        # Add children in reverse order so leftmost is processed first
        stack.extend(reversed(node.children))


def iter_tree_with_context(
    root: "tree_sitter.Node",
    context_types: set[str],
) -> Iterator[tuple["tree_sitter.Node", Optional["tree_sitter.Node"]]]:
    """Iterate over nodes with parent context tracking.

    Useful for edge extraction where we need to know the enclosing
    function/method when processing call expressions.

    Args:
        root: The root node of the tree to traverse
        context_types: Node types that establish context (e.g., {"function_definition"})

    Yields:
        Tuples of (node, context_node) where context_node is the nearest
        ancestor matching one of context_types, or None if outside any context.

    Example:
        for node, func_ctx in iter_tree_with_context(tree.root_node, {"function_definition"}):
            if node.type == "call_expression" and func_ctx:
                # We know which function contains this call
    """
    # Stack entries: (node, current_context)
    stack: list[tuple["tree_sitter.Node", Optional["tree_sitter.Node"]]] = [
        (root, None)
    ]
    while stack:
        node, context = stack.pop()

        # Update context if this node is a context type
        new_context = node if node.type in context_types else context

        yield node, context

        # Add children with updated context
        for child in reversed(node.children):
            stack.append((child, new_context))


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------


def make_file_finder(patterns: list[str]) -> Callable[[Path], Iterator[Path]]:  # pragma: no cover
    """Create a file finder function for specific patterns.

    Args:
        patterns: Glob patterns to match (e.g., ["*.go"], ["*.rs"])

    Returns:
        A function that yields matching files from a repo root.
    """

    def finder(repo_root: Path) -> Iterator[Path]:
        yield from find_files(repo_root, patterns)

    return finder


# ---------------------------------------------------------------------------
# TreeSitterAnalyzer base class
# ---------------------------------------------------------------------------


class TreeSitterAnalyzer:
    """Base class for tree-sitter-based language analyzers.

    Encapsulates the universal two-pass architecture used by 100+ analyzers:
      Pass 1: Discover files, parse with tree-sitter, extract symbols
      Pass 2: Re-walk ASTs, resolve calls/imports against global symbol registry

    Subclasses configure via class attributes and override template methods
    for language-specific extraction logic.

    How It Works
    ------------
    1. Check grammar availability (``_check_grammar_available``)
    2. Initialize parser and AnalysisRun
    3. Pass 1: ``extract_symbols_from_file()`` for each source file
    4. Build global symbol registry via ``register_symbol()``
    5. Pass 2: ``extract_edges_from_file()`` for each file
    6. Pass 2b: ``extract_usage_contexts_from_file()`` for each file
    7. ``post_process()`` hook for cross-cutting concerns
    8. Assemble and return AnalysisResult

    Why This Design
    ---------------
    Previously, each analyzer duplicated this two-pass loop (~100 lines).
    The base class captures the scaffolding so subclasses focus solely on
    language-specific extraction logic. Analyzers can override any template
    method, or override ``analyze()`` entirely for full control.

    Grammar Modes
    -------------
    Two ways to specify the grammar:

    - ``grammar_module = "tree_sitter_go"`` — direct package import
    - ``language_pack_name = "nim"`` — uses tree_sitter_language_pack

    Exactly one should be set. The base class handles availability checking
    and parser creation for both modes.

    Example (simple analyzer)::

        class NimAnalyzer(TreeSitterAnalyzer):
            lang = "nim"
            file_patterns = ["*.nim", "*.nims"]
            language_pack_name = "nim"

            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                analysis = FileAnalysis()
                for node in iter_tree(tree.root_node):
                    if node.type == "proc_declaration":
                        # ... extract symbol
                return analysis

            def extract_edges_from_file(self, ...):
                # ... extract edges
                return edges

        _analyzer = NimAnalyzer()

        @register_analyzer("nim")
        def analyze_nim(repo_root, max_files=None):
            return _analyzer.analyze(repo_root, max_files)
    """

    # -- Required configuration (set by subclass) --------------------------
    lang: str = ""
    """Language identifier (e.g., "go", "rust", "python")."""

    pass_id: str = ""
    """Pass identifier (e.g., "go-v1", "rust-v1")."""

    pass_version: str = ""
    """Version string (e.g., "hypergumbo-0.1.0")."""

    file_patterns: ClassVar[list[str]] = []
    """Glob patterns for source files (e.g., ["*.go"], ["*.rs"])."""

    # -- Grammar source: exactly one of these should be set ----------------
    grammar_module: Optional[str] = None
    """Direct grammar package name (e.g., "tree_sitter_go")."""

    language_pack_name: Optional[str] = None
    """Language-pack grammar name (e.g., "nim")."""

    # -- Optional configuration --------------------------------------------
    resolver_class: type = NameResolver
    """Resolver class for symbol lookup during Pass 2."""

    create_file_symbols: bool = False
    """Whether to emit file-level symbols for each source file."""

    supports_max_files: bool = False
    """Whether analyze() should respect the max_files parameter."""

    # -- Template methods: grammar setup -----------------------------------

    def _check_grammar_available(self) -> bool:
        """Check if the tree-sitter grammar is available.

        Default implementation handles both grammar_module and
        language_pack_name modes. Override for custom availability logic.

        Returns:
            True if grammar is importable and usable.
        """
        if self.grammar_module is not None:
            return is_grammar_available(self.grammar_module)
        if self.language_pack_name is not None:
            if importlib.util.find_spec("tree_sitter") is None:
                return False  # pragma: no cover
            if importlib.util.find_spec("tree_sitter_language_pack") is None:
                return False  # pragma: no cover
            try:
                from tree_sitter_language_pack import get_language

                get_language(self.language_pack_name)
                return True
            except Exception:  # pragma: no cover
                return False
        return False  # pragma: no cover - no grammar configured

    def _create_parser(self) -> "tree_sitter.Parser":
        """Create and return a tree-sitter parser.

        Default implementation handles both grammar_module and
        language_pack_name modes. Override for custom parser setup.

        Returns:
            A configured tree-sitter Parser instance.
        """
        import tree_sitter

        if self.grammar_module is not None:
            mod = importlib.import_module(self.grammar_module)
            lang = tree_sitter.Language(mod.language())
            return tree_sitter.Parser(lang)

        # language_pack_name mode
        from tree_sitter_language_pack import get_language

        lang = get_language(self.language_pack_name)
        return tree_sitter.Parser(lang)

    # -- Template methods: symbol extraction (Pass 1) ----------------------

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single parsed file.

        Override this method with language-specific symbol extraction.
        Default returns empty FileAnalysis.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            rel_path: Path relative to repo root
            run: Current AnalysisRun for provenance

        Returns:
            FileAnalysis with symbols and symbol_by_name populated.
        """
        return FileAnalysis()  # pragma: no cover

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract import alias to module path mappings.

        Used during Pass 2 for call disambiguation (e.g., "np" -> "numpy").
        Default returns empty dict.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes

        Returns:
            Mapping of alias name to full module path.
        """
        return {}

    # -- Template methods: edge extraction (Pass 2) ------------------------

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract edges from a single parsed file.

        Override for language-specific edge extraction.
        Default returns empty list.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            rel_path: Path relative to repo root
            local_symbols: Symbol-by-name dict for this file
            global_symbols: All symbols across all files
            run: Current AnalysisRun for provenance
            import_aliases: Import alias mappings from get_import_aliases
            resolver: Configured name resolver for symbol lookup

        Returns:
            List of Edge instances.
        """
        return []  # pragma: no cover

    def extract_usage_contexts_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract UsageContext records for framework pattern matching.

        Default returns empty list. Override for route-emitting analyzers.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            symbol_by_name: Symbol-by-name dict for this file

        Returns:
            List of UsageContext instances.
        """
        return []

    # -- Template methods: global symbol registry --------------------------

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Add a symbol to the global registry for cross-file resolution.

        Default stores by short name (last segment after ".").
        Override for language-specific indexing (e.g., Go stores lists).

        Args:
            symbol: Symbol to register
            global_symbols: Mutable global registry dict
        """
        global_symbols[symbol.name] = symbol

    # -- Template methods: file discovery ------------------------------------

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield source files to analyze.

        Default uses ``find_files(repo_root, self.file_patterns)``.
        Override for custom filtering (e.g., F# skips Forth .fs files).

        Args:
            repo_root: Root directory of the repository.

        Yields:
            Paths to source files.
        """
        yield from find_files(repo_root, self.file_patterns)

    # -- Template methods: post-processing ---------------------------------

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: AnalysisRun,
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Optional post-processing after both passes complete.

        Use for route extraction, annotation edges, or other
        cross-cutting concerns. Default is identity.

        Args:
            symbols: All symbols from Pass 1
            edges: All edges from Pass 2
            usage_contexts: All usage contexts from Pass 2
            run: Current AnalysisRun

        Returns:
            Tuple of (symbols, edges, usage_contexts), possibly modified.
        """
        return symbols, edges, usage_contexts

    # -- shape_id computation (ADR-0014 §1) ---------------------------------

    _SHAPE_SKIP_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"comment", "line_comment", "block_comment", "ERROR", "MISSING"}
    )

    def compute_shape_id(self, node: "tree_sitter.Node") -> str:
        """Compute shape_id by hashing the structural skeleton of a CST subtree.

        Walks the tree-sitter concrete syntax tree non-recursively, strips
        identifiers, literals, comments, and punctuation, then hashes the
        resulting S-expression.  This captures the structural "shape" of code
        independent of naming and formatting.

        Filtering strategy (ADR-0014 §1):
        - Anonymous nodes (punctuation like ``{``, ``;``, ``(``) are skipped
        - Comment, ERROR, and MISSING nodes are skipped
        - Named leaf nodes (identifiers, literals) emit only their type name
        - Named non-leaf nodes emit ``(type child1 child2 ...)`` structure

        Returns:
            Shape hash in ``sha256:{16-hex-chars}`` format matching
            the Python analyzer's convention.
        """
        structure = self._cst_structure(node)
        hash_val = hashlib.sha256(structure.encode()).hexdigest()[:16]
        return f"sha256:{hash_val}"

    def _cst_structure(self, node: "tree_sitter.Node") -> str:
        """Build an S-expression skeleton of a CST subtree.

        Uses an explicit stack to avoid RecursionError on deeply nested code
        (same rationale as ``iter_tree()``).  The output is a parenthesised
        S-expression where only *named* structural nodes contribute, with
        leaf nodes (identifiers, literals, keywords) represented by their
        type name alone.

        Example output for ``def foo(x): return x + 1``::

            (function_definition identifier (parameters identifier)
             (block (return_statement (binary_operator identifier integer))))
        """
        parts: list[str] = []
        skip = self._SHAPE_SKIP_TYPES
        # Stack entries: (node | None, phase)
        #   phase 0 → process / open the node
        #   phase 1 → close the node (append ")")
        stack: list[tuple["tree_sitter.Node | None", int]] = [(node, 0)]

        while stack:
            current, phase = stack.pop()

            if phase == 1:
                parts.append(")")
                continue

            assert current is not None  # pragma: no cover - type narrowing

            # Skip anonymous nodes (punctuation) and filtered types
            if not current.is_named or current.type in skip:
                continue

            # Collect named, non-skip children
            named_children = [
                c for c in current.children
                if c.is_named and c.type not in skip
            ]

            if not named_children:
                # Leaf: just emit the type name (covers identifiers, literals, etc.)
                parts.append(current.type)
            else:
                # Non-leaf: open S-expression, push closing marker + children
                parts.append(f"({current.type}")
                stack.append((None, 1))
                for child in reversed(named_children):
                    stack.append((child, 0))

        return " ".join(parts)

    # -- Parameter classification (ADR-0014 §2) ----------------------------

    # Node types that indicate variadic positional params, by grammar.
    # Languages may add to this set via _VARARGS_NODE_TYPES class attribute.
    _VARARGS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "rest_pattern",          # JS/TS
        "rest_element",          # JS/TS (alternative)
        "spread_parameter",      # Java
        "splat_parameter",       # Ruby
        "variadic_parameter",    # C/C++, PHP
    })

    # Node types that indicate variadic keyword params.
    _KWARGS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "hash_splat_parameter",  # Ruby
        "dictionary_splat_pattern",  # Python (tree-sitter)
    })

    # Node types that indicate default parameter values.
    _DEFAULT_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "assignment_pattern",    # JS/TS
        "optional_parameter",    # Ruby, TypeScript
        "default_parameter",     # Python (tree-sitter)
    })

    def classify_parameter_flags(
        self, params_node: "tree_sitter.Node",
    ) -> ArityFlags:
        """Classify a function's parameter list into ArityFlags.

        Examines the children of a tree-sitter parameter-list node to
        determine parameter count, defaults, varargs, and kwargs.

        The default implementation uses heuristic node-type matching that
        covers most C-family and scripting languages.  Override for
        languages with unusual parameter models.

        Args:
            params_node: A tree-sitter node representing the parameter list
                (e.g., ``formal_parameters``, ``parameters``, ``parameter_list``).

        Returns:
            ArityFlags with the classified values.
        """
        param_count = 0
        has_defaults = False
        has_varargs = False
        has_kwargs = False

        for child in params_node.children:
            if not child.is_named:
                continue  # skip punctuation

            node_type = child.type

            # Check for varargs/kwargs/defaults
            if node_type in self._VARARGS_NODE_TYPES:
                has_varargs = True
                param_count += 1
            elif node_type in self._KWARGS_NODE_TYPES:
                has_kwargs = True
                param_count += 1
            elif node_type in self._DEFAULT_NODE_TYPES:
                has_defaults = True
                param_count += 1
            else:
                # Regular parameter (identifier, typed_parameter, etc.)
                param_count += 1
                # Check if any child has a default value (= expr)
                for sub in child.children:
                    if not sub.is_named:
                        continue
                    if sub.type in self._DEFAULT_NODE_TYPES or sub.type == "default_value":
                        has_defaults = True
                        break

        return ArityFlags(
            param_count=param_count,
            has_defaults=has_defaults,
            has_varargs=has_varargs,
            has_kwargs=has_kwargs,
        )

    # -- Main analysis method (the two-pass loop) --------------------------

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run the full two-pass analysis.

        This method orchestrates the entire analysis pipeline:
        1. Check grammar availability
        2. Initialize parser and AnalysisRun
        3. Pass 1: extract symbols from each file
        4. Build global symbol registry
        5. Pass 2: extract edges from each file
        6. Pass 2b: extract usage contexts
        7. Post-process
        8. Assemble and return AnalysisResult

        Args:
            repo_root: Root directory of the repository
            max_files: Optional limit on files to process

        Returns:
            AnalysisResult with symbols, edges, usage_contexts, and run.
        """
        start_time = time.time()
        effective_pass_id = self.pass_id or make_pass_id(self.lang)
        effective_pass_version = self.pass_version or PASS_VERSION
        run = AnalysisRun.create(pass_id=effective_pass_id, version=effective_pass_version)

        # 1. Check grammar availability
        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        # 2. Initialize parser
        parser = self._create_parser()

        # 3. Pass 1: Extract symbols from all files
        file_analyses: dict[Path, tuple[FileAnalysis, dict[str, str]]] = {}
        files_analyzed = 0
        files_skipped = 0

        for source_file in self._find_source_files(repo_root):
            if max_files is not None and files_analyzed >= max_files:
                break

            try:
                source = source_file.read_bytes()
            except OSError:
                files_skipped += 1
                continue

            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))

            analysis = self.extract_symbols_from_file(
                tree, source, source_file, rel_path, run
            )

            # Optional: create file-level symbol
            if self.create_file_symbols:
                file_sym = Symbol(
                    id=make_file_id(self.lang, rel_path),
                    name=rel_path,
                    kind="file",
                    language=self.lang,
                    path=rel_path,
                    span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
                    origin=effective_pass_id,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.insert(0, file_sym)

            # Auto-compute shape_id for symbols with associated nodes (ADR-0014 §1)
            if analysis.node_for_symbol:
                sym_by_id = {s.id: s for s in analysis.symbols}
                for sym_id, ts_node in analysis.node_for_symbol.items():
                    sym = sym_by_id.get(sym_id)
                    if sym is not None and sym.shape_id is None:
                        sym.shape_id = self.compute_shape_id(ts_node)

            # Extract import aliases for Pass 2
            import_aliases = self.get_import_aliases(tree, source)

            file_analyses[source_file] = (analysis, import_aliases)
            files_analyzed += 1

        # 4. Build global symbol registry
        global_symbols: dict = {}
        for analysis, _ in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)

        # 5. Pass 2: Extract edges and usage contexts
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        all_contexts: list[UsageContext] = []
        resolver = self.resolver_class(global_symbols)

        for source_file, (analysis, import_aliases) in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            # Re-parse for Pass 2
            source = source_file.read_bytes()
            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))

            edges = self.extract_edges_from_file(
                tree, source, source_file, rel_path,
                analysis.symbol_by_name, global_symbols, run,
                import_aliases, resolver,
            )
            all_edges.extend(edges)

            # 6. Usage contexts
            contexts = self.extract_usage_contexts_from_file(
                tree, source, source_file, analysis.symbol_by_name,
            )
            all_contexts.extend(contexts)

        # 7. Post-process
        all_symbols, all_edges, all_contexts = self.post_process(
            all_symbols, all_edges, all_contexts, run,
        )

        # 8. Assemble result
        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            usage_contexts=all_contexts,
            run=run,
        )

    # -- Registration helper -----------------------------------------------

    def as_registered_analyzer(self) -> Callable:
        """Return a function suitable for use with @register_analyzer.

        Returns a function with signature
        ``(repo_root: Path, max_files: int | None = None) -> AnalysisResult``
        that delegates to ``self.analyze()``.

        Example::

            _analyzer = GoAnalyzer()

            @register_analyzer("go", priority=50)
            def analyze_go(repo_root, max_files=None):
                return _analyzer.analyze(repo_root, max_files)

            # Or equivalently:
            analyze_go = register_analyzer("go")(_analyzer.as_registered_analyzer())

        Returns:
            A callable that wraps ``self.analyze()``.
        """

        def analyze_fn(
            repo_root: Path, max_files: Optional[int] = None
        ) -> AnalysisResult:
            return self.analyze(repo_root, max_files)

        return analyze_fn
