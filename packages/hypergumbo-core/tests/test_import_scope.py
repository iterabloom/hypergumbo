# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hypergumbo_core.import_scope (WI-tihup foundation).

ImportScope is the shared analyzer-layer abstraction for per-file
import-binding bookkeeping. These tests cover its five population
methods, the resolve precedence between explicit and namespace
bindings, and the ExternalRef convenience constructor. The per-
language adoption tests live alongside each analyzer (e.g.
test_py_dst_ref.py for the Python reference adoption in PR1, and
sibling files for the 7 other analyzers in PR2).
"""

from hypergumbo_core.import_scope import CanonicalName, ImportScope
from hypergumbo_core.ir import ExternalRef


def test_add_named_default_alias_uses_name() -> None:
    scope = ImportScope()
    scope.add_named("urllib.request", "urlopen")
    assert scope.resolve("urlopen") == CanonicalName(
        module="urllib.request", name="urlopen"
    )


def test_add_named_with_alias() -> None:
    """from urllib.request import Request as MyRequest → MyRequest resolves to Request."""
    scope = ImportScope()
    scope.add_named("urllib.request", "Request", alias="MyRequest")
    assert scope.resolve("MyRequest") == CanonicalName(
        module="urllib.request", name="Request"
    )
    # The underlying canonical name is NOT bound under itself when aliased
    # (the source did not bring `Request` into scope, only `MyRequest`).
    assert scope.resolve("Request") is None


def test_add_namespace() -> None:
    """import numpy as np → np resolves to module=numpy, name=np."""
    scope = ImportScope()
    scope.add_namespace("numpy", "np")
    resolved = scope.resolve("np")
    assert resolved is not None
    assert resolved.module == "numpy"
    # For namespace resolution, name field equals the alias so callers
    # can still construct an ExternalRef for module-as-callable cases.
    assert resolved.name == "np"


def test_add_aliased_module_alias_of_namespace() -> None:
    """add_aliased_module is verb-flavor alias of add_namespace."""
    scope_a = ImportScope()
    scope_b = ImportScope()
    scope_a.add_aliased_module("Enum", "E")
    scope_b.add_namespace("Enum", "E")
    assert scope_a.resolve("E") == scope_b.resolve("E")


def test_add_wildcard_binds_each_export_under_bare_name() -> None:
    """from os.path import * → join, dirname, basename all bound to os.path."""
    scope = ImportScope()
    scope.add_wildcard("os.path", ["join", "dirname", "basename"])
    assert scope.resolve("join") == CanonicalName(module="os.path", name="join")
    assert scope.resolve("dirname") == CanonicalName(module="os.path", name="dirname")
    assert scope.resolve("basename") == CanonicalName(module="os.path", name="basename")


def test_wildcard_does_not_shadow_explicit_named() -> None:
    """Explicit named binding wins over wildcard for the same local name."""
    scope = ImportScope()
    scope.add_named("real_module", "myfunc")
    scope.add_wildcard("noisy_wildcard_module", ["myfunc", "other"])
    # Explicit named retained
    assert scope.resolve("myfunc") == CanonicalName(module="real_module", name="myfunc")
    # Wildcard's other names still bound
    assert scope.resolve("other") == CanonicalName(
        module="noisy_wildcard_module", name="other"
    )


def test_resolve_returns_none_for_unbound_local() -> None:
    scope = ImportScope()
    scope.add_named("os", "getcwd")
    assert scope.resolve("not_imported") is None


def test_resolve_precedence_named_before_module() -> None:
    """Explicit named binding wins over module-namespace alias for same local name."""
    scope = ImportScope()
    scope.add_namespace("some.module", "X")
    scope.add_named("other.module", "fn", alias="X")
    resolved = scope.resolve("X")
    assert resolved is not None
    # Named binding takes precedence — module=other.module, name=fn
    assert resolved.module == "other.module"
    assert resolved.name == "fn"


def test_dst_ref_for_returns_external_ref() -> None:
    scope = ImportScope()
    scope.add_named("urllib.request", "urlopen")
    ref = scope.dst_ref_for("urlopen", lang="python")
    assert ref == ExternalRef(
        lang="python", module_path="urllib.request", name="urlopen"
    )


def test_dst_ref_for_returns_none_for_unbound() -> None:
    scope = ImportScope()
    assert scope.dst_ref_for("not_imported", lang="python") is None


def test_dst_ref_for_namespace_binding() -> None:
    """For namespace bindings, dst_ref name field is the alias."""
    scope = ImportScope()
    scope.add_namespace("numpy", "np")
    ref = scope.dst_ref_for("np", lang="python")
    assert ref == ExternalRef(lang="python", module_path="numpy", name="np")


def test_empty_scope() -> None:
    """A fresh scope resolves nothing."""
    scope = ImportScope()
    assert scope.resolve("anything") is None
    assert scope.dst_ref_for("anything", lang="python") is None


def test_canonical_name_is_frozen() -> None:
    """CanonicalName is hashable and usable in sets/dicts."""
    a = CanonicalName(module="m", name="n")
    b = CanonicalName(module="m", name="n")
    c = CanonicalName(module="m", name="other")
    assert {a, b, c} == {a, c}


def test_re_adding_named_overwrites() -> None:
    """Last named binding wins for a given local name (analyzer-level convention)."""
    scope = ImportScope()
    scope.add_named("first.module", "foo")
    scope.add_named("second.module", "foo")
    assert scope.resolve("foo") == CanonicalName(module="second.module", name="foo")
