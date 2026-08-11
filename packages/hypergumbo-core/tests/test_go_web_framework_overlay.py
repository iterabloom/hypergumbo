# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Go web-framework rows moved OUT of go.yaml and must resolve from the overlay.

WHY THEY MOVED (INV-safig). ``go.yaml`` shipped 20 method rows for gin, echo,
fiber and gRPC. Two independent reasons to remove them, and either alone would
have been enough:

1. **They are third-party, and ADR-0016 scopes the built-in catalogue to the
   stdlib** — "a curated list of stdlib functions, not an unbounded set of
   library APIs" (§27), with §35 resolving the gap as "a project-local overlay,
   not more built-in rows".
2. **They matched nothing.** They declared a package-IDENTIFIER module
   (``gin.Context``) while the Go analyzer emits the module PATH it read from
   the import (``github.com/gin-gonic/gin``). Those never compare equal, so all
   twenty were unreachable from any real Go program. The reason that went
   unnoticed for so long is that the catalogue-reach probe synthesised its
   fixture's import FROM the catalogue's own module slot — it wrote
   ``import ("gin")``, an import no Go program contains, and the rows duly
   scored reachable against a hint production can never emit.

SO THIS FILE PINS THE SPELLING, WHICH IS THE THING THAT DRIFTED. Asserting only
"the overlay loads" would have passed just as happily with the old broken
modules. Every case here goes through ``lookup_with_module`` with the LITERAL
import path the analyzer reports, so a future edit that reverts to the short
form fails here rather than silently going dead again.

THE VERSION SUFFIX IS PART OF THE CONTRACT. Go's semantic import versioning puts
``/v2`` and ``/v4`` in the import path itself, so ``github.com/labstack/echo/v4``
is what the analyzer reports and what the overlay must declare. This pairs with
INV-javid, which fixed the analyzer half — before it, a ``/vN`` import bound the
alias ``vN`` and the package was never bound at all, so echo and fiber would
have stayed unreachable no matter how the catalogue spelled them.

THE ANALYZER HALF IS PINNED SEPARATELY, in the mainstream package's
``test_go_semantic_import_versioning``, because CI tests packages in isolation
and this package cannot import the Go analyzer. The two halves assert the same
literal strings from opposite ends; changing one without the other breaks the
other's test, which is the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog, load_overlay_catalog

#: The overlay's contract, keyed on what the Go analyzer actually emits as the
#: module hint. Left side is the hint; right side is (method, expected
#: qualified_name). These import paths are LITERAL on purpose.
_CONTRACT = [
    ("github.com/gin-gonic/gin", "JSON",
     "github.com/gin-gonic/gin.Context.JSON", "net_send"),
    ("github.com/gin-gonic/gin", "Run",
     "github.com/gin-gonic/gin.Engine.Run", "net_recv"),
    ("github.com/labstack/echo/v4", "JSON",
     "github.com/labstack/echo/v4.Context.JSON", "net_send"),
    ("github.com/labstack/echo/v4", "Start",
     "github.com/labstack/echo/v4.Echo.Start", "net_recv"),
    ("github.com/gofiber/fiber/v2", "Listen",
     "github.com/gofiber/fiber/v2.App.Listen", "net_recv"),
    ("google.golang.org/grpc", "Serve",
     "google.golang.org/grpc.Server.Serve", "net_recv"),
]


def _overlay_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "docs" / "io-primitives-overlays" / "go-web-frameworks.yaml"
    )


class TestTheRowsAreGoneFromTheShippedCatalogue:
    """Half the fix is a removal, and a removal needs its own assertion."""

    @pytest.mark.parametrize(
        "module",
        ["gin.Context", "gin.Engine", "echo.Context", "echo.Echo",
         "fiber.App", "grpc.Server"],
    )
    def test_third_party_framework_module_is_not_shipped(
        self, module: str,
    ) -> None:
        shipped = {p.module for p in load_catalog("go").primitives}
        assert module not in shipped, (
            f"{module} is third-party and must not ship in go.yaml "
            "(ADR-0016 §27/§35 — it belongs in a project-local overlay)"
        )

    def test_the_shipped_catalogue_is_still_non_trivial(self) -> None:
        """NON-VACUITY FLOOR. Every assertion above passes if ``load_catalog``
        returns nothing at all, and a catalogue that failed to load is exactly
        the shape that would make a removal test look green."""
        prims = load_catalog("go").primitives
        assert len(prims) > 150, len(prims)
        assert any(p.module == "net/http" for p in prims)


class TestTheOverlayResolvesWhatTheShippedRowsCouldNot:
    """The spelling IS the fix, so the spelling is what gets asserted."""

    def test_the_overlay_exists_and_declares_itself_an_overlay(self) -> None:
        path = _overlay_path()
        assert path.exists(), f"overlay missing at {path}"
        cat = load_overlay_catalog(path)
        assert cat.primitives, "overlay loaded but is empty"

    def test_it_is_not_beside_the_shipped_catalogues(self) -> None:
        """It lives under docs/ because hypergumbo does not own these rows."""
        from hypergumbo_core import io_boundary

        catalog_dir = Path(io_boundary.__file__).parent / "io_primitives"
        assert not (catalog_dir / "go-web-frameworks.yaml").exists()

    @pytest.mark.parametrize(
        ("hint", "method", "qualified", "boundary"), _CONTRACT,
    )
    def test_lookup_by_the_real_import_path(
        self, hint: str, method: str, qualified: str, boundary: str,
    ) -> None:
        cat = load_overlay_catalog(_overlay_path())
        hit = cat.lookup_with_module(method, hint, call_construct="method")
        assert hit is not None, (
            f"{method} did not resolve from hint {hint!r} — this is the exact "
            "failure the shipped rows had, reproduced in the overlay"
        )
        assert hit.qualified_name == qualified
        assert hit.boundary == boundary

    @pytest.mark.parametrize(
        ("short_hint", "method"),
        [("gin", "JSON"), ("echo", "JSON"), ("fiber", "Listen"),
         ("grpc", "Serve")],
    )
    def test_the_old_short_hint_no_longer_resolves(
        self, short_hint: str, method: str,
    ) -> None:
        """THE NEGATIVE HALF, and it is not redundant with the positive one.

        A module string of ``github.com/gin-gonic/gin.Context`` could in
        principle still match a bare ``gin`` hint through a permissive
        component rule, which would mean the overlay had merely ADDED a spelling
        rather than CORRECTED one — and the fabricated-import probe would keep
        reporting these rows as reachable. Pinning the old hint to ``None``
        keeps the two spellings from quietly coexisting.
        """
        cat = load_overlay_catalog(_overlay_path())
        assert cat.lookup_with_module(
            method, short_hint, call_construct="method",
        ) is None

    def test_version_suffixes_are_carried_verbatim(self) -> None:
        """Echo v4 must NOT resolve from an unversioned echo path.

        Go's semantic import versioning makes the suffix part of the path, so a
        v4-declared row answering to ``github.com/labstack/echo`` would be
        answering about a different module.
        """
        cat = load_overlay_catalog(_overlay_path())
        assert cat.lookup_with_module(
            "JSON", "github.com/labstack/echo", call_construct="method",
        ) is None
