# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the ``SymbolByName`` helper (WI-sofaf).

WI-sofaf escalates the ``dict[str, Symbol]`` anti-pattern from per-linker
fixes to a shared helper after the third sibling Verilog bug (INV-paroh)
fired. The helper centralises:

* Multi-value insertion (``add`` never overwrites a previously-inserted
  candidate with the same name).
* Kind-aware preference cascade on lookup (``prefer_kind="module"`` puts
  module candidates first for the Verilog instantiation case).
* Fallback signalling (``lookup_one`` returns ``(symbol, is_fallback)``
  so callers can apply the ``INV-zuhub`` lower-confidence contract).
* Optional case-insensitivity (Verilog symbol resolution is
  case-insensitive at the spec level, unlike most other languages).

These tests pin the contract before the verilog migration so future
retrofits can copy the behaviour confidently.
"""
from __future__ import annotations

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.symbol_indexes import SymbolByName


def _sym(name: str, kind: str = "function", path: str = "x.py", language: str = "python") -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-1:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
    )


class TestInsertionAndLookup:
    """``add`` accumulates; ``lookup`` returns all candidates."""

    def test_single_insert_single_lookup(self) -> None:
        index = SymbolByName()
        sym = _sym("Foo")
        index.add(sym)
        assert index.lookup("Foo") == [sym]

    def test_multi_insert_no_overwrite(self) -> None:
        index = SymbolByName()
        a = _sym("Foo", path="a.py")
        b = _sym("Foo", path="b.py")
        index.add(a)
        index.add(b)
        result = index.lookup("Foo")
        assert len(result) == 2
        assert a in result and b in result

    def test_lookup_missing_returns_empty_list(self) -> None:
        index = SymbolByName()
        assert index.lookup("Missing") == []

    def test_contains_works(self) -> None:
        index = SymbolByName()
        index.add(_sym("Foo"))
        assert "Foo" in index
        assert "Missing" not in index


class TestKindPreferenceCascade:
    """``prefer_kind`` sorts matching-kind candidates first."""

    def test_prefer_kind_sorts_module_first(self) -> None:
        """Verilog INV-paroh scenario: module Foo + interface Foo →
        lookup with prefer_kind='module' returns module first."""
        index = SymbolByName()
        iface = _sym("Foo", kind="interface", path="iface.v", language="verilog")
        mod = _sym("Foo", kind="module", path="mod.v", language="verilog")
        index.add(iface)
        index.add(mod)
        result = index.lookup("Foo", prefer_kind="module")
        assert result[0] is mod
        assert result[1] is iface

    def test_prefer_kind_no_match_preserves_order(self) -> None:
        """When no candidate matches prefer_kind, all are returned in
        insertion order (no spurious reordering)."""
        index = SymbolByName()
        a = _sym("Foo", kind="function", path="a.py")
        b = _sym("Foo", kind="class", path="b.py")
        index.add(a)
        index.add(b)
        result = index.lookup("Foo", prefer_kind="module")  # no module exists
        assert result == [a, b]

    def test_prefer_kind_none_preserves_insertion_order(self) -> None:
        index = SymbolByName()
        a = _sym("Foo", kind="function", path="a.py")
        b = _sym("Foo", kind="class", path="b.py")
        index.add(a)
        index.add(b)
        assert index.lookup("Foo") == [a, b]


class TestLookupOne:
    """``lookup_one`` returns ``(symbol, is_fallback)`` per INV-zuhub contract."""

    def test_lookup_one_no_match_returns_none(self) -> None:
        index = SymbolByName()
        assert index.lookup_one("Missing") is None

    def test_lookup_one_single_candidate_not_fallback(self) -> None:
        """One candidate ⇒ confident resolution (``is_fallback=False``)."""
        index = SymbolByName()
        sym = _sym("Foo")
        index.add(sym)
        result = index.lookup_one("Foo")
        assert result == (sym, False)

    def test_lookup_one_multi_candidate_is_fallback(self) -> None:
        """Multiple candidates ⇒ ambiguous (``is_fallback=True``); caller
        applies the INV-zuhub confidence-≤-0.5 + disambiguation_fallback
        contract."""
        index = SymbolByName()
        a = _sym("Foo", path="a.py")
        b = _sym("Foo", path="b.py")
        index.add(a)
        index.add(b)
        result = index.lookup_one("Foo")
        assert result is not None
        sym, is_fallback = result
        assert is_fallback is True
        # Result is deterministic — first candidate in insertion order
        # absent prefer_kind tiebreaker.
        assert sym is a

    def test_lookup_one_prefer_kind_disambiguates_to_not_fallback(self) -> None:
        """If prefer_kind narrows to exactly one match, is_fallback can
        stay True because there are still >1 candidates overall. This
        documents the contract: ``is_fallback`` reflects raw candidate
        count, not post-preference uniqueness — callers can join on
        kind to decide whether to apply the contract."""
        index = SymbolByName()
        mod = _sym("Foo", kind="module", path="m.v", language="verilog")
        iface = _sym("Foo", kind="interface", path="i.v", language="verilog")
        index.add(mod)
        index.add(iface)
        sym, is_fallback = index.lookup_one("Foo", prefer_kind="module")
        assert sym is mod
        assert is_fallback is True


class TestCaseInsensitive:
    """``case_insensitive=True`` matches Verilog's resolution semantics."""

    def test_insert_lookup_case_insensitive(self) -> None:
        index = SymbolByName(case_insensitive=True)
        sym = _sym("MyModule", kind="module", language="verilog")
        index.add(sym)
        assert index.lookup("mymodule") == [sym]
        assert index.lookup("MYMODULE") == [sym]
        assert index.lookup("MyModule") == [sym]

    def test_insert_different_case_same_bucket(self) -> None:
        index = SymbolByName(case_insensitive=True)
        a = _sym("Foo", path="a.v", language="verilog")
        b = _sym("FOO", path="b.v", language="verilog")
        index.add(a)
        index.add(b)
        assert len(index.lookup("foo")) == 2

    def test_default_is_case_sensitive(self) -> None:
        index = SymbolByName()
        sym = _sym("Foo")
        index.add(sym)
        assert index.lookup("foo") == []
        assert index.lookup("FOO") == []
        assert index.lookup("Foo") == [sym]


class TestVerilogScenario:
    """End-to-end: the INV-paroh acceptance scenario."""

    def test_module_instantiation_resolves_to_module_not_interface(self) -> None:
        """Two Verilog symbols share name 'Driver': one is a `module`,
        one is an `interface`. The instantiation site should resolve to
        the module's id — pre-fix the silent-overwrite anti-pattern
        could land either way depending on insert order."""
        index = SymbolByName(case_insensitive=True)
        iface = _sym("Driver", kind="interface", path="i.v", language="verilog")
        mod = _sym("Driver", kind="module", path="m.v", language="verilog")
        # Insert in BAD order — interface first, module second; the
        # pre-fix code's last-write-wins would still land on module
        # in this order but the test pins the design contract.
        index.add(iface)
        index.add(mod)
        result = index.lookup_one("driver", prefer_kind="module")
        assert result is not None
        resolved, _ = result
        assert resolved is mod

    def test_module_instantiation_resolves_to_module_even_when_module_inserted_first(
        self,
    ) -> None:
        """Same scenario but module inserted first, interface second.
        Pre-fix this case would have resolved to interface (last write
        wins). Post-fix it resolves to module via prefer_kind cascade."""
        index = SymbolByName(case_insensitive=True)
        mod = _sym("Driver", kind="module", path="m.v", language="verilog")
        iface = _sym("Driver", kind="interface", path="i.v", language="verilog")
        index.add(mod)
        index.add(iface)
        result = index.lookup_one("driver", prefer_kind="module")
        assert result is not None
        resolved, _ = result
        assert resolved is mod
