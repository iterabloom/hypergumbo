# SPDX-License-Identifier: AGPL-3.0-or-later
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
        import math
        assert abs(result.confidence - SymbolResolver.CONFIDENCE_AMBIGUOUS / math.sqrt(2)) < 0.01

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
        """Multiple name matches: unresolved, both candidates kept."""
        sym1 = make_symbol("common", "/pkg1/utils.py", "pkg1.utils")
        sym2 = make_symbol("common", "/pkg2/utils.py", "pkg2.utils")
        registry = {
            ("pkg1.utils", "common"): sym1,
            ("pkg2.utils", "common"): sym2,
        }
        resolver = SymbolResolver(registry)

        result = resolver.lookup_by_name("common")

        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "ambiguous"
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


class TestNameResolverPathHints:
    """Tests for import-scope disambiguation via path_hints in NameResolver."""

    def test_path_hints_skip_wrong_exact_match(self) -> None:
        """When path_hints are provided and exact match is NOT in an imported
        module, fall through to suffix matching which checks all candidates."""
        sym_wrong = make_symbol("error", "test/unrelated.d", "d")
        sym_right = make_symbol("error", "src/errors.d", "d")
        registry = {
            "error": sym_wrong,  # exact match points to wrong symbol
            "errors.error": sym_right,  # qualified name in suffix index
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("error", path_hints=["errors"])

        assert result.found is True
        assert result.symbol is sym_right
        assert result.confidence == NameResolver.CONFIDENCE_PATH_HINT

    def test_path_hints_confirm_correct_exact_match(self) -> None:
        """When path_hints are provided and exact match IS in an imported
        module, return the exact match with full confidence."""
        sym = make_symbol("error", "src/errors.d", "d")
        registry = {
            "error": sym,
            "errors.error": sym,
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("error", path_hints=["errors"])

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == NameResolver.CONFIDENCE_EXACT

    def test_path_hints_no_match_falls_through(self) -> None:
        """When path_hints don't match any candidate: an unresolved tie."""
        sym_a = make_symbol("error", "test/a.d", "d")
        sym_b = make_symbol("error", "test/b.d", "d")
        registry = {
            "error": sym_a,
            "a.error": sym_a,
            "b.error": sym_b,
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("error", path_hints=["nonexistent"])

        assert result.found is False
        assert {c.path for c in result.candidates} == {"test/a.d", "test/b.d"}


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
        """Ambiguous suffix match: no guess, candidates kept."""
        sym1 = make_symbol("doWork", "/pkg1/MyClass.java", "java")
        sym2 = make_symbol("doWork", "/pkg2/OtherClass.java", "java")
        registry = {
            "pkg1.MyClass.doWork": sym1,
            "pkg2.OtherClass.doWork": sym2,
        }
        resolver = NameResolver(registry)

        result = resolver.lookup("doWork")

        assert result.found is False
        assert result.symbol is None
        assert "ambiguous" in result.match_type
        assert len(result.candidates) == 2

    def test_suffix_match_many_candidates_low_confidence(self) -> None:
        """Many suffix candidates (15 classes defining Initialize()): unresolved."""
        registry = {}
        for i in range(15):
            sym = make_symbol("Initialize", f"/pkg{i}/Class{i}.cpp", "cpp")
            registry[f"pkg{i}::Class{i}::Initialize"] = sym
        resolver = NameResolver(registry)

        result = resolver.lookup("Initialize")

        assert result.found is False
        assert result.symbol is None
        assert len(result.candidates) == 15

    def test_suffix_key_disambiguation_same_file(self) -> None:
        """Key-based disambiguation when path_hint matches all candidates' paths.

        When multiple candidates share the same file path, path-based
        disambiguation fails (all match).  The resolver falls back to
        key-based matching: path_hint="Parser" matches key
        "Parser::Initialize" but not "Packager::Initialize".
        """
        sym1 = make_symbol("Initialize", "/src/parser.cpp", "cpp")
        sym2 = make_symbol("Initialize", "/src/parser.cpp", "cpp")
        registry = {
            "Parser::Initialize": sym1,
            "Packager::Initialize": sym2,
        }
        resolver = NameResolver(registry)

        # path_hint="Parser" should match key "Parser::Initialize" uniquely
        result = resolver.lookup("Initialize", path_hint="Parser")

        assert result.found is True
        assert result.symbol is sym1
        assert result.confidence == NameResolver.CONFIDENCE_PATH_HINT
        assert result.match_type == "path_hint"

    def test_suffix_key_disambiguation_multiple_key_matches(self) -> None:
        """When key matching produces multiple matches, return first."""
        sym1 = make_symbol("Init", "/src/core.cpp", "cpp")
        sym2 = make_symbol("Init", "/src/core.cpp", "cpp")
        sym3 = make_symbol("Init", "/src/core.cpp", "cpp")
        registry = {
            "Core::Parser::Init": sym1,
            "Core::Loader::Init": sym2,
            "Other::Init": sym3,
        }
        resolver = NameResolver(registry)

        # "Core" matches two keys (both Core::Parser and Core::Loader)
        result = resolver.lookup("Init", path_hint="Core")
        assert result.found is True
        assert result.symbol in [sym1, sym2]
        assert result.confidence == NameResolver.CONFIDENCE_PATH_HINT

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

    def test_single_candidate_with_matching_path_hint(self) -> None:
        """Single candidate whose path matches hint is returned normally."""
        sym = make_symbol("Encode", "/encoding/json/stream.go", "json")
        registry = {"Encode": [sym]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Encode", path_hint="encoding/json")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == 1.0

    def test_single_candidate_with_non_matching_path_hint(self) -> None:
        """Single candidate whose path doesn't match hint returns not-found.

        Prevents false edges like json.NewEncoder(w).Encode(data) resolving
        to a local MarshalEncoder.Encode method when the path_hint says the
        call targets encoding/json.
        """
        sym = make_symbol("Encode", "/myapp/marshal.go", "myapp")
        registry = {"Encode": [sym]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Encode", path_hint="encoding/json")

        assert result.found is False
        assert result.symbol is None

    def test_hint_single_candidate_matching_returns_exact(self) -> None:
        """A path hint that matches a lone candidate returns it, exact."""
        sym = make_symbol("prove", "/crates/core/nifs/src/lib.rs", "nifs")
        registry = {"prove": [sym]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("prove", path_hint="crates/core/nifs/src")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == 1.0

    def test_hard_hint_single_candidate_non_matching_rejects(self) -> None:
        """A lone candidate outside the path hint is rejected.

        Preserves Go-style import-path filtering behavior.
        """
        sym = make_symbol("Encode", "/myapp/marshal.go", "myapp")
        registry = {"Encode": [sym]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Encode", path_hint="encoding/json")

        assert result.found is False
        assert result.symbol is None

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
        """Multiple candidates without hint: no guess, both candidates kept."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {"Register": [sym_grpc, sym_http]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register")

        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "ambiguous"
        assert result.candidates == [sym_grpc, sym_http]

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
        """Path hint with no match: no fallback to the first candidate."""
        sym_grpc = make_symbol("Register", "/grpc/server.go", "grpc")
        sym_http = make_symbol("Register", "/http/server.go", "http")
        registry = {"Register": [sym_grpc, sym_http]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("Register", path_hint="nonexistent")

        assert result.found is False
        assert result.symbol is None
        assert len(result.candidates) == 2

    def test_short_path_hint_uses_full_path(self) -> None:
        """Short path hint like 'entities/bug' disambiguates via full path match.

        When the last segment alone is ambiguous (both paths contain
        'bug'), the full path 'entities/bug' should uniquely match.
        """
        sym_pkg = make_symbol("AddComment", "/entities/bug/op_add_comment.go", "go")
        sym_local = make_symbol("AddComment", "/cache/bug_cache.go", "go")
        registry = {"AddComment": [sym_pkg, sym_local]}
        resolver = ListNameResolver(registry)

        result = resolver.lookup("AddComment", path_hint="entities/bug")

        assert result.found is True
        assert result.symbol is sym_pkg
        assert result.confidence == ListNameResolver.CONFIDENCE_PATH_HINT
        assert result.match_type == "path_hint"


class TestListNameResolverCandidateScaling:
    """Ambiguous confidence scales down with more candidates.

    When many types implement the same method (e.g., Close(), String(),
    Name() in Go), a random pick among 50 candidates should have much
    lower confidence than a pick among 2. The formula is
    1/sqrt(num_candidates), giving:
      2 → 0.707,  5 → 0.447,  10 → 0.316,  50 → 0.141
    """

    def test_two_candidates_confidence(self) -> None:
        """2 candidates: unresolved, every candidate kept (no 1/sqrt(N) guess)."""
        syms = [make_symbol("Close", f"/pkg{i}/x.go", "go") for i in range(2)]
        result = ListNameResolver({"Close": syms}).lookup("Close")
        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 2

    def test_ten_candidates_lower_confidence(self) -> None:
        """10 candidates: unresolved, every candidate kept (no 1/sqrt(N) guess)."""
        syms = [make_symbol("String", f"/pkg{i}/x.go", "go") for i in range(10)]
        result = ListNameResolver({"String": syms}).lookup("String")
        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 10

    def test_fifty_candidates_very_low_confidence(self) -> None:
        """50 candidates: unresolved, every candidate kept (no 1/sqrt(N) guess)."""
        syms = [make_symbol("Name", f"/pkg{i:02d}/x.go", "go") for i in range(50)]
        result = ListNameResolver({"Name": syms}).lookup("Name")
        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 50

    def test_path_hint_not_affected_by_scaling(self) -> None:
        """Path hint disambiguation maintains full confidence regardless of candidate count."""
        syms = [make_symbol("Close", f"/pkg{i:02d}/x.go", "go") for i in range(50)]
        resolver = ListNameResolver({"Close": syms})
        result = resolver.lookup("Close", path_hint="/pkg07/")
        assert result.found is True
        assert result.confidence == ListNameResolver.CONFIDENCE_PATH_HINT

    def test_single_candidate_not_affected(self) -> None:
        """Single candidate still gets exact confidence."""
        syms = [make_symbol("Unique", "/pkg/x.go", "go")]
        resolver = ListNameResolver({"Unique": syms})
        result = resolver.lookup("Unique")
        assert result.found is True
        assert result.confidence == ListNameResolver.CONFIDENCE_EXACT


class TestListNameResolverAmbiguityThreshold:
    """Tests for the ambiguity_threshold parameter.

    When candidate count >= ambiguity_threshold and no path_hint match,
    the resolver should return an unresolved result (found=False) instead
    of picking an arbitrary candidate. This prevents false-positive call
    edges for common method names like Get(), Set(), Close(), String().

    Invariant: AMB-METHOD — method calls with 3+ ambiguous receiver types
    must not produce resolved call edges.
    """

    def test_threshold_returns_unresolved_at_boundary(self) -> None:
        """Exactly 3 candidates with threshold=3 → unresolved."""
        syms = [make_symbol("Close", f"/pkg{i}/x.go", "go") for i in range(3)]
        resolver = ListNameResolver({"Close": syms}, ambiguity_threshold=3)
        result = resolver.lookup("Close")
        assert result.found is False, (
            "3 candidates at threshold=3 should be unresolved"
        )
        assert result.is_ambiguous is True
        assert len(result.candidates) == 3

    def test_threshold_returns_unresolved_above_boundary(self) -> None:
        """5 candidates with threshold=3 → unresolved."""
        syms = [make_symbol("Get", f"/pkg{i}/x.go", "go") for i in range(5)]
        resolver = ListNameResolver({"Get": syms}, ambiguity_threshold=3)
        result = resolver.lookup("Get")
        assert result.found is False, (
            "5 candidates at threshold=3 should be unresolved"
        )
        assert result.is_ambiguous is True

    def test_below_threshold_still_resolves(self) -> None:
        """2 candidates with threshold=3: the guard does not fire, but a tie
        with no in-file evidence is still not a binding (WI-jomik)."""
        syms = [make_symbol("Run", f"/pkg{i}/x.go", "go") for i in range(2)]
        resolver = ListNameResolver({"Run": syms}, ambiguity_threshold=3)
        assert resolver.too_ambiguous("Run") is False
        result = resolver.lookup("Run")
        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 2

    def test_path_hint_bypasses_threshold(self) -> None:
        """Path hint resolves even when candidate count >= threshold."""
        syms = [make_symbol("Close", f"/pkg{i:02d}/x.go", "go") for i in range(5)]
        resolver = ListNameResolver({"Close": syms}, ambiguity_threshold=3)
        result = resolver.lookup("Close", path_hint="/pkg03/")
        assert result.found is True, (
            "Path hint should bypass ambiguity threshold"
        )
        assert result.confidence == ListNameResolver.CONFIDENCE_PATH_HINT

    def test_default_threshold_is_none(self) -> None:
        """Default threshold is None: the guard never fires; a tie is unresolved."""
        syms = [make_symbol("Close", f"/pkg{i}/x.go", "go") for i in range(10)]
        resolver = ListNameResolver({"Close": syms})
        assert resolver.ambiguity_threshold is None
        assert resolver.too_ambiguous("Close") is False
        result = resolver.lookup("Close")
        assert result.found is False
        assert result.symbol is None
        assert len(result.candidates) == 10

    def test_threshold_single_candidate_unaffected(self) -> None:
        """Single candidate is never affected by threshold."""
        syms = [make_symbol("Unique", "/pkg/x.go", "go")]
        resolver = ListNameResolver({"Unique": syms}, ambiguity_threshold=1)
        result = resolver.lookup("Unique")
        assert result.found is True, (
            "Single candidate should always resolve regardless of threshold"
        )

    def test_path_hint_narrowing_avoids_threshold(self) -> None:
        """When path hint narrows 3 → 2 candidates, threshold=3 is avoided.

        Three candidates for "Err":
          - /repo/pkg/log/handler.go  (standalone Err)
          - /repo/pkg/log/handler.go  (Handler.Err, stored by short name)
          - /repo/pkg/parser/lexer.go (Lexer.Err)

        Path hint "pkg/log" matches 2/3 candidates (both in pkg/log/), so the
        guard (3 >= 3) does not fire. The two left are a tie, and both in one
        file is no evidence for either, so the lookup is still unresolved.
        """
        syms = [
            make_symbol("Err", "/repo/pkg/log/handler.go", "go"),
            make_symbol("Handler.Err", "/repo/pkg/log/handler.go", "go"),
            make_symbol("Lexer.Err", "/repo/pkg/parser/lexer.go", "go"),
        ]
        # Store all under short name "Err" (as Go analyzer does)
        resolver = ListNameResolver({"Err": syms}, ambiguity_threshold=3)
        assert resolver.too_ambiguous("Err") is True
        assert resolver.too_ambiguous("Err", path_hint="pkg/log") is False
        result = resolver.lookup(
            "Err", path_hint="pkg/log", caller_path="/repo/pkg/log/handler.go",
        )
        assert result.found is False
        assert result.symbol is None

    def test_path_component_suffix_match_helper_edge_cases(self) -> None:
        """Direct tests for the _path_component_suffix_match helper.

        Covers edge cases that don't surface through ListNameResolver:
          - Empty hint parts (defensive — no signal).
          - Symbol path with no parent directory (bare filename).
        """
        from hypergumbo_core.symbol_resolution import (
            _path_component_suffix_match,
        )
        # Empty hint never matches.
        assert _path_component_suffix_match("/repo/api/api.go", []) is False
        # Bare filename has no parent components to match against.
        assert _path_component_suffix_match("api.go", ["api"]) is False
        # Sanity: a normal match still works.
        assert _path_component_suffix_match(
            "/repo/api/api.go", ["api"],
        ) is True

    def test_single_segment_hint_anchored_to_path_components(self) -> None:
        """Single-segment hint must match path components, not substrings.

        Regression: alertmanager has an ``api`` package at ``api/api.go``
        and a swagger-generated client tree at ``api/v2/client/<sub>/*.go``.
        Several of those generated files define ``func New(...)``.  When
        main.go calls ``api.New(...)``, the import path stripped against
        the module prefix yields the single-segment hint ``api``.

        Substring matching incorrectly admitted ``api/v2/client/general/g.go``,
        ``api/v2/client/silence/s.go`` etc. because the substring ``api``
        appears anywhere in the path.  With ``ambiguity_threshold=3`` the
        narrowed set was still ≥ 3 → unresolved → forward slice from main
        could not reach the api package.

        Component-anchored matching restricts the hint to a path-component
        suffix of the candidate's containing directory, so ``api`` matches
        only files whose parent directory IS ``api`` (not files in
        ``api/v2/client/<x>/``).
        """
        target = make_symbol("New", "/repo/api/api.go", "go")
        swagger1 = make_symbol("New", "/repo/api/v2/client/general/g.go", "go")
        swagger2 = make_symbol("New", "/repo/api/v2/client/silence/s.go", "go")
        swagger3 = make_symbol("New", "/repo/api/v2/client/alert/a.go", "go")
        swagger4 = make_symbol("New", "/repo/api/v2/client/receiver/r.go", "go")
        other = make_symbol("New", "/repo/notify/discord/d.go", "go")
        syms = [target, swagger1, swagger2, swagger3, swagger4, other]
        resolver = ListNameResolver({"New": syms}, ambiguity_threshold=3)
        result = resolver.lookup("New", path_hint="api")
        assert result.found is True, (
            "Single-segment hint 'api' should match only files whose parent "
            "directory is 'api', not files deeper in api/v2/client/*"
        )
        assert result.symbol is target
        assert result.confidence == ListNameResolver.CONFIDENCE_PATH_HINT
        assert result.match_type == "path_hint"


# ============================================================================
# NameResolver :: separator tests (Rust qualified names)
# ============================================================================


class TestNameResolverRustSeparator:
    """Tests for NameResolver suffix index handling of :: separators.

    Rust uses :: as the path separator (e.g., Diff::compute, crate::module::Foo).
    The suffix index must split on both . and :: to support suffix lookups
    like looking up "compute" and finding "Diff::compute".
    """

    def test_suffix_lookup_double_colon(self) -> None:
        """Looking up 'compute' finds 'Diff::compute' via suffix matching."""
        sym = make_symbol("Diff::compute", "/src/diff.rs", "rust")
        registry = {"Diff::compute": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("compute")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == NameResolver.CONFIDENCE_SUFFIX
        assert result.match_type == "suffix"

    def test_suffix_lookup_nested_double_colon(self) -> None:
        """Nested :: path supports suffix matching at multiple levels."""
        sym = make_symbol(
            "crate::module::Diff::compute", "/src/diff.rs", "rust",
        )
        registry = {"crate::module::Diff::compute": sym}
        resolver = NameResolver(registry)

        # Lookup by short name
        result = resolver.lookup("compute")
        assert result.found is True
        assert result.symbol is sym

        # Lookup by intermediate qualified name
        result2 = resolver.lookup("Diff::compute")
        assert result2.found is True
        assert result2.symbol is sym

    def test_exact_match_still_works(self) -> None:
        """Exact match on full :: name returns confidence 1.0."""
        sym = make_symbol("Diff::compute", "/src/diff.rs", "rust")
        registry = {"Diff::compute": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("Diff::compute")

        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == 1.0
        assert result.match_type == "exact"

    def test_ambiguous_double_colon(self) -> None:
        """Multiple :: entries with same suffix → unresolved suffix tie."""
        sym1 = make_symbol("Diff::compute", "/src/diff.rs", "rust")
        sym2 = make_symbol("Parser::compute", "/src/parser.rs", "rust")
        registry = {"Diff::compute": sym1, "Parser::compute": sym2}
        resolver = NameResolver(registry)

        result = resolver.lookup("compute")

        assert result.found is False
        assert result.symbol is None
        assert result.match_type == "suffix_ambiguous"
        assert len(result.candidates) == 2

    def test_mixed_separators(self) -> None:
        """Mixed . and :: in a key (e.g., 'pkg.Foo::bar') supports suffix lookup."""
        sym = make_symbol("pkg.Foo::bar", "/src/foo.rs", "rust")
        registry = {"pkg.Foo::bar": sym}
        resolver = NameResolver(registry)

        # Lookup by short name
        result = resolver.lookup("bar")
        assert result.found is True
        assert result.symbol is sym

        # Lookup by intermediate suffix crossing separator boundary
        result2 = resolver.lookup("Foo::bar")
        assert result2.found is True
        assert result2.symbol is sym

    def test_hash_separator_ruby(self) -> None:
        """Ruby # separator: 'User#save' → lookup 'save' finds it."""
        sym = make_symbol("User#save", "/app/user.rb", "ruby")
        registry = {"User#save": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("save")
        assert result.found is True
        assert result.symbol is sym
        assert result.confidence == NameResolver.CONFIDENCE_SUFFIX

    def test_backslash_separator_hack(self) -> None:
        r"""Hack \\ separator: 'App\\Utils\\helper' → lookup 'helper' finds it."""
        sym = make_symbol("App\\Utils\\helper", "/src/utils.hack", "hack")
        registry = {"App\\Utils\\helper": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("helper")
        assert result.found is True
        assert result.symbol is sym

        # Intermediate suffix
        result2 = resolver.lookup("Utils\\helper")
        assert result2.found is True
        assert result2.symbol is sym

    def test_colon_separator_luau(self) -> None:
        """Luau : separator: 'Player:attack' → lookup 'attack' finds it."""
        sym = make_symbol("Player:attack", "/src/player.luau", "luau")
        registry = {"Player:attack": sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("attack")
        assert result.found is True
        assert result.symbol is sym

    def test_colon_vs_double_colon(self) -> None:
        """Single : and :: are both handled without conflict."""
        sym_lua = make_symbol("Obj:method", "/src/obj.luau", "luau")
        sym_rust = make_symbol("Obj::method", "/src/obj.rs", "rust")
        registry = {"Obj:method": sym_lua, "Obj::method": sym_rust}
        resolver = NameResolver(registry)

        # "method" suffix matches both
        result = resolver.lookup("method")
        assert result.found is False
        assert result.match_type == "suffix_ambiguous"
        assert len(result.candidates) == 2

        # Full qualified name still exact matches
        result_lua = resolver.lookup("Obj:method")
        assert result_lua.found is True
        assert result_lua.symbol is sym_lua
        assert result_lua.confidence == 1.0

        result_rust = resolver.lookup("Obj::method")
        assert result_rust.found is True
        assert result_rust.symbol is sym_rust
        assert result_rust.confidence == 1.0


class TestNameResolverTestPathPreference:
    """Tests for NameResolver preferring non-test symbols over test symbols.

    When a non-test caller resolves a method name and both test-file and
    production-file candidates exist, the resolver should prefer the
    production candidate. This prevents false positives like
    ``server.startup()`` resolving to ``LogCleanerTest.startup()``
    instead of ``Server.startup()``.
    """

    def test_suffix_prefers_non_test_candidate(self) -> None:
        """When caller_path is non-test, prefer non-test suffix match."""
        prod_sym = make_symbol("Server.startup", "src/main/java/Server.java", "java")
        test_sym = make_symbol("LogCleanerTest.startup", "src/test/java/LogCleanerTest.java", "java")
        registry = {"Server.startup": prod_sym, "LogCleanerTest.startup": test_sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("startup", caller_path="src/main/java/App.java")

        assert result.found is True
        assert result.symbol is prod_sym, (
            f"Expected production symbol Server.startup, got {result.symbol.name}"
        )

    def test_suffix_no_preference_when_caller_is_test(self) -> None:
        """When caller_path is a test file, no test-path filtering: both stay."""
        prod_sym = make_symbol("Server.startup", "src/main/java/Server.java", "java")
        test_sym = make_symbol("TestHelper.startup", "src/test/java/TestHelper.java", "java")
        registry = {"Server.startup": prod_sym, "TestHelper.startup": test_sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("startup", caller_path="src/test/java/MyTest.java")
        assert result.found is False
        assert result.symbol is None
        assert len(result.candidates) == 2

    def test_suffix_single_test_candidate_still_resolves(self) -> None:
        """When only test candidates exist, still resolve (no elimination)."""
        test_sym = make_symbol("TestHelper.startup", "src/test/java/TestHelper.java", "java")
        registry = {"TestHelper.startup": test_sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("startup", caller_path="src/main/java/App.java")
        assert result.found is True
        assert result.symbol is test_sym  # Only option, still returned

    def test_no_caller_path_no_preference(self) -> None:
        """Without caller_path, no test-path preference: both stay candidates."""
        prod_sym = make_symbol("Server.startup", "src/main/java/Server.java", "java")
        test_sym = make_symbol("LogCleanerTest.startup", "src/test/java/LogCleanerTest.java", "java")
        registry = {"Server.startup": prod_sym, "LogCleanerTest.startup": test_sym}
        resolver = NameResolver(registry)

        result = resolver.lookup("startup")
        assert result.found is False
        assert result.symbol is None
        assert len(result.candidates) == 2

    def test_suffix_test_filter_with_path_hint_no_crash(self) -> None:
        """Test filtering + path_hint zip must not crash (B905 strict zip).

        When caller_path triggers test-path filtering, the candidates list
        shrinks but candidates_keys must shrink with it.  Otherwise the
        subsequent ``zip(candidates, candidates_keys, strict=True)`` in
        Stage 2 raises ValueError.
        """
        prod_sym = make_symbol(
            "Parser.Initialize", "src/parser.cc", "cpp"
        )
        test_sym = make_symbol(
            "ParserTest.Initialize", "test/parser_test.cc", "cpp"
        )
        registry = {
            "Parser.Initialize": prod_sym,
            "ParserTest.Initialize": test_sym,
        }
        resolver = NameResolver(registry)

        # Non-test caller + path_hint triggers both test filtering AND
        # path-hint Stage 2 zip.  Before the fix this raised ValueError.
        result = resolver.lookup(
            "Initialize",
            path_hint="parser",
            caller_path="src/main.cc",
        )
        assert result.found is True
        assert result.symbol is prod_sym


class TestNonUniquePickNeedsCallerFile:
    """WI-jomik phase 2: a pick among 2+ candidates needs in-file evidence.

    Measured on an 11-repo corpus, a non-unique pick (``ambiguous`` /
    ``suffix_ambiguous``) or a soft-hint fallback with no evidence from the
    caller's own file or its imports bound the wrong target ~80% of the time
    (45 of 57 judged), and 0 of 13 with it. So the resolvers no longer guess:
    such a lookup is unresolved, its candidates kept, unless exactly one
    candidate is defined in the caller's own file.
    """

    def _two(self) -> tuple[Symbol, Symbol]:
        return (make_symbol("doWork", "/pkg1/A.java", "pkg1"),
                make_symbol("doWork", "/pkg2/B.java", "pkg2"))

    def test_name_resolver_suffix_tie_is_unresolved(self) -> None:
        a, b = self._two()
        resolver = NameResolver({"pkg1.A.doWork": a, "pkg2.B.doWork": b})
        result = resolver.lookup("doWork", caller_path="/pkg3/C.java")
        assert result.found is False
        assert result.match_type == "suffix_ambiguous"
        assert sorted(c.path for c in result.candidates) == ["/pkg1/A.java", "/pkg2/B.java"]

    def test_name_resolver_suffix_tie_without_caller_is_unresolved(self) -> None:
        a, b = self._two()
        result = NameResolver({"pkg1.A.doWork": a, "pkg2.B.doWork": b}).lookup("doWork")
        assert result.found is False
        assert len(result.candidates) == 2

    def test_name_resolver_caller_file_candidate_wins(self) -> None:
        a, b = self._two()
        resolver = NameResolver({"pkg1.A.doWork": a, "pkg2.B.doWork": b})
        result = resolver.lookup("doWork", caller_path="/pkg2/B.java")
        assert result.symbol is b
        assert result.match_type == "caller_file"
        assert result.confidence == NameResolver.CONFIDENCE_PATH_HINT

    def test_two_candidates_in_the_caller_file_stay_unresolved(self) -> None:
        a = make_symbol("doWork", "/pkg1/A.java", "pkg1")
        b = make_symbol("doWork", "/pkg1/A.java", "pkg1")
        resolver = NameResolver({"pkg1.A.doWork": a, "pkg1.A.Inner.doWork": b})
        assert resolver.lookup("doWork", caller_path="/pkg1/A.java").found is False

    def test_list_resolver_tie_is_unresolved(self) -> None:
        a, b = self._two()
        result = ListNameResolver({"doWork": [a, b]}).lookup("doWork")
        assert result.found is False
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 2

    def test_list_resolver_hint_narrowed_tie_is_unresolved(self) -> None:
        syms = [make_symbol("Init", f"/src/{d}/init.go", d) for d in ("a/x", "b/x", "c/y")]
        result = ListNameResolver({"Init": syms}).lookup("Init", path_hint="x")
        assert result.found is False
        assert len(result.candidates) == 3

    def test_list_resolver_caller_file_candidate_wins(self) -> None:
        a, b = self._two()
        result = ListNameResolver({"doWork": [a, b]}).lookup(
            "doWork", caller_path="/pkg1/A.java")
        assert result.symbol is a
        assert result.match_type == "caller_file"

    def test_list_resolver_single_candidate_outside_hint_is_unresolved(self) -> None:
        sym = make_symbol("prove", "/crates/util/tower/src/lib.rs", "tower")
        result = ListNameResolver({"prove": [sym]}).lookup(
            "prove", path_hint="crates/core/keys/src/keys")
        assert result.found is False

    def test_soft_hint_lone_candidate_outside_hint_is_kept(self) -> None:
        sym = make_symbol("prove", "/crates/util/tower/src/lib.rs", "tower")
        result = ListNameResolver({"prove": [sym]}).lookup(
            "prove", path_hint="crates/core/keys/src/keys", soft_hint=True)
        assert result.symbol is sym
        assert result.match_type == "soft_hint_fallback"
        assert result.confidence < 1.0

    def test_soft_hint_does_not_license_a_tie(self) -> None:
        syms = [make_symbol("prove", f"/crates/{d}/src/lib.rs", d) for d in ("a", "b")]
        result = ListNameResolver({"prove": syms}).lookup(
            "prove", path_hint="crates/core/keys/src/keys", soft_hint=True)
        assert result.found is False

    def test_list_resolver_unique_and_path_hint_hits_unchanged(self) -> None:
        a, b = self._two()
        assert ListNameResolver({"doWork": [a]}).lookup("doWork").symbol is a
        hit = ListNameResolver({"doWork": [a, b]}).lookup("doWork", path_hint="pkg2")
        assert hit.symbol is b and hit.match_type == "path_hint"

    def test_symbol_resolver_name_tie_is_unresolved(self) -> None:
        a, b = self._two()
        result = SymbolResolver({("pkg1", "doWork"): a, ("pkg2", "doWork"): b}).lookup_by_name("doWork")
        assert result.found is False
        assert result.match_type == "ambiguous"
        assert len(result.candidates) == 2

    def test_too_ambiguous_single_candidate_and_selecting_hint(self) -> None:
        one = make_symbol("Run", "/pkg0/x.go", "go")
        assert ListNameResolver({"Run": [one]}, ambiguity_threshold=1).too_ambiguous("Run") is False
        syms = [make_symbol("Run", f"/pkg{i}/x.go", "go") for i in range(3)]
        resolver = ListNameResolver({"Run": syms}, ambiguity_threshold=3)
        assert resolver.too_ambiguous("Run") is True
        assert resolver.too_ambiguous("Run", path_hint="pkg1") is False

    def test_admit_filters_candidates_by_call_shape(self) -> None:
        fn = make_symbol("Err", "/repo/pkg/log/handler.go", "go")
        meth = Symbol(id="go:/repo/pkg/log/handler.go:3-5:Handler.Err:method", name="Handler.Err",
                      kind="method", language="go", path="/repo/pkg/log/handler.go",
                      span=Span(start_line=3, start_col=0, end_line=5, end_col=0))
        resolver = ListNameResolver({"Err": [meth, fn]})
        assert resolver.lookup("Err", path_hint="pkg/log").found is False  # a tie
        hit = resolver.lookup("Err", path_hint="pkg/log", admit=lambda s: s.kind != "method")
        assert hit.symbol is fn and hit.match_type == "exact"
        assert resolver.lookup("Err", admit=lambda s: False).found is False

    def test_variant_registry_binds_a_tie_deterministically(self) -> None:
        syms = [make_symbol("platform", f"/a/p_{o}.go", "a") for o in ("linux", "darwin")]
        result = ListNameResolver({"platform": syms}, variants=True).lookup("platform")
        assert result.symbol is not None and result.symbol.path == "/a/p_darwin.go"
        assert result.match_type == "ambiguous"
        assert abs(result.confidence - 1 / 2 ** 0.5) < 1e-9


class TestModuleInTree:
    """WI-gabak: does an absolute import of this module land in the registry's tree?"""

    def _resolver(self) -> SymbolResolver:
        return SymbolResolver({
            ("mlflow.entities.assessment", "AssessmentSourceType"): make_symbol(
                "AssessmentSourceType", "mlflow/entities/assessment.py",
                "mlflow.entities.assessment",
            ),
            ("mlflow.tensorflow", "autolog"): make_symbol(
                "autolog", "mlflow/tensorflow/__init__.py", "mlflow.tensorflow",
            ),
            ("clint.rules.k", "Klass"): make_symbol(
                "Klass", "dev/clint/src/clint/rules/k.py", "clint.rules.k",
            ),
        })

    @pytest.mark.parametrize("module", [
        "mlflow", "mlflow.entities", "mlflow.entities.assessment",
        "mlflow.protos.databricks_pb2",  # a generated module with no symbol of its own
        "clint", "clint.rules",  # registered from its source root below dev/clint/src
    ])
    def test_a_module_under_a_registered_top_level_package_is_in_the_tree(
        self, module: str,
    ) -> None:
        assert self._resolver().module_in_tree(module) is True

    @pytest.mark.parametrize("module", [
        "tensorflow", "tensorflow.keras",  # the flavour mlflow.tensorflow is not tensorflow
        "flask", "flask.request", "unittest", "entities", "assessment", "dev",
    ])
    def test_an_external_module_is_not(self, module: str) -> None:
        assert self._resolver().module_in_tree(module) is False

    def test_the_index_follows_the_registry_and_clears_with_the_others(self) -> None:
        registry = {("pkg.a", "f"): make_symbol("f", "pkg/a.py", "pkg.a")}
        first = SymbolResolver(registry)
        assert first.module_in_tree("pkg.b") is True
        assert first.module_in_tree("other") is False
        registry[("other.c", "g")] = make_symbol("g", "other/c.py", "other.c")
        # A grown registry is re-indexed by a fresh resolver (the size check);
        # the first resolver keeps its index until it is told to clear.
        assert SymbolResolver(registry).module_in_tree("other") is True
        assert first.module_in_tree("other") is False
        first.clear_indexes()
        assert first.module_in_tree("other") is True
