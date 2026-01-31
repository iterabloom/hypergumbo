"""Tests for the shared SymbolResolver module."""

import pytest
from hypergumbo_core.symbol_resolution import (
    SymbolResolver, LookupResult, lookup_symbol,
    NameResolver, ListNameResolver, lookup_name,
)
from hypergumbo_core.ir import Symbol, Span


def make_symbol(name: str, path: str, module: str) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=f"python:{path}:1-10:{name}:function",
        name=name,
        kind="function",
        language="python",
        path=path,
        span=Span(start_line=1, start_col=0, end_line=10, end_col=0),
    )


class TestLookupResult:
    """Tests for the LookupResult dataclass."""

    def test_found_property_when_symbol_exists(self) -> None:
        """found property returns True when symbol is present."""
        sym = make_symbol("foo", "/path/foo.py", "foo")
        result = LookupResult(symbol=sym)
        assert result.found is True

    def test_found_property_when_symbol_none(self) -> None:
        """found property returns False when symbol is None."""
        result = LookupResult(symbol=None)
        assert result.found is False

    def test_is_ambiguous_when_multiple_candidates(self) -> None:
        """is_ambiguous returns True with multiple candidates."""
        sym1 = make_symbol("foo", "/path1/foo.py", "path1")
        sym2 = make_symbol("foo", "/path2/foo.py", "path2")
        result = LookupResult(symbol=sym1, candidates=[sym1, sym2])
        assert result.is_ambiguous is True

    def test_is_ambiguous_when_single_candidate(self) -> None:
        """is_ambiguous returns False with single candidate."""
        sym = make_symbol("foo", "/path/foo.py", "foo")
        result = LookupResult(symbol=sym, candidates=[sym])
        assert result.is_ambiguous is False


class TestSymbolResolverExactMatch:
    """Tests for exact module/name matching."""

    def test_exact_match_returns_symbol(self) -> None:
        """Exact match on (module, name) returns the symbol."""
        sym = make_symbol("create_item", "/app/crud.py", "app.crud")
        registry = {("app.crud", "create_item"): sym}
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "create_item")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == 1.0
        assert result.match_type == "exact"

    def test_exact_match_not_found(self) -> None:
        """No match returns None symbol."""
        registry: dict = {}
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "create_item")

        assert result.found is False
        assert result.symbol is None


class TestSymbolResolverSuffixMatch:
    """Tests for suffix-based module matching."""

    def test_suffix_match_finds_nested_module(self) -> None:
        """Suffix match finds 'backend.app.crud' when looking for 'app.crud'."""
        sym = make_symbol("create_item", "/backend/app/crud.py", "backend.app.crud")
        registry = {("backend.app.crud", "create_item"): sym}
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "create_item")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == SymbolResolver.CONFIDENCE_SUFFIX
        assert result.match_type == "suffix"

    def test_suffix_match_prefers_exact(self) -> None:
        """Exact match is preferred over suffix match."""
        sym_exact = make_symbol("foo", "/app/crud.py", "app.crud")
        sym_suffix = make_symbol("foo", "/backend/app/crud.py", "backend.app.crud")
        registry = {
            ("app.crud", "foo"): sym_exact,
            ("backend.app.crud", "foo"): sym_suffix,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "foo")

        assert result.found is True
        assert result.symbol is sym_exact
        assert result.confidence == 1.0  # Exact match confidence

    def test_suffix_match_ambiguous_returns_none_by_default(self) -> None:
        """Ambiguous suffix match returns None by default."""
        sym1 = make_symbol("foo", "/pkg1/app/crud.py", "pkg1.app.crud")
        sym2 = make_symbol("foo", "/pkg2/app/crud.py", "pkg2.app.crud")
        registry = {
            ("pkg1.app.crud", "foo"): sym1,
            ("pkg2.app.crud", "foo"): sym2,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "foo")

        assert result.found is False
        assert result.symbol is None
        # Match type is "suffix_ambiguous" and candidates are populated
        assert "ambiguous" in result.match_type
        assert len(result.candidates) == 2

    def test_suffix_match_ambiguous_with_allow_flag(self) -> None:
        """Ambiguous suffix match returns first with allow_ambiguous=True."""
        sym1 = make_symbol("foo", "/pkg1/app/crud.py", "pkg1.app.crud")
        sym2 = make_symbol("foo", "/pkg2/app/crud.py", "pkg2.app.crud")
        registry = {
            ("pkg1.app.crud", "foo"): sym1,
            ("pkg2.app.crud", "foo"): sym2,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "foo", allow_ambiguous=True)

        assert result.found is True
        assert result.symbol in [sym1, sym2]
        assert result.confidence == SymbolResolver.CONFIDENCE_AMBIGUOUS

    def test_suffix_match_disabled(self) -> None:
        """Suffix matching can be disabled."""
        sym = make_symbol("foo", "/backend/app/crud.py", "backend.app.crud")
        registry = {("backend.app.crud", "foo"): sym}
        resolver = SymbolResolver(registry)

        result = resolver.lookup("app.crud", "foo", allow_suffix=False)

        assert result.found is False


class TestSymbolResolverPathHints:
    """Tests for Go-style path hint matching."""

    def test_path_hint_resolves_symbol(self) -> None:
        """Path hint helps resolve ambiguous name."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {
            ("grpc", "Register"): sym_grpc,
            ("http", "Register"): sym_http,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup(
            "pb", "Register",
            path_hints={"pb": "google.golang.org/grpc"}
        )

        assert result.found is True
        assert result.symbol is sym_grpc
        assert result.confidence == SymbolResolver.CONFIDENCE_PATH_HINT
        assert result.match_type == "path_hint"

    def test_path_hint_no_match(self) -> None:
        """Path hint with no matching symbol returns None."""
        sym = make_symbol("Other", "/other/file.go", "other")
        registry = {("other", "Other"): sym}
        resolver = SymbolResolver(registry)

        result = resolver.lookup(
            "pb", "Register",
            path_hints={"pb": "google.golang.org/grpc"}
        )

        assert result.found is False

    def test_path_hint_module_not_in_hints(self) -> None:
        """Path hints are skipped when module is not a key in path_hints."""
        sym = make_symbol("foo", "/other/file.go", "other")
        registry = {("other", "foo"): sym}
        resolver = SymbolResolver(registry)

        # Module "different" is not a key in path_hints, so path_hints lookup returns early
        # and the lookup falls through to suffix matching (which also won't find it)
        result = resolver.lookup(
            "different", "foo",
            path_hints={"pb": "google.golang.org/grpc"},
            allow_suffix=False
        )

        assert result.found is False


class TestSymbolResolverLookupByName:
    """Tests for name-only lookup with disambiguation."""

    def test_lookup_by_name_single_match(self) -> None:
        """Single name match returns the symbol."""
        sym = make_symbol("unique_func", "/app/utils.py", "app.utils")
        registry = {("app.utils", "unique_func"): sym}
        resolver = SymbolResolver(registry)

        result = resolver.lookup_by_name("unique_func")

        assert result.found is True
        assert result.symbol is sym

    def test_lookup_by_name_multiple_matches(self) -> None:
        """Multiple name matches returns first with low confidence."""
        sym1 = make_symbol("common", "/pkg1/utils.py", "pkg1.utils")
        sym2 = make_symbol("common", "/pkg2/utils.py", "pkg2.utils")
        registry = {
            ("pkg1.utils", "common"): sym1,
            ("pkg2.utils", "common"): sym2,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup_by_name("common")

        assert result.found is True
        assert result.symbol in [sym1, sym2]
        assert result.confidence == SymbolResolver.CONFIDENCE_AMBIGUOUS
        assert result.is_ambiguous is True

    def test_lookup_by_name_with_path_hint(self) -> None:
        """Path hint disambiguates multiple name matches."""
        sym1 = make_symbol("Init", "/grpc/init.go", "grpc")
        sym2 = make_symbol("Init", "/http/init.go", "http")
        registry = {
            ("grpc", "Init"): sym1,
            ("http", "Init"): sym2,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup_by_name("Init", path_hint="grpc")

        assert result.found is True
        assert result.symbol is sym1
        assert result.confidence == SymbolResolver.CONFIDENCE_PATH_HINT

    def test_lookup_by_name_not_found(self) -> None:
        """lookup_by_name returns None when name doesn't exist."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}
        resolver = SymbolResolver(registry)

        result = resolver.lookup_by_name("nonexistent")

        assert result.found is False
        assert result.symbol is None


class TestSymbolResolverIndexing:
    """Tests for lazy index building."""

    def test_suffix_index_built_lazily(self) -> None:
        """Suffix index is only built when needed."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}
        resolver = SymbolResolver(registry)

        # Index not built yet
        assert resolver._suffix_index is None

        # Exact lookup doesn't build index
        resolver.lookup("app.utils", "foo")
        assert resolver._suffix_index is None

        # Suffix lookup builds index
        resolver.lookup("utils", "bar")
        assert resolver._suffix_index is not None

    def test_suffix_index_early_return_when_built(self) -> None:
        """_ensure_suffix_index returns early if index already built."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}
        resolver = SymbolResolver(registry)

        # Build the index first via a suffix lookup
        resolver.lookup("utils", "bar")
        assert resolver._suffix_index is not None
        original_index = resolver._suffix_index

        # Calling again should return early without rebuilding
        resolver._ensure_suffix_index()
        assert resolver._suffix_index is original_index  # Same object

    def test_name_index_built_lazily(self) -> None:
        """Name index is only built when needed."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}
        resolver = SymbolResolver(registry)

        # Index not built yet
        assert resolver._name_index is None

        # lookup_by_name builds index
        resolver.lookup_by_name("foo")
        assert resolver._name_index is not None

    def test_name_index_early_return_when_built(self) -> None:
        """_ensure_name_index returns early if index already built."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}
        resolver = SymbolResolver(registry)

        # Build the index first
        resolver.lookup_by_name("foo")
        assert resolver._name_index is not None
        original_index = resolver._name_index

        # Calling again should return early without rebuilding
        resolver._ensure_name_index()
        assert resolver._name_index is original_index  # Same object

    def test_clear_indexes(self) -> None:
        """clear_indexes removes cached indexes."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}
        resolver = SymbolResolver(registry)

        # Build indexes
        resolver.lookup("utils", "foo")
        resolver.lookup_by_name("foo")
        assert resolver._suffix_index is not None
        assert resolver._name_index is not None

        # Clear
        resolver.clear_indexes()
        assert resolver._suffix_index is None
        assert resolver._name_index is None


class TestLookupSymbolConvenience:
    """Tests for the lookup_symbol convenience function."""

    def test_lookup_symbol_exact_match(self) -> None:
        """Convenience function finds exact match."""
        sym = make_symbol("foo", "/app/utils.py", "app.utils")
        registry = {("app.utils", "foo"): sym}

        result = lookup_symbol(registry, "app.utils", "foo")

        assert result is sym

    def test_lookup_symbol_suffix_match(self) -> None:
        """Convenience function finds suffix match."""
        sym = make_symbol("foo", "/backend/app/utils.py", "backend.app.utils")
        registry = {("backend.app.utils", "foo"): sym}

        result = lookup_symbol(registry, "app.utils", "foo")

        assert result is sym

    def test_lookup_symbol_not_found(self) -> None:
        """Convenience function returns None when not found."""
        registry: dict = {}

        result = lookup_symbol(registry, "app.utils", "foo")

        assert result is None


# ============================================================================
# NameResolver Tests (for dict[str, Symbol] registries)
# ============================================================================


class TestNameResolverExactMatch:
    """Tests for exact name matching in NameResolver."""

    def test_exact_match_returns_symbol(self) -> None:
        """Exact match on name returns the symbol."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("MyClass.doWork")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == 1.0
        assert result.match_type == "exact"

    def test_exact_match_not_found(self) -> None:
        """No match returns None symbol."""
        registry: dict = {}
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork")

        assert result.found is False
        assert result.symbol is None


class TestNameResolverSuffixMatch:
    """Tests for suffix-based name matching in NameResolver."""

    def test_suffix_match_finds_qualified_name(self) -> None:
        """Suffix match finds 'MyClass.doWork' when looking for 'doWork'."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == NameResolver.CONFIDENCE_SUFFIX
        assert result.match_type == "suffix"

    def test_suffix_match_prefers_exact(self) -> None:
        """Exact match is preferred over suffix match."""
        sym_exact = make_symbol("doWork", "/app/utils.java", "java")
        sym_suffix = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {
            "doWork": sym_exact,
            "MyClass.doWork": sym_suffix,
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork")

        assert result.found is True
        assert result.symbol is sym_exact
        assert result.confidence == 1.0  # Exact match confidence

    def test_suffix_match_ambiguous_returns_first(self) -> None:
        """Ambiguous suffix match returns first with low confidence."""
        sym1 = make_symbol("doWork", "/pkg1/MyClass.java", "java")
        sym2 = make_symbol("doWork", "/pkg2/OtherClass.java", "java")
        registry = {
            "pkg1.MyClass.doWork": sym1,
            "pkg2.OtherClass.doWork": sym2,
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork")

        assert result.found is True
        assert result.symbol in [sym1, sym2]
        assert result.confidence == NameResolver.CONFIDENCE_AMBIGUOUS
        assert "ambiguous" in result.match_type
        assert len(result.candidates) == 2

    def test_suffix_match_path_hint_disambiguates(self) -> None:
        """Path hint disambiguates among multiple suffix matches."""
        sym1 = make_symbol("doWork", "/pkg1/MyClass.java", "java")
        sym2 = make_symbol("doWork", "/pkg2/OtherClass.java", "java")
        registry = {
            "pkg1.MyClass.doWork": sym1,
            "pkg2.OtherClass.doWork": sym2,
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork", path_hint="pkg2")

        assert result.found is True
        assert result.symbol is sym2
        assert result.confidence == NameResolver.CONFIDENCE_PATH_HINT
        assert result.match_type == "path_hint"

    def test_suffix_match_disabled(self) -> None:
        """Suffix matching can be disabled."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork", allow_suffix=False)

        assert result.found is False


class TestNameResolverIndexing:
    """Tests for lazy index building in NameResolver."""

    def test_suffix_index_built_lazily(self) -> None:
        """Suffix index is only built when needed."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}
        resolver = NameResolver(registry)

        # Index not built yet
        assert resolver._suffix_index is None

        # Exact lookup doesn't build index
        resolver.lookup("MyClass.doWork")
        assert resolver._suffix_index is None

        # Suffix lookup builds index
        resolver.lookup("doWork")
        assert resolver._suffix_index is not None

    def test_suffix_index_early_return(self) -> None:
        """_ensure_suffix_index returns early if index already built."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}
        resolver = NameResolver(registry)

        # Build the index
        resolver.lookup("doWork")
        original_index = resolver._suffix_index

        # Calling again returns same object
        resolver._ensure_suffix_index()
        assert resolver._suffix_index is original_index

    def test_clear_indexes(self) -> None:
        """clear_indexes removes cached indexes."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}
        resolver = NameResolver(registry)

        # Build index
        resolver.lookup("doWork")
        assert resolver._suffix_index is not None

        # Clear
        resolver.clear_indexes()
        assert resolver._suffix_index is None


class TestLookupNameConvenience:
    """Tests for the lookup_name convenience function."""

    def test_lookup_name_exact_match(self) -> None:
        """Convenience function finds exact match."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}

        result = lookup_name(registry, "MyClass.doWork")

        assert result is sym

    def test_lookup_name_suffix_match(self) -> None:
        """Convenience function finds suffix match."""
        sym = make_symbol("doWork", "/app/MyClass.java", "java")
        registry = {"MyClass.doWork": sym}

        result = lookup_name(registry, "doWork")

        assert result is sym

    def test_lookup_name_not_found(self) -> None:
        """Convenience function returns None when not found."""
        registry: dict = {}

        result = lookup_name(registry, "doWork")

        assert result is None


# ============================================================================
# ListNameResolver Tests (for dict[str, list[Symbol]] registries)
# ============================================================================


class TestListNameResolverExactMatch:
    """Tests for exact name matching in ListNameResolver."""

    def test_single_candidate_returns_symbol(self) -> None:
        """Single candidate returns the symbol with exact confidence."""
        sym = make_symbol("Register", "/grpc/server.go", "grpc")
        registry = {"Register": [sym]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == 1.0
        assert len(result.candidates) == 1

    def test_not_found_returns_none(self) -> None:
        """No candidates returns None."""
        registry: dict = {}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register")

        assert result.found is False
        assert result.symbol is None

    def test_empty_list_returns_none(self) -> None:
        """Empty candidate list returns None."""
        registry: dict = {"Register": []}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register")

        assert result.found is False
        assert result.symbol is None


class TestListNameResolverDisambiguation:
    """Tests for disambiguation in ListNameResolver."""

    def test_multiple_candidates_without_hint_returns_first(self) -> None:
        """Multiple candidates without hint returns first with low confidence."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {"Register": [sym_grpc, sym_http]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register")

        assert result.found is True
        assert result.symbol is sym_grpc  # First candidate
        assert result.confidence == ListNameResolver.CONFIDENCE_AMBIGUOUS
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 2

    def test_path_hint_disambiguates(self) -> None:
        """Path hint selects correct candidate."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {"Register": [sym_grpc, sym_http]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register", path_hint="http")

        assert result.found is True
        assert result.symbol is sym_http
        assert result.confidence == ListNameResolver.CONFIDENCE_PATH_HINT
        assert result.match_type == "path_hint"
        assert len(result.candidates) == 2

    def test_path_hint_with_full_path(self) -> None:
        """Path hint extracts directory from full path."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {"Register": [sym_grpc, sym_http]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register", path_hint="github.com/foo/grpc")

        assert result.found is True
        assert result.symbol is sym_grpc
        assert result.confidence == ListNameResolver.CONFIDENCE_PATH_HINT

    def test_path_hint_no_match_falls_back(self) -> None:
        """Path hint with no match falls back to first candidate."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {"Register": [sym_grpc, sym_http]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register", path_hint="nonexistent")

        assert result.found is True
        assert result.symbol is sym_grpc  # Falls back to first
        assert result.confidence == ListNameResolver.CONFIDENCE_AMBIGUOUS
