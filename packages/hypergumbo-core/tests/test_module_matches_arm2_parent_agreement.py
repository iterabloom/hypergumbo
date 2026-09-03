# SPDX-License-Identifier: AGPL-3.0-or-later
"""Arm 2 of ``_module_matches`` decides by PARENT AGREEMENT, not by case value.

THE DEFECT (WI-zozun). When one module path is a strict component-prefix of
the other, the first extra component is either a TYPE inside the matched
module (``os/exec`` + ``Cmd`` -> match) or a SIBLING module that merely shares
the prefix (``net/http`` + ``fcgi`` -> no match). Arm 2 decided that with
``extra[:1].isupper()`` -- Go's "capitals mean types" convention -- in a
predicate that takes no language and serves fifteen catalogues. Where module
names are themselves capitalised that test is CONSTANT-TRUE and therefore
information-free: haskell 195/195 catalogue rows, swift 106/107, objc 126/131,
elixir 250/529.

MEASURED, not argued (WI-zozun, 2026-09-02): across eight repos in exactly
those languages arm 2 fired nine times and named a type ZERO times. Every
firing was a sub-module. Two were confirmed false positives read back against
source -- livebook's ``IO.ANSI.format`` builds chardata and performs no I/O,
tagged ``logging`` via the ``io`` row; ``Req.Request.merge_options`` is pure
struct manipulation, tagged ``net_send`` via ``Req.new``.

THE RULE. A component that names a TYPE is spelled differently from the module
it sits in; a component that names a SIBLING MODULE is spelled the same way.
So the extra component decides by whether its case DISAGREES with the
component it FOLLOWS within the longer path. For a lowercase parent (go, rust,
java, python, kotlin) this is identical to the old test, so every documented
match survives. For a capitalised parent it always rejects, which is the
information the old test threw away.

WHAT IT COSTS, and why the catalogue absorbs it rather than the predicate. The
haskell firings were BENIGN: ``Data.ByteString.Lazy.readFile`` really is a file
read, because the sibling is a parallel API with identical I/O semantics. This
rule rejects them, so those siblings are given their OWN catalogue rows and
arm 1 (exact) carries them. That is the only route to removed=0 in haskell:
``Data.ByteString + Lazy`` and ``Req + Response`` are orthographically
identical, and the difference between them -- does the sibling re-export the
same I/O surface? -- is semantic. No spelling rule can see it.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import (
    _extra_component_names_a_type,
    _module_matches,
)


class TestTheHelperIsTheOneHome:
    """The rule lives in one function so an instrument can patch exactly it."""

    @pytest.mark.parametrize(
        ("parent", "extra"),
        [("exec", "Cmd"), ("fs", "File"), ("io", "FileInputStream"),
         ("http", "Client")],
    )
    def test_a_capitalised_child_of_a_lowercase_parent_is_a_type(
        self, parent: str, extra: str,
    ) -> None:
        assert _extra_component_names_a_type(parent, extra)

    @pytest.mark.parametrize(
        ("parent", "extra"),
        [("http", "fcgi"), ("http", "httptest"), ("net", "smtp")],
    )
    def test_a_lowercase_child_of_a_lowercase_parent_is_a_sibling(
        self, parent: str, extra: str,
    ) -> None:
        assert not _extra_component_names_a_type(parent, extra)

    @pytest.mark.parametrize(
        ("parent", "extra"),
        [("ByteString", "Lazy"), ("ByteString", "Char8"), ("IO", "ANSI"),
         ("Req", "Response"), ("Task", "Supervisor"),
         ("Directory", "Extra"), ("Process", "Text")],
    )
    def test_a_capitalised_child_of_a_capitalised_parent_is_a_sibling(
        self, parent: str, extra: str,
    ) -> None:
        """THE CHANGE. Under the old test every one of these was a type."""
        assert not _extra_component_names_a_type(parent, extra)

    def test_an_empty_component_is_not_a_type(self) -> None:
        """A trailing separator yields an empty component; never a type."""
        assert not _extra_component_names_a_type("exec", "")


class TestTheNineMeasuredFirings:
    """Every (catalogue, hint) pair arm 2 was measured deciding, 2026-09-02.

    All nine are sub-modules and all nine now reject. The haskell ones are
    benign and are re-admitted THROUGH THE CATALOGUE (their own rows, matched
    by arm 1) -- asserted separately below -- not through this predicate.
    """

    @pytest.mark.parametrize(
        ("catalog", "hint", "repo"),
        [
            ("Data.ByteString", "Data.ByteString.Lazy", "postgrest, stack"),
            ("Data.ByteString", "Data.ByteString.Char8", "hls, stack"),
            ("Data.ByteString", "Data.ByteString.Lazy.Char8",
             "hls, servant, stack"),
            ("Data.ByteString", "Data.ByteString.Internal", "hls"),
            ("System.Directory", "System.Directory.Extra", "hls"),
            ("System.Process", "System.Process.Text", "hls"),
            ("Req", "Req.Response", "livebook"),
            ("Task", "Task.Supervisor", "livebook"),
            ("io", "IO.ANSI", "livebook"),
        ],
    )
    def test_a_capitalised_sibling_no_longer_matches_its_parent(
        self, catalog: str, hint: str, repo: str,
    ) -> None:
        assert _module_matches(catalog, hint) is False, (
            f"{catalog!r} still matches {hint!r} (fired on {repo})"
        )

    def test_the_two_confirmed_false_positives_by_name(self) -> None:
        """The pair the item's ruling was made on, read back against source."""
        # livebook lib/livebook/runtime/evaluator/formatter.ex:238
        assert _module_matches("io", "IO.ANSI") is False
        # livebook lib/livebook/teams/requests.ex:344
        assert _module_matches("Req", "Req.Response") is False


class TestEveryDocumentedTypeMatchSurvives:
    """Non-vacuity floor (L17): the fix must not be 'reject arm 2'.

    These are the docstring's own canonical cases plus the rows
    ``test_legitimate_matches_survive`` already pins. A lowercase parent
    followed by a capitalised child is a type under BOTH rules.
    """

    @pytest.mark.parametrize(
        ("catalog", "hint"),
        [
            ("os/exec", "os.exec.Cmd"),
            ("std::fs", "std::fs::File"),
            ("java.io", "java.io.FileInputStream"),
            ("net/http", "net/http.Client"),
            ("java.io.FileInputStream", "java.io"),   # edge is the prefix
        ],
    )
    def test_type_inside_a_lowercase_module_still_matches(
        self, catalog: str, hint: str,
    ) -> None:
        assert _module_matches(catalog, hint) is True

    @pytest.mark.parametrize(
        ("catalog", "hint"),
        [
            ("net/http", "net/http/fcgi"),
            ("net/http", "net/http/httptest"),
        ],
    )
    def test_lowercase_sibling_still_rejects(
        self, catalog: str, hint: str,
    ) -> None:
        assert _module_matches(catalog, hint) is False


class TestTheHaskellSiblingsAreCarriedByTheCatalogue:
    """The benign firings re-enter through arm 1, which is exact.

    This is the removed=0 half of the change. If a row here is missing the
    predicate change has silently deleted a true boundary in postgrest, stack,
    hls or servant.
    """

    def test_bytestring_sibling_modules_have_their_own_rows(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        cat = load_catalog("haskell")
        assert cat is not None
        mods = {p.module for p in cat.primitives}
        for sibling in ("Data.ByteString.Lazy", "Data.ByteString.Char8",
                        "Data.ByteString.Lazy.Char8"):
            assert sibling in mods, f"{sibling} has no catalogue row"

    @pytest.mark.parametrize(
        ("name", "module", "boundary"),
        [
            # the measured losses, one per shape
            ("readFile", "Data.ByteString.Lazy", "fs_read"),        # stack x4
            ("writeFile", "Data.ByteString.Lazy", "fs_write"),      # stack, hls
            ("readFile", "Data.ByteString.Char8", "fs_read"),       # stack, hls
            ("writeFile", "Data.ByteString.Lazy.Char8", "fs_write"),  # servant
        ],
    )
    def test_the_measured_true_boundary_is_reached_by_its_own_row(
        self, name: str, module: str, boundary: str,
    ) -> None:
        """Matched EXACTLY (arm 1), so arm 2's verdict is never consulted."""
        from hypergumbo_core.io_boundary import load_catalog
        cat = load_catalog("haskell")
        assert cat is not None
        hit = cat.lookup_with_module(name, module)
        assert hit is not None, f"{module}.{name} unreachable"
        assert hit.module == module
        assert hit.boundary == boundary

    @pytest.mark.parametrize(
        ("name", "module", "boundary"),
        [
            ("doesFileExist", "System.Directory.Extra", "fs_read"),
            ("setCurrentDirectory", "System.Directory.Extra", "fs_write"),
            ("readCreateProcessWithExitCode", "System.Process.Text",
             "subprocess"),
        ],
    )
    def test_the_hackage_siblings_are_carried_by_the_default_overlay(
        self, name: str, module: str, boundary: str,
    ) -> None:
        """hls's 8 lost boundaries; Hackage packages, so an overlay row."""
        from hypergumbo_core.io_boundary import load_catalog
        cat = load_catalog("haskell")
        assert cat is not None
        hit = cat.lookup_with_module(name, module)
        assert hit is not None, f"{module}.{name} unreachable"
        assert hit.module == module
        assert hit.boundary in (boundary, "ipc_recv")
