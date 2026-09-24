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

TWO THAT STAY, and why each is a measured refusal rather than an omission:

* go ``net/http.NewRequest``. With a typed client the flow survives at
  ``Client.Do``. Through ``http.DefaultClient.Do(req)`` the package variable's
  type is unknown, so ``Client.Do`` is never reached, and deleting the row LOSES
  the finding. It is compensating for WI-jikik.
* python ``urllib.request.Request``. The flow survives at ``urlopen``. But the
  ``Request(...)`` call then counts as an unclassified call into a module whose
  I/O surface is not enumerated, and the coverage gate withholds every clean
  verdict for such a program. All 18 of hypergumbo's own self-claims went
  ``inconclusive``. That waits for WI-bakik to enumerate ``urllib.request``.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog


def _boundaries(language: str, module: str, name: str) -> set[str]:
    return {p.boundary for p in load_catalog(language).primitives
            if (p.module, p.name) == (module, name)}


@pytest.mark.parametrize("language,module,name,boundary", [
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
])
def test_the_executor_still_carries_the_crossing(
        language: str, module: str, name: str, boundary: str) -> None:
    """The represented crossing Ruling 3 licenses the removal against."""
    assert boundary in _boundaries(language, module, name)


@pytest.mark.parametrize("language,module,name,blocker", [
    ("go", "net/http", "NewRequest", "WI-jikik"),
    ("python", "urllib.request", "Request", "WI-bakik"),
])
def test_a_constructor_is_kept_until_its_blocker_lands(
        language: str, module: str, name: str, blocker: str) -> None:
    """Remove the pin together with the row, once the blocker lands and the
    finding-level A/B (and, for python, scripts/check-self-claims) passes."""
    assert "net_send" in _boundaries(language, module, name), blocker
