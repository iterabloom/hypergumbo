# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Go call on a typed external receiver names the TYPE, not just its package (INV-vusum).

Go's slot for ``c.Get(u)`` with ``c *http.Client`` was ``net/http``, the same
slot ``http.Get(u)`` gets. The catalogue rows ``net/http.Get`` (function) and
``net/http.Client.Get`` (method) both matched it, and the first-declared won,
so every receiver call was credited to the package function:

    b.TempDir()   on *testing.B      -> testing.T.TempDir
    tr.Do(nil)    on *http.Transport -> net/http.Client.Do   (no such method)
    c.Get(u)      on *http.Client    -> net/http.Get
    l.Info("x")   on *slog.Logger    -> log/slog.Info

The analyzer already KNEW the type (``var_types`` holds ``http.Client``) and
resolved its package through go.mod (:func:`_external_package_for_type`); it
kept the package and dropped the type. A type in the slot is conformant under
the module-key axis ("a type names where the symbol is DEFINED"), and it is
what Python, JavaScript and Rust already emit (``requests.Session``,
``dns.Resolver``, ``std::fs::File``). The slot is always the FULL import path
plus the type, never a bare leaf: ``Client`` alone matches eight rows through
``_module_matches``' suffix arm.

Scope, stated: a typed local, a stdlib package variable
(``http.DefaultClient``) and a struct field of an external type
(``e.logger``). A tree with no go.mod keeps the package-granular slot, because
there the analyzer cannot tell an in-module package from an external one.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_GO = '''package main

import (
	"log/slog"
	"net/http"
	"testing"
)

func benchTempDir(b *testing.B) { b.TempDir() }

func transportDo() {
	tr := &http.Transport{}
	tr.Do(nil)
}

func clientGet(c *http.Client, u string) { c.Get(u) }

func pkgGet(u string) { http.Get(u) }

func logIt(l *slog.Logger) { l.Info("x") }

func defaultGet(u string) { http.DefaultClient.Get(u) }

type Endpoints struct {
	logger *slog.Logger
}

func (e *Endpoints) fieldLog() { e.logger.Error("x") }

func tripIt(rt http.RoundTripper, req *http.Request) { rt.RoundTrip(req) }

func main() {}
'''


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, set]:
    root = tmp_path_factory.mktemp("vusum")
    repo = root / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/fx\n\ngo 1.22\n")
    (repo / "main.go").write_text(_GO)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
    survey = root / "s.json"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main(["survey", str(repo), "--out", str(survey)])
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    finally:
        mp.undo()
    calls: dict[str, set[str]] = {}
    for e in json.loads(survey.read_text())["edges"]:
        if e["type"] == "calls":
            calls.setdefault(e["src"].split(":")[-2], set()).add(e["dst"])
    chains = {
        (b, c["primitive"], c["io_edge_src"].split(":")[-2])
        for b, v in json.loads(buf.getvalue())["boundaries"].items() for c in v["chains"]
    }
    return calls, chains


@pytest.mark.parametrize(("fn", "slot"), [
    ("clientGet", "net/http.Client"),
    ("logIt", "log/slog.Logger"),
    ("benchTempDir", "testing.B"),
    ("transportDo", "net/http.Transport"),
    ("defaultGet", "net/http.Client"),
    ("Endpoints.fieldLog", "log/slog.Logger"),
    ("tripIt", "net/http.RoundTripper"),
])
def test_a_typed_receiver_puts_its_type_in_the_slot(
    results: tuple[dict, set], fn: str, slot: str,
) -> None:
    calls, _ = results
    assert any(d.startswith(f"go:{slot}:0-0:") for d in calls[fn]), calls[fn]


def test_a_package_qualified_call_keeps_the_package(results: tuple[dict, set]) -> None:
    calls, _ = results
    assert "go:net/http:0-0:Get:external_symbol" in calls["pkgGet"]


@pytest.mark.parametrize(("primitive", "fn"), [
    ("net/http.Client.Get", "clientGet"),
    ("net/http.Client.Get", "defaultGet"),
    ("log/slog.Logger.Info", "logIt"),
    ("testing.B.TempDir", "benchTempDir"),
    ("net/http.Get", "pkgGet"),
    ("log/slog.Logger.Error", "Endpoints.fieldLog"),
    # The interface's own row: this reached Transport.RoundTrip only while the
    # slot was the bare package, and was lost (prometheus) once it named the type.
    ("net/http.RoundTripper.RoundTrip", "tripIt"),
])
def test_each_call_is_credited_to_its_own_row(
    results: tuple[dict, set], primitive: str, fn: str,
) -> None:
    _, chains = results
    assert any(p == primitive and f == fn for _, p, f in chains), chains


def test_a_method_the_type_does_not_have_is_no_boundary(results: tuple[dict, set]) -> None:
    """``http.Transport`` has no ``Do``: this was a ``net_send`` chain. It is now
    an unclassified call into a named module, which is what the disclosed-only
    ``external_potential`` bucket is for."""
    _, chains = results
    at_transport = {(b, p) for b, p, fn in chains if fn == "transportDo"}
    assert at_transport == {("external_potential", "net/http.Transport.Do")}
