# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalogue tells the user how much of itself is unverified (INV-nular).

WHAT THE ITEM SAYS AND WHY A COUNT CLOSES IT. A catalogue row asserts "this
NAME crosses THIS BOUNDARY", and nothing checks that the named primitive
performs that boundary operation -- ``newIORef`` was declared ``db_read``,
``socket`` was declared ``net_recv``, ``Request`` was declared ``net_send``.
Seven families of that shape were swept and fixed (F1 in-process cells, F2
setup/handles, F3 query builders, F4 lifecycle callbacks, F5 responses,
F6/F7 path adjacency), plus the named instances. The REMAINDER was never
swept, and the owner ruled (2026-09-06) that it is disclosed rather than
read: a sweep of ~2,300 names is not what the item is worth.

THE NUMBER IS COMPUTED AT RUN TIME, AND THAT IS THE POINT. The ruling was
made against "622 entries / 2,312 names", and a check one day later returned
2,312 names -- exactly -- with 773 entries. Counting ``(boundary, module)``
groups through the production loader gives a third answer, 563. Three
defensible definitions of "entry", three numbers, one day apart, on a
catalogue that did not move. NAMES is the unit that survived, and a literal
in a docstring would have shipped one of the other two. So the disclosure
derives its count from the catalogues the run actually loaded, and this file
pins that it is derived rather than restated.

WHY COMMUNITY-OVERLAY ROWS ARE COUNTED SEPARATELY. ADR-0047 ruling 6 already
established a THIRD provenance state: rows hypergumbo ships and does not
vouch for. 426 of them carry ``notes``, 91% -- so folding them into "carries a
written rationale" would credit, as evidence of adjudication, prose attached
to rows the tool explicitly declines to stand behind. They are reported as
their own line and are unverified by construction, which is the same fact
ADR-0047 states, counted.

WHAT THIS FILE DOES NOT ASSERT. That ``notes`` means "adjudicated". It does
not, and nothing records adjudication per row; the two sets differ and the
rendered text says so in its own words. Claiming the second while computing
the first is a wrong instrument returning a plausible number, which is the
failure mode this item exists to describe.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import (
    _CATALOG_DIR,
    kind_assertion_census,
    load_catalog,
)
from hypergumbo_core.verify_claims import (
    catalog_provenance,
    render_catalog_provenance_text,
)


ALL_LANGUAGES = sorted(p.stem for p in _CATALOG_DIR.glob("*.yaml"))


class TestTheCensusCountsWhatTheRunLoaded:
    def test_a_language_with_no_catalogue_contributes_nothing(self) -> None:
        """An unsupported language must not inflate the denominator.

        ``load_catalog`` returns a populated-looking fallback object for a
        language it has no file for (``is_supported=False``), so a census that
        trusted the object rather than the flag would count zero rows while
        reporting the language as covered.
        """
        census = kind_assertion_census(["fortran"])
        assert census["names"] == 0
        assert census["names_with_rationale"] == 0
        assert census["community_overlay_names"] == 0
        assert census["languages"] == []

    def test_a_real_language_is_non_vacuous(self) -> None:
        """A census over an empty population would make every claim below
        pass trivially -- the failure mode measurement 0006 was built to
        catch."""
        census = kind_assertion_census(["python"])
        assert census["names"] > 100
        assert 0 < census["names_with_rationale"] < census["names"]
        assert census["languages"] == ["python"]

    def test_the_count_is_derived_from_the_catalogue_not_restated(self) -> None:
        """THE ANTI-DRIFT TEST. The census must equal what the loader holds.

        This is the check that would have caught the 622-vs-773 drift: it
        compares the reported number against the live object rather than
        against a number written down when the disclosure was designed.
        """
        catalog = load_catalog("python", include_defaults=False)
        expected = len({
            (p.module, p.name, p.boundary, p.kind) for p in catalog.primitives
        })
        assert kind_assertion_census(["python"])["names"] == expected

    def test_community_overlay_names_are_counted_apart(self) -> None:
        """ADR-0047's third state, counted. Python ships a default overlay, so
        the two arms must differ and the overlay names must land in their own
        key rather than in either shipped total."""
        with_defaults = kind_assertion_census(["python"])
        without = kind_assertion_census(["python"], include_default_overlays=False)
        assert with_defaults["community_overlay_names"] > 0
        assert without["community_overlay_names"] == 0
        # The shipped totals are the SAME either way: an overlay never adds to
        # them. That is the property that keeps "written rationale" from
        # crediting a row hypergumbo does not vouch for.
        assert with_defaults["names"] == without["names"]
        assert (with_defaults["names_with_rationale"]
                == without["names_with_rationale"])

    def test_the_community_bucket_reads_the_stamp_not_a_re_derivation(
        self,
    ) -> None:
        """ADR-0047 stamps ``unvouched`` on every community row at merge, so
        the census reads it rather than diffing two loads of the catalogue.

        The synthetic row proves the flag is what decides: it is not in any
        catalogue file, so a set-difference against the base would also put it
        in the community bucket -- but only the flag can put a row there that
        the base ALSO defines, which is the case the subtraction below exists
        for."""
        from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive

        prim = IoPrimitive(boundary="net_send", module="thirdparty",
                           name="post", kind="function", notes="argued for",
                           unvouched=True)
        catalog = IoBoundaryCatalog(language="python", primitives=[prim])
        import hypergumbo_core.io_boundary as io_boundary_mod

        real = io_boundary_mod.load_catalog
        try:
            io_boundary_mod.load_catalog = lambda *a, **k: catalog
            census = kind_assertion_census(["python"])
        finally:
            io_boundary_mod.load_catalog = real
        assert census["community_overlay_names"] == 1
        # ...and its ``notes`` do NOT count as a written rationale, which is
        # the whole reason the two tiers are separate: prose on a row nobody
        # vouches for is not evidence the kind was argued.
        assert census["names"] == 0
        assert census["names_with_rationale"] == 0

    def test_two_languages_do_not_double_count_a_shared_row(self) -> None:
        """scala inherits java's catalogue. The same assertion reached through
        two languages is ONE assertion, and summing per-language totals would
        report it twice."""
        java = kind_assertion_census(["java"])["names"]
        scala = kind_assertion_census(["scala"])["names"]
        both = kind_assertion_census(["java", "scala"])["names"]
        assert both < java + scala

    def test_every_shipped_language_is_censusable(self) -> None:
        """No catalogue raises or silently returns nothing -- the census runs
        over whatever the user's repository happens to contain."""
        census = kind_assertion_census(ALL_LANGUAGES)
        assert census["languages"] == ALL_LANGUAGES
        assert census["names"] > 2000


class TestTheDisclosureReachesBothSurfaces:
    """INV-karud (a3): a disclosure that exists only under ``--json`` is half
    shipped."""

    def _prov(self, langs: list[str]) -> dict:
        return catalog_provenance({}, (), catalog_languages=langs)

    def test_the_json_envelope_carries_the_census(self) -> None:
        prov = self._prov(["python"])
        assert prov["kind_adjudication"]["names"] > 0

    def test_the_text_renderer_states_the_unverified_remainder(self) -> None:
        text = "\n".join(render_catalog_provenance_text(self._prov(["python"])))
        assert "UNVERIFIED" in text
        census = kind_assertion_census(["python"])
        remainder = census["names"] - census["names_with_rationale"]
        assert str(remainder) in text
        assert str(census["names"]) in text

    def test_the_text_refuses_to_equate_a_rationale_with_adjudication(
        self,
    ) -> None:
        """The caveat is load-bearing, not decoration: ``notes`` is what the
        catalogue records and is NOT the set the F1-F7 sweep adjudicated, which
        is recorded nowhere per row."""
        text = "\n".join(render_catalog_provenance_text(self._prov(["python"])))
        assert "not the same set" in text

    def test_the_text_names_all_three_enforced_guards(self) -> None:
        """Three guards, three reaches, and the text names all three.

        A 2026-09-07 premise check for this re-scope concluded there was NO
        direction check, having searched for the phrase "direction guard" and
        for a ``scripts/check-*``. There is one, and it is neither: it is
        ``test_inv_nular_false_sources.py::test_the_direction_sweep_is_otherwise_clean``.
        The search failed the way its own subject fails -- a name-based lookup
        over an incomplete vocabulary does not error, it returns clean -- which
        is the same mechanism that let ``responseLBS`` past the sweep because
        the vocabulary held ``respond`` and not ``response``.

        So the text says direction IS checked, AND says the reach is partial,
        because "every row's direction is checked" is the overstatement the
        ruling made and a user acting on it would over-trust the catalogue."""
        text = "\n".join(render_catalog_provenance_text(self._prov(["python"])))
        assert "DIRECTION contradicts its boundary fails CI" in text
        assert "only for names that state a direction at all" in text
        assert "declares WHY" in text
        assert "both directions is refused at load" in text

    def test_the_community_line_is_absent_when_no_overlay_loaded(self) -> None:
        """``--no-default-overlays`` suppresses the community LAYER, not the
        shipped catalogue. The block must still render its shipped counts and
        simply drop the community sentence -- reporting "0 more from community
        overlays" would describe a layer that was never loaded as one that was
        loaded and found empty."""
        prov = catalog_provenance(
            {}, (), catalog_languages=["python"],
            include_default_overlays=False,
        )
        text = "\n".join(render_catalog_provenance_text(prov))
        assert "community overlays" not in text
        assert "UNVERIFIED" in text
        assert prov["kind_adjudication"]["community_overlay_names"] == 0
        # The shipped half is byte-identical to the arm that DID load them.
        with_overlays = catalog_provenance({}, (), catalog_languages=["python"])
        assert (prov["kind_adjudication"]["names"]
                == with_overlays["kind_adjudication"]["names"])

    def test_a_run_touching_no_catalogued_language_renders_nothing(self) -> None:
        """A census with no rows behind it has nothing to disclose, and a
        block reading '0 of 0 unverified' would be noise."""
        prov = catalog_provenance({}, (), catalog_languages=["fortran"])
        assert prov["kind_adjudication"]["names"] == 0
        assert render_catalog_provenance_text(prov) == []

    def test_the_key_is_present_even_when_the_caller_passes_no_languages(
        self,
    ) -> None:
        """Stable envelope shape, the convention ``dataflow_coverage`` and
        ``load_bearing_grants`` already follow: a consumer never reads a
        missing key as zero."""
        prov = catalog_provenance({}, ())
        assert prov["kind_adjudication"]["names"] == 0
        assert prov["kind_adjudication"]["languages"] == []


class TestTheCliPassesTheRunsOwnLanguages:
    """The one thing the tests above cannot see: WHAT the CLI hands the
    census.

    The failure this guards is specific and easy to reintroduce. The argument
    beside it is ``() if no_default_overlays else languages`` -- correct for
    the community-overlay block, because that flag really does decide whether
    those rows loaded. Copying that expression onto the census would be wrong
    in a way no output inspects: ``--no-default-overlays`` suppresses the
    COMMUNITY layer only, the run still rests on every shipped row, and the
    disclosure would silently report zero assertions for a verdict resting on
    hundreds. Zero rows renders NOTHING, so the block would simply vanish and
    the run would look like one with no catalogue behind it.
    """

    def test_the_census_languages_are_not_gated_on_the_overlay_flag(
        self,
    ) -> None:
        import inspect

        from hypergumbo_core import cli

        source = inspect.getsource(cli.cmd_verify_claims)
        assert "catalog_languages=languages" in source, (
            "the CLI must pass the run's languages to the census "
            "unconditionally; gating them on --no-default-overlays would "
            "report zero assertions for a verdict resting on the shipped "
            "catalogue"
        )
        assert (
            "include_default_overlays=not getattr(args, "
            "\"no_default_overlays\", False)"
        ) in source, (
            "the flag must still reach the census, or the community line "
            "would describe a layer that was never loaded"
        )


class TestTheEnforcedGatesAreLiveAndNonVacuous:
    """The statement this item is re-scoped ONTO names two mechanisms. A
    statement is satisfied on its own terms, so both are asserted here rather
    than taken from the prose that describes them."""

    def test_no_shipped_multi_boundary_primitive_lacks_a_reason(self) -> None:
        """The CI gate, restated at this item's own address so the re-scoped
        statement has a control that fails if the gate is removed."""
        import collections

        from hypergumbo_core.io_boundary import multi_boundary_reason

        missing = []
        seen = collections.Counter()
        for lang in ALL_LANGUAGES:
            catalog = load_catalog(lang)
            by_name: dict[str, set[str]] = collections.defaultdict(set)
            for prim in catalog.primitives:
                by_name[prim.qualified_name].add(prim.boundary)
            for qualified, boundaries in by_name.items():
                if len(boundaries) > 1:
                    reason = multi_boundary_reason(catalog, qualified)
                    seen[reason] += 1
                    if reason is None:
                        missing.append(f"{lang}:{qualified}")
        assert not missing, missing
        assert sum(seen.values()) > 25, "population too small to be a check"

    def test_the_direction_sweep_catches_a_planted_mismatch(self) -> None:
        """POSITIVE CONTROL for the guard the re-scoped statement leans on
        hardest, asserted here because a statement is satisfied ON ITS OWN
        TERMS and this one names the sweep by mechanism.

        The plant is a send declared as a receive -- family F5's shape, the
        one whose failure direction is REPORTS SAFE."""
        from test_inv_nular_false_sources import (  # type: ignore[import-not-found]
            DIRECTION_RECVY,
            DIRECTION_SENDY,
        )

        assert DIRECTION_SENDY.search("sendPayload")
        assert not DIRECTION_RECVY.search("sendPayload")

    def test_the_direction_sweep_does_not_examine_a_direction_silent_name(
        self,
    ) -> None:
        """THE CONTROL'S CONTROL, and the reason this item closes with a known
        defective row still shipped.

        ``urllib.request.Request`` is a constructor declared ``net_send``,
        adjudicated wrong on 2026-08-27 and still present. It survived every
        sweep because the sweep is name-based and "Request" states no
        direction, so it was never examined. Pinning that here keeps the
        limit from being quietly re-read as coverage."""
        from test_inv_nular_false_sources import (  # type: ignore[import-not-found]
            DIRECTION_RECVY,
            DIRECTION_SENDY,
        )

        for silent in ("Request", "NewRequest", "new"):
            assert not DIRECTION_SENDY.search(silent), silent
            assert not DIRECTION_RECVY.search(silent), silent

    def test_a_primitive_gated_in_both_directions_is_refused_at_load(
        self,
    ) -> None:
        """THE POSITIVE CONTROL for the closest real thing to the 'direction
        guard' the 09-06 ruling described. It is not per-row and it is not
        every row: it fires only when ONE primitive is stream-gated in both
        directions, which is the case no single ``io_target_kind`` stamp can
        resolve."""
        from hypergumbo_core.io_boundary import (
            IoPrimitive,
            _target_kind_gated_directions,
        )

        planted = [
            IoPrimitive(boundary=b, module="m", name="f", kind="function")
            for b in ("fs_read", "ipc_recv", "fs_write", "ipc_send")
        ]
        with pytest.raises(ValueError, match="both the read and the write"):
            _target_kind_gated_directions(planted)

    def test_the_refusal_does_not_fire_on_the_mode_shape(self) -> None:
        """The control's control. ``builtins.open`` is fs_read + fs_write --
        one boundary per direction -- and must pass, or the guard above would
        be refusing the 14 legitimate keys the premise check enumerated."""
        from hypergumbo_core.io_boundary import (
            IoPrimitive,
            _target_kind_gated_directions,
        )

        planted = [
            IoPrimitive(boundary=b, module="builtins", name="open",
                        kind="function")
            for b in ("fs_read", "fs_write")
        ]
        assert _target_kind_gated_directions(planted) == {}
