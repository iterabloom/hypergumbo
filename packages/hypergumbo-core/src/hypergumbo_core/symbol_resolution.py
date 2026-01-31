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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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

        # Ambiguous - return first with low confidence
        return LookupResult(
            symbol=candidates[0],
            confidence=self.CONFIDENCE_AMBIGUOUS,
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
                confidence=self.CONFIDENCE_AMBIGUOUS,
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
    ) -> LookupResult:
        """Look up a symbol by name with optional suffix matching.

        Tries strategies in order:
        1. Exact match on name
        2. Suffix matching (if allow_suffix=True)

        Args:
            name: The symbol name to look up.
            allow_suffix: Whether to try suffix matching as fallback.
            path_hint: Optional path substring to prefer among candidates.

        Returns:
            LookupResult with the found symbol and match metadata.
        """
        # Strategy 1: Exact match (O(1))
        if name in self.registry:
            return LookupResult(
                symbol=self.registry[name],
                confidence=self.CONFIDENCE_EXACT,
            )

        # Strategy 2: Suffix matching
        if allow_suffix:
            result = self._lookup_suffix(name, path_hint)
            if result.found or result.candidates:
                return result

        return LookupResult(symbol=None)

    def _lookup_suffix(self, name: str, path_hint: str | None) -> LookupResult:
        """Look up symbol using suffix matching.

        Finds any key that ends with '.{name}' or equals '{name}'.
        For example, looking for 'doWork' matches 'MyClass.doWork'.

        Args:
            name: The symbol name suffix to match.
            path_hint: Optional path substring to prefer among candidates.

        Returns:
            LookupResult with suffix match or None.
        """
        self._ensure_suffix_index()
        assert self._suffix_index is not None

        candidates_keys = self._suffix_index.get(name, [])
        if not candidates_keys:
            return LookupResult(symbol=None)

        candidates = [self.registry[key] for key in candidates_keys]

        # Try path hint disambiguation if multiple candidates
        if path_hint and len(candidates) > 1:
            for candidate in candidates:
                if path_hint in candidate.path:
                    return LookupResult(
                        symbol=candidate,
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

        # Multiple - ambiguous, return first
        return LookupResult(
            symbol=candidates[0],
            confidence=self.CONFIDENCE_AMBIGUOUS,
            match_type="suffix_ambiguous",
            candidates=candidates,
        )

    def _ensure_suffix_index(self) -> None:
        """Build suffix index lazily on first use.

        The suffix index maps each possible name suffix to all keys that
        have that suffix. For key "pkg.ClassName.method", we index:
        - "method" -> ["pkg.ClassName.method"]
        - "ClassName.method" -> ["pkg.ClassName.method"]
        - "pkg.ClassName.method" -> ["pkg.ClassName.method"]
        """
        if self._suffix_index is not None:
            return

        self._suffix_index = {}
        for key in self.registry.keys():
            parts = key.split(".")
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                if suffix not in self._suffix_index:
                    self._suffix_index[suffix] = []
                self._suffix_index[suffix].append(key)

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
    CONFIDENCE_AMBIGUOUS = 0.70

    def __init__(self, registry: dict[str, list[Symbol]]) -> None:
        """Initialize resolver with a list-valued symbol registry.

        Args:
            registry: Dict mapping symbol_name -> list of Symbol objects.
        """
        self.registry = registry

    def lookup(
        self,
        name: str,
        *,
        path_hint: str | None = None,
    ) -> LookupResult:
        """Look up a symbol by name with disambiguation.

        Args:
            name: The symbol name to look up.
            path_hint: Optional path substring to prefer among candidates.

        Returns:
            LookupResult with the found symbol and match metadata.
        """
        candidates = self.registry.get(name, [])

        if not candidates:
            return LookupResult(symbol=None)

        if len(candidates) == 1:
            return LookupResult(
                symbol=candidates[0],
                confidence=self.CONFIDENCE_EXACT,
                candidates=candidates,
            )

        # Multiple candidates - try to disambiguate with path hint
        if path_hint:
            # Try progressively shorter suffixes of the path hint to find unique match
            # e.g., for "github.com/example/src/zzz_correct/genproto", try:
            #   1. "src/zzz_correct/genproto" (longest useful suffix)
            #   2. "zzz_correct/genproto"
            #   3. "genproto" (shortest)
            path_parts = path_hint.rstrip("/").split("/")

            # Start from second-to-last segment (skip domain parts like github.com)
            # and try progressively shorter suffixes
            for i in range(len(path_parts) - 1, 0, -1):
                suffix = "/".join(path_parts[i:])
                matching = [c for c in candidates if suffix in c.path]
                if len(matching) == 1:
                    return LookupResult(
                        symbol=matching[0],
                        confidence=self.CONFIDENCE_PATH_HINT,
                        match_type="path_hint",
                        candidates=candidates,
                    )

            # Fallback: try just the last segment
            dir_hint = path_parts[-1]
            matching = [c for c in candidates if dir_hint in c.path]
            if len(matching) == 1:
                return LookupResult(
                    symbol=matching[0],
                    confidence=self.CONFIDENCE_PATH_HINT,
                    match_type="path_hint",
                    candidates=candidates,
                )

        # Ambiguous - sort for deterministic ordering, return first with low confidence
        sorted_candidates = sorted(candidates, key=lambda s: s.path)
        return LookupResult(
            symbol=sorted_candidates[0],
            confidence=self.CONFIDENCE_AMBIGUOUS,
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
