# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suffix index belongs to the REGISTRY, not to the resolver.

`_ensure_suffix_index` builds a whole-registry index (for each of R keys, split
the module and join every one of its D suffixes). `lookup_symbol` constructs a
fresh resolver per call, so an index cached on the RESOLVER would be rebuilt per
lookup: O(lookups x registry), and both factors grow with file count.

Caching on registry identity, rather than relying on callers to thread one
resolver through their call layers, is deliberate: an optional resolver argument
that forwarding functions default to None silently drops every caller onto the
rebuild-per-lookup path. Making the shared index the default protects every
caller, present and future. (history: WI-bavuz)
"""
from __future__ import annotations

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.symbol_resolution import (
    SymbolResolver,
    clear_registry_index_cache,
)


def _sym(name: str) -> Symbol:
    return Symbol(
        id=f"python:app/m.py:1-2:{name}:function",
        name=name,
        kind="function",
        path="app/m.py",
        language="python",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
    )


def _registry(n: int = 3) -> dict[tuple[str, str], Symbol]:
    return {
        (f"backend.app.mod{i}", f"fn{i}"): _sym(f"fn{i}") for i in range(n)
    }


class TestTheIndexBelongsToTheRegistry:
    def setup_method(self) -> None:
        clear_registry_index_cache()

    def test_two_resolvers_over_one_registry_share_the_index(self) -> None:
        reg = _registry()
        a, b = SymbolResolver(reg), SymbolResolver(reg)
        a.lookup("app.mod1", "fn1")
        b.lookup("app.mod1", "fn1")
        assert a._suffix_index is not None
        assert a._suffix_index is b._suffix_index, "index rebuilt per resolver"

    def test_distinct_registries_do_not_share(self) -> None:
        a = SymbolResolver(_registry())
        b = SymbolResolver(_registry())
        a.lookup("app.mod1", "fn1")
        b.lookup("app.mod1", "fn1")
        assert a._suffix_index is not b._suffix_index

    def test_the_name_index_is_shared_on_the_same_terms(self) -> None:
        reg = _registry()
        a, b = SymbolResolver(reg), SymbolResolver(reg)
        a._ensure_name_index()
        b._ensure_name_index()
        assert a._name_index is b._name_index


class TestSharingNeverServesAStaleIndex:
    def setup_method(self) -> None:
        clear_registry_index_cache()

    def test_a_grown_registry_is_reindexed(self) -> None:
        reg = _registry(3)
        a = SymbolResolver(reg)
        a.lookup("app.mod1", "fn1")
        reg[("backend.app.mod9", "fn9")] = _sym("fn9")
        b = SymbolResolver(reg)
        assert b.lookup("app.mod9", "fn9").symbol is not None
        assert a._suffix_index is not b._suffix_index

    def test_clear_indexes_evicts_the_shared_entry(self) -> None:
        reg = _registry()
        a = SymbolResolver(reg)
        a.lookup("app.mod1", "fn1")
        first = a._suffix_index
        a.clear_indexes()
        b = SymbolResolver(reg)
        b.lookup("app.mod1", "fn1")
        assert b._suffix_index is not first

    def test_the_cache_is_bounded(self) -> None:
        """A long-lived process must not accumulate registries forever."""
        from hypergumbo_core.symbol_resolution import _INDEX_CACHE_MAX, _INDEX_CACHE
        held = []
        for _ in range(_INDEX_CACHE_MAX + 4):
            reg = _registry()
            held.append(reg)
            SymbolResolver(reg).lookup("app.mod1", "fn1")
        assert len(_INDEX_CACHE) <= _INDEX_CACHE_MAX


class TestLookupsStillAnswerTheSame:
    def setup_method(self) -> None:
        clear_registry_index_cache()

    def test_suffix_match_still_resolves(self) -> None:
        reg = _registry()
        r = SymbolResolver(reg)
        got = r.lookup("app.mod2", "fn2")
        assert got.symbol is not None
        assert got.symbol.name == "fn2"
        assert got.match_type == "suffix"

    def test_exact_match_still_resolves(self) -> None:
        reg = _registry()
        r = SymbolResolver(reg)
        got = r.lookup("backend.app.mod0", "fn0")
        assert got.symbol is not None
        assert got.match_type == "exact"

    def test_a_miss_is_still_a_miss(self) -> None:
        r = SymbolResolver(_registry())
        assert r.lookup("nope.nothing", "absent").symbol is None

    def test_a_shared_index_answers_like_a_private_one(self) -> None:
        """The second resolver reuses the index and must not change answers."""
        reg = _registry()
        first = SymbolResolver(reg).lookup("app.mod1", "fn1")
        second = SymbolResolver(reg).lookup("app.mod1", "fn1")
        assert first.symbol is second.symbol
        assert first.match_type == second.match_type
        assert first.confidence == second.confidence
