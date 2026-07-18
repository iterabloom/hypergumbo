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

from hypergumbo_core.analyze.base import defer_bare_method_call


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
