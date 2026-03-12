# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unified symbol resolution with pluggable matching strategies.

This module provides a shared framework for cross-file symbol resolution
across all language analyzers. It supports three registry formats used by
different language analyzers:

Registry Formats
----------------
1. **SymbolResolver**: For `dict[tuple[str, str], Symbol]` (Python)
   - Keys are (module_name, symbol_name) tuples
   - Suffix matching on module names: finds `backend.app.crud` for `app.crud`

2. **NameResolver**: For `dict[str, Symbol]` (JS/TS, Java, C#, Kotlin, Rust)
   - Keys are simple or qualified names ("foo" or "MyClass.foo")
   - Suffix matching on names: finds `MyClass.doWork` for `doWork`

3. **ListNameResolver**: For `dict[str, list[Symbol]]` (Go)
   - Keys are names, values are lists (multiple symbols can share a name)
   - Disambiguates using path hints from import statements

Problem Example
---------------
A Python file at `backend/app/crud.py` is registered with module name
`backend.app.crud`, but imports say `from app.crud import X`. The exact
lookup `(app.crud, X)` fails, but suffix matching finds `(backend.app.crud, X)`.

Similarly, in Java, a method `doWork` might be registered as `MyClass.doWork`,
but a call site only knows `doWork`. Suffix matching resolves this.

Usage
-----
```python
from hypergumbo_core.symbol_resolution import SymbolResolver, NameResolver, ListNameResolver

# Python: (module, name) keyed registry
resolver = SymbolResolver(global_symbols)
result = resolver.lookup("app.crud", "create_item")

# JS/Java/etc: name-keyed registry
resolver = NameResolver(global_symbols)
result = resolver.lookup("doWork")  # Finds "MyClass.doWork"

# Go: list-valued registry with disambiguation
resolver = ListNameResolver(global_symbols)
result = resolver.lookup("Register", path_hint="grpc")
```

Design Rationale
----------------
- **Lazy indexing**: Suffix index is built on first fuzzy lookup, not upfront
- **Confidence tracking**: Fuzzy matches return lower confidence multipliers
- **Strategy composition**: Multiple strategies can be combined per lookup
- **Language agnostic**: Core logic works for any language; strategies adapt

This replaces per-analyzer implementations with a shared, tested, optimizable
component.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .selection.filters import is_test_path

if TYPE_CHECKING:
    from .ir import Symbol


@dataclass
class LookupResult:
    """Result of a symbol lookup operation.

    Attributes:
        symbol: The resolved symbol, or None if not found.
        confidence: Confidence multiplier (1.0 for exact, lower for fuzzy).
        match_type: How the symbol was matched ("exact", "suffix", "path_hint").
        candidates: For ambiguous matches, all candidates found.
    """

    symbol: Symbol | None
    confidence: float = 1.0
    match_type: str = "exact"
    candidates: list[Symbol] = field(default_factory=list)

    @property
    def found(self) -> bool:
        """Whether a symbol was found."""
        return self.symbol is not None

    @property
    def is_ambiguous(self) -> bool:
        """Whether multiple candidates were found."""
        return len(self.candidates) > 1


class SymbolResolver:
    """Unified symbol resolution with lazy indexing and pluggable strategies.

    This class wraps a symbol registry (dict mapping (module, name) -> Symbol)
    and provides flexible lookup with fallback strategies for handling module
    name mismatches.

    The resolver builds indexes lazily on first use to avoid overhead when
    exact matches suffice (the common case).

    Attributes:
        registry: The underlying symbol registry.
    """

    # Confidence multipliers for different match types
    CONFIDENCE_EXACT = 1.0
    CONFIDENCE_SUFFIX = 0.85
    CONFIDENCE_PATH_HINT = 0.90
    CONFIDENCE_AMBIGUOUS = 0.70

    def __init__(self, registry: dict[tuple[str, str], Symbol]) -> None:
        """Initialize resolver with a symbol registry.

        Args:
            registry: Dict mapping (module_name, symbol_name) -> Symbol.
                      This is the standard format used by language analyzers.
        """
        self.registry = registry
        self._suffix_index: dict[str, list[tuple[str, str]]] | None = None
        self._name_index: dict[str, list[tuple[str, str]]] | None = None

    def lookup(
        self,
        module: str,
        name: str,
        *,
        path_hints: dict[str, str] | None = None,
        allow_suffix: bool = True,
        allow_ambiguous: bool = False,
    ) -> LookupResult:
        """Look up a symbol with fallback strategies.

        Tries strategies in order:
        1. Exact match on (module, name)
        2. Path hint matching (if path_hints provided)
        3. Suffix matching (if allow_suffix=True)

        Args:
            module: The module name from the import statement.
            name: The symbol name being looked up.
            path_hints: Optional dict mapping aliases to full paths (Go-style).
            allow_suffix: Whether to try suffix matching as fallback.
            allow_ambiguous: If True, return first match even if ambiguous.

        Returns:
            LookupResult with the found symbol and match metadata.
        """
        # Strategy 1: Exact match (O(1), always tried first)
        exact = self.registry.get((module, name))
        if exact is not None:
            return LookupResult(symbol=exact, confidence=self.CONFIDENCE_EXACT)

        # Strategy 2: Path hint matching (Go-style)
        if path_hints is not None:
            result = self._lookup_with_path_hints(module, name, path_hints)
            if result.found:
                return result

        # Strategy 3: Suffix matching
        if allow_suffix:
            result = self._lookup_suffix(module, name, allow_ambiguous)
            if result.found or result.candidates:
                return result

        # Not found
        return LookupResult(symbol=None)

    def lookup_by_name(
        self,
        name: str,
        *,
        path_hint: str | None = None,
    ) -> LookupResult:
        """Look up a symbol by name only, with optional path hint for disambiguation.

        Useful when the module is unknown but we have a path hint (like Go's
        import path) to help disambiguate among multiple candidates.

        Args:
            name: The symbol name to look up.
            path_hint: Optional path substring to prefer in candidates.

        Returns:
            LookupResult, potentially with multiple candidates if ambiguous.
        """
        self._ensure_name_index()
        assert self._name_index is not None

        candidates_keys = self._name_index.get(name, [])
        if not candidates_keys:
            return LookupResult(symbol=None)

        candidates = [self.registry[key] for key in candidates_keys]

        if len(candidates) == 1:
            return LookupResult(
                symbol=candidates[0],
                confidence=self.CONFIDENCE_EXACT,
                candidates=candidates,
            )

        # Multiple candidates - try to disambiguate with path hint
        if path_hint:
            for candidate in candidates:
                if path_hint in candidate.path:
                    return LookupResult(
                        symbol=candidate,
                        confidence=self.CONFIDENCE_PATH_HINT,
                        match_type="path_hint",
                        candidates=candidates,
                    )

        # Ambiguous — scale confidence by 1/sqrt(N) so that high-ambiguity
        # names (e.g., Initialize() defined in 15 classes) get proportionally
        # lower confidence than a two-way ambiguity.
        return LookupResult(
            symbol=candidates[0],
            confidence=self.CONFIDENCE_AMBIGUOUS / math.sqrt(len(candidates)),
            match_type="ambiguous",
            candidates=candidates,
        )

    def _lookup_suffix(
        self, module: str, name: str, allow_ambiguous: bool
    ) -> LookupResult:
        """Look up symbol using suffix matching.

        Finds any (mod, name) where mod ends with '.{module}'.
        For example, looking for 'app.crud' matches 'backend.app.crud'.

        Args:
            module: The module suffix to match.
            name: The symbol name.
            allow_ambiguous: Whether to return a result if multiple match.

        Returns:
            LookupResult with suffix match or None.
        """
        self._ensure_suffix_index()
        assert self._suffix_index is not None

        # Look up all (module, name) pairs where module ends with this suffix
        candidates_keys = self._suffix_index.get(module, [])
        matching = [key for key in candidates_keys if key[1] == name]

        if not matching:
            return LookupResult(symbol=None)

        if len(matching) == 1:
            symbol = self.registry[matching[0]]
            return LookupResult(
                symbol=symbol,
                confidence=self.CONFIDENCE_SUFFIX,
                match_type="suffix",
                candidates=[symbol],
            )

        # Multiple matches - ambiguous
        candidates = [self.registry[key] for key in matching]
        if allow_ambiguous:
            return LookupResult(
                symbol=candidates[0],
                confidence=self.CONFIDENCE_AMBIGUOUS / math.sqrt(len(candidates)),
                match_type="suffix_ambiguous",
                candidates=candidates,
            )

        # Return None but include candidates for debugging
        return LookupResult(
            symbol=None,
            match_type="suffix_ambiguous",
            candidates=candidates,
        )

    def _lookup_with_path_hints(
        self, module: str, name: str, path_hints: dict[str, str]
    ) -> LookupResult:
        """Look up symbol using Go-style path hints.

        If `module` is an alias in `path_hints`, use the full path to find
        symbols whose file path contains that import path.

        Args:
            module: The module alias (e.g., "pb").
            name: The symbol name.
            path_hints: Dict mapping alias -> full import path.

        Returns:
            LookupResult if found via path hints.
        """
        if module not in path_hints:
            return LookupResult(symbol=None)

        import_path = path_hints[module]

        # Convert import path to directory hint (last component)
        # e.g., "github.com/foo/bar" -> "bar"
        dir_hint = import_path.rstrip("/").rsplit("/", 1)[-1]

        # Search for symbols with matching name whose path contains the hint
        self._ensure_name_index()
        assert self._name_index is not None

        candidates_keys = self._name_index.get(name, [])
        for key in candidates_keys:
            symbol = self.registry[key]
            if dir_hint in symbol.path:
                return LookupResult(
                    symbol=symbol,
                    confidence=self.CONFIDENCE_PATH_HINT,
                    match_type="path_hint",
                )

        return LookupResult(symbol=None)

    def _ensure_suffix_index(self) -> None:
        """Build suffix index lazily on first use.

        The suffix index maps each possible module suffix to all
        (module, name) keys that have that suffix.

        For module "backend.app.crud", we index:
        - "crud" -> [(backend.app.crud, *)]
        - "app.crud" -> [(backend.app.crud, *)]
        - "backend.app.crud" -> [(backend.app.crud, *)]
        """
        if self._suffix_index is not None:
            return

        self._suffix_index = {}
        for module, name in self.registry.keys():
            parts = module.split(".")
            # Generate all suffixes (including the full module name)
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                if suffix not in self._suffix_index:
                    self._suffix_index[suffix] = []
                self._suffix_index[suffix].append((module, name))

    def _ensure_name_index(self) -> None:
        """Build name index lazily on first use.

        The name index maps each symbol name to all (module, name) keys
        with that name. Used for name-only lookups and disambiguation.
        """
        if self._name_index is not None:
            return

        self._name_index = {}
        for module, name in self.registry.keys():
            if name not in self._name_index:
                self._name_index[name] = []
            self._name_index[name].append((module, name))

    def clear_indexes(self) -> None:
        """Clear cached indexes.

        Call this if the underlying registry is modified after resolver
        creation (not recommended - prefer creating a new resolver).
        """
        self._suffix_index = None
        self._name_index = None


class NameResolver:
    """Symbol resolver for string-keyed registries (dict[str, Symbol]).

    Used by JS/TS, Java, C#, Kotlin, Rust, and other analyzers where symbols
    are indexed by their name or qualified name (e.g., "MyClass.method").

    Suffix matching helps find "ClassName.method" when looking up "method",
    or "pkg.ClassName.method" when looking up "ClassName.method".

    Example
    -------
    ```python
    from hypergumbo_core.symbol_resolution import NameResolver

    # Registry keyed by name/qualified name
    global_symbols = {"MyClass.doWork": symbol, "utils.helper": symbol2}
    resolver = NameResolver(global_symbols)

    # Exact lookup
    result = resolver.lookup("MyClass.doWork")  # Found with confidence 1.0

    # Suffix matching: "doWork" finds "MyClass.doWork"
    result = resolver.lookup("doWork")  # Found with confidence 0.85
    ```
    """

    # Confidence multipliers for different match types
    CONFIDENCE_EXACT = 1.0
    CONFIDENCE_SUFFIX = 0.85
    CONFIDENCE_PATH_HINT = 0.90
    CONFIDENCE_AMBIGUOUS = 0.70

    def __init__(self, registry: dict[str, Symbol]) -> None:
        """Initialize resolver with a string-keyed symbol registry.

        Args:
            registry: Dict mapping symbol_name -> Symbol.
                      Keys can be simple names ("foo") or qualified ("Class.foo").
        """
        self.registry = registry
        self._suffix_index: dict[str, list[str]] | None = None

    def lookup(
        self,
        name: str,
        *,
        allow_suffix: bool = True,
        path_hint: str | None = None,
        path_hints: list[str] | None = None,
        caller_path: str | None = None,
    ) -> LookupResult:
        """Look up a symbol by name with optional suffix matching.

        Tries strategies in order:
        1. Exact match on name (skipped if path_hints provided and no match)
        2. Suffix matching with path hint disambiguation (if allow_suffix=True)

        Args:
            name: The symbol name to look up.
            allow_suffix: Whether to try suffix matching as fallback.
            path_hint: Optional path substring to prefer among candidates.
            path_hints: Optional list of path substrings from import
                declarations.  When provided, exact matches are only returned
                if their path matches at least one hint; otherwise we fall
                through to suffix matching where ALL candidates (including
                the exact-match symbol) are checked against the hints.  This
                lets callers with import scope (e.g., D ``import errors;``)
                disambiguate identically-named symbols across files.
            caller_path: Optional path of the calling file.  When the caller
                is in a non-test file and candidates include both test-file
                and production-file symbols, production candidates are
                preferred.  This prevents false positives like
                ``server.startup()`` resolving to ``LogCleanerTest.startup()``
                instead of ``Server.startup()``.

        Returns:
            LookupResult with the found symbol and match metadata.
        """
        # Strategy 1: Exact match (O(1))
        if name in self.registry:
            exact_sym = self.registry[name]
            # If caller provided import-scope hints, verify the exact match
            # is in an imported module.  If not, fall through to suffix
            # matching so that ALL candidates are checked.
            if path_hints:
                if any(h in exact_sym.path for h in path_hints):
                    return LookupResult(
                        symbol=exact_sym,
                        confidence=self.CONFIDENCE_EXACT,
                    )
                # else: fall through — exact match is NOT in an imported module
            else:
                return LookupResult(
                    symbol=exact_sym,
                    confidence=self.CONFIDENCE_EXACT,
                )

        # Strategy 2: Suffix matching
        if allow_suffix:
            # Merge single path_hint into hints list for unified handling
            effective_hints = path_hints
            if path_hint and not effective_hints:
                effective_hints = [path_hint]
            result = self._lookup_suffix(
                name, path_hint=None, path_hints=effective_hints,
                caller_path=caller_path,
            )
            if result.found or result.candidates:
                return result

        return LookupResult(symbol=None)

    def _lookup_suffix(
        self,
        name: str,
        path_hint: str | None,
        path_hints: list[str] | None = None,
        caller_path: str | None = None,
    ) -> LookupResult:
        """Look up symbol using suffix matching.

        Finds any key whose suffix (after separator splits) matches
        ``name``.  Separators: ``.``, ``::``, ``#``, ``\\``, ``:``.
        For example, ``'doWork'`` matches ``'MyClass.doWork'``, and
        ``'compute'`` matches ``'Diff::compute'``.

        When ``caller_path`` is provided and is a non-test file, candidates
        from test files are deprioritized: non-test candidates are preferred
        if any exist.  Test-file callers get no such preference (test code
        legitimately calls test utilities).

        Args:
            name: The symbol name suffix to match.
            path_hint: Optional single path substring to prefer among candidates.
            path_hints: Optional list of path substrings (e.g., from imports)
                to prefer among candidates.  Takes precedence over path_hint.
            caller_path: Optional path of the calling file for test-path
                preference filtering.

        Returns:
            LookupResult with suffix match or None.
        """
        self._ensure_suffix_index()
        assert self._suffix_index is not None

        candidates_keys = self._suffix_index.get(name, [])
        if not candidates_keys:
            return LookupResult(symbol=None)

        candidates = [self.registry[key] for key in candidates_keys]

        # Test-path preference: when caller is non-test, prefer non-test candidates
        if (
            caller_path
            and not is_test_path(caller_path)
            and len(candidates) > 1
        ):
            non_test_pairs = [
                (c, k) for c, k in zip(candidates, candidates_keys, strict=True)
                if not is_test_path(c.path)
            ]
            if non_test_pairs:
                candidates = [p[0] for p in non_test_pairs]
                candidates_keys = [p[1] for p in non_test_pairs]

        # Try path hints disambiguation (from imports or single hint).
        # Two-stage matching: (1) file path, (2) registry key (qualified name).
        # Key matching handles C++ same-file disambiguation where
        # path_hint="Parser" matches key "Parser::Initialize" but not
        # "Packager::Initialize" even when both are in parser.cpp.
        all_hints = path_hints or ([path_hint] if path_hint else None)
        if all_hints and len(candidates) > 1:
            # Stage 1: filter by file path
            path_matched = [
                c for c in candidates
                if any(h in c.path for h in all_hints)
            ]
            if len(path_matched) == 1:
                return LookupResult(
                    symbol=path_matched[0],
                    confidence=self.CONFIDENCE_PATH_HINT,
                    match_type="path_hint",
                    candidates=candidates,
                )
            # Stage 2: filter by registry key (qualified symbol name)
            key_matched = [
                c for c, k in zip(candidates, candidates_keys, strict=True)
                if any(h in k for h in all_hints)
            ]
            if len(key_matched) == 1:
                return LookupResult(
                    symbol=key_matched[0],
                    confidence=self.CONFIDENCE_PATH_HINT,
                    match_type="path_hint",
                    candidates=candidates,
                )
            # Return first match from either stage
            first_match = key_matched[0] if key_matched else (
                path_matched[0] if path_matched else None
            )
            if first_match is not None:
                return LookupResult(
                    symbol=first_match,
                    confidence=self.CONFIDENCE_PATH_HINT,
                    match_type="path_hint",
                    candidates=candidates,
                )

        if len(candidates) == 1:
            return LookupResult(
                symbol=candidates[0],
                confidence=self.CONFIDENCE_SUFFIX,
                match_type="suffix",
                candidates=candidates,
            )

        # Multiple — ambiguous, return first.  Scale confidence by
        # 1/sqrt(N) so common method names (Initialize with 15 classes)
        # get proportionally lower confidence than a two-way ambiguity.
        return LookupResult(
            symbol=candidates[0],
            confidence=self.CONFIDENCE_AMBIGUOUS / math.sqrt(len(candidates)),
            match_type="suffix_ambiguous",
            candidates=candidates,
        )

    # Regex that splits qualified names on any language separator.
    # ``::`` must precede ``:`` so the longer match wins.
    # Covers: ``.`` (Java/JS/C#/Scala/Swift/Groovy), ``::`` (Rust/C++/Perl),
    # ``#`` (Ruby), ``\\`` (Hack/PHP), ``:`` (Luau/Lua).
    _SEPARATOR_RE = re.compile(r"::|[.#\\:]")

    def _ensure_suffix_index(self) -> None:
        """Build suffix index lazily on first use.

        The suffix index maps each possible name suffix to all keys that
        have that suffix. Splits on ``.``, ``::``, ``#``, ``\\``, and
        ``:`` to support all language-specific qualified name separators.

        For key ``crate::Diff::compute``, we index:
        - ``"compute"`` → [``"crate::Diff::compute"``]
        - ``"Diff::compute"`` → [``"crate::Diff::compute"``]
        - ``"crate::Diff::compute"`` → [``"crate::Diff::compute"``]

        Suffixes use the **original separator** from the key string, so
        ``"Diff::compute"`` (not ``"Diff.compute"``) is indexed.
        """
        if self._suffix_index is not None:
            return

        self._suffix_index = {}
        for key in self.registry.keys():
            self._index_key_suffixes(key)

    def _index_key_suffixes(self, key: str) -> None:
        """Index a key by all its name suffixes.

        Walks the key backwards from the rightmost part, extracting each
        suffix as a substring of the original key. This preserves the
        original separator characters.

        Keys with no separators are also indexed (the full key is its own
        suffix). This is needed when path hints cause the exact-match path
        to be skipped — suffix matching must still find the bare key.
        """
        parts = self._SEPARATOR_RE.split(key)
        # Walk backwards through the key to find suffix start positions.
        # For "crate::Diff::compute" with parts ["crate","Diff","compute"]:
        #   i=2 → pos points to "compute"
        #   i=1 → pos points to "Diff::compute"
        #   i=0 → pos points to "crate::Diff::compute"
        assert self._suffix_index is not None
        pos = len(key)
        for i in range(len(parts) - 1, -1, -1):
            part_len = len(parts[i])
            pos -= part_len
            suffix = key[pos:]
            if suffix not in self._suffix_index:
                self._suffix_index[suffix] = []
            self._suffix_index[suffix].append(key)
            # Skip the separator before this part
            if pos > 0:
                sep_len = 2 if key[pos - 2:pos] == "::" else 1
                pos -= sep_len

    def clear_indexes(self) -> None:
        """Clear cached indexes."""
        self._suffix_index = None


class ListNameResolver:
    """Symbol resolver for list-valued registries (dict[str, list[Symbol]]).

    Used by Go and other analyzers where multiple symbols can share the same
    name. This resolver handles disambiguation among candidates using path
    hints derived from import statements.

    Example
    -------
    ```python
    from hypergumbo_core.symbol_resolution import ListNameResolver

    # Registry with multiple symbols per name
    global_symbols = {
        "Register": [grpc_symbol, http_symbol],
        "Init": [pkg1_init, pkg2_init],
    }
    resolver = ListNameResolver(global_symbols)

    # Lookup with path hint for disambiguation
    result = resolver.lookup("Register", path_hint="grpc")
    # Returns grpc_symbol with confidence 0.90
    ```
    """

    # Confidence multipliers for different match types
    CONFIDENCE_EXACT = 1.0
    CONFIDENCE_PATH_HINT = 0.90
    # Legacy constant; ambiguous confidence now scales as 1/sqrt(N)
    CONFIDENCE_AMBIGUOUS = 0.70

    def __init__(
        self,
        registry: dict[str, list[Symbol]],
        ambiguity_threshold: int | None = None,
    ) -> None:
        """Initialize resolver with a list-valued symbol registry.

        Args:
            registry: Dict mapping symbol_name -> list of Symbol objects.
            ambiguity_threshold: When set, if the candidate count for a name
                meets or exceeds this value and no path_hint disambiguates,
                return an unresolved result instead of picking an arbitrary
                candidate. Set to 3 to guard against common method name
                false positives (Get, Set, Close, String).
        """
        self.registry = registry
        self.ambiguity_threshold = ambiguity_threshold

    def lookup(
        self,
        name: str,
        *,
        path_hint: str | None = None,
        soft_hint: bool = False,
    ) -> LookupResult:
        """Look up a symbol by name with disambiguation.

        Ambiguous confidence scales as ``1/sqrt(N)`` where *N* is the number
        of candidates.  This means common interface methods like ``Close()``,
        ``String()``, or ``Name()`` that are defined on dozens of types get
        proportionally lower confidence (e.g. 50 candidates → 0.14) than a
        two-way ambiguity (0.71).

        Args:
            name: The symbol name to look up.
            path_hint: Optional path substring to prefer among candidates.
            soft_hint: When True, path_hint is treated as a preference rather
                than a filter.  A single candidate that doesn't match the hint
                is still returned (with reduced confidence) instead of rejected.
                Use for Rust-style "prefer same-module" semantics where the hint
                is the caller's directory, not Go-style import-path evidence.

        Returns:
            LookupResult with the found symbol and match metadata.
        """
        candidates = self.registry.get(name, [])

        if not candidates:
            return LookupResult(symbol=None)

        if len(candidates) == 1:
            # When a path_hint is provided, the caller has evidence that the
            # call targets a specific import package.  If the sole candidate
            # doesn't match the hint, the candidate is a different symbol
            # that happens to share the name (e.g. local MarshalEncoder.Encode
            # vs encoding/json Encode).  Return not-found so the caller can
            # create an unresolved edge.
            #
            # With soft_hint=True, the hint is preference-level evidence (e.g.
            # Rust caller directory), not filter-level evidence (e.g. Go import
            # path).  Return the candidate with reduced confidence instead of
            # rejecting it.
            if path_hint:
                path_parts = path_hint.rstrip("/").split("/")
                matched = False
                for i in range(len(path_parts) - 1, -1, -1):
                    suffix = "/".join(path_parts[i:])
                    if suffix in candidates[0].path:
                        matched = True
                        break
                if not matched:
                    if soft_hint:
                        return LookupResult(
                            symbol=candidates[0],
                            confidence=self.CONFIDENCE_PATH_HINT,
                            match_type="soft_hint_fallback",
                            candidates=candidates,
                        )
                    return LookupResult(symbol=None)
            return LookupResult(
                symbol=candidates[0],
                confidence=self.CONFIDENCE_EXACT,
                candidates=candidates,
            )

        # Multiple candidates - try to disambiguate with path hint.
        # Track the best (smallest) narrowed candidate set: even if no
        # suffix yields a unique match, narrowing from 10 → 2 candidates
        # is useful — it means only 2 symbols are in the matching package.
        narrowed = candidates
        if path_hint:
            # Try progressively shorter suffixes of the path hint to find unique match
            # e.g., for "github.com/example/src/zzz_correct/genproto", try:
            #   1. "src/zzz_correct/genproto" (longest useful suffix)
            #   2. "zzz_correct/genproto"
            #   3. "genproto" (shortest)
            path_parts = path_hint.rstrip("/").split("/")

            # Try progressively longer suffixes of the path hint.
            # Start with just the last segment, extend toward the full path.
            for i in range(len(path_parts) - 1, -1, -1):
                suffix = "/".join(path_parts[i:])
                matching = [c for c in candidates if suffix in c.path]
                if len(matching) == 1:
                    return LookupResult(
                        symbol=matching[0],
                        confidence=self.CONFIDENCE_PATH_HINT,
                        match_type="path_hint",
                        candidates=candidates,
                    )
                if 1 < len(matching) < len(narrowed):
                    narrowed = matching

        # Ambiguity guard: when the NARROWED candidate count meets or
        # exceeds the threshold, return unresolved instead of picking an
        # arbitrary candidate.  Using narrowed (not original candidates)
        # means that suffix matching that filtered e.g. 10 → 2 candidates
        # avoids the threshold.
        if (
            self.ambiguity_threshold is not None
            and len(narrowed) >= self.ambiguity_threshold
        ):
            return LookupResult(
                symbol=None,
                match_type="ambiguous",
                candidates=candidates,
            )

        # Ambiguous — scale confidence by 1/sqrt(N) so that common interface
        # methods (Close, String, Name) with dozens of implementations get
        # proportionally lower confidence than a two-way ambiguity.
        sorted_narrowed = sorted(narrowed, key=lambda s: s.path)
        scaled_confidence = 1.0 / (len(narrowed) ** 0.5)
        return LookupResult(
            symbol=sorted_narrowed[0],
            confidence=scaled_confidence,
            match_type="ambiguous",
            candidates=candidates,
        )


def lookup_symbol(
    registry: dict[tuple[str, str], Symbol],
    module: str,
    name: str,
    *,
    path_hints: dict[str, str] | None = None,
    allow_suffix: bool = True,
) -> Symbol | None:
    """Convenience function for one-off lookups.

    For repeated lookups, prefer creating a SymbolResolver instance
    to benefit from index caching.

    Args:
        registry: The symbol registry.
        module: Module name from import statement.
        name: Symbol name to look up.
        path_hints: Optional Go-style path hints.
        allow_suffix: Whether to try suffix matching.

    Returns:
        The found Symbol, or None.
    """
    resolver = SymbolResolver(registry)
    result = resolver.lookup(module, name, path_hints=path_hints, allow_suffix=allow_suffix)
    return result.symbol


def lookup_name(
    registry: dict[str, Symbol],
    name: str,
    *,
    allow_suffix: bool = True,
    path_hint: str | None = None,
) -> Symbol | None:
    """Convenience function for one-off name lookups.

    For repeated lookups, prefer creating a NameResolver instance
    to benefit from index caching.

    Args:
        registry: The symbol registry (string-keyed).
        name: Symbol name to look up.
        allow_suffix: Whether to try suffix matching.
        path_hint: Optional path substring to prefer.

    Returns:
        The found Symbol, or None.
    """
    resolver = NameResolver(registry)
    result = resolver.lookup(name, allow_suffix=allow_suffix, path_hint=path_hint)
    return result.symbol
