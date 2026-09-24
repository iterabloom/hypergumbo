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

ONE THAT STAYS, a measured refusal rather than an omission:

* go ``net/http.NewRequest``. With a typed client the flow survives at
  ``Client.Do``. Through ``http.DefaultClient.Do(req)`` the package variable's
  type is unknown, so ``Client.Do`` is never reached, and deleting the row LOSES
  the finding. It is compensating for WI-jikik.

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
])
def test_the_executor_still_carries_the_crossing(
        language: str, module: str, name: str, boundary: str) -> None:
    """The represented crossing Ruling 3 licenses the removal against."""
    assert boundary in _boundaries(language, module, name)


@pytest.mark.parametrize("language,module,name,blocker", [
    ("go", "net/http", "NewRequest", "WI-jikik"),
])
def test_a_constructor_is_kept_until_its_blocker_lands(
        language: str, module: str, name: str, blocker: str) -> None:
    """Remove the pin together with the row, once the blocker lands and the
    finding-level A/B (and, for python, scripts/check-self-claims) passes."""
    assert "net_send" in _boundaries(language, module, name), blocker


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
