# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jikik: a method called on a stdlib PACKAGE VARIABLE reaches its catalogued row.

``http.DefaultClient.Do(req)`` names no local variable: its receiver is a package
variable whose type the analyzer did not know. The call was emitted as
``go:external:0-0:Do`` and ``net/http.Client.Do``'s net_send row was never
reached. The same call on a typed local (``client := &http.Client{}``) reached
it. So the false ``net/http.NewRequest`` send row was compensating: a secret
sent through ``http.DefaultClient`` was reported only because the request
CONSTRUCTOR was rowed as the send (INV-gujoh).

The package variables' types are catalogued in library_signatures/go.yaml under
``package_variables``. Each test asserts reach (the call edge exists) before
the classification.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_lang_mainstream.go import analyze_go

_CATALOGS = {"go": load_catalog("go")}


def _call(tmp_path: Path, body: str, imports: str, callee: str):
    (tmp_path / "main.go").write_text(
        f"package main\n\nimport (\n{imports}\n)\n\nfunc send(req *http.Request) {{\n{body}\n}}\n")
    edges = [e for e in analyze_go(tmp_path).edges
             if e.edge_type == "calls" and e.dst.split(":")[-2] == callee]
    assert len(edges) == 1, [(e.dst, e.line) for e in analyze_go(tmp_path).edges]  # reach
    return edges[0]


@pytest.mark.parametrize("imports,body", [
    ('\t"net/http"', "\thttp.DefaultClient.Do(req)"),
    ('\tnethttp "net/http"\n\thttp "net/http"', "\tnethttp.DefaultClient.Do(req)"),
])
def test_default_client_do_is_the_client_send(tmp_path: Path, imports: str, body: str) -> None:
    edge = _call(tmp_path, body, imports, "Do")
    assert edge.dst == "go:net/http:0-0:Do:unresolved"
    primitive = classify_call(_CATALOGS, edge.dst, edge.meta)
    assert primitive is not None
    assert (primitive.module, primitive.name, primitive.boundary) == ("net/http.Client", "Do", "net_send")


def test_default_resolver_lookup_is_a_dns_receive(tmp_path: Path) -> None:
    edge = _call(tmp_path, "\tnet.DefaultResolver.LookupHost(nil, req.Host)",
                 '\t"net"\n\t"net/http"', "LookupHost")
    assert edge.dst == "go:net:0-0:LookupHost:unresolved"
    primitive = classify_call(_CATALOGS, edge.dst, edge.meta)
    assert primitive is not None and primitive.boundary == "net_recv"


def test_control_an_uncatalogued_package_variable_stays_external(tmp_path: Path) -> None:
    """The table is the gate: a package variable nobody catalogued is not guessed."""
    edge = _call(tmp_path, "\thttp.NoBody.Close()", '\t"net/http"', "Close")
    assert edge.dst == "go:external:0-0:Close:unresolved"
