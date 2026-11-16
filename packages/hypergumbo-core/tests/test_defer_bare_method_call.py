# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``defer_bare_method_call`` — the shared INV-fahub decision.

A BARE (implicit-``this``/``self``) call may legitimately reach a free
function/object or a method of its OWN enclosing class, but must NOT bind to a
DIFFERENT class's method on weak short-name evidence (the cross-language magnet
misbind). The helper returns ``True`` to withhold+defer such a call to the
``inherited_calls`` Site-1 walker, ``False`` to bind directly. These cases are
also exercised end-to-end by the Scala/Swift analyzer tests; this pins the pure
decision table.
"""

from hypergumbo_core.analyze.base import defer_bare_method_call, defer_unresolved_tie
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.symbol_resolution import ListNameResolver


def test_non_method_targets_bind() -> None:
    # free function / object apply — a bare call reaches these legitimately.
    assert defer_bare_method_call("function", "helper", "suffix", "Calc") is False
    assert defer_bare_method_call("object", "App", "suffix", None) is False


def test_same_class_method_binds() -> None:
    # implicit ``this``/``self`` — owner class == enclosing class.
    assert (
        defer_bare_method_call("method", "Calc.helper", "suffix", "Calc") is False
    )


def test_cross_class_weak_match_defers() -> None:
    assert (
        defer_bare_method_call("method", "FileCopyTask.copy", "suffix", "InputEnv")
        is True
    )
    assert defer_bare_method_call("method", "X.m", "suffix_ambiguous", "Y") is True
    assert defer_bare_method_call("method", "X.m", "ambiguous", "Y") is True


def test_cross_class_strong_match_binds() -> None:
    # exact / import-scoped (path_hint) resolution is trustworthy even cross-class.
    assert defer_bare_method_call("method", "X.m", "exact", "Y") is False
    assert defer_bare_method_call("method", "X.m", "path_hint", "Y") is False


def test_owner_unknown_method_defers_on_weak() -> None:
    # a method symbol whose name carries no ``Owner.`` — owner is None, which is
    # never == the enclosing class, so a weak match still defers.
    assert defer_bare_method_call("method", "orphan", "suffix", "Calc") is True


def test_enclosing_none_defers_cross_class_weak() -> None:
    # a top-level def with no owning class calling a bare weak-matched method.
    assert defer_bare_method_call("method", "X.m", "suffix", None) is True


def test_custom_separator_parses_owner() -> None:
    # Rust / C++ methods are named "Owner::method"; with separator="::" the
    # same-class shortcut fires and cross-class weak matches still defer.
    assert (
        defer_bare_method_call("method", "Foo::bar", "suffix", "Foo", separator="::")
        is False
    )
    assert (
        defer_bare_method_call("method", "Foo::bar", "suffix", "Other", separator="::")
        is True
    )
    # the default "." separator cannot find the owner in a "::" name (owner=None),
    # so it defers on match_type alone — tolerable because same-class calls are
    # pre-caught upstream, but this is why "::" callers pass the separator.
    assert defer_bare_method_call("method", "Foo::bar", "suffix", "Foo") is True


class TestDeferUnresolvedTie:
    """WI-jomik: a tie the resolver refused to guess is deferred like a weak pick."""

    @staticmethod
    def _sym(name: str, kind: str, path: str) -> Symbol:
        return Symbol(id=f"x:{path}:1-2:{name}:{kind}", name=name, kind=kind,
                      language="x", path=path, span=Span(1, 0, 2, 0))

    def test_tie_among_methods_defers(self) -> None:
        a, b = self._sym("A.run", "method", "/a.x"), self._sym("B.run", "method", "/b.x")
        assert defer_unresolved_tie(ListNameResolver({"run": [a, b]}).lookup("run")) is True

    def test_tie_among_free_functions_does_not_defer(self) -> None:
        a, b = self._sym("run", "function", "/a.x"), self._sym("run", "function", "/b.x")
        assert defer_unresolved_tie(ListNameResolver({"run": [a, b]}).lookup("run")) is False

    def test_a_binding_or_a_miss_does_not_defer(self) -> None:
        a = self._sym("A.run", "method", "/a.x")
        resolver = ListNameResolver({"run": [a]})
        assert defer_unresolved_tie(resolver.lookup("run")) is False
        assert defer_unresolved_tie(resolver.lookup("nothing")) is False
