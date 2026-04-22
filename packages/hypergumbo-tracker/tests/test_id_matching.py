# SPDX-License-Identifier: MPL-2.0
"""Tests for :mod:`hypergumbo_tracker.id_matching`.

Covers the regex builder (kind enumeration, prefix escaping, word-boundary
handling) and the scanner (match order, skip-range filtering, CVCVC rules).
"""

from __future__ import annotations

import re

import pytest

from hypergumbo_tracker.id_matching import (
    ItemIdMatch,
    build_item_id_pattern,
    find_item_ids,
)
from hypergumbo_tracker.models import KindConfig, TrackerConfig


def _cfg(prefixes: list[str]) -> TrackerConfig:
    """Build a minimal TrackerConfig with the given kind prefixes."""
    kinds = {
        p.lower(): KindConfig(prefix=p) for p in prefixes
    }
    return TrackerConfig(
        kinds=kinds,
        statuses=["todo_soft"],
        blocking_statuses=["todo_soft"],
        resolved_statuses=["done"],
    )


class TestBuildItemIdPattern:
    def test_matches_default_kinds(self):
        cfg = _cfg(["WI", "INV", "META"])
        pat = build_item_id_pattern(cfg)
        assert pat.search("see WI-lusab-baril-muziv-tinok-baroh-sobij-sakub-maful here")
        assert pat.search("see INV-lusab-baril here")
        assert pat.search("see META-lusab-baril here")

    def test_rejects_unconfigured_prefix(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        assert not pat.search("INV-lusab-baril")

    def test_matches_custom_prefix(self):
        cfg = _cfg(["CUSTOM"])
        pat = build_item_id_pattern(cfg)
        assert pat.search("CUSTOM-lusab-baril")

    def test_raises_on_empty_kinds(self):
        cfg = TrackerConfig(
            kinds={},
            statuses=[],
            blocking_statuses=[],
            resolved_statuses=[],
        )
        with pytest.raises(ValueError):
            build_item_id_pattern(cfg)

    def test_prefix_escaping(self):
        cfg = _cfg(["A.B"])  # Dot must be escaped in regex.
        pat = build_item_id_pattern(cfg)
        # Literal "A.B" matches.
        assert pat.search("A.B-lusab-baril")
        # The unescaped dot interpretation (where "." matched any char)
        # would also match "AXB" — verify it does not.
        assert not pat.search("AXB-lusab-baril")

    def test_rejects_short_id_with_only_one_syllable(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        assert not pat.search("WI-lusab")  # one syllable block only

    def test_accepts_two_syllable_minimum(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        assert pat.search("WI-lusab-baril")

    def test_accepts_full_eight_syllable_id(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        full = "WI-lusab-baril-muziv-tinok-baroh-sobij-sakub-maful"
        m = pat.search(f"pre {full} post")
        assert m is not None
        assert m.group(0) == full

    def test_rejects_non_proquint_syllable(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        # "aaaaa" is all vowels — not CVCVC.
        assert not pat.search("WI-aaaaa-baril")

    def test_rejects_word_attached_at_front(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        # Leading alphanumeric character invalidates the prefix anchor.
        assert not pat.search("aWI-lusab-baril")
        # A 'X' in "XWI" likewise — the prefix must be free-standing.
        assert not pat.search("XWI-lusab-baril")

    def test_rejects_word_attached_at_tail(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        # Trailing alphanumeric after last syllable rejects the match.
        assert not pat.search("WI-lusab-barilX")

    def test_allows_hyphen_boundary(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        # Hyphens on either side are part of natural prose; the ID
        # should still be extracted cleanly.
        text = "see-WI-lusab-baril-now"
        m = pat.search(text)
        assert m is not None
        # The trailing "-now" is not a valid syllable (3 chars), so the
        # match stops at "-baril".
        assert m.group(0) == "WI-lusab-baril"


class TestFindItemIds:
    def test_returns_matches_in_document_order(self):
        cfg = _cfg(["WI", "INV"])
        pat = build_item_id_pattern(cfg)
        text = "first WI-lusab-baril then INV-nomiv-fatar end"
        results = find_item_ids(text, pat)
        assert [r.item_id for r in results] == [
            "WI-lusab-baril",
            "INV-nomiv-fatar",
        ]
        # Offsets are valid slice indices.
        for r in results:
            assert text[r.start:r.end] == r.item_id

    def test_returns_empty_list_when_no_matches(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        assert find_item_ids("no IDs here, just prose.", pat) == []

    def test_skip_range_filters_interior_match(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        text = "first WI-lusab-baril then WI-nomiv-fatar"
        # Skip the whole text region spanning the first match.
        skipped = find_item_ids(text, pat, skip_ranges=[(0, 20)])
        assert [r.item_id for r in skipped] == ["WI-nomiv-fatar"]

    def test_skip_range_filters_partial_overlap(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        text = "WI-lusab-baril"
        # Any intersection drops the match.
        assert find_item_ids(text, pat, skip_ranges=[(5, 7)]) == []

    def test_skip_range_non_overlapping_keeps_match(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        text = "WI-lusab-baril trailing text"
        # Skip range entirely after the match.
        kept = find_item_ids(text, pat, skip_ranges=[(20, 28)])
        assert len(kept) == 1
        assert kept[0].item_id == "WI-lusab-baril"

    def test_match_objects_are_nametuples(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        r = find_item_ids("WI-lusab-baril", pat)[0]
        assert isinstance(r, ItemIdMatch)
        assert r.item_id == "WI-lusab-baril"
        assert r.start == 0
        assert r.end == len("WI-lusab-baril")

    def test_empty_text_yields_empty_list(self):
        cfg = _cfg(["WI"])
        pat = build_item_id_pattern(cfg)
        assert find_item_ids("", pat) == []

    def test_accepts_compiled_pattern_directly(self):
        # Consumer can compose with other patterns; no implicit
        # dependence on TrackerConfig at scan time.
        manual = re.compile(r"WI-\w+")
        results = find_item_ids("WI-foo", manual)
        assert len(results) == 1
        assert results[0].item_id == "WI-foo"
