"""Linker registry for dynamic dispatch.

This module provides a registration system for cross-language linkers,
enabling loop-based dispatch in run_behavior_map() instead of
many repetitive code blocks.

How It Works
------------
1. Each linker module calls `register_linker()` at import time
2. The registry stores linker functions by name
3. `run_behavior_map()` iterates over `get_all_linkers()`
4. Each linker is called uniformly via `run_linker()` with LinkerContext

Why This Design
---------------
- Adding a new linker requires only creating the linker file
- No need to edit cli.py imports or run_behavior_map()
- Linkers can specify their own ordering priority
- Consistent interface for all linkers despite different needs

LinkerContext
-------------
Linkers have heterogeneous input needs (some need repo_root only,
others need filtered symbols, captured symbols, etc.). LinkerContext
provides all possible inputs, and each linker takes what it needs.

Usage
-----
In a linker module:

    from .registry import register_linker, LinkerContext, LinkerResult

    @register_linker("ipc", priority=50)
    def link_ipc(ctx: LinkerContext) -> LinkerResult:
        repo_root = ctx.repo_root
        # ... do linking ...
        return LinkerResult(symbols=symbols, edges=edges, run=run)

In cli.py:

    from .linkers.registry import get_all_linkers, run_all_linkers, LinkerContext

    ctx = LinkerContext(repo_root=repo_root, symbols=all_symbols, ...)
    results = run_all_linkers(ctx)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:
    from ..ir import AnalysisRun, Edge, Symbol


@dataclass
class LinkerContext:
    """Context passed to all linkers.

    Contains all possible inputs a linker might need. Each linker
    picks what it needs from this context.

    Attributes:
        repo_root: Repository root path
        symbols: All symbols collected so far
        edges: All edges collected so far
        captured_symbols: Symbols captured by specific analyzers (for JNI, etc.)
            Maps analyzer name to list of symbols (e.g., {"c": [...], "java": [...]})

    Unresolved Edge Protocol
    ------------------------
    Analyzers create "unresolved" edges when they detect calls to external
    symbols that can't be resolved within the same pass. Format:

        {lang}:{package_or_path}:0-0:{name}:unresolved

    Linkers can use `get_unresolved_edges()` to find these edges and resolve
    them using `find_symbols_matching()`. This enables:

    1. Go analyzer creates unresolved edge: go:github.com/foo/grpc:0-0:RegisterUserServer:unresolved
    2. gRPC linker finds this edge and resolves it to the actual RegisterUserServer function
    3. Linker creates proper edge to the resolved symbol
    """

    repo_root: Path
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    captured_symbols: dict[str, list[Symbol]] = field(default_factory=dict)

    # Cached indexes, built lazily
    _symbol_by_id: dict[str, "Symbol"] | None = field(
        default=None, init=False, repr=False
    )
    _symbols_by_name: dict[str, list["Symbol"]] | None = field(
        default=None, init=False, repr=False
    )
    _symbols_by_path: dict[str, list["Symbol"]] | None = field(
        default=None, init=False, repr=False
    )

    def _ensure_indexes(self) -> None:
        """Build symbol indexes if not already built."""
        if self._symbol_by_id is None:
            self._symbol_by_id = {s.id: s for s in self.symbols}
            self._symbols_by_name = {}
            self._symbols_by_path = {}
            for s in self.symbols:
                # Index by short name (last component)
                short_name = s.name.split(".")[-1] if "." in s.name else s.name
                if short_name not in self._symbols_by_name:
                    self._symbols_by_name[short_name] = []
                self._symbols_by_name[short_name].append(s)
                # Index by path for enclosing symbol lookups
                if s.path not in self._symbols_by_path:
                    self._symbols_by_path[s.path] = []
                self._symbols_by_path[s.path].append(s)

    def get_symbol_by_id(self, symbol_id: str) -> "Symbol | None":
        """Look up a symbol by its ID.

        Args:
            symbol_id: The symbol ID to look up

        Returns:
            The Symbol if found, None otherwise.
        """
        self._ensure_indexes()
        assert self._symbol_by_id is not None  # for type checker
        return self._symbol_by_id.get(symbol_id)

    def find_symbols_by_name(self, name: str) -> list["Symbol"]:
        """Find all symbols matching a name.

        Args:
            name: The symbol name to search for (matches short name)

        Returns:
            List of matching symbols (may be empty).
        """
        self._ensure_indexes()
        assert self._symbols_by_name is not None  # for type checker
        return self._symbols_by_name.get(name, [])

    def find_enclosing_symbol(
        self,
        path: str,
        line: int,
        kinds: tuple[str, ...] = ("function", "method", "class", "module"),
    ) -> "Symbol | None":
        """Find the symbol that encloses a given line.

        Used by linkers to connect synthetic nodes (grpc_stub, mq_publisher)
        to the functions that contain them, enabling slice traversal.

        Args:
            path: File path (can be absolute or relative, matches suffix)
            line: Line number to find enclosing symbol for
            kinds: Symbol kinds to consider (default: function, method, class, module)
                   Module nodes are created for script-only Python files.

        Returns:
            The smallest enclosing symbol, or None if no match.
            Prefers more specific symbols (method > function > class > module).
        """
        self._ensure_indexes()
        assert self._symbols_by_path is not None  # for type checker

        # Try exact path match first
        candidates = self._symbols_by_path.get(path, [])

        # If no match, try suffix matching (handles absolute vs relative paths)
        if not candidates:
            for p, syms in self._symbols_by_path.items():
                if p.endswith(path) or path.endswith(p):
                    candidates = syms
                    break

        if not candidates:
            return None

        # Filter by kind and find enclosing symbols
        enclosing = []
        for sym in candidates:
            if sym.kind not in kinds:
                continue
            if sym.span is None:  # pragma: no cover - defensive for malformed symbols
                continue
            if sym.span.start_line <= line <= sym.span.end_line:
                enclosing.append(sym)

        if not enclosing:
            return None

        # Return the smallest (most specific) enclosing symbol
        # Prefer function/method over class over module
        def specificity(s: "Symbol") -> tuple[int, int]:
            # Lower is better: (kind_priority, span_size)
            kind_priority = {"method": 0, "function": 1, "class": 2, "module": 3}.get(s.kind, 4)
            span_size = (s.span.end_line - s.span.start_line) if s.span else 9999
            return (kind_priority, span_size)

        return min(enclosing, key=specificity)

    def get_unresolved_edges(
        self,
        lang: str | None = None,
    ) -> list["Edge"]:
        """Get edges pointing to unresolved symbols.

        Unresolved edges have dst matching pattern:
            {lang}:{package}:0-0:{name}:unresolved

        Args:
            lang: Optional language filter (e.g., "go", "python")

        Returns:
            List of edges with unresolved destinations.
        """
        result = []
        for edge in self.edges:
            if not edge.dst.endswith(":unresolved"):
                continue
            if lang is not None:
                # Check if edge dst starts with the language
                if not edge.dst.startswith(f"{lang}:"):
                    continue
            result.append(edge)
        return result

    def parse_unresolved_dst(
        self,
        dst: str,
    ) -> dict[str, str] | None:
        """Parse an unresolved destination ID into components.

        Args:
            dst: Destination ID like "go:github.com/foo/pkg:0-0:FuncName:unresolved"

        Returns:
            Dict with keys 'lang', 'package', 'name' or None if not unresolved format.
        """
        if not dst.endswith(":unresolved"):
            return None

        parts = dst.split(":")
        if len(parts) < 5:
            return None

        return {
            "lang": parts[0],
            "package": parts[1],
            "name": parts[-2],  # second to last is the name
        }


@dataclass
class LinkerResult:
    """Result from running a linker.

    Attributes:
        symbols: New symbols created by the linker
        edges: New edges created by the linker
        run: AnalysisRun metadata (optional)
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


# Type alias for linker functions
LinkerFunc = Callable[[LinkerContext], LinkerResult]


@dataclass
class LinkerRequirement:
    """A requirement for a linker to produce useful edges.

    Attributes:
        name: Short identifier (e.g., "java_native", "c_jni_functions")
        description: Human-readable description (e.g., "Java native methods")
        check: Function that takes LinkerContext and returns count of available items.
            Return 0 to indicate the requirement is unmet.
    """

    name: str
    description: str
    check: Callable[[LinkerContext], int]


@dataclass
class RegisteredLinker:
    """Metadata for a registered linker.

    Attributes:
        name: Unique identifier (e.g., "jni", "http", "ipc")
        func: The linker function
        priority: Execution order (lower = earlier). Default 50.
            Early linkers (JNI) run first; late linkers (dependency) run last.
        description: Human-readable description
        requirements: List of requirements the linker needs to produce useful edges.
    """

    name: str
    func: LinkerFunc
    priority: int = 50
    description: str = ""
    requirements: list[LinkerRequirement] = field(default_factory=list)


# Global registry of linkers
_LINKER_REGISTRY: dict[str, RegisteredLinker] = {}


def register_linker(
    name: str,
    priority: int = 50,
    description: str = "",
    requirements: list[LinkerRequirement] | None = None,
) -> Callable[[LinkerFunc], LinkerFunc]:
    """Decorator to register a linker function.

    Args:
        name: Unique identifier for this linker (e.g., "jni", "http")
        priority: Execution order (lower = earlier).
        description: Human-readable description of what the linker does.
        requirements: List of requirements the linker needs. When requirements
            are unmet (check returns 0), the linker may produce no edges.

    Returns:
        Decorator that registers the function and returns it unchanged.

    Example:
        @register_linker("ipc", priority=50, description="IPC patterns")
        def link_ipc(ctx: LinkerContext) -> LinkerResult:
            ...
    """

    def decorator(func: LinkerFunc) -> LinkerFunc:
        _LINKER_REGISTRY[name] = RegisteredLinker(
            name=name,
            func=func,
            priority=priority,
            description=description,
            requirements=requirements or [],
        )
        return func

    return decorator


def get_linker(name: str) -> RegisteredLinker | None:
    """Get a registered linker by name.

    Args:
        name: The linker identifier

    Returns:
        The RegisteredLinker, or None if not found.
    """
    return _LINKER_REGISTRY.get(name)


def get_all_linkers() -> Iterator[RegisteredLinker]:
    """Get all registered linkers in priority order.

    Yields:
        RegisteredLinker objects, sorted by priority (ascending).
    """
    for linker in sorted(_LINKER_REGISTRY.values(), key=lambda lnk: lnk.priority):
        yield linker


def run_linker(
    name: str,
    ctx: LinkerContext,
) -> LinkerResult:
    """Run a specific linker by name.

    Args:
        name: The linker identifier
        ctx: LinkerContext with all inputs

    Returns:
        LinkerResult from the linker

    Raises:
        KeyError: If the linker is not registered.
    """
    linker = _LINKER_REGISTRY.get(name)
    if linker is None:
        raise KeyError(f"Unknown linker: {name}")
    return linker.func(ctx)


def run_all_linkers(ctx: LinkerContext) -> list[tuple[str, LinkerResult]]:
    """Run all registered linkers in priority order.

    After all linkers run, a post-processing pass connects synthetic nodes
    (grpc_stub, mq_publisher, etc.) to their enclosing functions. This
    enables slice traversal from application code through linker boundaries.

    Args:
        ctx: LinkerContext with all inputs

    Returns:
        List of (name, result) tuples in execution order.
    """
    results = []
    all_linker_symbols: list[Symbol] = []

    # Run all registered linkers
    for linker in get_all_linkers():
        result = linker.func(ctx)
        results.append((linker.name, result))
        all_linker_symbols.extend(result.symbols)

    # Post-process: connect synthetic nodes to enclosing functions
    enclosure_edges = _connect_synthetic_to_enclosing(ctx, all_linker_symbols)
    if enclosure_edges:
        from ..ir import AnalysisRun
        run = AnalysisRun.create(  # nosec B106 - pass_id is not a password
            pass_id="enclosure-linker-v1",
            version="hypergumbo-0.1.0",
        )
        results.append(("enclosure", LinkerResult(edges=enclosure_edges, run=run)))

    return results


# Synthetic node kinds that should be connected to enclosing functions
SYNTHETIC_KINDS = frozenset({
    "grpc_stub",
    "grpc_server",
    "mq_publisher",
    "mq_subscriber",
    "websocket_endpoint",
    "websocket_emitter",
    "websocket_listener",
    "event_publisher",
    "event_subscriber",
    "ipc_publisher",
    "ipc_subscriber",
    "db_query",
    "http_client",
})


def _connect_synthetic_to_enclosing(
    ctx: LinkerContext,
    linker_symbols: list["Symbol"],
) -> list["Edge"]:
    """Connect synthetic nodes to their enclosing functions.

    This post-processing pass enables slice traversal from application code
    through linker-created synthetic nodes (grpc_stub, mq_publisher, etc.).

    Args:
        ctx: LinkerContext with analyzer symbols for enclosing lookup
        linker_symbols: Symbols created by linkers in this run

    Returns:
        List of 'uses' edges from enclosing functions to synthetic nodes.
    """
    from ..ir import Edge

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()

    for sym in linker_symbols:
        # Skip non-Symbol objects (e.g., mock data in tests)
        if not hasattr(sym, "kind"):
            continue

        # Only process synthetic node kinds
        if sym.kind not in SYNTHETIC_KINDS:
            continue

        # Need span to find enclosing function
        if sym.span is None:  # pragma: no cover - defensive for malformed symbols
            continue

        # Find enclosing function/method/class
        enclosing = ctx.find_enclosing_symbol(sym.path, sym.span.start_line)
        if enclosing is None:
            continue

        # Avoid duplicate edges
        pair = (enclosing.id, sym.id)
        if pair in seen_pairs:  # pragma: no cover - rare edge case
            continue
        seen_pairs.add(pair)

        # Create edge from enclosing function to synthetic node
        edges.append(Edge.create(
            src=enclosing.id,
            dst=sym.id,
            edge_type="uses",
            line=sym.span.start_line,
            confidence=0.9,
            origin="enclosure-linker-v1",
            evidence_type="enclosing_scope",
        ))

    return edges


def clear_registry() -> None:
    """Clear the linker registry. For testing only."""
    _LINKER_REGISTRY.clear()


def list_registered() -> list[str]:
    """List all registered linker names. For debugging."""
    return list(_LINKER_REGISTRY.keys())


@dataclass
class RequirementStatus:
    """Status of a single linker requirement.

    Attributes:
        name: Requirement identifier
        description: Human-readable description
        count: Number of matching items found (0 = unmet)
        met: True if count > 0
    """

    name: str
    description: str
    count: int
    met: bool


@dataclass
class LinkerDiagnostics:
    """Diagnostics for a linker's requirements.

    Attributes:
        linker_name: Name of the linker
        linker_description: Description of what the linker does
        requirements: Status of each requirement
        all_met: True if all requirements are met
    """

    linker_name: str
    linker_description: str
    requirements: list[RequirementStatus]
    all_met: bool


def check_linker_requirements(ctx: LinkerContext) -> list[LinkerDiagnostics]:
    """Check which linkers have met/unmet requirements.

    This helps users understand why a linker produced no edges.
    For example, the JNI linker requires both Java native methods
    AND C JNI functions - if either is missing, it produces no edges.

    Args:
        ctx: LinkerContext with symbols, edges, etc.

    Returns:
        List of LinkerDiagnostics, one per linker with requirements.
        Linkers without requirements are omitted.

    Example output:
        LinkerDiagnostics(
            linker_name="jni",
            linker_description="Java/C JNI bridge",
            requirements=[
                RequirementStatus(name="java_native", description="Java native methods", count=0, met=False),
                RequirementStatus(name="c_jni_functions", description="C JNI functions", count=5, met=True),
            ],
            all_met=False,
        )
    """
    diagnostics = []

    for linker in get_all_linkers():
        if not linker.requirements:
            continue

        statuses = []
        all_met = True

        for req in linker.requirements:
            count = req.check(ctx)
            met = count > 0
            statuses.append(RequirementStatus(
                name=req.name,
                description=req.description,
                count=count,
                met=met,
            ))
            if not met:
                all_met = False

        diagnostics.append(LinkerDiagnostics(
            linker_name=linker.name,
            linker_description=linker.description,
            requirements=statuses,
            all_met=all_met,
        ))

    return diagnostics
