# SPDX-License-Identifier: AGPL-3.0-or-later
"""A method called on a call's RESULT takes the result's type as its receiver (WI-vinuh).

``slog.New(h).Error(msg)`` and ``json.NewEncoder(w).Encode(v)`` call a method
directly on what a package function returned. go.py's chained-call branch knew
only the inner call's PACKAGE and put it in the module slot, so the method was
matched by name inside that package: ``log/slog``'s package FUNCTION ``Error``
won over ``log/slog.Logger.Error`` by declaration order (the INV-vusum shape,
for the receiver that has no variable to type).

The type a call returns is already answered in go.py, for ``x := f(...)``
bindings: the ``NewXxx`` constructor convention, then the return-type registry
(analysed declarations plus library_signatures/go.yaml). The chained branch now
asks the same question of its operand, and a result type from OUTSIDE the
module fills the slot with ``<import path>.<Type>`` exactly as a typed local
does. ``slog.New`` is not a ``NewXxx`` constructor, so it needed its row.

Each test asserts the call edge exists (reach) before what it says.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_lang_mainstream.go import analyze_go

_CATALOGS = {"go": load_catalog("go")}


def _edges(tmp_path: Path, imports: str, body: str, extra: str = "") -> list:
    (tmp_path / "go.mod").write_text("module example.com/t\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text(
        f"package main\n\nimport (\n{imports}\n)\n\n{extra}\n"
        f"func use(h slog.Handler, w io.Writer, v any) {{\n{body}\n}}\n")
    return [e for e in analyze_go(tmp_path).edges if e.edge_type == "calls"]


def _the_call(edges: list, callee: str):
    # The name slot is `Type.Method` on a resolved edge, the bare name otherwise.
    hits = [e for e in edges if e.dst.split(":")[-2].rsplit(".", 1)[-1] == callee]
    assert len(hits) == 1, [(e.dst, e.line) for e in edges]  # reach
    return hits[0]


_IMPORTS = '\t"encoding/json"\n\t"io"\n\t"log/slog"\n\t"os/exec"'


@pytest.mark.parametrize("body", [
    "\tslog.New(h).Error(\"boom\")",
    "\tslog.Default().Error(\"boom\")",
    "\tslog.With(\"k\", v).Error(\"boom\")",
    # The same producer row types a binding, which used to go to `external`.
    "\tl := slog.New(h)\n\tl.Error(\"boom\")",
])
def test_a_slog_logger_method_is_the_logger_row(tmp_path: Path, body: str) -> None:
    edge = _the_call(_edges(tmp_path, _IMPORTS, body), "Error")
    assert edge.dst == "go:log/slog.Logger:0-0:Error:unresolved"
    assert edge.meta["call_construct"] == "method"
    primitive = classify_call(_CATALOGS, edge.dst, edge.meta)
    assert primitive is not None
    # The METHOD row, not the package function log/slog.Error.
    assert (primitive.module, primitive.name, primitive.boundary) == (
        "log/slog.Logger", "Error", "logging")


def test_the_chained_encoder_is_the_bound_encoder(tmp_path: Path) -> None:
    """The constructor convention already typed `e := json.NewEncoder(w)`; the
    chain now gets the same slot instead of the bare package."""
    chained = _the_call(
        _edges(tmp_path, _IMPORTS, "\tjson.NewEncoder(w).Encode(v)"), "Encode")
    bound = _the_call(
        _edges(tmp_path, _IMPORTS, "\te := json.NewEncoder(w)\n\te.Encode(v)"), "Encode")
    assert chained.dst == bound.dst == "go:encoding/json.Encoder:0-0:Encode:unresolved"
    assert chained.is_resolved is False


def test_a_library_producer_row_types_the_chain(tmp_path: Path) -> None:
    edge = _the_call(_edges(tmp_path, _IMPORTS, "\texec.Command(\"ls\").Run()"), "Run")
    assert edge.dst == "go:os/exec.Cmd:0-0:Run:unresolved"
    primitive = classify_call(_CATALOGS, edge.dst, edge.meta)
    assert primitive is not None and primitive.module == "os/exec.Cmd"
    assert primitive.boundary == "subprocess"


def test_a_method_result_of_an_external_type_reaches_its_row(tmp_path: Path) -> None:
    """Not only package functions: an in-repo method whose declared result is
    `*http.Client` types `s.Client().Do(req)`. It went to `go:external` (an
    ambiguous name, so no row) before."""
    extra = (
        "type Server struct{ c *http.Client }\n\n"
        "func (s *Server) Client() *http.Client { return s.c }\n")
    edges = _edges(
        tmp_path, _IMPORTS + '\n\t"net/http"',
        "\tvar s *Server\n\tvar req *http.Request\n\ts.Client().Do(req)", extra)
    edge = _the_call(edges, "Do")
    assert edge.dst == "go:net/http.Client:0-0:Do:unresolved"
    primitive = classify_call(_CATALOGS, edge.dst, edge.meta)
    assert primitive is not None and primitive.boundary == "net_send"


def test_an_in_repo_package_named_like_the_stdlib_is_no_impostor(tmp_path: Path) -> None:
    """The slot names a type this module cannot define, so no in-repo method of
    that name is looked up. `internal/json`'s `Enc.Encode` matched the `json`
    path hint's last component and took the call before."""
    (tmp_path / "internal" / "json").mkdir(parents=True)
    (tmp_path / "internal" / "json" / "enc.go").write_text(
        "package json\n\ntype Enc struct{}\n\nfunc (e *Enc) Encode(v any) error { return nil }\n")
    edges = _edges(tmp_path, _IMPORTS, "\tjson.NewEncoder(w).Encode(v)")
    edge = _the_call(edges, "Encode")
    assert edge.dst == "go:encoding/json.Encoder:0-0:Encode:unresolved"
    assert not [e for e in edges if "Enc.Encode" in e.dst]


def test_control_a_result_of_unknown_type_keeps_the_package_slot(tmp_path: Path) -> None:
    """No convention, no row: nothing is guessed, the slot stays the package."""
    edge = _the_call(
        _edges(tmp_path, _IMPORTS + '\n\t"context"', "\tcontext.Background().Done()"),
        "Done")
    assert edge.dst == "go:context:0-0:Done:unresolved"


def test_control_an_in_repo_result_type_is_resolved_as_before(tmp_path: Path) -> None:
    """A result type INSIDE the module is the in-repo resolver's to bind."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "a.go").write_text(
        "package a\n\ntype Foo struct{}\n\nfunc NewFoo() *Foo { return &Foo{} }\n\n"
        "func (f *Foo) Bar() {}\n")
    edges = _edges(tmp_path, _IMPORTS + '\n\t"example.com/t/a"', "\ta.NewFoo().Bar()")
    edge = _the_call(edges, "Bar")
    assert edge.dst.endswith("a/a.go:7-7:Foo.Bar:method")


@pytest.mark.parametrize("body", [
    "\thttp.DefaultClient.Do(req)",   # a stdlib package variable (WI-jikik)
    "\ts.cl.Do(req)",                 # a struct field of an external type
])
def test_the_same_rule_holds_for_the_sibling_receivers(tmp_path: Path, body: str) -> None:
    """The two other routes that put an external TYPE in the slot asked the
    in-repo resolver anyway, and an `internal/http` method `Do` took the call
    -- so `http.DefaultClient.Do`, a catalogued net_send, read as an in-repo
    call."""
    (tmp_path / "internal" / "http").mkdir(parents=True)
    (tmp_path / "internal" / "http" / "x.go").write_text(
        "package http\n\ntype X struct{}\n\nfunc (x *X) Do(v any) error { return nil }\n")
    extra = "type S struct{ cl *http.Client }\n"
    edges = _edges(tmp_path, _IMPORTS + '\n\t"net/http"',
                   f"\tvar s *S\n\tvar req *http.Request\n{body}", extra)
    edge = _the_call(edges, "Do")
    assert edge.dst == "go:net/http.Client:0-0:Do:unresolved"
    primitive = classify_call(_CATALOGS, edge.dst, edge.meta)
    assert primitive is not None and primitive.boundary == "net_send"
