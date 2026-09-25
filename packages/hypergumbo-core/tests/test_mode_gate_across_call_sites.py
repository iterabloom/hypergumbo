# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vukiv, consumer half: the mode gate must see EVERY collapsed site.

Preserving the per-site modes on the edge (``io_mode_values``) fixes the map's
honesty but changes no verdict on its own, because ``resolve_mode_boundary``
reads the singular ``io_mode`` — and once the sites disagree that key is gone,
which resolves to ``fs_read`` (the documented default-on-absence) and deletes
the write exactly as before. The gate has to ask the plural question.

DIRECTION, and it is the reverse of the one ``resolve_mode_boundary`` argues
for absence. That docstring is right that guessing ``fs_write`` from IGNORANCE
would re-create the false-positive population the mode gate removed: an absent
or computed mode licenses nothing. Sites that disagree are not ignorance —
``open(p, 'w')`` was read off the source at one of them. With positive evidence
that a write happens at some collapsed site, ``fs_write`` is evidence-backed,
and choosing ``fs_read`` would delete a write the analyzer actually saw. So:
ANY site that writes makes the collapsed relationship a write.
"""

from hypergumbo_core.io_boundary import (
    IoPrimitive,
    call_site_modes,
    resolve_mode_boundary,
    resolve_mode_boundary_across_sites,
    select_by_mode,
)


def _pair():
    return [
        IoPrimitive(boundary="fs_read", module="builtins", name="open",
                    kind="function"),
        IoPrimitive(boundary="fs_write", module="builtins", name="open",
                    kind="function"),
    ]


# ---------------------------------------------------------------------------
# Reading the modes off an edge
# ---------------------------------------------------------------------------


def test_a_single_site_edge_reports_its_one_mode():
    assert call_site_modes({"io_mode": "w"}) == ("w",)


def test_a_collapsed_edge_reports_every_site_mode():
    assert call_site_modes({"io_mode_values": ["r", "w"]}) == ("r", "w")


def test_an_edge_with_no_mode_reports_nothing():
    assert call_site_modes({}) == ()
    assert call_site_modes(None) == ()


def test_a_non_string_mode_value_is_ignored_not_crashed():
    """``meta`` is deserialized from an artifact that may have been edited."""
    assert call_site_modes({"io_mode_values": ["w", 7, None]}) == ("w",)


def test_a_non_list_values_key_falls_back_to_the_singular():
    assert call_site_modes({"io_mode_values": "w", "io_mode": "a"}) == ("a",)


# ---------------------------------------------------------------------------
# Resolving across sites
# ---------------------------------------------------------------------------


def test_no_modes_keeps_the_documented_default_on_absence():
    assert resolve_mode_boundary_across_sites(()) == resolve_mode_boundary(None)
    assert resolve_mode_boundary_across_sites(None) == "fs_read"


def test_one_reading_site_resolves_to_read():
    assert resolve_mode_boundary_across_sites(("r",)) == "fs_read"


def test_one_writing_site_resolves_to_write():
    assert resolve_mode_boundary_across_sites(("w",)) == "fs_write"


def test_a_write_among_reads_makes_the_relationship_a_write():
    """The truncate_logs measurement: 'r' first must not delete the 'w'."""
    assert resolve_mode_boundary_across_sites(("r", "w")) == "fs_write"
    assert resolve_mode_boundary_across_sites(("w", "r")) == "fs_write"


def test_update_mode_still_counts_as_a_write():
    assert resolve_mode_boundary_across_sites(("r", "r+")) == "fs_write"


# ---------------------------------------------------------------------------
# The shared selector both the tagger and the sink matcher consume
# ---------------------------------------------------------------------------


def test_select_by_mode_picks_the_write_row_when_any_site_writes():
    chosen = select_by_mode(_pair(), ("r", "w"))
    assert chosen is not None and chosen.boundary == "fs_write"


def test_select_by_mode_picks_the_read_row_when_no_site_writes():
    chosen = select_by_mode(_pair(), ("r",))
    assert chosen is not None and chosen.boundary == "fs_read"


def test_select_by_mode_leaves_a_lone_candidate_alone():
    only = [_pair()[0]]
    assert select_by_mode(only, ("w",)) is only[0]


def test_select_by_mode_still_returns_none_for_an_empty_candidate_set():
    assert select_by_mode([], ("w",)) is None


def test_select_by_mode_falls_back_when_no_row_matches_the_wanted_boundary():
    """A bucket with neither fs boundary must still yield a candidate."""
    others = [
        IoPrimitive(boundary="net_send", module="socket", name="send",
                    kind="function"),
        IoPrimitive(boundary="net_recv", module="socket", name="send",
                    kind="function"),
    ]
    assert select_by_mode(others, ("w",)) is others[0]


# ---------------------------------------------------------------------------
# Spanning both fs boundaries — deliberately narrow
# ---------------------------------------------------------------------------


def test_sites_that_span_both_boundaries_report_both():
    from hypergumbo_core.io_boundary import load_catalog, mode_spanned_boundaries
    catalog = load_catalog("python")
    match = catalog.lookup_with_module("open", "builtins", io_modes=("w",))
    assert match is not None
    assert mode_spanned_boundaries(catalog, match, ("r", "w")) == frozenset(
        {"fs_read", "fs_write"}
    )


def test_sites_that_agree_on_a_boundary_span_nothing():
    """Two writing modes are two sites, not two boundaries."""
    from hypergumbo_core.io_boundary import load_catalog, mode_spanned_boundaries
    catalog = load_catalog("python")
    match = catalog.lookup_with_module("open", "builtins", io_modes=("w",))
    assert match is not None
    assert mode_spanned_boundaries(catalog, match, ("w", "a")) == frozenset()


def test_a_primitive_no_mode_discriminates_spans_nothing():
    """``unistd.read`` is fs_read, ipc_recv AND net_recv; no mode settles it."""
    from hypergumbo_core.io_boundary import load_catalog, mode_spanned_boundaries
    catalog = load_catalog("c")
    match = IoPrimitive(boundary="net_recv", module="unistd", name="read",
                        kind="function")
    assert mode_spanned_boundaries(catalog, match, ("r", "w")) == frozenset()
