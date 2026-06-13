# SPDX-License-Identifier: AGPL-3.0-or-later
"""Linker registry for dynamic dispatch.

This module provides a registration system for cross-language
linkers (Tier 2 edge-recovery passes), enabling loop-based dispatch
in run_behavior_map() instead of many repetitive code blocks.

Subcategory taxonomy (ADR-0003-ext)
-----------------------------------
Every linker belongs to one of four subcategories. The categorization
is declared in each linker module's top-level docstring (per the
``generate-architecture`` lint) and pinned by ``_LINKER_SUBCATEGORIES``:

- **Protocol** — framework-agnostic over-the-wire shapes
  (HTTP, WebSocket, message queues, SQL).
- **Bridge** — language-pair FFI (JNI, wasm_bindgen, Tauri IPC,
  cgo, napi, Lua FFI, Ruby FFI, PyFFI / ctypes / cffi / PyO3).
- **Framework** — framework-specific dispatch (gRPC, GraphQL,
  React/Vue components, DI resolution, ORM, view-template).
- **Infrastructure** — graph-structural utilities (containment,
  inheritance, module imports, method-call recovery).

How It Works
------------
1. Each linker module calls ``register_linker()`` at import time.
   Registration optionally includes:
   - ``priority``: orders execution; lower runs first
   - ``activation``: a ``LinkerActivation`` declaring when the
     linker should run (``always``, ``frameworks=...``,
     ``language_pairs=...``). Per ADR-0003, framework-gated
     linkers stay dormant when their framework isn't detected.
   - ``requirements``: language presence the linker depends on,
     consumed by ``partial_install_warnings`` to surface "linker X
     ran with only some of its requirements met" diagnostics.
2. The registry stores linker functions by name.
3. ``run_all_linkers()`` groups linkers by priority and dispatches
   each priority cohort to a ``ThreadPoolExecutor`` so independent
   linkers run in parallel. A shared ``parsed_trees`` cross-linker
   parse cache (per-language) avoids re-parsing the same files.
4. Each linker is called uniformly via ``run_linker()`` with
   LinkerContext.
5. A post-pass (``_connect_synthetic_to_enclosing``) wires synthetic
   nodes minted by linkers into their enclosing real symbols.

Synthetic-symbol vocabulary
---------------------------
``SYNTHETIC_FRAMEWORK_ROLES`` enumerates the canonical
``meta["framework_role"]`` values that linkers may attach to
synthetic Symbol nodes (e.g., a synthetic "Tauri IPC handler" node).
This vocabulary replaced the old ``SYNTHETIC_KINDS`` set after
ADR-0027's Phase-4b kind-vs-framework-role axis split.

Why This Design
---------------
- Adding a new linker requires only creating the linker file
- No need to edit cli.py imports or run_behavior_map()
- Linkers can specify their own ordering priority and gating
- Consistent interface for all linkers despite different needs
- Parallel within-priority dispatch keeps wall time bounded even
  as the linker set grows

LinkerContext
-------------
Linkers have heterogeneous input needs (some need repo_root only,
others need filtered symbols, captured symbols, etc.). LinkerContext
provides all possible inputs, and each linker takes what it needs.

``run_all_linkers()`` populates per-linker identity fields
(``linker_pass_id``, ``linker_pass_version``) on each context before
dispatch, and stamps ``pass_version`` on the returned ``AnalysisRun``
via ``_stamp_pass_version()`` — so linker bodies don't need to thread
pass_version manually.

Usage
-----
In a linker module:

    from .registry import register_linker, LinkerContext, LinkerResult

    @register_linker(
        "ipc",
        priority=50,
        activation=LinkerActivation(frameworks={"tauri"}),
        requirements=LinkerRequirement(...),
    )
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

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

if TYPE_CHECKING:
    from ..ir import AnalysisRun, Edge, Symbol
    from ..limits import Limits


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

    # Framework and language detection results (for linker filtering)
    detected_frameworks: set[str] = field(default_factory=set)
    detected_languages: set[str] = field(default_factory=set)

    # Per-linker identity — populated by run_all_linkers before dispatch.
    linker_pass_id: str = ""
    linker_pass_version: str = ""

    # Cross-linker tree-sitter parse cache. Populated lazily by the linker
    # docstring/comment masker (linkers/_text_filters) so the second linker
    # to scan a given file reuses the first linker's parse. Forward-compat
    # entry point for the analyze pass to pre-populate trees.
    # Key: (absolute_path_str, language). Value: tree_sitter.Tree.
    parsed_trees: dict[tuple[str, str], Any] = field(default_factory=dict)

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

    def create_run(self) -> "AnalysisRun":
        """Create an AnalysisRun stamped with this linker's pass_version."""
        if not self.linker_pass_id:
            raise ValueError(
                "linker_pass_id is empty — create_run() requires "
                "per-linker identity (set by run_all_linkers)"
            )
        from ..ir import PASS_VERSION, AnalysisRun

        return AnalysisRun.create(
            pass_id=self.linker_pass_id,
            version=PASS_VERSION,
            pass_version=self.linker_pass_version,
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
        kinds: tuple[str, ...] = ("function", "method", "class", "module", "file"),
    ) -> "Symbol | None":
        """Find the symbol that encloses a given line.

        Used by linkers to connect synthetic nodes (grpc_stub, mq_publisher)
        to the functions that contain them, enabling slice traversal.

        Args:
            path: File path (can be absolute or relative, matches suffix)
            line: Line number to find enclosing symbol for
            kinds: Symbol kinds to consider (default: function, method, class,
                   module, file). Module nodes are created for script-only
                   non-Python files; INV-hojus collapsed Python's module-kind
                   pseudo-node into file-kind, so file is the canonical
                   "this file" container.

        Returns:
            The smallest enclosing symbol, or None if no match.
            Prefers more specific symbols (method > function > class > module).
        """
        self._ensure_indexes()
        assert self._symbols_by_path is not None  # for type checker

        # Collect candidate symbols from matching paths.
        # When linker-produced synthetic nodes share a file with analyzer
        # symbols, the exact path may return only synthetic nodes (wrong
        # kind).  We gather from ALL matching paths (exact + suffix) so
        # the kind filter below has the full candidate set.
        candidate_sets: list[list["Symbol"]] = []

        exact = self._symbols_by_path.get(path, [])
        if exact:
            candidate_sets.append(exact)

        # Suffix matching (handles absolute vs relative path mismatches).
        # Always check — even when exact match exists — because analyzer
        # symbols may be indexed under relative paths while linker symbols
        # use absolute paths (or vice versa after CLI normalization).
        for p, syms in self._symbols_by_path.items():
            if p == path:
                continue  # already included via exact match
            if p.endswith(path) or path.endswith(p):
                candidate_sets.append(syms)

        if not candidate_sets:
            return None

        # Merge candidates (may contain duplicates across sets, but that's
        # harmless — we pick the smallest enclosing match below)
        candidates: list["Symbol"] = []
        for s in candidate_sets:
            candidates.extend(s)

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
        # Prefer function/method over class over module/file
        def specificity(s: "Symbol") -> tuple[int, int]:
            # Lower is better: (kind_priority, span_size)
            # INV-hojus: file shares the file-level slot with module so
            # nested children always win over the file container.
            kind_priority = {
                "method": 0, "function": 1, "class": 2,
                "module": 3, "file": 3,
            }.get(s.kind, 4)
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
class LinkerActivation:
    """Activation conditions for a linker (ADR-0003).

    Linkers have different activation conditions:
    - Protocol linkers: always=True (run unconditionally)
    - Framework linkers: frameworks=["grpc"] (run if framework detected)
    - Language-pair linkers: language_pairs=[("java", "c")] (run if both present)

    Activation is evaluated as:
    - If always=True: always run
    - Otherwise: run if ANY framework matches OR ANY language pair matches

    Attributes:
        always: If True, linker always runs (protocol linkers)
        frameworks: List of frameworks that trigger this linker
        language_pairs: List of (lang1, lang2) tuples; linker runs if both present
    """

    always: bool = False
    frameworks: list[str] = field(default_factory=list)
    language_pairs: list[tuple[str, str]] = field(default_factory=list)

    def should_run(
        self,
        detected_frameworks: set[str],
        detected_languages: set[str],
    ) -> bool:
        """Check if this linker should run given detected frameworks/languages.

        Args:
            detected_frameworks: Set of detected framework names
            detected_languages: Set of detected language names

        Returns:
            True if the linker should run, False otherwise.
        """
        if self.always:
            return True

        # Check framework conditions (any match)
        if self.frameworks:
            for fw in self.frameworks:
                if fw in detected_frameworks:
                    return True

        # Check language pair conditions (any pair with both present)
        if self.language_pairs:
            for lang1, lang2 in self.language_pairs:
                if lang1 in detected_languages and lang2 in detected_languages:
                    return True

        # No conditions met, and not always=True
        return False


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
        activation: Conditions under which this linker should run (ADR-0003).
    """

    name: str
    func: LinkerFunc
    priority: int = 50
    description: str = ""
    requirements: list[LinkerRequirement] = field(default_factory=list)
    activation: LinkerActivation = field(default_factory=lambda: LinkerActivation(always=True))
    # INV-morag PR 2: catalog metadata co-located with registration.
    pass_label: str = ""
    backend: str = ""
    languages: list[str] = field(default_factory=list)
    availability: str = "core"
    requires: str | None = None
    pass_version: str = ""
    # WI-hupaz / WI-dilab / INV-hujog: pass-id dependencies surfaced into
    # ``Pass.depends_on``, expressed in CNF (outer-AND of inner-OR clauses).
    # Distinct from ``activation`` (which gates "should this linker run at all
    # for this repo") and ``requirements`` (which gates "does this linker have
    # enough symbols to do useful work"). ``depends_on`` names the upstream
    # analyzer pass IDs whose output this linker structurally requires —
    # i.e., without those analyzers running, this linker cannot produce its
    # intended edges at all.
    #
    # CNF shape examples:
    #   [["javascript"]]              — single conjunct, single literal:
    #                                    "javascript must be active"
    #   [["python", "javascript"]]    — single conjunct, multi-literal OR:
    #                                    "python OR javascript must be active"
    #   [["java"], ["c", "cpp", "rust"]]  — two conjuncts:
    #                                    "java AND (c OR cpp OR rust)"
    #   []                            — no declared dependency (honest for
    #                                    language-agnostic Infrastructure
    #                                    linkers like containment)
    depends_on: list[list[str]] = field(default_factory=list)


# Global registry of linkers
_LINKER_REGISTRY: dict[str, RegisteredLinker] = {}


def register_linker(  # nosec B107 — pass_label/backend defaults are tag strings, not passwords; bandit flags any "pass*" name with "" default
    name: str,
    priority: int = 50,
    description: str = "",
    requirements: list[LinkerRequirement] | None = None,
    activation: LinkerActivation | None = None,
    pass_label: str = "",
    backend: str = "",
    languages: list[str] | None = None,
    availability: str = "core",
    requires: str | None = None,
    depends_on: list[list[str]] | None = None,
) -> Callable[[LinkerFunc], LinkerFunc]:
    """Decorator to register a linker function.

    Args:
        name: Unique identifier for this linker (e.g., "jni", "http")
        priority: Execution order (lower = earlier).
        description: Human-readable description of what the linker does.
        requirements: List of requirements the linker needs. When requirements
            are unmet (check returns 0), the linker may produce no edges.
        activation: Conditions under which this linker should run (ADR-0003).
            If None, defaults to always=True (protocol linker behavior).

    Returns:
        Decorator that registers the function and returns it unchanged.

    Example:
        @register_linker(
            "grpc",
            priority=30,
            description="gRPC service linking",
            activation=LinkerActivation(frameworks=["grpc", "protobuf"]),
        )
        def link_grpc(ctx: LinkerContext) -> LinkerResult:
            ...
    """

    def decorator(func: LinkerFunc) -> LinkerFunc:
        from ..ir import compute_pass_version

        _LINKER_REGISTRY[name] = RegisteredLinker(
            name=name,
            func=func,
            priority=priority,
            description=description,
            requirements=requirements or [],
            activation=activation or LinkerActivation(always=True),
            pass_label=pass_label or name,
            backend=backend,
            languages=list(languages) if languages else [],
            availability=availability,
            requires=requires,
            pass_version=compute_pass_version(func),
            depends_on=[list(clause) for clause in depends_on] if depends_on else [],
        )
        return func

    return decorator


def should_run_linker(
    name: str,
    detected_frameworks: set[str],
    detected_languages: set[str],
) -> bool:
    """Check if a linker should run given detected frameworks/languages.

    Args:
        name: The linker identifier
        detected_frameworks: Set of detected framework names
        detected_languages: Set of detected language names

    Returns:
        True if the linker should run, False if not found or shouldn't run.
    """
    linker = _LINKER_REGISTRY.get(name)
    if linker is None:  # pragma: no cover - defensive for unknown linker
        return False
    return linker.activation.should_run(detected_frameworks, detected_languages)


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


def _run_linker_with_cache(
    func: "Callable[[LinkerContext], LinkerResult]",
    ctx: LinkerContext,
) -> "LinkerResult":
    """Invoke ``func(ctx)`` with the parse cache contextvar bound.

    Centralizes the contextvar plumbing so the masker in
    ``linkers/_text_filters`` can read/write ``ctx.parsed_trees`` without
    every linker passing the cache explicitly.
    """
    from ._text_filters import reset_active_parse_cache, set_active_parse_cache

    token = set_active_parse_cache(ctx.parsed_trees)
    try:
        return func(ctx)
    finally:
        reset_active_parse_cache(token)


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
    return _run_linker_with_cache(linker.func, ctx)


def _stamp_pass_version(result: LinkerResult, linker: RegisteredLinker) -> None:
    """Stamp the linker's code-hash pass_version onto its AnalysisRun."""
    if result.run is not None and not result.run.pass_version:
        result.run.pass_version = linker.pass_version


def _record_linker_crash(
    limits: "Limits | None", linker_name: str, exc: BaseException
) -> None:
    """Record a linker that crashed mid-run so the run stays fail-open.

    §17 / WI-madal L3: an exception escaping a single linker must not abort the
    whole linker phase. When a ``Limits`` sink is supplied (the
    ``run_behavior_map`` path), the crash is recorded pass-level via
    ``Limits.record_crashed_pass``; callers without a sink (direct/test
    invocations) still get skip-and-continue containment.
    """
    if limits is not None:
        limits.record_crashed_pass(linker_name, exc)


def run_all_linkers(
    ctx: LinkerContext, limits: "Limits | None" = None
) -> list[tuple[str, LinkerResult]]:
    """Run all registered linkers in priority order.

    Linkers are filtered by their activation conditions:
    - always=True: Run unconditionally (protocol linkers)
    - frameworks=[...]: Run if any framework is detected
    - language_pairs=[...]: Run if both languages in a pair are detected

    A linker that raises is contained (§17 fail-open / WI-madal L3): the run
    continues with the remaining linkers, and — when ``limits`` is supplied —
    the crash is recorded pass-level in ``limits.skipped_passes``.

    After all linkers run, a post-processing pass connects synthetic nodes
    (grpc_stub, mq_publisher, etc.) to their enclosing functions. This
    enables slice traversal from application code through linker boundaries.

    Args:
        ctx: LinkerContext with all inputs (including detected_frameworks/languages)
        limits: Optional Limits sink; when provided, a crashing linker is
            recorded there instead of being silently skipped.

    Returns:
        List of (name, result) tuples in execution order.
    """
    results = []
    all_linker_symbols: list[Symbol] = []

    # Build accumulating lists that include original + linker-produced data.
    # We copy so that extending these doesn't mutate the caller's lists.
    accum_symbols = list(ctx.symbols)
    accum_edges = list(ctx.edges)

    # Filter to active linkers (already sorted by priority from get_all_linkers)
    active_linkers = [
        linker for linker in get_all_linkers()
        if linker.activation.should_run(
            ctx.detected_frameworks, ctx.detected_languages
        )
    ]

    # Group by priority — linkers at the same priority are independent
    # and can run in parallel.  E.g., inheritance linker (priority 15)
    # creates implements edges that type_hierarchy (priority 60) needs,
    # but linkers *within* the same priority never depend on each other.
    for _priority, group_iter in groupby(active_linkers, key=lambda lnk: lnk.priority):
        group = list(group_iter)

        # Snapshot accumulated state for this priority group. The
        # parsed_trees dict is shared by reference across all per-linker
        # contexts so trees parsed by one linker are reused by the next.
        running_ctx = LinkerContext(
            repo_root=ctx.repo_root,
            symbols=accum_symbols,
            edges=accum_edges,
            captured_symbols=ctx.captured_symbols,
            detected_frameworks=ctx.detected_frameworks,
            detected_languages=ctx.detected_languages,
            parsed_trees=ctx.parsed_trees,
        )

        if len(group) == 1:
            # Single linker — run directly (avoids thread pool overhead)
            linker = group[0]
            running_ctx.linker_pass_id = linker.name
            running_ctx.linker_pass_version = linker.pass_version
            try:
                result = _run_linker_with_cache(linker.func, running_ctx)
            except Exception as exc:
                # §17 fail-open (WI-madal L3): contain a crashing linker and
                # move on to the next priority group.
                _record_linker_crash(limits, linker.name, exc)
                continue
            _stamp_pass_version(result, linker)
            results.append((linker.name, result))
            all_linker_symbols.extend(result.symbols)
            if result.edges:
                accum_edges.extend(result.edges)
            if result.symbols:
                accum_symbols.extend(result.symbols)
        else:
            # Multiple linkers at same priority — run in parallel.
            # Each gets its own LinkerContext to avoid index-building
            # race conditions (lazy _ensure_indexes is not thread-safe).
            # parsed_trees is shared (dict.get/__setitem__ are atomic in
            # CPython, and the cache is content-stable per file).
            worker_count = min(len(group), os.cpu_count() or 1)
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                future_to_linker = {}
                for linker in group:
                    lctx = LinkerContext(
                        repo_root=ctx.repo_root,
                        symbols=accum_symbols,
                        edges=accum_edges,
                        captured_symbols=ctx.captured_symbols,
                        detected_frameworks=ctx.detected_frameworks,
                        detected_languages=ctx.detected_languages,
                        parsed_trees=ctx.parsed_trees,
                        linker_pass_id=linker.name,
                        linker_pass_version=linker.pass_version,
                    )
                    future_to_linker[
                        pool.submit(_run_linker_with_cache, linker.func, lctx)
                    ] = linker
                for future in as_completed(future_to_linker):
                    linker = future_to_linker[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        # §17 fail-open (WI-madal L3): contain a crashing
                        # linker; the rest of the priority group still lands.
                        _record_linker_crash(limits, linker.name, exc)
                        continue
                    _stamp_pass_version(result, linker)
                    results.append((linker.name, result))
                    all_linker_symbols.extend(result.symbols)
                    if result.edges:
                        accum_edges.extend(result.edges)
                    if result.symbols:
                        accum_symbols.extend(result.symbols)

    # Post-process: connect synthetic nodes to enclosing functions
    enclosure_ctx = LinkerContext(
        repo_root=ctx.repo_root,
        symbols=accum_symbols,
        edges=accum_edges,
        captured_symbols=ctx.captured_symbols,
        detected_frameworks=ctx.detected_frameworks,
        detected_languages=ctx.detected_languages,
    )
    from ..ir import PASS_VERSION, AnalysisRun, make_pass_id
    enclosure_pass_id = make_pass_id("enclosure-linker")
    enclosure_run = AnalysisRun.create(  # nosec B106 - pass_id is not a password
        pass_id=enclosure_pass_id,
        version=PASS_VERSION,
    )
    try:
        enclosure_edges = _connect_synthetic_to_enclosing(
            enclosure_ctx,
            all_linker_symbols,
            pass_id=enclosure_pass_id,
            run_id=enclosure_run.execution_id,
        )
    except Exception as exc:
        # §17 fail-open (WI-madal L3): the enclosure post-pass is itself a pass
        # (it mints its own AnalysisRun and is appended to results), so a crash
        # here must not abort the run. Contain it and return the linker results
        # gathered so far.
        _record_linker_crash(limits, "enclosure", exc)
        return results
    if enclosure_edges:
        results.append(("enclosure", LinkerResult(edges=enclosure_edges, run=enclosure_run)))

    return results


# Synthetic node kinds that should be connected to enclosing functions.
#
# Cluster D framework_role values per ADR-0027 — post-Phase-4b
# (PR #3633, WI-butol). These names are now exclusively
# ``Symbol.meta["framework_role"]`` values; producers emit
# ``Symbol.kind="function"|"method"`` and the role qualifier lives on
# meta. The set is canonical for the *framework_role* vocabulary used
# by ``_is_synthetic_node`` below, not for ``Symbol.kind`` (so the L1
# drift linter at ``scripts/check-symbol-kind-drift`` treats this
# target name as out-of-scope via its ``excluded_target_names``).
#
# Why kept under the ``KINDS`` name: changing the public symbol would
# ripple through ~12 consumer call sites for no behavioural gain. The
# set's *role* (a slice of the framework_role meta-key vocabulary)
# matters; its name is incidental.
SYNTHETIC_FRAMEWORK_ROLES = frozenset({
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
    "subprocess_call",
    "abi_call",
})


def _is_synthetic_node(sym: "Symbol") -> bool:
    """True if *sym* is a linker-synthesized framework-role node.

    Post-Phase-4b (ADR-0027 §6, PR #3633): the dual-shape predicate
    collapsed to its post-fold branch. Producers emit
    ``Symbol.kind="function"|"method"`` with the role qualifier on
    ``Symbol.meta["framework_role"]``; the pre-Phase-4b legacy branch
    (``sym.kind in <role-name>``) is structurally impossible now that
    the role-name vocabulary is no longer in the ``Symbol.kind``
    registry.
    """
    if sym.kind in {"function", "method"}:
        meta = getattr(sym, "meta", None) or {}
        return meta.get("framework_role") in SYNTHETIC_FRAMEWORK_ROLES
    return False


def _connect_synthetic_to_enclosing(
    ctx: LinkerContext,
    linker_symbols: list["Symbol"],
    *,
    pass_id: str,
    run_id: str,
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

    # Pre-Phase-3 the synthetic stubs carried framework-role values in
    # ``Symbol.kind`` (e.g. ``"grpc_stub"``), which sit outside
    # ``find_enclosing_symbol``'s default ``kinds`` filter, so the
    # synthetic node never matched itself as its own encloser. Post-
    # Phase-3 (ADR-0027 §"Phase 3" Wave 5) the canonical kind is
    # ``"function"`` or ``"method"`` and the role moves to
    # ``Symbol.meta["framework_role"]`` — which means the synthetic
    # node DOES match the default kinds and could be returned as its
    # own encloser. Build an enclosing-search context that excludes
    # the synthetic nodes from this run; only non-synthetic real
    # callables can be enclosers.
    synthetic_ids = {
        s.id for s in linker_symbols
        if hasattr(s, "kind") and _is_synthetic_node(s)
    }
    if synthetic_ids:
        enclosing_ctx = LinkerContext(
            repo_root=ctx.repo_root,
            symbols=[s for s in ctx.symbols if s.id not in synthetic_ids],
            edges=ctx.edges,
            captured_symbols=ctx.captured_symbols,
            detected_frameworks=ctx.detected_frameworks,
            detected_languages=ctx.detected_languages,
        )
    else:  # pragma: no cover - defensive: no synthetics means no edges anyway
        enclosing_ctx = ctx

    for sym in linker_symbols:
        # Skip non-Symbol objects (e.g., mock data in tests)
        if not hasattr(sym, "kind"):
            continue

        # Only process synthetic framework-role nodes (forward-compatible
        # with ADR-0027 §"Phase 3" Wave 5 framework_role fold).
        if not _is_synthetic_node(sym):
            continue

        # Need span to find enclosing function
        if sym.span is None:  # pragma: no cover - defensive for malformed symbols
            continue

        # Find enclosing function/method/class (excluding synthetic
        # nodes themselves, which would otherwise self-match post-Phase-3).
        enclosing = enclosing_ctx.find_enclosing_symbol(sym.path, sym.span.start_line)
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
            origin=pass_id,
            origin_run_id=run_id,
            evidence_type="enclosing_scope",
            derived_from=[enclosing.id, sym.id],
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
