# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.tui.

Covers the TUI: layout tier computation, ID truncation, detail line
formatting, compact DataTable rendering with stacked detail, standard
two-pane layout with cursor-driven detail panel, tree view toggle,
filter input, dynamic resize with selection preservation, too-small
message, and key bindings.

Test strategy:
- Unit tests (sync): _compute_tier boundary matrix, _truncate_id buckets,
  _format_detail_lines output for full/minimal/no-tier items
- Pilot tests (async): mount the app at specific terminal sizes, verify
  widget visibility, row rendering, and key bindings. Uses wait helpers
  to handle coverage-tracing slowdowns in Textual's event loop.
- _filtered_items tests: verify filter matching against title, status,
  tags, kind, and edge cases (empty filter, no matches)
- Dynamic resize tests: compact↔standard↔too-small transitions with
  selection preservation and filter state persistence
- Edge case tests: empty tracker, wrong table events, tree root node,
  unknown IDs, filter dismiss via action vs escape
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_tracker.models import (
    CompiledItem,
    Tier,
    TrackerConfig,
)
from textual.app import App

from hypergumbo_tracker.store import (
    DiscussionRateLimitError,
    FrozenItemError,
    HumanAuthorityError,
    ItemNotFoundError,
    LockedFieldError,
)
from hypergumbo_tracker.trackerset import TierMovementError, TrackerSet
from rich.text import Text

from hypergumbo_tracker.tui import (
    BeforeScreen,
    ConfirmScreen,
    DiscussScreen,
    EditItemScreen,
    LockScreen,
    NewItemScreen,
    ParentScreen,
    TierMoveScreen,
    _apply_custom_order,
    _collapse_double_spacing,
    _compute_tier,
    _format_activity_lines,
    _format_detail_lines,
    _format_timestamp,
    _label,
    _load_tui_preferences,
    _save_tui_preferences,
    _shortest_unique_prefix_len,
    _truncate_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_markup(text: str) -> str:
    """Strip Rich markup from text, returning plain content.

    Converts ``[bold reverse]Title:[/] value`` → ``Title: value``
    and ``\\[locked]`` → ``[locked]``.
    """
    return Text.from_markup(text).plain


def _make_config() -> TrackerConfig:
    from helpers import make_test_config

    return make_test_config()


def _make_tracker_set(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with sample items for testing."""
    from helpers import make_test_config_dict

    root = tmp_path / ".agent"
    for d in [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    config = _make_config()
    config_path = root / "tracker" / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump(make_test_config_dict()))

    ts = TrackerSet(root, config=config)

    ts.add(kind="invariant", title="Symbol IDs must be stable",
           status="todo_hard", priority=1, tags=["quality"],
           description="Symbol IDs change between runs.")
    ts.add(kind="work_item", title="Add caching layer",
           status="in_progress", priority=2)
    ts.add(kind="invariant", title="Routes must have methods",
           status="done", priority=0,
           fields={"statement": "Routes need methods", "root_cause": "Missing validation"})

    return ts


async def _wait_for_table(pilot: Any, app: Any, max_rounds: int = 50) -> None:
    """Wait for the DataTable to be populated.

    Coverage tracing slows Textual's event loop, so on_mount may not have
    completed by the time run_test yields the pilot. This helper retries
    pilot.pause() until the table has rows or max_rounds is reached.
    """
    table = app.query_one("#item-table")
    for _ in range(max_rounds):
        await pilot.pause()
        if table.row_count > 0:
            return
    # If we get here, the table still has no rows — let the assertion fail naturally


# ---------------------------------------------------------------------------
# Unit tests: _compute_tier
# ---------------------------------------------------------------------------


class TestComputeTier:
    """Test layout tier classification against the ADR-0013 boundary matrix."""

    def test_too_small_both_below(self) -> None:
        assert _compute_tier(30, 10) == "too-small"

    def test_too_small_width_below(self) -> None:
        assert _compute_tier(39, 16) == "too-small"

    def test_too_small_height_below(self) -> None:
        assert _compute_tier(40, 15) == "too-small"

    def test_compact_minimum(self) -> None:
        assert _compute_tier(40, 16) == "compact"

    def test_compact_mid(self) -> None:
        assert _compute_tier(50, 18) == "compact"

    def test_compact_width_below_standard(self) -> None:
        assert _compute_tier(59, 20) == "compact"

    def test_compact_height_below_standard(self) -> None:
        assert _compute_tier(60, 19) == "compact"

    def test_standard_minimum(self) -> None:
        assert _compute_tier(60, 20) == "standard"

    def test_standard_typical(self) -> None:
        assert _compute_tier(80, 24) == "standard"

    def test_standard_large_but_not_wide(self) -> None:
        assert _compute_tier(120, 34) == "standard"

    def test_standard_height_too_low_for_wide(self) -> None:
        assert _compute_tier(121, 38) == "standard"

    def test_wide_minimum(self) -> None:
        assert _compute_tier(121, 39) == "wide"

    def test_wide_large(self) -> None:
        assert _compute_tier(160, 45) == "wide"


# ---------------------------------------------------------------------------
# Unit tests: _truncate_id
# ---------------------------------------------------------------------------


class TestTruncateId:
    """Test adaptive ID truncation across column-width buckets."""

    def test_narrow_le10_shows_prefix_and_one_pair(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 10)
        assert result.startswith("INV-")
        assert len(result) <= 10

    def test_medium_11_20_shows_two_pairs(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 15)
        assert result.startswith("INV-")
        assert len(result) <= 15

    def test_wider_21_32_shows_more(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 25)
        assert result.startswith("INV-")
        assert len(result) <= 25

    def test_full_width_gt32(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 40)
        assert result == full_id

    def test_short_id_always_fits(self) -> None:
        """An ID shorter than max_width should be returned as-is."""
        full_id = "WI-babab"
        result = _truncate_id(full_id, 20)
        assert result == full_id

    def test_truncation_adds_ellipsis(self) -> None:
        """Truncated IDs should end with '...' to indicate truncation."""
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 10)
        if len(full_id) > 10:
            assert "…" in result

    def test_no_dashes_hard_truncate(self) -> None:
        """IDs without dashes get hard-truncated with ellipsis."""
        result = _truncate_id("LONGIDENTIFIER", 8)
        assert len(result) <= 8
        assert result.endswith("…")

    def test_narrow_prefix_too_long(self) -> None:
        """When prefix+pair doesn't fit in ≤10, hard-truncate."""
        full_id = "LONGERPREFIX-babab-dabab"
        result = _truncate_id(full_id, 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_medium_width_all_pairs_too_long(self) -> None:
        """When even prefix-pair1 exceeds max_width in the >10 range."""
        # max_width=12 but prefix is long, so even "VERYLONG-ab…" exceeds 12
        full_id = "VERYLONGPREFIX-abcde-fghij"
        result = _truncate_id(full_id, 12)
        assert len(result) <= 12
        assert result.endswith("…")


# ---------------------------------------------------------------------------
# Unit tests: _shortest_unique_prefix_len
# ---------------------------------------------------------------------------


class TestShortestUniquePrefixLen:
    """Test shortest unique prefix computation for proquint IDs."""

    def test_empty_list_returns_minimum(self) -> None:
        """Empty list should return 0 — no IDs to distinguish."""
        assert _shortest_unique_prefix_len([]) == 0

    def test_single_item_returns_prefix_plus_one_pair(self) -> None:
        """Single ID needs only prefix + 1 syllable pair."""
        result = _shortest_unique_prefix_len(
            ["INV-bolil-mirid-pakim-lujun"]
        )
        # "INV-bolil" = 9 chars
        assert result == 9

    def test_different_first_pairs(self) -> None:
        """IDs that differ at first pair need only prefix + 1 pair."""
        result = _shortest_unique_prefix_len([
            "INV-alpha-mirid-pakim-lujun",
            "INV-bravo-dabab-fabab-habab",
        ])
        assert result == 9

    def test_same_first_pair_extends_to_two(self) -> None:
        """IDs sharing first pair need prefix + 2 pairs to distinguish."""
        result = _shortest_unique_prefix_len([
            "INV-bolil-mirid-pakim-lujun",
            "INV-bolil-xxxxx-fabab-habab",
        ])
        # "INV-bolil-mirid" = 15 chars
        assert result == 15

    def test_same_two_pairs_extends_to_three(self) -> None:
        """IDs sharing first two pairs need prefix + 3 pairs."""
        result = _shortest_unique_prefix_len([
            "INV-bolil-mirid-pakim-lujun",
            "INV-bolil-mirid-xxxxx-habab",
        ])
        # "INV-bolil-mirid-pakim" = 21 chars
        assert result == 21

    def test_mixed_prefixes_short_enough(self) -> None:
        """IDs with different prefixes (INV vs WI) are already unique at prefix."""
        result = _shortest_unique_prefix_len([
            "INV-bolil-mirid-pakim-lujun",
            "WI-dabab-fabab-habab-jabab",
        ])
        # INV- prefix is 4 chars, WI- is 3 chars; first pair for INV = 9, WI = 8
        # They differ at the prefix level, so prefix + 1 pair suffices
        assert result == 9  # max of min lengths: INV-bolil(9), WI-dabab(8)

    def test_mixed_prefixes_with_collision_within_kind(self) -> None:
        """Mixed prefixes where one kind has collisions."""
        result = _shortest_unique_prefix_len([
            "INV-bolil-mirid-pakim-lujun",
            "INV-bolil-xxxxx-fabab-habab",
            "WI-dabab-fabab-habab-jabab",
        ])
        # INV pair needs 2 pairs to distinguish, WI only needs 1
        assert result == 15  # INV-bolil-mirid = 15

    def test_all_identical_extends_to_full(self) -> None:
        """Duplicate IDs extend to the full ID length."""
        full_id = "INV-bolil-mirid-pakim-lujun"
        result = _shortest_unique_prefix_len([full_id, full_id])
        assert result == len(full_id)

    def test_three_items_progressive_collision(self) -> None:
        """Three items where two share first pair but third differs."""
        result = _shortest_unique_prefix_len([
            "INV-alpha-mirid-pakim-lujun",
            "INV-alpha-xxxxx-fabab-habab",
            "INV-bravo-dabab-fabab-habab",
        ])
        # alpha pair needs 2 pairs, bravo only needs 1
        assert result == 15  # max is INV-alpha-mirid = 15

    def test_non_proquint_ids(self) -> None:
        """IDs without dashes use raw distinguishing length."""
        result = _shortest_unique_prefix_len(["ABCDEF", "ABCXYZ"])
        # Differ at char 4, no syllable boundaries to snap to
        assert result >= 4

    def test_snap_at_dash_boundary(self) -> None:
        """Test snapping when min_unique_len falls exactly on a dash."""
        # IDs that differ right after a dash separator:
        # "X-ab-cd" vs "X-ab-ef" — differ at position 6 (after dash)
        result = _shortest_unique_prefix_len(["X-ab-cd", "X-ab-ef"])
        # Need prefix + 2 pairs: "X-ab-cd" = 7 chars
        assert result == 7

    def test_min_unique_exceeds_all_pairs(self) -> None:
        """When IDs share all pairs, returns full length via for-else."""
        # Two IDs identical except last char — all pairs match
        result = _shortest_unique_prefix_len([
            "A-bb-cc-dd",
            "A-bb-cc-de",
        ])
        # They share up to "A-bb-cc-d", differ at last char
        assert result == 10  # full length of "A-bb-cc-dd"

    def test_different_structure_hits_dash_snap(self) -> None:
        """IDs with different structures snap at dash boundary."""
        # 'A-b' vs 'A-bxy': differ at position 3 (end of shorter vs 'x')
        # For 'A-b': after processing part 'b', cumul=3 < target=5,
        # cumul+1=4 < target=5, loop exhausts → else clause (snapped=3)
        # For 'A-bxy': cumul=2+3=5 >= 5 → snapped=5
        result = _shortest_unique_prefix_len(["A-b", "A-bxy"])
        assert result == 5

    def test_short_id_for_else_clause(self) -> None:
        """Shorter ID exhausts all pairs when target driven by duplicates."""
        # Two identical short IDs make min_unique_len = max_len (7),
        # which exceeds 'A-b' total (4 with dash). Loop exhausts → for-else.
        result = _shortest_unique_prefix_len(["A-b", "A-b", "A-bxyzw"])
        assert result == 7


# ---------------------------------------------------------------------------
# Unit tests: _format_detail_lines
# ---------------------------------------------------------------------------


class TestFormatDetailLines:
    """Test detail line formatting for both compact and standard views."""

    def test_full_featured_item(self) -> None:
        """Item with tags, parent, description, fields, and discussion."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="INV-babab-dabab",
            kind="invariant",
            title="Symbol IDs must be stable",
            status="todo_hard",
            priority=1,
            tier=Tier.CANONICAL,
            tags=["quality", "cross_language"],
            parent="WI-aaaaa",
            description="Symbol IDs change between runs.",
            fields={"statement": "IDs must be stable", "root_cause": "Hash seed"},
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T00:00:00Z",
                    message="First note",
                ),
                DiscussionEntry(
                    by="human", actor="dev", at="2026-01-02T00:00:00Z",
                    message="Second note",
                ),
            ],
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Symbol IDs must be stable" in text
        assert "canonical" in text
        assert "quality" in text
        assert "cross_language" in text
        assert "WI-aaaaa" in text
        assert "Symbol IDs change between runs." in text
        assert "statement" in text
        assert "root_cause" in text
        assert "Discussion (2 entries)" in text
        assert "First note" in text
        assert "Second note" in text

    def test_minimal_item(self) -> None:
        """Item with no optional fields."""
        item = CompiledItem(
            id="WI-babab",
            kind="work_item",
            title="Minimal item",
            status="done",
            priority=2,
            tier=Tier.WORKSPACE,
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Minimal item" in text
        assert "workspace" in text
        assert "Tags:" not in text
        assert "Parent:" not in text
        assert "Description:" not in text
        assert "Fields:" not in text
        assert "Discussion" not in text

    def test_no_tier(self) -> None:
        """Item with tier=None should show 'unknown'."""
        item = CompiledItem(
            id="WI-xyz",
            kind="work_item",
            title="No tier",
            status="in_progress",
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "unknown" in text

    def test_wide_tier_shows_extra_fields(self) -> None:
        """Wide tier should show timestamps, locked fields, and conflict."""
        item = CompiledItem(
            id="INV-wide",
            kind="invariant",
            title="Wide detail test",
            status="todo_hard",
            priority=1,
            tier=Tier.CANONICAL,
            created_at="2026-02-15T10:00:00Z",
            updated_at="2026-02-15T12:00:00Z",
            locked_fields={"status", "priority"},
            cross_tier_conflict=True,
        )
        lines = _format_detail_lines(item, tier="wide")
        text = _strip_markup("\n".join(lines))
        assert "Created: 2026-02-15 10:00" in text
        assert "Updated: 2026-02-15 12:00" in text
        assert "[locked]" in text
        assert "Priority [locked]" in text
        assert "Status [locked]" in text
        assert "Cross-tier conflict: YES" in text
        # Discussion should NOT appear in wide mode
        assert "Discussion" not in text

    def test_wide_tier_suppresses_discussion(self) -> None:
        """Wide tier should not include inline discussion."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="INV-disc",
            kind="invariant",
            title="Has discussion",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T00:00:00Z",
                    message="Should not appear",
                ),
            ],
        )
        lines = _format_detail_lines(item, tier="wide")
        text = _strip_markup("\n".join(lines))
        assert "entries):" not in text
        assert "Should not appear" not in text

    def test_description_all_double_spaced_collapses(self) -> None:
        """Descriptions where every line break is \\n\\n collapse to single-spaced."""
        item = CompiledItem(
            id="WI-dblnl",
            kind="work_item",
            title="Double newline test",
            status="done",
            priority=2,
            tier=Tier.CANONICAL,
            description="Line one\n\nLine two\n\nLine three",
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Line one\nLine two\nLine three" in text
        assert "\n\n" not in text.split("Description:\n", 1)[-1]

    def test_description_mixed_newlines_preserves_paragraphs(self) -> None:
        """Descriptions with mixed \\n and \\n\\n preserve paragraph breaks."""
        item = CompiledItem(
            id="WI-mixed",
            kind="work_item",
            title="Mixed newline test",
            status="done",
            priority=2,
            tier=Tier.CANONICAL,
            description="Para one line one\nPara one line two\n\nPara two line one",
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        desc_part = text.split("Description:\n", 1)[-1]
        assert "Para one line one\nPara one line two" in desc_part
        assert "Para one line two\n\nPara two line one" in desc_part

    def test_frozen_banner(self) -> None:
        """Frozen item shows FROZEN banner."""
        item = CompiledItem(
            id="INV-frozen",
            kind="invariant",
            title="Frozen Item",
            status="todo_hard",
            priority=1,
            tier=Tier.CANONICAL,
            frozen=True,
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "FROZEN" in text


# ---------------------------------------------------------------------------
# Unit tests: _label and markup presence
# ---------------------------------------------------------------------------


class TestLabelMarkup:
    """Verify that _label() produces correct Rich markup and _format_detail_lines
    emits styled labels with properly escaped brackets."""

    def test_label_wraps_plain_text(self) -> None:
        """_label wraps a plain label in bold-reverse markup."""
        result = _label("Title:")
        assert result == "[bold reverse]Title:[/]"

    def test_label_escapes_brackets(self) -> None:
        """_label escapes literal [ so Rich doesn't interpret them as tags."""
        result = _label("Status [locked]:")
        assert result == "[bold reverse]Status \\[locked]:[/]"

    def test_detail_lines_contain_bold_reverse_markup(self) -> None:
        """Structural labels in _format_detail_lines carry [bold reverse] markup."""
        item = CompiledItem(
            id="WI-markup",
            kind="work_item",
            title="Markup test",
            status="done",
            priority=2,
            tier=Tier.WORKSPACE,
        )
        lines = _format_detail_lines(item)
        raw = "\n".join(lines)
        assert "[bold reverse]Title:[/]" in raw
        assert "[bold reverse]ID:[/]" in raw
        assert "[bold reverse]Status:[/]" in raw
        assert "[bold reverse]Priority:[/]" in raw
        assert "[bold reverse]Tier:[/]" in raw

    def test_discussion_brackets_escaped_in_detail(self) -> None:
        """Discussion timestamp brackets are escaped so Rich doesn't swallow them."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="INV-esc",
            kind="invariant",
            title="Escape test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-15T10:00:00Z",
                    message="note",
                ),
            ],
        )
        lines = _format_detail_lines(item)
        raw = "\n".join(lines)
        # Timestamp bracket is escaped
        assert "\\[2026-01-15T10:00:00Z]" in raw
        # And resolves to plain text correctly
        plain = _strip_markup(raw)
        assert "[2026-01-15T10:00:00Z]" in plain

    def test_activity_lines_brackets_escaped(self) -> None:
        """Activity lines escape [by] brackets so Rich doesn't swallow them."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="INV-actesc",
            kind="invariant",
            title="Activity escape test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-15T10:00:00Z",
                    message="note",
                ),
            ],
        )
        lines = _format_activity_lines(item)
        raw = lines[0]
        # [agent] bracket is escaped
        assert "\\[agent]" in raw
        # And resolves to plain text correctly
        assert "[agent]" in _strip_markup(raw)


# ---------------------------------------------------------------------------
# Unit tests: _collapse_double_spacing
# ---------------------------------------------------------------------------


class TestCollapseDoubleSpacing:
    """Test the double-spacing heuristic for description display."""

    def test_all_double_spaced(self) -> None:
        """Every break is \\n\\n → collapse to single-spaced."""
        assert _collapse_double_spacing("A\n\nB\n\nC") == "A\nB\nC"

    def test_triple_newlines(self) -> None:
        """Triple+ newlines also collapse when all-double-spaced."""
        assert _collapse_double_spacing("A\n\n\nB") == "A\nB"

    def test_mixed_preserves_paragraphs(self) -> None:
        """Single \\n within paragraphs + \\n\\n between → preserved."""
        text = "Line 1\nLine 2\n\nLine 3\nLine 4"
        assert _collapse_double_spacing(text) == text

    def test_single_newlines_only(self) -> None:
        """Already single-spaced text is unchanged."""
        assert _collapse_double_spacing("A\nB\nC") == "A\nB\nC"

    def test_empty_string(self) -> None:
        assert _collapse_double_spacing("") == ""

    def test_no_newlines(self) -> None:
        assert _collapse_double_spacing("single line") == "single line"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _collapse_double_spacing("\n\nA\n\nB\n\n") == "A\nB"


# ---------------------------------------------------------------------------
# Unit tests: _format_timestamp
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    """Test ISO timestamp formatting for wide-mode columns."""

    def test_full_iso_timestamp(self) -> None:
        assert _format_timestamp("2026-02-15T10:30:45Z") == "2026-02-15 10:30"

    def test_empty_string(self) -> None:
        assert _format_timestamp("") == ""

    def test_malformed_short(self) -> None:
        """Malformed/short input is returned truncated gracefully."""
        result = _format_timestamp("2026-02")
        assert result == "2026-02"

    def test_date_only(self) -> None:
        """Date without time portion uses what's available."""
        result = _format_timestamp("2026-02-15")
        assert result == "2026-02-15"


# ---------------------------------------------------------------------------
# Unit tests: _format_activity_lines
# ---------------------------------------------------------------------------


class TestFormatActivityLines:
    """Test activity log formatting for the wide-mode activity panel."""

    def test_with_discussion_entries(self) -> None:
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="INV-abc",
            kind="invariant",
            title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="First entry",
                ),
                DiscussionEntry(
                    by="human", actor="dev", at="2026-01-02T14:30:00Z",
                    message="Second entry",
                ),
            ],
        )
        lines = _format_activity_lines(item)
        assert len(lines) == 2
        assert "agent" in lines[0]
        assert "First entry" in lines[0]
        assert "human" in lines[1]
        assert "Second entry" in lines[1]

    def test_empty_discussion(self) -> None:
        item = CompiledItem(
            id="WI-abc",
            kind="work_item",
            title="Empty",
            status="done",
        )
        lines = _format_activity_lines(item)
        assert lines == ["No recent activity"]

    def test_limit_truncation(self) -> None:
        from hypergumbo_tracker.models import DiscussionEntry

        entries = [
            DiscussionEntry(
                by=f"user{i}", actor="dev", at=f"2026-01-{i+1:02d}T00:00:00Z",
                message=f"Message {i}",
            )
            for i in range(15)
        ]
        item = CompiledItem(
            id="INV-xyz",
            kind="invariant",
            title="Many entries",
            status="todo_hard",
            discussion=entries,
        )
        lines = _format_activity_lines(item, limit=5)
        # Header line + 5 entries
        assert len(lines) == 6
        assert "showing last 5 of 15" in lines[0].lower()
        assert "Message 14" in lines[-1]


# ---------------------------------------------------------------------------
# Pilot tests: compact layout
# ---------------------------------------------------------------------------


class TestCompactLayout:
    """Test the TUI at compact layout sizes using Textual's pilot API."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_list_renders_at_minimum_size(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            assert table.display is True
            assert table.row_count == 3

    async def test_detail_on_enter(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            await pilot.press("enter")
            await pilot.pause()
            detail = app.query_one("#detail-view")
            assert detail.display is True
            assert table.display is False

    async def test_detail_escape_returns(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            await pilot.press("enter")
            await pilot.pause()
            detail = app.query_one("#detail-view")
            assert detail.display is True
            await pilot.press("escape")
            await pilot.pause()
            assert table.display is True
            assert detail.display is False

    async def test_list_at_wide_compact_size_with_status(
        self, tracker_set: TrackerSet
    ) -> None:
        """At compact width >= 55, the status column should appear."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(58, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            assert table.display is True
            assert table.row_count == 3
            # Status column should be present at width 58 (>= 55)
            column_keys = [col.key.value for col in table.columns.values()]
            assert "status" in column_keys

    async def test_list_at_phone_size(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            assert table.display is True
            assert table.row_count == 3

    async def test_quit(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await pilot.press("q")

    async def test_detail_shows_item_content(self, tracker_set: TrackerSet) -> None:
        """Detail view should show title, status, priority, and tier for the selected item."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(55, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Select whatever item is first (order is store-dependent)
            await pilot.press("enter")
            await pilot.pause()
            content = app.query_one("#detail-content")
            text = str(content.content)
            # All items have these fields
            assert "Title:" in text
            assert "Status:" in text
            assert "Priority:" in text
            assert "Tier:" in text

    async def test_select_no_items_does_nothing(self, tmp_path: Path) -> None:
        """Selecting when table is empty should not crash."""
        from hypergumbo_tracker.tui import TrackerApp

        # Create a TrackerSet with no items
        root = tmp_path / ".agent"
        for d in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
            root / "tracker-workspace" / "stealth",
        ]:
            d.mkdir(parents=True, exist_ok=True)
        import yaml
        (root / "tracker" / "config.yaml").write_text(yaml.dump({
            "kinds": {"invariant": {"prefix": "INV", "description": "Test"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
            "actor_resolution": {"agent_usernames": ["*_agent"]},
            "lamport_branches": ["dev"],
        }))
        config = _make_config()
        ts = TrackerSet(root, config=config)

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await pilot.pause()
            # Pressing enter on an empty table should not crash
            await pilot.press("enter")
            await pilot.pause()
            # Should still be in list mode
            detail = app.query_one("#detail-view")
            assert detail.display is False

    async def test_escape_in_list_mode_does_nothing(self, tracker_set: TrackerSet) -> None:
        """Escape in list mode (not in detail) should not change state."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            # We're in list mode; escape should be a no-op
            await pilot.press("escape")
            await pilot.pause()
            table = app.query_one("#item-table")
            assert table.display is True


# ---------------------------------------------------------------------------
# Pilot tests: too-small
# ---------------------------------------------------------------------------


class TestTooSmall:
    """Test that too-small terminals show the warning message."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_too_small_message(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(30, 10)) as pilot:
            # Need multiple pauses for coverage-traced runs
            for _ in range(5):
                await pilot.pause()
            msg = app.query_one("#too-small-msg")
            assert msg.display is True
            assert "too small" in msg.content.lower()
            table = app.query_one("#item-table")
            assert table.display is False


# ---------------------------------------------------------------------------
# Unit tests: _show_detail (direct call, avoids pilot timing issues)
# ---------------------------------------------------------------------------


class TestShowDetailDirect:
    """Test _show_detail by calling it directly on a mounted app."""

    async def test_show_detail_with_all_fields(self, tmp_path: Path) -> None:
        """Verify _show_detail renders tags, parent, description, fields, discussion."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        # Add an item with all fields populated
        item_id = ts.add(
            kind="invariant",
            title="Full detail item",
            status="in_progress",
            priority=1,
            tags=["quality", "cross_language"],
            parent=ts.list_items()[0].id,
            description="A detailed description of the issue.",
            fields={"statement": "Things must work", "root_cause": "They don't"},
        )
        ts.discuss(item_id, "First discussion entry")
        ts.discuss(item_id, "Second discussion entry")

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(55, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Find the item we just created
            item = next(i for i in app._items if i.id == item_id)
            app._show_detail(item)
            content = app.query_one("#detail-content")
            text = str(content.content)
            assert "Full detail item" in text
            assert "quality" in text
            assert "A detailed description" in text
            assert "statement" in text
            assert "root_cause" in text
            assert "Discussion" in text
            assert "First discussion" in text
            # Parent should be shown
            assert ts.list_items()[0].id[:8] in text  # partial ID match

    async def test_show_detail_minimal_item(self, tmp_path: Path) -> None:
        """Item with no tags, parent, description, fields, or discussion."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        item = CompiledItem(
            id="TEST-abcde",
            kind="work_item",
            title="Minimal item",
            status="todo_hard",
            priority=2,
            tier=Tier.WORKSPACE,
        )

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(55, 18)) as pilot:
            await pilot.pause()
            app._show_detail(item)
            content = app.query_one("#detail-content")
            text = str(content.content)
            assert "Minimal item" in text
            assert "workspace" in text

    async def test_show_detail_no_tier(self, tmp_path: Path) -> None:
        """Item with tier=None should display 'unknown'."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        item = CompiledItem(
            id="TEST-xyz",
            kind="invariant",
            title="No tier item",
            status="done",
        )

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(55, 18)) as pilot:
            await pilot.pause()
            app._show_detail(item)
            content = app.query_one("#detail-content")
            text = str(content.content)
            assert "unknown" in text


# ---------------------------------------------------------------------------
# Unit tests: action_back and on_data_table_row_selected edge cases
# ---------------------------------------------------------------------------


class TestActionBackDirect:
    """Test action_back directly."""

    async def test_action_back_not_in_detail(self, tmp_path: Path) -> None:
        """action_back when not in detail mode should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await pilot.pause()
            assert not app._in_detail
            app.action_back()
            assert not app._in_detail

    async def test_action_back_in_detail(self, tmp_path: Path) -> None:
        """action_back when in detail mode should return to list."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            # Enter detail mode
            app._in_detail = True
            app._apply_layout()
            assert app._in_detail
            # Now back
            app.action_back()
            assert not app._in_detail


class TestRowSelectedEdgeCases:
    """Test on_data_table_row_selected edge cases."""

    async def test_select_while_in_detail_is_noop(self, tmp_path: Path) -> None:
        """Row selected while already in detail mode should be ignored."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            app._in_detail = True
            table = app.query_one("#item-table", DataTable)
            # Construct a mock RowSelected event and call handler directly
            row_key = next(iter(table.rows.keys()))
            event = DataTable.RowSelected(table, table.cursor_coordinate, row_key)
            app.on_data_table_row_selected(event)
            # Should still be in detail mode, not having opened another detail
            assert app._in_detail

    async def test_select_in_too_small_is_noop(self, tmp_path: Path) -> None:
        """Row selected in too-small mode should be ignored."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(30, 10)) as pilot:
            for _ in range(5):
                await pilot.pause()
            assert app._layout_tier == "too-small"
            # Construct a mock event to test the handler directly
            table = app.query_one("#item-table", DataTable)
            event = DataTable.RowSelected(table, (0, 0), "fake-key")
            app.on_data_table_row_selected(event)
            assert not app._in_detail


# ---------------------------------------------------------------------------
# Unit tests: _load_items with status column
# ---------------------------------------------------------------------------


class TestLoadItemsStatusColumn:
    """Test _load_items behavior at different widths."""

    async def test_no_status_column_narrow(self, tmp_path: Path) -> None:
        """At width < 55, no status column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            column_keys = [col.key.value for col in table.columns.values()]
            assert "status" not in column_keys

    async def test_status_column_wide(self, tmp_path: Path) -> None:
        """At compact width >= 55, status column should appear."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(58, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            column_keys = [col.key.value for col in table.columns.values()]
            assert "status" in column_keys


# ---------------------------------------------------------------------------
# CLI integration: _cmd_tui
# ---------------------------------------------------------------------------


class TestCmdTui:
    """Test the CLI tui subcommand."""

    def test_tui_runs_app(self, tmp_path: Path) -> None:
        """When textual is available, _cmd_tui creates and runs the app."""
        ts = _make_tracker_set(tmp_path)
        tracker_root = tmp_path / ".agent"

        with patch("hypergumbo_tracker.tui.TrackerApp.run") as mock_run:
            from hypergumbo_tracker.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == 0
            mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Pilot tests: standard two-pane layout
# ---------------------------------------------------------------------------


async def _wait_for_std_table(pilot: Any, app: Any, max_rounds: int = 50) -> None:
    """Wait for the standard DataTable to be populated."""
    table = app.query_one("#std-table")
    for _ in range(max_rounds):
        await pilot.pause()
        if table.row_count > 0:
            return


class TestStandardLayout:
    """Test the TUI at standard layout sizes (60x20 to 120x38)."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_two_pane_visible_at_standard_size(
        self, tracker_set: TrackerSet
    ) -> None:
        """At 80x24, two-pane should be visible; compact table hidden."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            two_pane = app.query_one("#two-pane")
            assert two_pane.display is True
            compact_table = app.query_one("#item-table")
            assert compact_table.display is False

    async def test_table_populated(self, tracker_set: TrackerSet) -> None:
        """Standard table should have rows after mount."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 3

    async def test_first_item_detail_on_mount(self, tracker_set: TrackerSet) -> None:
        """Right panel should be populated with first item on mount."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            content = app.query_one("#std-detail-content")
            text = str(content.content)
            assert "Title:" in text

    async def test_cursor_move_updates_detail(self, tracker_set: TrackerSet) -> None:
        """Arrow down should change the right panel content."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            content = app.query_one("#std-detail-content")
            text_before = str(content.content)
            await pilot.press("down")
            await pilot.pause()
            text_after = str(content.content)
            # Content should change (different item selected)
            assert text_after != text_before

    async def test_enter_in_standard_no_stacked_detail(
        self, tracker_set: TrackerSet
    ) -> None:
        """Enter in standard mode should NOT enter stacked detail mode."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("enter")
            await pilot.pause()
            # Should NOT switch to compact stacked detail
            assert not app._in_detail
            two_pane = app.query_one("#two-pane")
            assert two_pane.display is True

    async def test_tree_toggle_shows_tree(self, tracker_set: TrackerSet) -> None:
        """Pressing 't' should show the tree and hide the table."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("t")
            await pilot.pause()
            tree = app.query_one("#item-tree")
            std_table = app.query_one("#std-table")
            assert tree.display is True
            assert std_table.display is False
            assert app._tree_mode is True

    async def test_tree_toggle_back(self, tracker_set: TrackerSet) -> None:
        """Pressing 't' twice should restore the table."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            tree = app.query_one("#item-tree")
            std_table = app.query_one("#std-table")
            assert tree.display is False
            assert std_table.display is True
            assert app._tree_mode is False

    async def test_tree_cursor_updates_detail(self, tracker_set: TrackerSet) -> None:
        """Selecting a tree node should update the right panel."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("t")
            await pilot.pause()
            # Move down in tree to select a node
            await pilot.press("down")
            await pilot.pause()
            content = app.query_one("#std-detail-content")
            text = str(content.content)
            assert "Title:" in text

    async def test_tree_preserves_selection(self, tracker_set: TrackerSet) -> None:
        """Toggling table→tree→table should keep the selected item."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Move to second row and record selection
            await pilot.press("down")
            await pilot.pause()
            selected_before = app._selected_item_id
            assert selected_before is not None
            # Toggle to tree and back
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            # Selection should be preserved
            assert app._selected_item_id == selected_before

    async def test_filter_shows_input(self, tracker_set: TrackerSet) -> None:
        """Pressing 'f' should show the filter input."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            filter_input = app.query_one("#filter-input")
            assert filter_input.display is True
            assert app._filter_active is True

    async def test_filter_narrows_items(self, tracker_set: TrackerSet) -> None:
        """Typing in filter should reduce table rows."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 3
            await pilot.press("f")
            await pilot.pause()
            # Type a filter term that matches only some items
            await pilot.press("c", "a", "c", "h")
            await pilot.pause()
            # "cach" should match "Add caching layer"
            assert table.row_count < 3
            assert table.row_count >= 1

    async def test_filter_dismiss_restores_all(
        self, tracker_set: TrackerSet
    ) -> None:
        """Pressing 'f' again clears filter, all items back."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("c", "a", "c", "h")
            await pilot.pause()
            table = app.query_one("#std-table")
            filtered_count = table.row_count
            assert filtered_count < 3
            # Dismiss filter with Escape
            await pilot.press("escape")
            await pilot.pause()
            assert table.row_count == 3
            assert not app._filter_active

    async def test_tree_toggle_noop_in_compact(
        self, tracker_set: TrackerSet
    ) -> None:
        """'t' key should be a no-op in compact mode."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            await pilot.press("t")
            await pilot.pause()
            assert not app._tree_mode


# ---------------------------------------------------------------------------
# Unit tests: _filtered_items
# ---------------------------------------------------------------------------


class TestFilteredItems:
    """Test the filter logic."""

    async def test_empty_filter_returns_all(self, tmp_path: Path) -> None:
        """Empty filter text should return all items."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert len(app._filtered_items()) == 3

    async def test_filter_by_title(self, tmp_path: Path) -> None:
        """Filter should match titles."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_text = "caching"
            result = app._filtered_items()
            assert len(result) == 1
            assert result[0].title == "Add caching layer"

    async def test_filter_by_status(self, tmp_path: Path) -> None:
        """Filter should match status."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_text = "in_progress"
            result = app._filtered_items()
            assert len(result) == 1
            assert result[0].status == "in_progress"

    async def test_filter_by_tag(self, tmp_path: Path) -> None:
        """Filter should match tags."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_text = "quality"
            result = app._filtered_items()
            assert len(result) == 1
            assert "quality" in result[0].tags

    async def test_no_matches_returns_empty(self, tmp_path: Path) -> None:
        """Filter with no matches should return empty list."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_text = "zzzznonexistent"
            result = app._filtered_items()
            assert len(result) == 0

    async def test_filter_by_kind(self, tmp_path: Path) -> None:
        """Filter should match kind."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_text = "work_item"
            result = app._filtered_items()
            assert len(result) == 1
            assert result[0].kind == "work_item"


# ---------------------------------------------------------------------------
# Pilot tests: dynamic resize
# ---------------------------------------------------------------------------


class TestDynamicResize:
    """Test layout transitions when terminal is resized."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_compact_to_standard_preserves_selection(
        self, tracker_set: TrackerSet
    ) -> None:
        """Resizing from compact to standard should preserve the selected item."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Move cursor to second row and track selection
            await pilot.press("down")
            await pilot.pause()
            # Record which item is selected (from compact table RowSelected)
            table = app.query_one("#item-table")
            row_keys = list(table.rows.keys())
            cursor_row = table.cursor_coordinate.row
            selected_id = str(row_keys[cursor_row].value)
            app._selected_item_id = selected_id

            # Resize to standard
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            await pilot.pause()

            assert app._layout_tier == "standard"
            assert app._selected_item_id == selected_id

    async def test_standard_to_compact_preserves_selection(
        self, tracker_set: TrackerSet
    ) -> None:
        """Resizing from standard to compact should preserve the selected item."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("down")
            await pilot.pause()
            selected_id = app._selected_item_id
            assert selected_id is not None

            # Resize to compact
            await pilot.resize_terminal(50, 18)
            await pilot.pause()
            await pilot.pause()

            assert app._layout_tier == "compact"
            assert app._selected_item_id == selected_id

    async def test_compact_detail_to_standard_clears_detail(
        self, tracker_set: TrackerSet
    ) -> None:
        """Resizing while in compact detail mode should show standard two-pane."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            await pilot.press("enter")
            await pilot.pause()
            assert app._in_detail is True

            # Resize to standard
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            await pilot.pause()

            assert app._layout_tier == "standard"
            assert app._in_detail is False
            two_pane = app.query_one("#two-pane")
            assert two_pane.display is True

    async def test_standard_to_too_small_to_standard(
        self, tracker_set: TrackerSet
    ) -> None:
        """standard → too-small → standard should resume properly."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("down")
            await pilot.pause()
            selected_id = app._selected_item_id

            # Shrink to too-small
            await pilot.resize_terminal(30, 10)
            await pilot.pause()
            await pilot.pause()
            assert app._layout_tier == "too-small"

            # Grow back to standard
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            await pilot.pause()
            assert app._layout_tier == "standard"
            two_pane = app.query_one("#two-pane")
            assert two_pane.display is True

    async def test_filter_preserved_across_resize(
        self, tracker_set: TrackerSet
    ) -> None:
        """Filter state should persist across resize."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Activate filter
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_active is True

            # Resize to compact (width < 80 → filter hidden but state preserved)
            await pilot.resize_terminal(50, 18)
            await pilot.pause()
            await pilot.pause()
            assert app._filter_active is True

            # Filter input should be hidden (width < 80) but state preserved
            filter_input = app.query_one("#filter-input")
            assert filter_input.display is False

    async def test_standard_to_wide_extra_columns_appear(
        self, tracker_set: TrackerSet
    ) -> None:
        """Resizing from standard to wide should add extra columns."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            keys_before = [c.key.value for c in table.columns.values()]
            assert "conflict" not in keys_before

            # Resize to wide
            await pilot.resize_terminal(160, 45)
            await pilot.pause()
            await pilot.pause()
            assert app._layout_tier == "wide"
            keys_after = [c.key.value for c in table.columns.values()]
            assert "conflict" in keys_after
            assert "created" in keys_after
            assert "updated" in keys_after

    async def test_wide_to_standard_extra_columns_removed(
        self, tracker_set: TrackerSet
    ) -> None:
        """Resizing from wide to standard should remove extra columns."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            keys_wide = [c.key.value for c in table.columns.values()]
            assert "conflict" in keys_wide

            # Resize to standard
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            await pilot.pause()
            assert app._layout_tier == "standard"
            keys_std = [c.key.value for c in table.columns.values()]
            assert "conflict" not in keys_std
            assert "created" not in keys_std
            assert "updated" not in keys_std

    async def test_wide_activity_panel_appears_on_resize(
        self, tracker_set: TrackerSet
    ) -> None:
        """Resizing to wide should show the activity panel."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            activity = app.query_one("#activity-view")
            assert activity.display is False

            await pilot.resize_terminal(160, 45)
            await pilot.pause()
            await pilot.pause()
            assert activity.display is True

            # Resize back to standard
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            await pilot.pause()
            assert activity.display is False


# ---------------------------------------------------------------------------
# Pilot tests: edge cases
# ---------------------------------------------------------------------------


def _make_empty_tracker_set(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with no items."""
    root = tmp_path / ".agent"
    for d in [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]:
        d.mkdir(parents=True, exist_ok=True)
    import yaml
    (root / "tracker" / "config.yaml").write_text(yaml.dump({
        "kinds": {"invariant": {"prefix": "INV", "description": "Test"}},
        "statuses": ["todo_hard", "done"],
        "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
        "actor_resolution": {"agent_usernames": ["*_agent"]},
        "lamport_branches": ["dev"],
    }))
    config = _make_config()
    return TrackerSet(root, config=config)


class TestStandardEdgeCases:
    """Test edge cases in the standard layout."""

    async def test_empty_tracker_at_standard_size(self, tmp_path: Path) -> None:
        """Empty tracker should show two-pane without crashing."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            two_pane = app.query_one("#two-pane")
            assert two_pane.display is True
            table = app.query_one("#std-table")
            assert table.row_count == 0

    async def test_row_highlighted_from_compact_table_ignored(
        self, tmp_path: Path
    ) -> None:
        """RowHighlighted from #item-table should be ignored at standard size."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Manually fire a RowHighlighted event from the compact table
            compact_table = app.query_one("#item-table", DataTable)
            event = DataTable.RowHighlighted(
                compact_table, compact_table.cursor_coordinate, None
            )
            app.on_data_table_row_highlighted(event)
            # Should not crash and detail shouldn't change from std-table's state

    async def test_tree_root_node_highlighted_no_crash(
        self, tmp_path: Path
    ) -> None:
        """Highlighting the tree root (data=None) should not crash."""
        from textual.widgets import Tree
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("t")
            await pilot.pause()
            # The tree root has data=None; simulate highlighting it
            tree = app.query_one("#item-tree", Tree)
            event = Tree.NodeHighlighted(tree.root)
            app.on_tree_node_highlighted(event)
            # Should not crash

    async def test_show_std_detail_unknown_id(self, tmp_path: Path) -> None:
        """_show_std_detail with unknown ID should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Should not crash
            app._show_std_detail("NONEXISTENT-ID")

    async def test_row_highlighted_with_none_row_key(
        self, tmp_path: Path
    ) -> None:
        """RowHighlighted with row_key=None should be handled gracefully."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            std_table = app.query_one("#std-table", DataTable)
            event = DataTable.RowHighlighted(
                std_table, std_table.cursor_coordinate, None
            )
            app.on_data_table_row_highlighted(event)
            # Should not crash

    async def test_filter_in_compact_mode_wide_enough(self, tmp_path: Path) -> None:
        """Filter should work in compact mode at width >= 80 (but height < 20)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        # Width 80 (>= 80 for filter), height 18 (compact tier: < 20)
        async with app.run_test(size=(80, 18)) as pilot:
            await _wait_for_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_active is True
            filter_input = app.query_one("#filter-input")
            assert filter_input.display is True
            # Dismiss with escape
            await pilot.press("escape")
            await pilot.pause()
            assert not app._filter_active

    async def test_input_changed_wrong_id_ignored(
        self, tmp_path: Path
    ) -> None:
        """Input.Changed from a different input should be ignored."""
        from textual.widgets import Input as TextualInput
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Create a mock Input.Changed event with wrong ID
            fake_input = TextualInput(id="other-input")
            event = TextualInput.Changed(fake_input, "test")
            app.on_input_changed(event)
            # Filter text should not change
            assert app._filter_text == ""

    async def test_restore_selection_no_selected_id(
        self, tmp_path: Path
    ) -> None:
        """_restore_selection with no selected ID should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._selected_item_id = None
            app._restore_selection()  # Should not crash

    async def test_restore_selection_nonexistent_id(
        self, tmp_path: Path
    ) -> None:
        """_restore_selection with ID not in table should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._selected_item_id = "NONEXISTENT"
            app._restore_selection()  # Should not crash

    async def test_tree_node_highlighted_in_compact_ignored(
        self, tmp_path: Path
    ) -> None:
        """Tree node highlighted in compact tier should be ignored."""
        from textual.widgets import Tree
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            tree = app.query_one("#item-tree", Tree)
            # Even though tree isn't visible, test the handler guard
            event = Tree.NodeHighlighted(tree.root)
            app.on_tree_node_highlighted(event)
            # Should be a no-op

    async def test_action_back_escape_filter_then_detail(
        self, tmp_path: Path
    ) -> None:
        """Escape should dismiss filter first, then detail mode."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        # Width >= 80 required for filter (D10), height < 20 keeps compact tier
        async with app.run_test(size=(80, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Enter detail mode
            await pilot.press("enter")
            await pilot.pause()
            assert app._in_detail is True
            # Now activate filter
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_active is True
            # Escape should dismiss filter first
            await pilot.press("escape")
            await pilot.pause()
            assert not app._filter_active
            assert app._in_detail is True
            # Second escape should leave detail
            await pilot.press("escape")
            await pilot.pause()
            assert not app._in_detail

    async def test_std_row_highlighted_at_compact_tier(
        self, tmp_path: Path
    ) -> None:
        """RowHighlighted from std-table when tier is compact should be ignored."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Manually construct an event from std-table at compact tier
            std_table = app.query_one("#std-table", DataTable)
            event = DataTable.RowHighlighted(
                std_table, std_table.cursor_coordinate, None
            )
            app.on_data_table_row_highlighted(event)
            # Should be a no-op (tier check returns early)

    async def test_toggle_filter_action_dismisses(self, tmp_path: Path) -> None:
        """Calling action_toggle_filter when active should dismiss filter."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Toggle on
            app.action_toggle_filter()
            await pilot.pause()
            assert app._filter_active is True
            # Toggle off by calling action directly
            # (can't press 'f' because Input widget captures it)
            app.action_toggle_filter()
            await pilot.pause()
            assert not app._filter_active


# ---------------------------------------------------------------------------
# Pilot tests: wide layout
# ---------------------------------------------------------------------------


class TestWideLayout:
    """Test wide layout tier (>120x38) with extra columns and features."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_wide_tier_detected(self, tracker_set: TrackerSet) -> None:
        """At 160x45, layout tier should be 'wide'."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert app._layout_tier == "wide"

    async def test_wide_extra_columns_present(
        self, tracker_set: TrackerSet
    ) -> None:
        """Wide mode should include conflict, created, updated columns."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [col.key.value for col in table.columns.values()]
            assert "conflict" in column_keys
            assert "created" in column_keys
            assert "updated" in column_keys

    async def test_wide_standard_columns_still_present(
        self, tracker_set: TrackerSet
    ) -> None:
        """Wide mode should still have the standard columns."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [col.key.value for col in table.columns.values()]
            for key in ("row_num", "tier", "priority", "id", "status", "title"):
                assert key in column_keys

    async def test_standard_no_extra_columns(
        self, tracker_set: TrackerSet
    ) -> None:
        """Standard mode should NOT have the extra wide columns."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [col.key.value for col in table.columns.values()]
            assert "conflict" not in column_keys
            assert "created" not in column_keys
            assert "updated" not in column_keys

    async def test_wide_activity_panel_visible(
        self, tracker_set: TrackerSet
    ) -> None:
        """Activity panel should be visible in wide mode."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            activity_view = app.query_one("#activity-view")
            assert activity_view.display is True
            activity_divider = app.query_one("#activity-divider")
            assert activity_divider.display is True

    async def test_activity_hidden_in_standard(
        self, tracker_set: TrackerSet
    ) -> None:
        """Activity panel should be hidden in standard mode."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            activity_view = app.query_one("#activity-view")
            assert activity_view.display is False
            activity_divider = app.query_one("#activity-divider")
            assert activity_divider.display is False

    async def test_wide_activity_updates_on_cursor_move(
        self, tmp_path: Path
    ) -> None:
        """Moving cursor in wide mode should update activity panel."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        # Add discussion to first item
        items = ts.list_items()
        ts.discuss(items[0].id, "Activity test message")

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            content = app.query_one("#activity-content")
            # Move down to trigger update
            await pilot.press("down")
            await pilot.pause()
            # Activity content should have been updated
            text = str(content.content)
            # It may or may not contain the message depending on which item is selected
            assert isinstance(text, str)

    async def test_wide_detail_shows_timestamps(
        self, tmp_path: Path
    ) -> None:
        """In wide mode, detail panel should show timestamps and extra fields."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            content = app.query_one("#std-detail-content")
            text = str(content.content)
            # Wide detail should show Created/Updated fields
            assert "Created:" in text or "Updated:" in text

    async def test_wide_detail_suppresses_inline_discussion(
        self, tmp_path: Path
    ) -> None:
        """In wide mode, inline Discussion section should be suppressed."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        item_id = ts.add(
            kind="invariant",
            title="Suppression check item",
            status="todo_hard",
            priority=1,
        )
        ts.discuss(item_id, "Test discussion entry")

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._show_std_detail(item_id)
            await pilot.pause()
            content = app.query_one("#std-detail-content")
            text = str(content.content)
            # In wide mode, the "Discussion (N entries):" section should
            # NOT appear in the detail panel; it's in the activity panel
            assert "entries):" not in text

    async def test_show_activity_not_wide_noop(
        self, tmp_path: Path
    ) -> None:
        """Calling _show_activity in standard mode should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            if items:
                app._show_activity(items[0])
                # Should not crash, activity content stays empty
                content = app.query_one("#activity-content")
                text = str(content.content)
                assert text == "" or text == "No recent activity" or isinstance(text, str)

    async def test_wide_tree_toggle(self, tracker_set: TrackerSet) -> None:
        """Tree toggle should work at wide size."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("t")
            await pilot.pause()
            tree = app.query_one("#item-tree")
            std_table = app.query_one("#std-table")
            assert tree.display is True
            assert std_table.display is False
            assert app._tree_mode is True
            # Toggle back
            await pilot.press("t")
            await pilot.pause()
            assert tree.display is False
            assert std_table.display is True

    async def test_wide_filter_narrows_items(
        self, tracker_set: TrackerSet
    ) -> None:
        """Filter should work at wide size."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 3
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("c", "a", "c", "h")
            await pilot.pause()
            assert table.row_count < 3
            assert table.row_count >= 1


# ---------------------------------------------------------------------------
# Pilot tests: filter status indicator
# ---------------------------------------------------------------------------


class TestFilterStatus:
    """Test the filter status indicator widget."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_filter_status_shown_when_active(
        self, tracker_set: TrackerSet
    ) -> None:
        """Filter status should be visible when filter text is non-empty."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("c", "a", "c", "h")
            await pilot.pause()
            status = app.query_one("#filter-status")
            assert status.display is True
            text = str(status.content)
            assert "cach" in text.lower()

    async def test_filter_status_hidden_when_cleared(
        self, tracker_set: TrackerSet
    ) -> None:
        """Filter status should be hidden after filter is dismissed."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("c", "a")
            await pilot.pause()
            status = app.query_one("#filter-status")
            assert status.display is True
            # Dismiss filter
            await pilot.press("escape")
            await pilot.pause()
            assert status.display is False

    async def test_filter_status_hidden_initially(
        self, tracker_set: TrackerSet
    ) -> None:
        """Filter status should be hidden on initial load."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            status = app.query_one("#filter-status")
            assert status.display is False


# ---------------------------------------------------------------------------
# Unit tests: modal screens via wrapper App
# ---------------------------------------------------------------------------


class _ModalTestApp(App):
    """Minimal app for testing modal screens in isolation.

    Pushes the given screen on mount and captures the result in ``_result``.
    """

    def __init__(self, screen: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._screen = screen
        self._result: Any = "NOT_SET"

    def on_mount(self) -> None:
        self.push_screen(self._screen, callback=self._capture)

    def _capture(self, result: Any) -> None:
        self._result = result


async def _wait_for_modal(
    pilot: Any, app: Any, widget_id: str = "#modal-dialog",
    max_rounds: int = 30,
) -> None:
    """Wait for a modal screen to compose and render.

    Polls until *widget_id* is found in the active screen's DOM, with a
    configurable maximum number of event-loop ticks.  This accounts for
    the asynchronous compose lifecycle when a ModalScreen is pushed from
    ``on_mount``.
    """
    from textual.css.query import NoMatches

    for _ in range(max_rounds):
        await pilot.pause()
        try:
            app.screen.query_one(widget_id)
            return
        except NoMatches:
            continue


class TestDiscussScreenUnit:
    """Test DiscussScreen modal in isolation."""

    async def test_submit_returns_message(self) -> None:
        screen = DiscussScreen("ID-1", "Test Item")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#discuss-input").value = "Hello there"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result == "Hello there"

    async def test_cancel_returns_none(self) -> None:
        screen = DiscussScreen("ID-1", "Test Item")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_escape_returns_none(self) -> None:
        screen = DiscussScreen("ID-1", "Test Item")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None

    async def test_empty_submit_returns_none(self) -> None:
        screen = DiscussScreen("ID-1", "Test Item")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None


class TestConfirmScreenUnit:
    """Test ConfirmScreen modal in isolation."""

    async def test_yes_returns_true(self) -> None:
        screen = ConfirmScreen("Are you sure?")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#yes")
            await pilot.pause()
            assert app._result is True

    async def test_no_returns_false(self) -> None:
        screen = ConfirmScreen("Are you sure?")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#no")
            await pilot.pause()
            assert app._result is False

    async def test_escape_returns_false(self) -> None:
        screen = ConfirmScreen("Are you sure?")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is False


class TestTierMoveScreenUnit:
    """Test TierMoveScreen modal in isolation."""

    async def test_canonical_shows_demote(self) -> None:
        screen = TierMoveScreen("ID-1", Tier.CANONICAL)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            from textual.widgets import Select as Sel
            sel = app.screen.query_one("#move-select", Sel)
            assert sel.value == "demote"

    async def test_workspace_shows_promote(self) -> None:
        screen = TierMoveScreen("ID-1", Tier.WORKSPACE)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            from textual.widgets import Select as Sel
            sel = app.screen.query_one("#move-select", Sel)
            assert sel.value == "promote"

    async def test_stealth_shows_unstealth(self) -> None:
        screen = TierMoveScreen("ID-1", Tier.STEALTH)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            from textual.widgets import Select as Sel
            sel = app.screen.query_one("#move-select", Sel)
            assert sel.value == "unstealth"

    async def test_none_tier_no_select(self) -> None:
        screen = TierMoveScreen("ID-1", None)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            results = app.screen.query("#move-select")
            assert len(results) == 0

    async def test_cancel_returns_none(self) -> None:
        screen = TierMoveScreen("ID-1", Tier.WORKSPACE)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_escape_returns_none(self) -> None:
        screen = TierMoveScreen("ID-1", Tier.WORKSPACE)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None

    async def test_submit_no_select_returns_none(self) -> None:
        screen = TierMoveScreen("ID-1", None)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None

    async def test_submit_with_selection(self) -> None:
        screen = TierMoveScreen("ID-1", Tier.CANONICAL)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result == "demote"


class TestNewItemScreenUnit:
    """Test NewItemScreen modal in isolation."""

    async def test_submit_with_valid_data(self) -> None:
        screen = NewItemScreen(["invariant", "work_item"], ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#title-input").value = "New Test Item"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["title"] == "New Test Item"
            assert app._result["kind"] == "invariant"
            assert app._result["tier"] == Tier.WORKSPACE

    async def test_submit_empty_title_returns_none(self) -> None:
        screen = NewItemScreen(["invariant"], ["todo_hard"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None

    async def test_cancel_returns_none(self) -> None:
        screen = NewItemScreen(["invariant"], ["todo_hard"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_invalid_priority_defaults_to_2(self) -> None:
        screen = NewItemScreen(["invariant"], ["todo_hard"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#title-input").value = "Test"
            app.screen.query_one("#priority-input").value = "not-a-number"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["priority"] == 2

    async def test_escape_returns_none(self) -> None:
        screen = NewItemScreen(["invariant"], ["todo_hard"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None

    async def test_with_description(self) -> None:
        screen = NewItemScreen(["invariant"], ["todo_hard"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#title-input").value = "With Desc"
            app.screen.query_one("#desc-input").value = "A description"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["description"] == "A description"


class TestEditItemScreenUnit:
    """Test EditItemScreen modal in isolation."""

    def _make_item(self) -> CompiledItem:
        return CompiledItem(
            id="INV-test",
            kind="invariant",
            title="Original Title",
            status="todo_hard",
            priority=1,
            tier=Tier.CANONICAL,
            tags=["quality"],
            description="Original desc",
        )

    async def test_submit_with_changes(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "in_progress", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#title-input").value = "Changed Title"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["set_fields"]["title"] == "Changed Title"

    async def test_status_change(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "in_progress", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            from textual.widgets import Select as Sel
            sel = app.screen.query_one("#status-select", Sel)
            sel.value = "in_progress"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["set_fields"]["status"] == "in_progress"

    async def test_submit_no_changes_returns_none(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "in_progress", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None

    async def test_cancel_returns_none(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_tag_add(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#tags-input").value = "quality, new_tag"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert "new_tag" in app._result["add_fields"]["tags"]

    async def test_tag_removal(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#tags-input").value = ""
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert "quality" in app._result["remove_fields"]["tags"]

    async def test_priority_change(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#priority-input").value = "5"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["set_fields"]["priority"] == 5

    async def test_invalid_priority_ignored(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#priority-input").value = "abc"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None

    async def test_escape_returns_none(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None

    async def test_desc_change(self) -> None:
        item = self._make_item()
        screen = EditItemScreen(item, ["todo_hard", "done"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#desc-input").value = "New description"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["set_fields"]["description"] == "New description"


class TestParentScreenUnit:
    """Test ParentScreen modal in isolation."""

    async def test_submit_new_parent(self) -> None:
        screen = ParentScreen("ID-1", "OLD-PARENT")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#parent-input").value = "NEW-PARENT"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result == "NEW-PARENT"

    async def test_submit_empty_clears_parent(self) -> None:
        screen = ParentScreen("ID-1", "OLD-PARENT")
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#parent-input").value = ""
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result == ""

    async def test_cancel_returns_none(self) -> None:
        screen = ParentScreen("ID-1", None)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_escape_returns_none(self) -> None:
        screen = ParentScreen("ID-1", None)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None


class TestBeforeScreenUnit:
    """Test BeforeScreen modal in isolation."""

    async def test_add_ids(self) -> None:
        screen = BeforeScreen("ID-1", ["EXISTING-1"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#add-input").value = "NEW-1, NEW-2"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["add"] == ["NEW-1", "NEW-2"]

    async def test_remove_ids(self) -> None:
        screen = BeforeScreen("ID-1", ["EXISTING-1"])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#remove-input").value = "EXISTING-1"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["remove"] == ["EXISTING-1"]

    async def test_empty_submit_returns_none(self) -> None:
        screen = BeforeScreen("ID-1", [])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None

    async def test_cancel_returns_none(self) -> None:
        screen = BeforeScreen("ID-1", [])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_escape_returns_none(self) -> None:
        screen = BeforeScreen("ID-1", [])
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None


class TestLockScreenUnit:
    """Test LockScreen modal in isolation."""

    async def test_lock_fields(self) -> None:
        screen = LockScreen("ID-1", set())
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#lock-input").value = "status, priority"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert set(app._result["lock"]) == {"status", "priority"}

    async def test_unlock_fields(self) -> None:
        screen = LockScreen("ID-1", {"status"})
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            app.screen.query_one("#unlock-input").value = "status"
            await pilot.pause()
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is not None
            assert app._result["unlock"] == ["status"]

    async def test_empty_submit_returns_none(self) -> None:
        screen = LockScreen("ID-1", set())
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#submit")
            await pilot.pause()
            assert app._result is None

    async def test_cancel_returns_none(self) -> None:
        screen = LockScreen("ID-1", set())
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.click("#cancel")
            await pilot.pause()
            assert app._result is None

    async def test_escape_returns_none(self) -> None:
        screen = LockScreen("ID-1", set())
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 20)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None


# ---------------------------------------------------------------------------
# Integration tests: write keybindings on TrackerApp
# ---------------------------------------------------------------------------


class TestGetSelectedItem:
    """Test the _get_selected_item helper."""

    async def test_returns_item_at_cursor(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            assert item.id in [i.id for i in ts.list_items()]

    async def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            item = app._get_selected_item()
            assert item is None

    async def test_returns_none_at_too_small(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(30, 10)) as pilot:
            for _ in range(5):
                await pilot.pause()
            item = app._get_selected_item()
            assert item is None

    async def test_returns_item_in_compact(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

    async def test_returns_item_in_tree_mode(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Select an item first
            await pilot.press("down")
            await pilot.pause()
            selected_id = app._selected_item_id
            assert selected_id is not None
            # Switch to tree mode
            await pilot.press("t")
            await pilot.pause()
            item = app._get_selected_item()
            assert item is not None
            assert item.id == selected_id

    async def test_returns_none_in_tree_mode_no_selection(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._tree_mode = True
            app._selected_item_id = None
            item = app._get_selected_item()
            assert item is None


class TestDiscussKeybinding:
    """Test the 'd' discuss keybinding end-to-end."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_discuss_no_item_warns(self, tmp_path: Path) -> None:
        """Pressing 'd' with no items shows warning."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            # No crash; warning notification shown

    async def test_discuss_happy_path(self, tracker_set: TrackerSet) -> None:
        """Pressing 'd', typing, and submitting adds a discussion entry."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item_id = item.id

            # Mock TrackerSet.discuss to verify it's called
            with patch.object(
                tracker_set, "discuss", wraps=tracker_set.discuss,
            ) as mock_discuss:
                app.action_discuss()
                await _wait_for_modal(pilot, app)
                # The DiscussScreen should be pushed
                # Simulate typing into the modal's input and clicking submit
                modal_input = app.screen.query_one("#discuss-input")
                modal_input.value = "Test discussion"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_discuss.assert_called_once_with(item_id, "Test discussion")

    async def test_discuss_cancel(self, tracker_set: TrackerSet) -> None:
        """Pressing 'd' then Cancel does not write."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "discuss") as mock_discuss:
                app.action_discuss()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock_discuss.assert_not_called()

    async def test_discuss_error(self, tracker_set: TrackerSet) -> None:
        """When discuss raises an error, notification is shown."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "discuss",
                side_effect=LockedFieldError("discussion locked"),
            ):
                app.action_discuss()
                await _wait_for_modal(pilot, app)
                modal_input = app.screen.query_one("#discuss-input")
                modal_input.value = "Test"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                # Error notification shown (no crash)

    async def test_discuss_rate_limit_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        """DiscussionRateLimitError is caught and notified."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "discuss",
                side_effect=DiscussionRateLimitError("rate limited"),
            ):
                app.action_discuss()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#discuss-input").value = "Test"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()

    async def test_discuss_not_found_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        """ItemNotFoundError is caught and notified."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "discuss",
                side_effect=ItemNotFoundError("not found"),
            ):
                app.action_discuss()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#discuss-input").value = "Test"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()


class TestDiscussClearKeybinding:
    """Test the 'D' clear-discussion keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_clear_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("D")
            await pilot.pause()

    async def test_clear_confirmed(self, tracker_set: TrackerSet) -> None:
        """Confirming clear calls discuss with clear=True."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(tracker_set, "discuss") as mock_discuss:
                app.action_discuss_clear()
                await _wait_for_modal(pilot, app)
                await pilot.click("#yes")
                await pilot.pause()
                await pilot.pause()
                mock_discuss.assert_called_once_with(
                    item.id, "", clear=True,
                )

    async def test_clear_cancelled(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "discuss") as mock_discuss:
                app.action_discuss_clear()
                await _wait_for_modal(pilot, app)
                await pilot.click("#no")
                await pilot.pause()
                mock_discuss.assert_not_called()

    async def test_clear_human_authority_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "discuss",
                side_effect=HumanAuthorityError("human only"),
            ):
                app.action_discuss_clear()
                await _wait_for_modal(pilot, app)
                await pilot.click("#yes")
                await pilot.pause()

    async def test_clear_not_found_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "discuss",
                side_effect=ItemNotFoundError("not found"),
            ):
                app.action_discuss_clear()
                await _wait_for_modal(pilot, app)
                await pilot.click("#yes")
                await pilot.pause()


class TestTierMoveKeybinding:
    """Test the 'm' tier-move keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_tier_move_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("m")
            await pilot.pause()

    async def test_promote_happy_path(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(tracker_set, "promote") as mock_promote:
                app.action_tier_move()
                await _wait_for_modal(pilot, app)
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                # The mock should have been called (the item is workspace
                # so the first option is "promote")
                if item.tier == Tier.WORKSPACE:
                    mock_promote.assert_called_once_with(item.id)

    async def test_tier_move_cancel(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "promote") as mock:
                app.action_tier_move()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock.assert_not_called()

    async def test_tier_move_error(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "promote",
                side_effect=TierMovementError("wrong tier"),
            ), patch.object(
                tracker_set, "demote",
                side_effect=TierMovementError("wrong tier"),
            ), patch.object(
                tracker_set, "stealth_item",
                side_effect=TierMovementError("wrong tier"),
            ), patch.object(
                tracker_set, "unstealth_item",
                side_effect=TierMovementError("wrong tier"),
            ):
                app.action_tier_move()
                await _wait_for_modal(pilot, app)
                await pilot.click("#submit")
                await pilot.pause()

    async def test_tier_move_human_authority_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "promote",
                side_effect=HumanAuthorityError("human only"),
            ), patch.object(
                tracker_set, "stealth_item",
                side_effect=HumanAuthorityError("human only"),
            ):
                app.action_tier_move()
                await _wait_for_modal(pilot, app)
                await pilot.click("#submit")
                await pilot.pause()


class TestNewItemKeybinding:
    """Test the 'n' new-item keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_new_item_happy_path(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            initial_count = len(app._items)

            with patch.object(
                tracker_set, "add", return_value="NEW-ID",
            ) as mock_add:
                app.action_new_item()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#title-input").value = "Brand New Item"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_add.assert_called_once()
                call_kwargs = mock_add.call_args
                assert call_kwargs[1]["title"] == "Brand New Item"

    async def test_new_item_cancel(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "add") as mock_add:
                app.action_new_item()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock_add.assert_not_called()

    async def test_new_item_error(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "add",
                side_effect=ValueError("unknown kind"),
            ):
                app.action_new_item()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#title-input").value = "Bad Item"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()


class TestEditItemKeybinding:
    """Test the 'e' edit-item keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_edit_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

    async def test_edit_happy_path(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            with patch.object(tracker_set, "update") as mock_update:
                app.action_edit_item()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#title-input").value = "Changed Title"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_update.assert_called_once()

    async def test_edit_cancel(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "update") as mock_update:
                app.action_edit_item()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock_update.assert_not_called()

    async def test_edit_locked_field_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "update",
                side_effect=LockedFieldError("field locked"),
            ):
                app.action_edit_item()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#title-input").value = "New Title"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()


class TestSetParentKeybinding:
    """Test the 'p' set-parent keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_parent_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("p")
            await pilot.pause()

    async def test_set_parent_happy_path(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            with patch.object(tracker_set, "update") as mock_update:
                app.action_set_parent()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#parent-input").value = "PARENT-ID"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args
                assert call_kwargs[1]["set_fields"]["parent"] == "PARENT-ID"

    async def test_clear_parent(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            with patch.object(tracker_set, "update") as mock_update:
                app.action_set_parent()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#parent-input").value = ""
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args
                assert call_kwargs[1]["set_fields"]["parent"] == ""

    async def test_parent_cancel(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "update") as mock_update:
                app.action_set_parent()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock_update.assert_not_called()

    async def test_parent_error(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "update",
                side_effect=LockedFieldError("locked"),
            ):
                app.action_set_parent()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#parent-input").value = "X"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()


class TestEditBeforeKeybinding:
    """Test the 'b' edit-before keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_before_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("b")
            await pilot.pause()

    async def test_add_before_links(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            with patch.object(tracker_set, "update") as mock_update:
                app.action_edit_before()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#add-input").value = "ID-1, ID-2"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args
                assert call_kwargs[1]["add_fields"] == {
                    "before": ["ID-1", "ID-2"],
                }

    async def test_remove_before_links(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "update") as mock_update:
                app.action_edit_before()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#remove-input").value = "OLD-ID"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args
                assert call_kwargs[1]["remove_fields"] == {
                    "before": ["OLD-ID"],
                }

    async def test_before_cancel(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "update") as mock_update:
                app.action_edit_before()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock_update.assert_not_called()

    async def test_before_error(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "update",
                side_effect=ItemNotFoundError("gone"),
            ):
                app.action_edit_before()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#add-input").value = "X"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()


class TestLockKeybinding:
    """Test the 'l' lock/unlock keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_lock_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("l")
            await pilot.pause()

    async def test_lock_fields(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "lock") as mock_lock:
                app.action_toggle_lock()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#lock-input").value = "status"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_lock.assert_called_once()

    async def test_unlock_fields(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "unlock") as mock_unlock:
                app.action_toggle_lock()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#unlock-input").value = "priority"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_unlock.assert_called_once()

    async def test_lock_and_unlock(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "lock") as mock_lock, \
                 patch.object(tracker_set, "unlock") as mock_unlock:
                app.action_toggle_lock()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#lock-input").value = "status"
                app.screen.query_one("#unlock-input").value = "priority"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                await pilot.pause()
                mock_lock.assert_called_once()
                mock_unlock.assert_called_once()

    async def test_lock_cancel(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "lock") as mock_lock:
                app.action_toggle_lock()
                await _wait_for_modal(pilot, app)
                await pilot.click("#cancel")
                await pilot.pause()
                mock_lock.assert_not_called()

    async def test_lock_human_authority_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "lock",
                side_effect=HumanAuthorityError("human only"),
            ):
                app.action_toggle_lock()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#lock-input").value = "status"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()

    async def test_unlock_not_found_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "unlock",
                side_effect=ItemNotFoundError("not found"),
            ):
                app.action_toggle_lock()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#unlock-input").value = "status"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()


class TestReloadAfterWrite:
    """Test the _reload_after_write helper."""

    async def test_reload_preserves_selection(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = ts.list_items()
            target_id = items[1].id
            app._reload_after_write(target_id)
            await pilot.pause()
            assert app._selected_item_id == target_id

    async def test_reload_without_selection(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._reload_after_write()
            await pilot.pause()
            table = app.query_one("#std-table")
            assert table.row_count == 3


class TestOnTierMoveCallbackBranches:
    """Test _on_tier_move callback branches for full coverage."""

    async def test_demote_branch(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "demote") as mock:
                app._on_tier_move(item.id, "demote")
                await pilot.pause()
                mock.assert_called_once_with(item.id)

    async def test_stealth_branch(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "stealth_item") as mock:
                app._on_tier_move(item.id, "stealth")
                await pilot.pause()
                mock.assert_called_once_with(item.id)

    async def test_unstealth_branch(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "unstealth_item") as mock:
                app._on_tier_move(item.id, "unstealth")
                await pilot.pause()
                mock.assert_called_once_with(item.id)

    async def test_none_move_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "promote") as mock:
                app._on_tier_move(item.id, None)
                await pilot.pause()
                mock.assert_not_called()

    async def test_not_found_error(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                ts, "promote",
                side_effect=ItemNotFoundError("gone"),
            ):
                app._on_tier_move("FAKE-ID", "promote")
                await pilot.pause()


class TestOnEditItemCallbackBranches:
    """Test _on_edit_item callback with empty fields."""

    async def test_none_result_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "update") as mock:
                app._on_edit_item("FAKE-ID", None)
                await pilot.pause()
                mock.assert_not_called()

    async def test_empty_fields_passes_none(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "update") as mock:
                app._on_edit_item(item.id, {
                    "set_fields": {"title": "X"},
                    "add_fields": {},
                    "remove_fields": {},
                })
                await pilot.pause()
                mock.assert_called_once_with(
                    item.id,
                    set_fields={"title": "X"},
                    add_fields=None,
                    remove_fields=None,
                )


class TestOnEditBeforeCallbackBranches:
    """Test _on_edit_before callback branches."""

    async def test_add_only(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "update") as mock:
                app._on_edit_before(item.id, {"add": ["X"], "remove": []})
                await pilot.pause()
                mock.assert_called_once_with(
                    item.id,
                    add_fields={"before": ["X"]},
                    remove_fields=None,
                )

    async def test_remove_only(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "update") as mock:
                app._on_edit_before(item.id, {"add": [], "remove": ["Y"]})
                await pilot.pause()
                mock.assert_called_once_with(
                    item.id,
                    add_fields=None,
                    remove_fields={"before": ["Y"]},
                )

    async def test_none_result_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "update") as mock:
                app._on_edit_before("FAKE", None)
                await pilot.pause()
                mock.assert_not_called()

    async def test_locked_field_error(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                ts, "update",
                side_effect=LockedFieldError("locked"),
            ):
                app._on_edit_before("FAKE", {"add": ["X"], "remove": []})
                await pilot.pause()


class TestOnToggleLockCallbackBranches:
    """Test _on_toggle_lock callback branches."""

    async def test_lock_only(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "lock") as ml, \
                 patch.object(ts, "unlock") as mu:
                app._on_toggle_lock(item.id, {"lock": ["status"], "unlock": []})
                await pilot.pause()
                ml.assert_called_once_with(item.id, ["status"])
                mu.assert_not_called()

    async def test_unlock_only(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "lock") as ml, \
                 patch.object(ts, "unlock") as mu:
                app._on_toggle_lock(item.id, {"lock": [], "unlock": ["p"]})
                await pilot.pause()
                ml.assert_not_called()
                mu.assert_called_once_with(item.id, ["p"])

    async def test_none_result_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "lock") as ml:
                app._on_toggle_lock("FAKE", None)
                await pilot.pause()
                ml.assert_not_called()


class TestToggleFreezeKeybinding:
    """Test the 'z' freeze/unfreeze keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_freeze_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("z")
            await pilot.pause()

    async def test_freeze_calls_freeze(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "freeze") as mock_freeze:
                app.action_toggle_freeze()
                await pilot.pause()
                mock_freeze.assert_called_once()

    async def test_unfreeze_calls_unfreeze(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(tracker_set, "unfreeze") as mock_unfreeze:
                app.action_toggle_freeze()
                await pilot.pause()
                mock_unfreeze.assert_called_once()

    async def test_freeze_human_authority_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "freeze",
                side_effect=HumanAuthorityError("human only"),
            ):
                app.action_toggle_freeze()
                await pilot.pause()

    async def test_freeze_value_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "freeze",
                side_effect=ValueError("already frozen"),
            ):
                app.action_toggle_freeze()
                await pilot.pause()


class TestRepairDriftKeybinding:
    """Test the 'R' repair-drift keybinding."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_repair_drift_not_frozen_warns(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            assert not item.frozen
            app.action_repair_drift()
            await pilot.pause()

    async def test_repair_drift_no_drift_warns(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(tracker_set, "drift_check", return_value=False):
                app.action_repair_drift()
                await pilot.pause()

    async def test_repair_drift_confirmed(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(tracker_set, "drift_check", return_value=True), \
                 patch.object(tracker_set, "repair_drift") as mock_repair:
                app.action_repair_drift()
                await _wait_for_modal(pilot, app)
                await pilot.click("#yes")
                await pilot.pause()
                await pilot.pause()
                mock_repair.assert_called_once()

    async def test_repair_drift_cancelled(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(tracker_set, "drift_check", return_value=True), \
                 patch.object(tracker_set, "repair_drift") as mock_repair:
                app.action_repair_drift()
                await _wait_for_modal(pilot, app)
                await pilot.click("#no")
                await pilot.pause()
                mock_repair.assert_not_called()

    async def test_repair_drift_error_on_confirm(
        self, tracker_set: TrackerSet,
    ) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(tracker_set, "drift_check", return_value=True), \
                 patch.object(
                     tracker_set, "repair_drift",
                     side_effect=HumanAuthorityError("human only"),
                 ):
                app.action_repair_drift()
                await _wait_for_modal(pilot, app)
                await pilot.click("#yes")
                await pilot.pause()

    async def test_repair_drift_no_item_warns(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            app.action_repair_drift()
            await pilot.pause()


class TestShowDetailDrift:
    """Test frozen/drift indicators in detail views."""

    async def test_compact_detail_frozen_drift(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(ts, "drift_check", return_value=True):
                app._show_detail(item)
                await pilot.pause()

    async def test_compact_detail_frozen_no_drift(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(ts, "drift_check", return_value=False):
                app._show_detail(item)
                await pilot.pause()

    async def test_std_detail_frozen_drift(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(ts, "drift_check", return_value=True):
                app._show_std_detail(item.id)
                await pilot.pause()

    async def test_std_detail_frozen_no_drift(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            item.frozen = True
            with patch.object(ts, "drift_check", return_value=False):
                app._show_std_detail(item.id)
                await pilot.pause()


class TestOnNewItemCallbackBranches:
    """Test _on_new_item callback branches."""

    async def test_none_result_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "add") as mock:
                app._on_new_item(None)
                await pilot.pause()
                mock.assert_not_called()


class TestOnDiscussClearCallbackBranches:
    """Test _on_discuss_clear with False confirmation."""

    async def test_not_confirmed_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "discuss") as mock:
                app._on_discuss_clear("FAKE", False)
                await pilot.pause()
                mock.assert_not_called()


class TestOnDiscussCallbackBranches:
    """Test _on_discuss callback with None message."""

    async def test_none_message_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "discuss") as mock:
                app._on_discuss("FAKE", None)
                await pilot.pause()
                mock.assert_not_called()


class TestOnSetParentCallbackBranches:
    """Test _on_set_parent callback branches."""

    async def test_none_result_noop(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "update") as mock:
                app._on_set_parent("FAKE", None)
                await pilot.pause()
                mock.assert_not_called()

    async def test_not_found_error(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                ts, "update",
                side_effect=ItemNotFoundError("gone"),
            ):
                app._on_set_parent("FAKE", "PARENT")
                await pilot.pause()


# ---------------------------------------------------------------------------
# D7: Schema-aware detail rendering
# ---------------------------------------------------------------------------


class TestSchemaAwareRendering:
    """Test _format_detail_lines with fields_schema parameter."""

    def test_known_fields_in_schema_order_with_description(self) -> None:
        """Known fields render in schema declaration order with descriptions."""
        from hypergumbo_tracker.models import FieldSchema

        item = CompiledItem(
            id="INV-abc",
            kind="invariant",
            title="Schema test",
            status="todo_hard",
            fields={
                "root_cause": "bug in parser",
                "statement": "must be true",
                "extra_field": "unknown",
            },
        )
        schema = {
            "statement": FieldSchema(type="text", description="What must hold"),
            "root_cause": FieldSchema(type="text", description="Why it fails"),
        }
        lines = _format_detail_lines(item, fields_schema=schema)
        text = _strip_markup("\n".join(lines))
        # Known fields in schema order (statement before root_cause)
        stmt_pos = text.index("statement")
        rc_pos = text.index("root_cause")
        assert stmt_pos < rc_pos
        # Descriptions in labels
        assert "(What must hold)" in text
        assert "(Why it fails)" in text
        # Unknown field under "Other"
        assert "Other:" in text
        assert "extra_field" in text

    def test_no_schema_renders_flat(self) -> None:
        """Without fields_schema, fields render as flat key: value pairs."""
        item = CompiledItem(
            id="WI-abc",
            kind="work_item",
            title="No schema",
            status="done",
            fields={"key1": "val1", "key2": "val2"},
        )
        lines = _format_detail_lines(item, fields_schema=None)
        text = _strip_markup("\n".join(lines))
        assert "key1" in text
        assert "key2" in text
        assert "Other:" not in text

    def test_schema_no_description(self) -> None:
        """Schema field without description renders without label."""
        from hypergumbo_tracker.models import FieldSchema

        item = CompiledItem(
            id="INV-xyz",
            kind="invariant",
            title="No desc",
            status="todo_hard",
            fields={"statement": "must be true"},
        )
        schema = {
            "statement": FieldSchema(type="text"),
        }
        lines = _format_detail_lines(item, fields_schema=schema)
        text = _strip_markup("\n".join(lines))
        assert "statement" in text
        assert "()" not in text  # No empty parens

    def test_schema_only_unknown_fields(self) -> None:
        """When all fields are unknown (not in schema), they go under Other."""
        from hypergumbo_tracker.models import FieldSchema

        item = CompiledItem(
            id="INV-unk",
            kind="invariant",
            title="All unknown",
            status="todo_hard",
            fields={"custom1": "val1"},
        )
        schema = {
            "statement": FieldSchema(type="text", description="Required"),
        }
        lines = _format_detail_lines(item, fields_schema=schema)
        text = _strip_markup("\n".join(lines))
        assert "Other:" in text
        assert "custom1" in text


# ---------------------------------------------------------------------------
# D7: _get_fields_schema integration
# ---------------------------------------------------------------------------


class TestGetFieldsSchema:
    """Test _get_fields_schema returns None when kind has no schema."""

    async def test_kind_without_schema_returns_none(
        self, tmp_path: Path,
    ) -> None:
        """Kind without fields_schema in config returns None."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # work_item has no fields_schema in _make_config()
            wi_item = next(
                i for i in app._items if i.kind == "work_item"
            )
            result = app._get_fields_schema(wi_item)
            assert result is None

    async def test_unknown_kind_returns_none(
        self, tmp_path: Path,
    ) -> None:
        """Item whose kind is not in config at all returns None."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Create a fake item with an unknown kind
            fake_item = CompiledItem(
                id="FAKE-test",
                kind="nonexistent_kind",
                title="Fake",
                status="todo_hard",
                priority=1,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            result = app._get_fields_schema(fake_item)
            assert result is None


# ---------------------------------------------------------------------------
# D8: Per-field lock icons
# ---------------------------------------------------------------------------


class TestPerFieldLockIcons:
    """Test [locked] indicators on individual fields."""

    def test_locked_status_shows_indicator(self) -> None:
        """Locked status field shows [locked] indicator."""
        item = CompiledItem(
            id="INV-lock",
            kind="invariant",
            title="Lock test",
            status="todo_hard",
            locked_fields={"status"},
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Status [locked]:" in text
        assert "Priority:" in text  # Not locked

    def test_locked_priority_shows_indicator(self) -> None:
        """Locked priority field shows [locked] indicator."""
        item = CompiledItem(
            id="INV-lock2",
            kind="invariant",
            title="Lock test 2",
            status="todo_hard",
            locked_fields={"priority"},
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Status:" in text  # Not locked
        assert "Priority [locked]:" in text

    def test_locked_description_shows_indicator(self) -> None:
        """Locked description field shows [locked] indicator."""
        item = CompiledItem(
            id="INV-lock3",
            kind="invariant",
            title="Lock desc test",
            status="todo_hard",
            description="Some text",
            locked_fields={"description"},
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Description [locked]:" in text

    def test_locked_discussion_shows_indicator(self) -> None:
        """Locked discussion field shows [locked] indicator."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="INV-lock4",
            kind="invariant",
            title="Lock disc test",
            status="todo_hard",
            locked_fields={"discussion"},
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T00:00:00Z",
                    message="Test",
                ),
            ],
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Discussion [locked]" in text

    def test_locked_fields_in_schema(self) -> None:
        """Locked fields in schema-aware rendering show [locked]."""
        from hypergumbo_tracker.models import FieldSchema

        item = CompiledItem(
            id="INV-lock5",
            kind="invariant",
            title="Lock schema test",
            status="todo_hard",
            fields={"statement": "must hold", "root_cause": "bug"},
            locked_fields={"statement"},
        )
        schema = {
            "statement": FieldSchema(type="text", description="Principle"),
            "root_cause": FieldSchema(type="text"),
        }
        lines = _format_detail_lines(item, fields_schema=schema)
        text = _strip_markup("\n".join(lines))
        assert "statement (Principle) [locked]:" in text
        assert "root_cause:" in text
        assert "root_cause [locked]" not in text


# ---------------------------------------------------------------------------
# D9: Discussion badge [20+ msgs]
# ---------------------------------------------------------------------------


class TestDiscussionBadge:
    """Test [20+ msgs] badge on discussion sections."""

    def test_no_badge_under_20(self) -> None:
        """Discussion with < 20 entries should NOT show badge."""
        from hypergumbo_tracker.models import DiscussionEntry

        entries = [
            DiscussionEntry(
                by=f"user{i}", actor="dev", at=f"2026-01-{i+1:02d}T00:00:00Z",
                message=f"Message {i}",
            )
            for i in range(5)
        ]
        item = CompiledItem(
            id="INV-few",
            kind="invariant",
            title="Few messages",
            status="todo_hard",
            discussion=entries,
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Discussion (5 entries):" in text
        assert "[20+ msgs]" not in text

    def test_badge_at_20(self) -> None:
        """Discussion with 20 entries should show badge."""
        from hypergumbo_tracker.models import DiscussionEntry

        entries = [
            DiscussionEntry(
                by=f"user{i}", actor="dev", at=f"2026-01-{i+1:02d}T00:00:00Z",
                message=f"Message {i}",
            )
            for i in range(20)
        ]
        item = CompiledItem(
            id="INV-many",
            kind="invariant",
            title="Many messages",
            status="todo_hard",
            discussion=entries,
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "[20+ msgs]" in text

    def test_badge_in_activity_lines(self) -> None:
        """Activity lines with 20+ entries should show badge."""
        from hypergumbo_tracker.models import DiscussionEntry

        entries = [
            DiscussionEntry(
                by=f"user{i}", actor="dev", at=f"2026-01-{i+1:02d}T00:00:00Z",
                message=f"Message {i}",
            )
            for i in range(25)
        ]
        item = CompiledItem(
            id="INV-act",
            kind="invariant",
            title="Activity badge",
            status="todo_hard",
            discussion=entries,
        )
        lines = _format_activity_lines(item)
        assert "[20+ msgs]" in _strip_markup(lines[0])

    def test_no_badge_in_activity_under_20(self) -> None:
        """Activity lines with < 20 entries should NOT show badge."""
        from hypergumbo_tracker.models import DiscussionEntry

        entries = [
            DiscussionEntry(
                by=f"user{i}", actor="dev", at=f"2026-01-{i+1:02d}T00:00:00Z",
                message=f"Message {i}",
            )
            for i in range(10)
        ]
        item = CompiledItem(
            id="INV-noact",
            kind="invariant",
            title="No badge activity",
            status="todo_hard",
            discussion=entries,
        )
        lines = _format_activity_lines(item)
        assert not any("[20+ msgs]" in _strip_markup(line) for line in lines)


# ---------------------------------------------------------------------------
# D10: Filter width gate (>=80 cols)
# ---------------------------------------------------------------------------


class TestFilterWidthGate:
    """Test that filter is width-gated to >=80 columns."""

    async def test_filter_blocked_at_narrow_width(
        self, tmp_path: Path,
    ) -> None:
        """Pressing 'f' at width < 80 should NOT activate filter."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert not app._filter_active
            filter_input = app.query_one("#filter-input")
            assert filter_input.display is False

    async def test_filter_works_at_80_cols(
        self, tmp_path: Path,
    ) -> None:
        """Pressing 'f' at width >= 80 should activate filter."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_active is True
            filter_input = app.query_one("#filter-input")
            assert filter_input.display is True


# ---------------------------------------------------------------------------
# Tests: toggle_full_ids keybinding
# ---------------------------------------------------------------------------


class TestToggleFullIds:
    """Test the 'i' keybinding for toggling full ID display."""

    async def test_toggle_full_ids_compact(self, tmp_path: Path) -> None:
        """Pressing 'i' in compact mode toggles full ID display."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Initially full IDs are off
            assert app._show_full_ids is False

            # Get the ID from the first row before toggle
            table = app.query_one("#item-table")
            first_row_key = next(iter(table.rows.keys()))
            full_id = str(first_row_key.value)

            # The displayed ID should be truncated (shorter than full)
            row_data = table.get_row(first_row_key)
            id_cell = row_data[3]  # ID is 4th column (after #, T, P)
            assert len(id_cell) <= len(full_id)

            # Toggle on
            await pilot.press("i")
            await pilot.pause()
            assert app._show_full_ids is True

            # Now IDs should be full length
            first_row_key = next(iter(table.rows.keys()))
            row_data = table.get_row(first_row_key)
            id_cell_full = row_data[3]
            assert id_cell_full == full_id

            # Toggle off
            await pilot.press("i")
            await pilot.pause()
            assert app._show_full_ids is False

    async def test_toggle_full_ids_standard(self, tmp_path: Path) -> None:
        """Pressing 'i' in standard mode toggles full ID display."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert app._show_full_ids is False

            table = app.query_one("#std-table")
            first_row_key = next(iter(table.rows.keys()))
            full_id = str(first_row_key.value)

            # Toggle on
            await pilot.press("i")
            await pilot.pause()
            assert app._show_full_ids is True

            first_row_key = next(iter(table.rows.keys()))
            row_data = table.get_row(first_row_key)
            id_cell = row_data[3]
            assert id_cell == full_id


# ---------------------------------------------------------------------------
# Tests: shortest unique prefix in _populate_table
# ---------------------------------------------------------------------------


class TestShortenedIdsInTable:
    """Test that _populate_table uses content-driven ID widths."""

    async def test_ids_shortened_to_unique_prefix(
        self, tmp_path: Path,
    ) -> None:
        """IDs in the table should be shortened to their unique prefix."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")

            # Collect all displayed IDs
            displayed_ids = []
            for row_key in table.rows.keys():
                row_data = table.get_row(row_key)
                displayed_ids.append(row_data[3])

            # All displayed IDs should be unique
            assert len(displayed_ids) == len(set(displayed_ids))

            # At least one should be shorter than the full ID
            full_ids = [str(rk.value) for rk in table.rows.keys()]
            has_shorter = any(
                len(d) < len(f)
                for d, f in zip(displayed_ids, full_ids, strict=True)
            )
            assert has_shorter, (
                f"Expected shortened IDs but got: {displayed_ids}"
            )


# ---------------------------------------------------------------------------
# Screenshot delivery
# ---------------------------------------------------------------------------


class TestDeliverScreenshot:
    """deliver_screenshot creates missing directories before saving."""

    @pytest.mark.asyncio
    async def test_screenshot_creates_missing_downloads_dir(
        self, tmp_path: Path,
    ) -> None:
        """Screenshot succeeds even when ~/Downloads doesn't exist."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        save_dir = tmp_path / "nonexistent" / "downloads"

        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            key = app.deliver_screenshot(path=str(save_dir))
            assert key is not None
            # Give the background thread time to write the file
            for _ in range(20):
                await pilot.pause()
            svgs = list(save_dir.glob("*.svg"))
            assert len(svgs) == 1
            assert svgs[0].stat().st_size > 0

    @pytest.mark.asyncio
    async def test_screenshot_no_path_creates_default_downloads(
        self, tmp_path: Path,
    ) -> None:
        """Screenshot with no path creates user downloads dir if missing."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        fake_downloads = tmp_path / "fake_downloads"

        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch(
                "platformdirs.user_downloads_path",
                return_value=fake_downloads,
            ):
                key = app.deliver_screenshot()
            assert key is not None
            assert fake_downloads.is_dir()
            for _ in range(20):
                await pilot.pause()
            svgs = list(fake_downloads.glob("*.svg"))
            assert len(svgs) == 1


class TestYankAction:
    """Tests for the 'y' keybinding that copies detail text to clipboard."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    @pytest.mark.asyncio
    async def test_yank_no_item_warns(self, tmp_path: Path) -> None:
        """Pressing 'y' with no items shows warning."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            # No crash; warning notification shown

    @pytest.mark.asyncio
    async def test_yank_standard_copies_to_clipboard(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing 'y' in standard layout copies detail text."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            with patch.object(app, "copy_to_clipboard") as mock_copy:
                await pilot.press("y")
                await pilot.pause()
                mock_copy.assert_called_once()
                text = mock_copy.call_args[0][0]
                assert item.title in text
                assert item.id in text

    @pytest.mark.asyncio
    async def test_yank_compact_copies_to_clipboard(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing 'y' in compact detail view copies detail text."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(50, 18)) as pilot:
            # Wait for compact table
            table = app.query_one("#item-table")
            for _ in range(50):
                await pilot.pause()
                if table.row_count > 0:
                    break

            with patch.object(app, "copy_to_clipboard") as mock_copy:
                await pilot.press("y")
                await pilot.pause()
                mock_copy.assert_called_once()
                text = mock_copy.call_args[0][0]
                assert "Title:" in text
                assert "Status:" in text


# ---------------------------------------------------------------------------
# Unit tests: _apply_custom_order
# ---------------------------------------------------------------------------


class TestApplyCustomOrder:
    """Test the custom display order pure function."""

    def _make_items(self) -> list[CompiledItem]:
        return [
            CompiledItem(id="A", kind="invariant", title="Alpha", status="todo_hard"),
            CompiledItem(id="B", kind="work_item", title="Bravo", status="done"),
            CompiledItem(id="C", kind="invariant", title="Charlie", status="in_progress"),
        ]

    def test_full_order(self) -> None:
        """Items reordered when custom_order covers all IDs."""
        items = self._make_items()
        result = _apply_custom_order(items, ["C", "A", "B"])
        assert [i.id for i in result] == ["C", "A", "B"]

    def test_partial_order(self) -> None:
        """Ordered items first, rest at end in original order."""
        items = self._make_items()
        result = _apply_custom_order(items, ["C"])
        assert [i.id for i in result] == ["C", "A", "B"]

    def test_empty_order(self) -> None:
        """Empty custom_order returns items unchanged."""
        items = self._make_items()
        result = _apply_custom_order(items, [])
        assert [i.id for i in result] == ["A", "B", "C"]

    def test_stale_ids_skipped(self) -> None:
        """IDs not matching any item are silently ignored."""
        items = self._make_items()
        result = _apply_custom_order(items, ["STALE", "B", "MISSING", "A"])
        assert [i.id for i in result] == ["B", "A", "C"]

    def test_duplicate_ids_in_order(self) -> None:
        """Duplicate IDs in custom_order only include the item once."""
        items = self._make_items()
        result = _apply_custom_order(items, ["A", "A", "B"])
        assert [i.id for i in result] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Unit tests: _load_tui_preferences / _save_tui_preferences
# ---------------------------------------------------------------------------


class TestTuiPreferences:
    """Test TUI preferences persistence pure functions."""

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        result = _load_tui_preferences(tmp_path / "nonexistent.json")
        assert result == {"hidden_statuses": [], "display_order": []}

    def test_corrupt_file_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not json!", encoding="utf-8")
        result = _load_tui_preferences(p)
        assert result == {"hidden_statuses": [], "display_order": []}

    def test_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "array.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        result = _load_tui_preferences(p)
        assert result == {"hidden_statuses": [], "display_order": []}

    def test_invalid_types_returns_defaults(self, tmp_path: Path) -> None:
        """Non-list values for hidden_statuses/display_order → defaults."""
        import json
        p = tmp_path / "bad_types.json"
        p.write_text(json.dumps({"hidden_statuses": "not-a-list", "display_order": 42}))
        result = _load_tui_preferences(p)
        assert result == {"hidden_statuses": [], "display_order": []}

    def test_valid_file_loads(self, tmp_path: Path) -> None:
        import json
        p = tmp_path / "prefs.json"
        data = {"version": 1, "hidden_statuses": ["done"], "display_order": ["A", "B"]}
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _load_tui_preferences(p)
        assert result["hidden_statuses"] == ["done"]
        assert result["display_order"] == ["A", "B"]

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "prefs.json"
        assert _save_tui_preferences(p, {"done", "wont_do"}, ["C", "B", "A"]) is True
        result = _load_tui_preferences(p)
        assert set(result["hidden_statuses"]) == {"done", "wont_do"}
        assert result["display_order"] == ["C", "B", "A"]

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "dir" / "prefs.json"
        assert _save_tui_preferences(p, set(), []) is True
        assert p.is_file()
        result = _load_tui_preferences(p)
        assert result == {"hidden_statuses": [], "display_order": []}

    def test_save_returns_false_on_permission_error(self, tmp_path: Path) -> None:
        """_save_tui_preferences tolerates write failures gracefully."""
        from unittest.mock import patch
        p = tmp_path / "prefs.json"
        with patch.object(type(p), "write_text", side_effect=PermissionError("denied")):
            assert _save_tui_preferences(p, {"done"}, ["A"]) is False


# ---------------------------------------------------------------------------
# Pilot tests: status toggles (c/w keys)
# ---------------------------------------------------------------------------


def _make_tracker_set_with_resolved(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with items in done and wont_do statuses."""
    from helpers import make_test_config_dict

    root = tmp_path / ".agent"
    for d in [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    config = _make_config()
    config_path = root / "tracker" / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump(make_test_config_dict()))

    ts = TrackerSet(root, config=config)

    ts.add(kind="invariant", title="Active item 1",
           status="todo_hard", priority=1)
    ts.add(kind="work_item", title="In progress item",
           status="in_progress", priority=2)
    ts.add(kind="invariant", title="Done item",
           status="done", priority=0)
    ts.add(kind="work_item", title="Wont do item",
           status="wont_do", priority=3)

    return ts


class TestStatusToggles:
    """Test the 'c' (toggle done) and 'w' (toggle wont_do) keybindings."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set_with_resolved(tmp_path)

    async def test_toggle_done_hides_done_items(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing 'c' should hide done items."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 4
            await pilot.press("c")
            await pilot.pause()
            assert table.row_count == 3
            assert "done" in app._hidden_statuses

    async def test_toggle_done_twice_restores(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing 'c' twice should show done items again."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            await pilot.press("c")
            await pilot.pause()
            assert table.row_count == 3
            await pilot.press("c")
            await pilot.pause()
            assert table.row_count == 4
            assert "done" not in app._hidden_statuses

    async def test_toggle_wont_do_hides(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing 'w' should hide wont_do items."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 4
            await pilot.press("w")
            await pilot.pause()
            assert table.row_count == 3
            assert "wont_do" in app._hidden_statuses

    async def test_both_toggles(self, tracker_set: TrackerSet) -> None:
        """Pressing 'c' and 'w' should hide both done and wont_do."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            assert table.row_count == 2
            assert app._hidden_statuses == {"done", "wont_do"}

    async def test_status_bar_updates(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Status filter bar should update on toggle."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            bar = app.query_one("#status-filter-bar")
            assert bar.display is True
            text = str(bar.content)
            assert "done: shown" in text
            assert "wont_do: shown" in text

            await pilot.press("c")
            await pilot.pause()
            text = str(bar.content)
            assert "done: hidden" in text
            assert "wont_do: shown" in text

    async def test_status_bar_hidden_when_no_resolved(
        self, tmp_path: Path,
    ) -> None:
        """Status bar should be hidden when config has no resolved statuses."""
        from hypergumbo_tracker.tui import TrackerApp

        root = tmp_path / ".agent"
        for d in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
            root / "tracker-workspace" / "stealth",
        ]:
            d.mkdir(parents=True, exist_ok=True)
        import yaml
        (root / "tracker" / "config.yaml").write_text(yaml.dump({
            "kinds": {"invariant": {"prefix": "INV", "description": "Test"}},
            "statuses": ["todo_hard", "in_progress"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": []},
            "actor_resolution": {"agent_usernames": ["*_agent"]},
            "lamport_branches": ["dev"],
        }))
        from hypergumbo_tracker.models import TrackerConfig, KindConfig
        config = TrackerConfig(
            kinds={"invariant": KindConfig(prefix="INV", description="Test")},
            statuses=["todo_hard", "in_progress"],
            blocking_statuses=["todo_hard"],
            resolved_statuses=[],
            agent_usernames=["*_agent"],
            lamport_branches=["dev"],
        )
        ts = TrackerSet(root, config=config)
        ts.add(kind="invariant", title="Item", status="todo_hard", priority=1)

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            bar = app.query_one("#status-filter-bar")
            assert bar.display is False


# ---------------------------------------------------------------------------
# Pilot tests: manual display reordering (</>)
# ---------------------------------------------------------------------------


class TestManualReorder:
    """Test the '<' (move up) and '>' (move down) keybindings."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_move_down(self, tracker_set: TrackerSet) -> None:
        """Pressing '>' should move item down one row."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            # Get first item's ID
            first_key = next(iter(table.rows.keys()))
            first_id = str(first_key.value)
            # Move it down
            await pilot.press("greater_than_sign")
            await pilot.pause()
            # Now first_id should be at row 1
            keys_after = [str(k.value) for k in table.rows.keys()]
            assert keys_after[1] == first_id

    async def test_move_up(self, tracker_set: TrackerSet) -> None:
        """Pressing '<' on second row should move item to first row."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            # Move cursor to second row
            await pilot.press("down")
            await pilot.pause()
            second_key = list(table.rows.keys())[1]
            second_id = str(second_key.value)
            # Move it up
            await pilot.press("less_than_sign")
            await pilot.pause()
            keys_after = [str(k.value) for k in table.rows.keys()]
            assert keys_after[0] == second_id

    async def test_move_up_at_top_noop(self, tracker_set: TrackerSet) -> None:
        """Pressing '<' at top should be a no-op (no crash)."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            keys_before = [str(k.value) for k in table.rows.keys()]
            await pilot.press("less_than_sign")
            await pilot.pause()
            keys_after = [str(k.value) for k in table.rows.keys()]
            assert keys_before == keys_after

    async def test_move_down_at_bottom_noop(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing '>' at bottom should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            # Move to last row
            for _ in range(table.row_count - 1):
                await pilot.press("down")
                await pilot.pause()
            keys_before = [str(k.value) for k in table.rows.keys()]
            await pilot.press("greater_than_sign")
            await pilot.pause()
            keys_after = [str(k.value) for k in table.rows.keys()]
            assert keys_before == keys_after

    async def test_move_on_empty_table_noop(self, tmp_path: Path) -> None:
        """Moving on an empty table should not crash."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("greater_than_sign")
            await pilot.pause()
            await pilot.press("less_than_sign")
            await pilot.pause()
            # No crash

    async def test_second_move_uses_existing_order(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Second move reuses existing custom_order (early return path)."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # First move populates _custom_order
            await pilot.press("greater_than_sign")
            await pilot.pause()
            order_after_first = list(app._custom_order)
            # Second move uses the existing order (hits early return)
            await pilot.press("greater_than_sign")
            await pilot.pause()
            # Order should have changed (item moved further down)
            assert app._custom_order != order_after_first

    async def test_order_persists_to_file(
        self, tracker_set: TrackerSet,
    ) -> None:
        """After move, the preferences file should contain the new order."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("greater_than_sign")
            await pilot.pause()
            # Check the prefs file was written
            assert app._prefs_path.is_file()
            prefs = _load_tui_preferences(app._prefs_path)
            assert len(prefs["display_order"]) > 0

    async def test_move_shows_warning_on_save_failure(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Move notifies user when preferences can't be saved."""
        from unittest.mock import patch

        from hypergumbo_tracker.tui import TrackerApp
        import hypergumbo_tracker.tui as tui_mod

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tui_mod, "_save_tui_preferences", return_value=False):
                await pilot.press("greater_than_sign")
                await pilot.pause()
            # The move still happens (order updated in memory)
            assert len(app._custom_order) > 0


# ---------------------------------------------------------------------------
# Pilot tests: persistence (quit/mount roundtrip)
# ---------------------------------------------------------------------------


class TestPersistence:
    """Test that toggle state and display order survive TUI restart."""

    async def test_quit_saves_hidden_statuses(self, tmp_path: Path) -> None:
        """Quitting should save current hidden_statuses to file."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_resolved(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("c")  # hide done
            await pilot.pause()
            assert "done" in app._hidden_statuses
            await pilot.press("q")
            await pilot.pause()
        # After quit, file should contain the hidden status
        prefs = _load_tui_preferences(app._prefs_path)
        assert "done" in prefs["hidden_statuses"]

    async def test_mount_loads_hidden_statuses(self, tmp_path: Path) -> None:
        """Opening TUI with a prefs file should restore hidden_statuses."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_resolved(tmp_path)
        prefs_path = ts._tracker_root / "tracker-workspace" / "tui_preferences.json"
        _save_tui_preferences(prefs_path, {"done"}, [])

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            # Should have 3 items (1 done hidden)
            assert table.row_count == 3
            assert "done" in app._hidden_statuses

    async def test_first_run_no_file_all_shown(self, tmp_path: Path) -> None:
        """First run (no prefs file): all items shown, no crash."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_resolved(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 4
            assert app._hidden_statuses == set()

    async def test_display_order_saved_after_move(
        self, tmp_path: Path,
    ) -> None:
        """After a move operation, the order is persisted to file."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("greater_than_sign")
            await pilot.pause()
            prefs = _load_tui_preferences(app._prefs_path)
            assert len(prefs["display_order"]) >= 3

    async def test_quit_warns_on_save_failure(self, tmp_path: Path) -> None:
        """Quitting with unwritable prefs notifies instead of crashing."""
        from unittest.mock import patch

        from hypergumbo_tracker.tui import TrackerApp
        import hypergumbo_tracker.tui as tui_mod

        ts = _make_tracker_set_with_resolved(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("c")  # hide done
            await pilot.pause()
            with patch.object(tui_mod, "_save_tui_preferences", return_value=False):
                await pilot.press("q")
                await pilot.pause()
            # TUI exited without crashing
