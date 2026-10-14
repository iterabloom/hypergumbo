# SPDX-License-Identifier: AGPL-3.0-or-later
"""When a call's module slot names a row's owner exactly, that row wins (INV-vusum).

A short name can be catalogued twice under one package: as a package-level
FUNCTION (``requests.get``, ``net/http.Get``, ``dns.resolve4``,
``std::fs.read_to_string``) and as a METHOD of a type in that package
(``requests.Session.get``, ``net/http.Client.Get``, ``dns.Resolver.resolve4``,
``std::fs::File.read_to_string``). The module filter keeps both whenever the
slot names the type, because ``_module_matches`` admits a type under its
package by the component rule, and selection then returned the FIRST-DECLARED
row, which is the function in every one of these pairs. So
``s = requests.Session(); s.get(u)``, whose slot is ``requests.Session``, was
reported as ``requests.get``.

64 such pairs ship (go 17, javascript 14, python 27, rust 6), and in every
one both rows carry the same boundary, so this changes the reported primitive
and never a boundary, zone or verdict. The one exception is not a pair at
all: ``tr.Do()`` on an ``http.Transport``, which a package-granular Go slot
matched to ``net/http.Client.Do``. With the receiver's type in the slot it
matches nothing, because no row names ``Transport.Do``.

The preference is a NARROWING, not a filter: when no candidate's module equals
a spelling of the slot, every candidate the module filter kept is still there,
so no match that fired before is lost.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import _module_matches, load_catalog


@pytest.mark.parametrize(("language", "name", "slot", "expected"), [
    ("python", "get", "requests.Session", "requests.Session"),
    ("python", "get", "requests", "requests"),
    ("python", "post", "httpx.Client", "httpx.Client"),
    ("python", "post", "httpx.AsyncClient", "httpx.AsyncClient"),
    ("python", "pprint", "pprint.PrettyPrinter", "pprint.PrettyPrinter"),
    ("javascript", "resolve4", "dns.Resolver", "dns.Resolver"),
    ("javascript", "resolve4", "dns", "dns"),
    ("rust", "read_to_string", "std::fs::File", "std::fs::File"),
    ("rust", "read_to_string", "std::fs", "std::fs"),
    ("go", "Get", "net/http.Client", "net/http.Client"),
    ("go", "Get", "net/http", "net/http"),
    ("go", "Info", "log/slog.Logger", "log/slog.Logger"),
    ("go", "LookupHost", "net.Resolver", "net.Resolver"),
    ("go", "TempDir", "testing.B", "testing.B"),
])
def test_the_row_the_slot_names_is_selected(
    language: str, name: str, slot: str, expected: str,
) -> None:
    row = load_catalog(language).lookup_with_module(name, slot, call_construct="method")
    assert row is not None and row.module == expected


def test_every_function_method_pair_still_resolves_by_its_own_owner() -> None:
    """Enumerated over the live catalogues, not a remembered list."""
    checked = 0
    for language in ("go", "javascript", "python", "rust"):
        catalog = load_catalog(language)
        rows = list({(p.module, p.name): p for p in catalog.primitives}.values())
        for row in rows:
            siblings = [
                p for p in rows if p.name == row.name and p is not row
                and p.kind != row.kind
                and (_module_matches(p.module, row.module)
                     or _module_matches(row.module, p.module))
            ]
            if not siblings:
                continue
            got = catalog.lookup_with_module(row.name, row.module)
            assert got is not None and got.module == row.module, (language, row.module, row.name)
            checked += 1
    assert checked >= 64  # reach: the population the docstring states


def test_a_type_the_package_does_not_row_matches_nothing() -> None:
    """``tr.Do()`` on an ``http.Transport``: no row names Transport.Do."""
    assert load_catalog("go").lookup_with_module("Do", "net/http.Transport",
                                                 call_construct="method") is None


def test_a_third_party_type_with_a_stdlib_leaf_name_matches_nothing() -> None:
    """The negative control INV-vusum set for a type-granular slot, in the form
    this slot takes: always the full import path, never a bare leaf. A bare
    leaf (``Client``) matches 56 Go rows through ``_module_matches``' suffix
    arm, which is why the slot must never carry one."""
    go = load_catalog("go")
    for slot in ("github.com/foo/bar.Client", "example.com/x.Logger", "example.com/y.Conn"):
        for name in ("Get", "Do", "Info", "Read", "Write"):
            assert go.lookup_with_module(name, slot, call_construct="method") is None, slot


def test_no_exact_owner_keeps_every_candidate() -> None:
    """A narrowing, not a filter: java's unqualified ``System`` names no row's
    module exactly and still reaches ``java.lang.System``."""
    row = load_catalog("java").lookup_with_module("getenv", "System")
    assert row is not None and row.module == "java.lang.System"
