# SPDX-License-Identifier: AGPL-3.0-or-later
"""A package-qualified Go call is a FUNCTION call, not a method call.

INV-tanom. ``go.py`` stamped ``call_construct="method"`` on every selector call,
so ``http.Get(u)`` (a free function in package ``net/http``) and ``c.Get(u)``
(a method on a ``*http.Client``) were byte-identical in (name, module slot,
construct). ``base.py`` defines the value as "``method`` for a call on a
receiver", and ADR-0059 files a call on the module itself as a function; a
package qualifier is neither a receiver nor an instance.

It was declared a zero-cost blindness on 2026-09-09 because its only reader then
was io-boundary's no-module gate, which a package-qualified call never reaches.
WI-fuvaj is the cost that arrived later: 8.1.0's ``unknown_receiver_scope``
caveat counts ``call_construct == "method"`` edges as its DENOMINATOR, so every
``fmt.Println`` was counted as a method call site whose receiver could have been
typed. On alertmanager the caveat printed 18.5% untyped where the share over
real method sites is about 31%.

THE LINE IS DRAWN WHERE THE ANALYZER ALREADY DRAWS IT. The emitter decides a
selector's operand is a package by finding it in the file's import aliases; a
tracked local of the same name stays a receiver. Each test pairs the two shapes
so a constant stamp in either direction fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.go import analyze_go

_SOURCE = """package main

import (
\t"fmt"
\t"net/http"
\t"net/url"
\t"os"
)

func pkgGet(u string)          { http.Get(u) }
func pkgRead(p string)         { os.ReadFile(p) }
func pkgPrint()                { fmt.Println("x") }
func recvGet(c *http.Client, u string) { c.Get(u) }
func recvClose(f *os.File)     { f.Close() }
func shadow(raw string) {
\turl, _ := url.Parse(raw)
\turl.String()
}
"""


def _constructs(tmp_path: Path) -> dict[tuple[str, str], str | None]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/t\n\ngo 1.21\n")
    (repo / "main.go").write_text(_SOURCE)
    analysis = analyze_go(repo)
    names = {s.id: s.name for s in analysis.symbols}
    out: dict[tuple[str, str], str | None] = {}
    for e in analysis.edges:
        if e.edge_type != "calls":
            continue
        callee = e.dst.split(":")[-2]
        out[(names.get(e.src, e.src), callee)] = (e.meta or {}).get("call_construct")
    return out


def test_a_package_qualified_call_is_a_function(tmp_path: Path) -> None:
    got = _constructs(tmp_path)
    assert got[("pkgGet", "Get")] == "function"
    assert got[("pkgRead", "ReadFile")] == "function"
    assert got[("pkgPrint", "Println")] == "function"


def test_a_receiver_call_stays_a_method(tmp_path: Path) -> None:
    """The control: without it a constant ``function`` would pass the test above."""
    got = _constructs(tmp_path)
    assert got[("recvGet", "Get")] == "method"
    assert got[("recvClose", "Close")] == "method"


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN LIMITATION, pinned so it stays visible: var_types is per function, "
    "not per position, so a local that shadows its import is not told apart "
    "from the package (the module slot makes the same call, unchanged)."
))
def test_a_local_that_shadows_its_import_is_a_receiver(tmp_path: Path) -> None:
    """``url, _ := url.Parse(raw); url.String()``: the second ``url`` is the
    local, so its call is really a method call."""
    got = _constructs(tmp_path)
    assert got[("shadow", "String")] == "method"


def test_the_call_that_defines_the_shadowing_local_is_a_function(
    tmp_path: Path,
) -> None:
    """Why the limitation is accepted rather than fixed with ``var_types``:
    consulting it would mark THIS call, which runs before the local exists,
    as a method call."""
    got = _constructs(tmp_path)
    assert got[("shadow", "Parse")] == "function"


def test_the_module_slot_is_unchanged(tmp_path: Path) -> None:
    """The fix changes the construct only; the slot the io-boundary gate reads
    still names the package, so no catalogue match can move."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/t\n\ngo 1.21\n")
    (repo / "main.go").write_text(_SOURCE)
    dsts = {e.dst for e in analyze_go(repo).edges if e.edge_type == "calls"}
    assert "go:net/http:0-0:Get:unresolved" in dsts
    assert "go:os:0-0:ReadFile:unresolved" in dsts


def test_the_unknown_receiver_denominator_counts_only_receiver_calls(
    tmp_path: Path,
) -> None:
    """WI-fuvaj on the production path: the caveat's "N of M method call
    site(s)" denominator, fed the real analyzer's edges. The file holds 4
    package-qualified calls and 2 typed-receiver calls; before INV-tanom's fix
    all 6 were counted."""
    from hypergumbo_core.io_boundary import load_catalog
    from hypergumbo_core.verify_claims import unknown_receiver_scope

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/t\n\ngo 1.21\n")
    (repo / "main.go").write_text(_SOURCE.replace("\turl.String()\n", ""))
    raw = [e.to_dict() for e in analyze_go(repo).edges]
    _sites, total, _names = unknown_receiver_scope(raw, {"go": load_catalog("go")})
    assert total == 2
