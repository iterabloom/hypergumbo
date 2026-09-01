# SPDX-License-Identifier: AGPL-3.0-or-later
"""Arm 3 of ``_module_matches`` must not match a TYPE against a VARIABLE.

INV-dijor. ``_module_matches`` arm 3 ("dropped qualification") compares
trailing components after casefolding BOTH sides, so it loses the one signal
that distinguishes java's ``System`` — an unqualified reference to the class
``java.lang.System`` — from go's ``context``, a parameter variable of type
``*cli.Context`` that happens to be spelled like the trailing component of
``github.com/gin-gonic/gin.Context``.

WHY CASE AGREEMENT AND NOT A CAPITALISATION TEST. The item's own first step
lists three candidate fixes and warns that (a), "a capitalisation test on arm 3
symmetric with arm 2", is not obviously right. It is not: arm 2 asks whether a
component IS capitalised, which encodes "capitals mean types" — Go's convention,
applied by a predicate that takes no language and serves fifteen catalogues, and
carrying no information at all where module names are capitalised (haskell 100%,
swift 97%, objc 95%, elixir 52%). Arm 3 needs a strictly weaker question: do the
two sides AGREE in how they are spelled? That is language-neutral. In
capitalised-module languages both sides are capitalised, so they agree and every
existing match survives; in go a lowercase variable disagrees with a capitalised
type and is refused.

The axiom (ADR-0051) is what says this is the right cut: the module key names an
owner path, and a receiver VARIABLE is the non-conformant notion. Case
disagreement is evidence the hint is a variable rather than an owner path — and
this is EVIDENCE, not the declaration ADR-0051 anticipates. Arm 2's case-VALUE
inference is untouched and remains the residual.
"""
from __future__ import annotations

import pytest


# (catalogue module, edge hint, should_match, why)
_CASES = [
    # --- must keep matching -------------------------------------------
    (
        "java.lang.System", "System", True,
        "java unqualified class reference — INV-januj and INV-hahak both "
        "depend on this arm; breaking it re-loses java's only ipc_recv row",
    ),
    ("java.io.FileInputStream", "FileInputStream", True, "java unqualified class"),
    (
        "path/filepath", "filepath", True,
        "go true positive named by INV-dijor as at risk from fix (a)",
    ),
    (
        "google.golang.org/grpc", "grpc", True,
        "go true positive named by INV-dijor as at risk from fix (a)",
    ),
    ("net/http", "http", True, "source spells http.Get after importing net/http"),
    (
        "Data.ByteString.Lazy", "Data.ByteString.Lazy", True,
        "haskell — every component capitalised, so both sides agree",
    ),
    (
        "pathlib.Path", "Path", True,
        "the CLASS Path, correctly spelled — agrees in case, still matches",
    ),
    # --- must stop matching -------------------------------------------
    (
        "github.com/gin-gonic/gin.Context", "context", False,
        "INV-dijor's filed instance: sops reads a urfave/cli flag on a "
        "PARAMETER named context and it classified as net_send",
    ),
    (
        "pathlib.Path", "path", False,
        "40 python rows carry a trailing Path; `path` is the commonest "
        "variable name there is",
    ),
    (
        "java.sql.Connection", "connection", False,
        "same shape in a second language — a `connection` variable",
    ),
]


@pytest.mark.parametrize(
    "catalog_module,hint,expected,why",
    _CASES,
    ids=[f"{c}<-{h}" for c, h, _e, _w in _CASES],
)
def test_arm3_requires_case_agreement(catalog_module, hint, expected, why):
    from hypergumbo_core.io_boundary import _module_matches

    assert _module_matches(catalog_module, hint) is expected, why


def test_the_filed_repro_end_to_end():
    """INV-dijor's own repro, through the production catalogue lookup.

    The item verified this live on sops (cmd/sops/main.go:2383) and recorded
    both the defect and a control. Both are asserted here so the closure rests
    on the item's own shape rather than on a unit test of the predicate.
    """
    from hypergumbo_core.io_boundary import load_catalog

    catalog = load_catalog("go")
    assert catalog is not None

    hit = catalog.lookup_with_module(
        "String", "context", call_construct="method",
    )
    assert hit is None, (
        f"context.String still reaches {hit.module}.{hit.name} "
        f"({hit.boundary}) — a CLI flag read modelled as network egress"
    )

    # The row must stay reachable from the hint it is FOR: this is a
    # precision fix, and losing the true positive would be the trade
    # INV-dijor explicitly warns against.
    intended = catalog.lookup_with_module(
        "String", "github.com/gin-gonic/gin", call_construct="method",
    )
    assert intended is not None
    assert intended.module == "github.com/gin-gonic/gin.Context"


def test_control_still_discriminates():
    """The instrument must not have become a blanket refusal.

    INV-dijor's own control was ``context.Deadline -> None``. A fix that
    returns None for everything would pass the assertion above while being
    worthless, so a call that SHOULD match is checked in the same run.
    """
    from hypergumbo_core.io_boundary import load_catalog

    go = load_catalog("go")
    assert go is not None
    assert go.lookup_with_module("Deadline", "context", call_construct="method") is None
    # A genuine go stdlib I/O call still resolves.
    assert go.lookup_with_module("Open", "os", call_construct="method") is not None
