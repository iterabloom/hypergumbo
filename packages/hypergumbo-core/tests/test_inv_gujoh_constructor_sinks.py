# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-gujoh: a constructor declared under a data-transfer SINK boundary.

The sink-side twin of INV-nular's F3 (query builders declared ``db_read``). A
call that BUILDS a request or a table and sends or stores nothing was rowed under
the boundary its later executor crosses. So the flow was credited one call early,
at the constructor. ``ets.new`` was worse: it creates an empty table, and the
write was already rowed at ``ets.insert``, so one logical write counted twice.

THE RULE, F3's rule on the sink side. ADR-0049 Ruling 3 makes removal
licensed only against a REPRESENTED crossing, tested at the FINDING level. Each
removal below was run as a verify-claims A/B, the claim ``host_secret`` must not
reach network or database, on a fixture where the secret goes into the
constructor and then the executor. It was also run with the generic taint claims
on rebar3, cowboy, ejabberd, vernemq, plausible and livebook: no verdict moved.

  erlang  ets:new(...) -> ets:insert(T, {k, S})       violated, now at ets.insert
  elixir  Req.new(body: s) -> Req.request!(req)       violated, now at Req.request!
          Finch.build(.., s) -> Finch.request(req)    violated, now at Finch.request
          Req.new() |> Req.Request.run_request()      violated, at the executor
                                                      rowed WITH the removal
  python  Request(url, data=s) -> urlopen(req)        violated, now at urlopen
                                                      (WI-bakik; see below)
  go      http.NewRequest(.., s) -> Client.Do(req)    violated, now at Client.Do
                                                      (WI-jikik; see below)

The last two each waited on a prerequisite.

go ``net/http.NewRequest`` waited for WI-jikik. With a typed client the flow
survived at ``Client.Do``, but through ``http.DefaultClient.Do(req)`` the package
variable was untyped, so ``Client.Do`` was never reached and deleting the row
LOST the finding. WI-jikik types stdlib package variables
(library_signatures/go.yaml ``package_variables``), and
:func:`test_default_client_do_reaches_the_client_send` pins that on real
analyzer output.

python ``urllib.request.Request`` waited for WI-bakik. The flow survived at
``urlopen``, but the ``Request(...)`` call then counted as an unclassified call
into a module whose I/O surface was not enumerated, and the coverage gate
withheld every clean verdict for such a program: all 18 of hypergumbo's own
self-claims went ``inconclusive``. WI-bakik enumerated ``urllib.request`` and
declared it complete, so the row went with it.
:class:`TestUrllibRequestIsEnumerated` pins that on real analyzer output, with
a control that removes the completeness entry and watches the gate withhold.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog


def _boundaries(language: str, module: str, name: str) -> set[str]:
    return {p.boundary for p in load_catalog(language).primitives
            if (p.module, p.name) == (module, name)}


@pytest.mark.parametrize("language,module,name,boundary", [
    ("python", "urllib.request", "Request", "net_send"),
    ("go", "net/http", "NewRequest", "net_send"),
    ("erlang", "ets", "new", "db_write"),
    ("elixir", "ets", "new", "db_write"),
    ("elixir", "Req", "new", "net_send"),
    ("elixir", "Finch", "build", "net_send"),
])
def test_the_constructor_is_not_rowed_under_the_sink(
        language: str, module: str, name: str, boundary: str) -> None:
    assert boundary not in _boundaries(language, module, name)


@pytest.mark.parametrize("language,module,name,boundary", [
    ("erlang", "ets", "insert", "db_write"),
    ("elixir", "ets", "insert", "db_write"),
    ("elixir", "Req", "request!", "net_send"),
    ("elixir", "Req", "post!", "net_send"),
    # Rowed WITH the Req.new removal: plausible's `Req.new() |>
    # Req.Request.run_request()` had no other represented executor.
    ("elixir", "Req.Request", "run_request", "net_send"),
    ("elixir", "Finch", "request", "net_send"),
    ("python", "urllib.request", "urlopen", "net_send"),
    ("go", "net/http.Client", "Do", "net_send"),
])
def test_the_executor_still_carries_the_crossing(
        language: str, module: str, name: str, boundary: str) -> None:
    """The represented crossing Ruling 3 licenses the removal against."""
    assert boundary in _boundaries(language, module, name)


def test_default_client_do_reaches_the_client_send(tmp_path) -> None:
    """The represented crossing the go removal is licensed against, through the
    package variable that used to leave it unreachable."""
    from hypergumbo_core.io_boundary import classify_call
    from hypergumbo_lang_mainstream.go import analyze_go

    (tmp_path / "main.go").write_text(
        'package main\n\nimport (\n\t"bytes"\n\t"net/http"\n)\n\n'
        'func send(url, secret string) {\n'
        '\treq, _ := http.NewRequest("POST", url, bytes.NewBufferString(secret))\n'
        '\thttp.DefaultClient.Do(req)\n}\n')
    edges = [e for e in analyze_go(tmp_path).edges if e.edge_type == "calls"]
    by_name = {e.dst.split(":")[-2]: e for e in edges}
    assert {"NewRequest", "Do"} <= set(by_name), sorted(by_name)  # reach
    catalogs = {"go": load_catalog("go")}
    assert classify_call(catalogs, by_name["NewRequest"].dst, by_name["NewRequest"].meta) is None
    send = classify_call(catalogs, by_name["Do"].dst, by_name["Do"].meta)
    assert send is not None and (send.module, send.name, send.boundary) == (
        "net/http.Client", "Do", "net_send")


class TestUrllibRequestIsEnumerated:
    """WI-bakik: with ``Request`` unrowed, a program that builds one is still
    adjudicable, because ``urllib.request``'s surface is enumerated.

    Real analyzer output, real shipped catalogue, the real coverage gate.
    """

    _SOURCE = (
        "from urllib.request import Request, urlopen\n"
        "\n"
        "\n"
        "def send(url, body):\n"
        "    req = Request(url, data=body)\n"
        "    return urlopen(req)\n"
    )

    def _edges(self, tmp_path):
        from hypergumbo_lang_mainstream.py import analyze_python

        (tmp_path / "client.py").write_text(self._SOURCE)
        edges = [e.to_dict() for e in analyze_python(tmp_path).edges]
        called = {e["dst"].split(":")[-2] for e in edges if e["type"] in ("calls", "instantiates")}
        assert {"Request", "urlopen"} <= called, sorted(called)  # reach
        return edges

    def _coverage(self, edges, catalog):
        from hypergumbo_core.verify_claims import compute_boundary_coverage

        return compute_boundary_coverage(edges, {"python"}, {"python": catalog})

    def test_the_gate_admits_a_program_that_builds_a_request(self, tmp_path) -> None:
        coverage = self._coverage(self._edges(tmp_path), load_catalog("python"))
        assert coverage.complete is True, coverage.reason

    def test_control_without_the_enumeration_the_gate_withholds(self, tmp_path) -> None:
        from dataclasses import replace

        catalog = load_catalog("python")
        assert "urllib.request" in catalog.module_completeness
        without = replace(catalog, module_completeness={
            m: d for m, d in catalog.module_completeness.items() if m != "urllib.request"})
        coverage = self._coverage(self._edges(tmp_path), without)
        assert coverage.complete is False
        assert "urllib.request" in coverage.reason
