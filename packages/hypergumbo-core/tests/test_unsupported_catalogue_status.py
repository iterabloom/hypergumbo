# SPDX-License-Identifier: AGPL-3.0-or-later
"""A catalogue that does not exist does not get to say its provenance is
declared (WI-gofah).

WHAT THE ITEM ASKED FOR, AND WHY THAT IS NOT WHAT SHIPPED. WI-gofah proposed
flipping ``load_catalog``'s missing-file default from ``provenance_declared`` to
``in_progress``, and listed the consequence it expected: "the flip would newly
mark every catalogue-less language's ``external_potential`` chains unreliable".
Measured at tip, that consequence cannot occur and the flip would make the
output WORSE:

  1. THERE ARE NO SUCH CHAINS. ``_external_potential_chains`` skips any source
     language whose catalogue is ``not is_supported`` (io_boundary.py:2309), and
     ``cmd_io_boundaries`` never even puts a catalogue-less language into the
     ``catalogs`` dict (cli.py:5173). A catalogue-less language produces no
     chains of any kind, so there is nothing for the flag to mark.

  2. THE USER ALREADY GETS THE HONEST DISCLOSURE, and it is better worded than
     the one the flip would add. Run on a fixture whose only languages are
     catalogue-less::

         $ hypergumbo io-boundaries <lua+ruby fixture>
         Note: no I/O primitive catalog for language(s): lua, ruby. Zero
         boundaries reported for these languages does NOT mean the code is
         I/O-free — it means hypergumbo cannot detect I/O for this language
         yet. (INV-javam)

     Flipping the status would ALSO make ``in_progress_languages`` warn
     "io_primitives catalog for 'lua' is in_progress", which is a second
     disclosure saying something false: there is no catalogue, partial or
     otherwise.

WHAT IS ACTUALLY WRONG, AND IS FIXED HERE. The field still LIES to anyone who
reads it. ``status`` is a claim about a catalogue's provenance, and on the
missing-file fallback it answers "provenance_declared" — for a catalogue that
does not exist. Nothing reads it that way today only because both readers ask
``== "in_progress"``, which is a property of today's two call sites and not a
property of the field. The next consumer to ask ``status == "provenance_declared"``
gets a citation-backed answer for a language nobody catalogued, and it fails
OPEN.

So the fallback now carries its own value, ``"unsupported"``. That is
behaviour-preserving by construction -- both existing readers test for
``"in_progress"`` and neither changes -- and it removes the trap rather than
relocating it.
"""

import pytest

from hypergumbo_core.io_boundary import (
    CATALOG_STATUS_UNSUPPORTED,
    IoBoundaryCatalog,
    in_progress_languages,
    load_catalog,
)


class TestTheFallbackDoesNotClaimProvenance:
    def test_a_missing_catalogue_reports_unsupported(self) -> None:
        cat = load_catalog("cobol")
        assert cat.is_supported is False
        assert cat.status == CATALOG_STATUS_UNSUPPORTED

    def test_a_real_catalogue_is_unaffected(self) -> None:
        """Controls in the same class, so "everything is unsupported now"
        cannot pass."""
        assert load_catalog("python").status == "provenance_declared"
        assert load_catalog("go").status == "in_progress"
        assert load_catalog("python").is_supported is True

    def test_an_alias_and_a_parent_still_resolve_first(self) -> None:
        """The missing-file path is rarer than it looks, which is part of why
        the item's expected consequence never materialised: ``typescript``
        resolves to javascript's catalogue and ``kotlin`` inherits java's."""
        assert load_catalog("typescript").is_supported is True
        assert load_catalog("kotlin").is_supported is True
        assert load_catalog("kotlin").status != CATALOG_STATUS_UNSUPPORTED


class TestNeitherReaderChangesBehaviour:
    """The two call sites that read ``status``, asserted directly rather than
    inferred, because "behaviour-preserving" is the whole claim."""

    def test_in_progress_disclosure_does_not_fire_for_an_uncatalogued_language(
        self,
    ) -> None:
        assert in_progress_languages(["cobol", "lua", "ruby"]) == []

    def test_in_progress_disclosure_still_fires_for_a_partial_catalogue(
        self,
    ) -> None:
        assert in_progress_languages(["go", "python", "cobol"]) == ["go"]

    @pytest.mark.parametrize("status,expected", [
        ("in_progress", True),
        ("provenance_declared", False),
        (CATALOG_STATUS_UNSUPPORTED, False),
    ])
    def test_the_unreliable_flag_reads_only_in_progress(
        self, status: str, expected: bool,
    ) -> None:
        """``dst_classification_unreliable=(catalog.status == "in_progress")``
        — pinned as an equality against the vocabulary so a future edit that
        widens it to "anything but provenance_declared" has to notice the third
        value exists."""
        cat = IoBoundaryCatalog(language="x", primitives=[], status=status)
        assert (cat.status == "in_progress") is expected


class TestTheValueCannotBeDECLAREDByAFile:
    """``unsupported`` describes the ABSENCE of a catalogue, so a catalogue
    file claiming it would be a contradiction. The YAML validator's existing
    two-value check already refuses it; asserted here so the third value's
    scope is explicit rather than incidental."""

    def test_a_yaml_may_not_declare_unsupported(self, tmp_path) -> None:
        bad = tmp_path / "x.yaml"
        bad.write_text("language: x\nstatus: unsupported\n")
        with pytest.raises(ValueError, match="unsupported"):
            IoBoundaryCatalog.from_yaml(bad)
