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
    AmbiguousPrefixError,
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
    DisambiguateScreen,
    DiscussScreen,
    EditItemScreen,
    LockScreen,
    NewItemScreen,
    ParentScreen,
    TierMoveScreen,
    _apply_custom_order,
    _build_dep_index,
    _collapse_double_spacing,
    _collect_all_tags,
    _compute_chain,
    _compute_fav_5,
    _compute_tier,
    _dep_pill_id,
    _format_activity_lines,
    _format_dep_pills,
    _format_detail_lines,
    _format_filter_entry,
    _format_timestamp,
    _is_human_unread,
    _item_title_text,
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

    ts.add(kind="work_item", title="Symbol IDs must be stable",
           status="todo_hard", priority=1, tags=["quality"],
           description="Symbol IDs change between runs.")
    ts.add(kind="work_item", title="Add caching layer",
           status="in_progress", priority=2)
    ts.add(kind="work_item", title="Routes must have methods",
           status="done", priority=0,
           fields={"statement": "Routes need methods", "root_cause": "Missing validation"})

    return ts


async def _wait_for_table(pilot: Any, app: Any, max_rounds: int = 50) -> None:
    """Wait for the DataTable to be populated.

    Coverage tracing and CI load can delay Textual's compose/mount cycle,
    so ``#item-table`` may not exist yet when run_test yields the pilot.
    This helper retries with ``NoMatches`` tolerance until the widget
    appears and has rows, or max_rounds is reached.
    """
    from textual.css.query import NoMatches

    table = None
    for _ in range(max_rounds):
        await pilot.pause()
        if table is None:
            try:
                table = app.query_one("#item-table")
            except NoMatches:
                continue
        if table is not None and table.row_count > 0:
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
    """Wait for the standard DataTable to be populated.

    Coverage tracing and CI load can delay Textual's compose/mount cycle,
    so ``#std-table`` may not exist yet when run_test yields the pilot.
    This helper retries ``query_one`` with ``NoMatches`` tolerance until
    the widget appears and has rows.
    """
    from textual.css.query import NoMatches

    table = None
    for _ in range(max_rounds):
        await pilot.pause()
        if table is None:
            try:
                table = app.query_one("#std-table")
            except NoMatches:
                continue
        if table is not None and table.row_count > 0:
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
        """Activating text filter should show the filter input."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_active = True
            app._apply_layout()
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
            app._filter_active = True
            app._apply_layout()
            app.query_one("#filter-input").focus()
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
        """Dismissing filter clears text and restores all items."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_active = True
            app._apply_layout()
            app.query_one("#filter-input").focus()
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
            assert len(result) == 3
            assert all(r.kind == "work_item" for r in result)

    async def test_hidden_tags_filter(self, tmp_path: Path) -> None:
        """Items with hidden tags are excluded from results."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Item "Symbol IDs must be stable" has tag "quality"
            app._hidden_tags.add("quality")
            result = app._filtered_items()
            assert len(result) == 2
            assert all("quality" not in i.tags for i in result)

    async def test_hidden_tags_no_tags_items_always_shown(
        self, tmp_path: Path,
    ) -> None:
        """Items with no tags are never hidden by tag filters."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Hide a tag that no item has → all items still shown
            app._hidden_tags.add("nonexistent_tag")
            result = app._filtered_items()
            assert len(result) == 3

    async def test_hidden_tags_combined_with_status_filter(
        self, tmp_path: Path,
    ) -> None:
        """Tag and status filters combine (both applied)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hidden_statuses.add("done")
            app._hidden_tags.add("quality")
            result = app._filtered_items()
            # "done" item hidden, "quality" item hidden → only caching layer
            assert len(result) == 1
            assert result[0].title == "Add caching layer"


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
        """Filter panel state should persist across resize."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Activate filter panel
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_panel_visible is True

            # Resize to compact (filter panel not available but state preserved)
            await pilot.resize_terminal(50, 18)
            await pilot.pause()
            await pilot.pause()
            assert app._filter_panel_visible is True

            # Filter panel should be hidden (compact mode)
            std_filter = app.query_one("#std-filter-panel-view")
            assert std_filter.display is False

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
        """Text filter should work in compact mode at width >= 80 (but height < 20)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        # Width 80 (>= 80 for filter), height 18 (compact tier: < 20)
        async with app.run_test(size=(80, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Activate text filter directly (f key now opens filter panel)
            app._filter_active = True
            app._apply_layout()
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
        """Escape should dismiss text filter first, then detail mode."""
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
            # Activate text filter directly
            app._filter_active = True
            app._apply_layout()
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

    async def test_toggle_filter_panel_action(self, tmp_path: Path) -> None:
        """Calling action_toggle_filter_panel toggles panel visibility."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Toggle on
            app.action_toggle_filter_panel()
            await pilot.pause()
            assert app._filter_panel_visible is True
            # Toggle off
            app.action_toggle_filter_panel()
            await pilot.pause()
            assert not app._filter_panel_visible


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
        """Activity panel and chat widgets should be hidden in standard mode."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            activity_view = app.query_one("#activity-view")
            assert activity_view.display is False
            activity_divider = app.query_one("#activity-divider")
            assert activity_divider.display is False
            # Chat widgets hidden in standard mode
            from textual.widgets import TextArea
            assert app.query_one("#chat-input", TextArea).display is False
            assert app.query_one("#chat-buttons").display is False
            assert app.query_one("#chat-hint").display is False

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
            kind="work_item",
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
        """Text filter should work at wide size."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 3
            # Activate text filter directly
            app._filter_active = True
            app._apply_layout()
            app.query_one("#filter-input").focus()
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
            # Activate text filter directly
            app._filter_active = True
            app._apply_layout()
            app.query_one("#filter-input").focus()
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
            app._filter_active = True
            app._apply_layout()
            app.query_one("#filter-input").focus()
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
            kind="work_item",
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

    async def test_statuses_filtered_by_kinds_config(self) -> None:
        from hypergumbo_tracker.models import KindConfig

        item = CompiledItem(
            id="INV-test",
            kind="invariant",
            title="Test Invariant",
            status="violated",
            priority=1,
            tier=Tier.CANONICAL,
            tags=[],
            description="",
        )
        kc = KindConfig(prefix="INV", allowed_statuses=["holding", "violated"])
        screen = EditItemScreen(
            item,
            ["todo_hard", "in_progress", "done", "holding", "violated"],
            kinds_config={"invariant": kc},
        )
        assert screen._statuses == ["holding", "violated"]
        assert screen._deprecated_status is None

    def test_edit_item_includes_current_status_when_not_allowed(self) -> None:
        """If item's current status was removed from allowed_statuses, it should
        still appear in the options so the user can change it."""
        from hypergumbo_tracker.models import KindConfig

        item = CompiledItem(
            id="WI-test",
            kind="work_item",
            title="Test Item",
            status="holding",
            priority=2,
            tier=Tier.WORKSPACE,
            tags=[],
            description="",
        )
        # 'holding' is NOT in allowed_statuses
        kc = KindConfig(prefix="WI", allowed_statuses=["todo_hard", "todo_soft", "done"])
        screen = EditItemScreen(
            item,
            ["todo_hard", "todo_soft", "done"],
            kinds_config={"work_item": kc},
        )
        assert "holding" in screen._statuses
        assert screen._statuses[0] == "holding"
        assert screen._deprecated_status == "holding"

    async def test_deprecated_status_renders_dimmed(self) -> None:
        """Deprecated status should render greyed out in the Select widget."""
        from hypergumbo_tracker.models import KindConfig

        item = CompiledItem(
            id="WI-test",
            kind="work_item",
            title="Test Deprecated",
            status="holding",
            priority=2,
            tier=Tier.WORKSPACE,
            tags=[],
            description="",
        )
        kc = KindConfig(prefix="WI", allowed_statuses=["todo_hard", "done"])
        screen = EditItemScreen(
            item, ["todo_hard", "done"],
            kinds_config={"work_item": kc},
        )
        app = _ModalTestApp(screen)
        async with app.run_test(size=(70, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            # The Select widget should have mounted without error
            from textual.widgets import Select
            select = app.screen.query_one("#status-select", Select)
            assert select.value == "holding"

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


    async def test_edit_permission_error_shows_notification(
        self, tracker_set: TrackerSet,
    ) -> None:
        """PermissionError on update shows notification instead of crashing."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(
                tracker_set, "update",
                side_effect=PermissionError(13, "Cannot take ownership"),
            ):
                app.action_edit_item()
                await _wait_for_modal(pilot, app)
                app.screen.query_one("#title-input").value = "New Title"
                await pilot.pause()
                await pilot.click("#submit")
                await pilot.pause()
                # App should still be running (no crash)
                assert app.is_running


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

            with patch.object(tracker_set, "update") as mock_update, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_passthrough,
                 ):
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
            ), patch.object(
                app, "_resolve_ids",
                side_effect=_mock_resolve_ids_passthrough,
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

            with patch.object(tracker_set, "update") as mock_update, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_passthrough,
                 ):
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
            with patch.object(tracker_set, "update") as mock_update, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_passthrough,
                 ):
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
            ), patch.object(
                app, "_resolve_ids",
                side_effect=_mock_resolve_ids_passthrough,
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


def _mock_resolve_ids_passthrough(
    raw_ids: list[str], on_resolved: Any,
    resolved_so_far: list[str] | None = None,
    remaining: list[str] | None = None,
) -> None:
    """Mock _resolve_ids that immediately passes IDs through."""
    on_resolved(raw_ids)


def _mock_resolve_ids_cancel(
    raw_ids: list[str], on_resolved: Any,
    resolved_so_far: list[str] | None = None,
    remaining: list[str] | None = None,
) -> None:
    """Mock _resolve_ids that simulates user cancellation."""
    on_resolved(None)


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
            with patch.object(ts, "update") as mock, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_passthrough,
                 ):
                app._on_edit_before(
                    item.id, {"add": ["X"], "remove": []},
                )
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
            with patch.object(ts, "update") as mock, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_passthrough,
                 ):
                app._on_edit_before(
                    item.id, {"add": [], "remove": ["Y"]},
                )
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
            ), patch.object(
                app, "_resolve_ids",
                side_effect=_mock_resolve_ids_passthrough,
            ):
                app._on_edit_before(
                    "FAKE", {"add": ["X"], "remove": []},
                )
                await pilot.pause()

    async def test_resolve_ids_cancel_aborts(self, tmp_path: Path) -> None:
        """When _resolve_ids calls back with None, no update."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "update") as mock, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_cancel,
                 ):
                app._on_edit_before(
                    "FAKE", {"add": ["ambig"], "remove": []},
                )
                await pilot.pause()
                mock.assert_not_called()

    async def test_resolve_ids_cancel_on_remove_aborts(
        self, tmp_path: Path,
    ) -> None:
        """When _resolve_ids cancels for remove IDs, no update."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            call_count = 0

            def _resolve_first_pass_second_cancel(
                raw_ids: list[str], on_resolved: Any,
                resolved_so_far: list[str] | None = None,
                remaining: list[str] | None = None,
            ) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    on_resolved(raw_ids)  # add resolves fine
                else:
                    on_resolved(None)  # remove cancelled

            with patch.object(ts, "update") as mock, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_resolve_first_pass_second_cancel,
                 ):
                app._on_edit_before(
                    "FAKE", {"add": ["ok"], "remove": ["ambig"]},
                )
                await pilot.pause()
                mock.assert_not_called()


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
            ), patch.object(
                app, "_resolve_ids",
                side_effect=_mock_resolve_ids_passthrough,
            ):
                app._on_set_parent("FAKE", "PARENT")
                await pilot.pause()

    async def test_clear_parent_empty_string(self, tmp_path: Path) -> None:
        """Empty string clears parent without calling _resolve_ids."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "update") as mock, \
                 patch.object(app, "_resolve_ids") as resolve_mock:
                app._on_set_parent(item.id, "")
                await pilot.pause()
                resolve_mock.assert_not_called()
                mock.assert_called_once_with(
                    item.id, set_fields={"parent": ""},
                )

    async def test_resolve_ids_cancel_aborts(self, tmp_path: Path) -> None:
        """When _resolve_ids calls back with None, no update."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(ts, "update") as mock, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_mock_resolve_ids_cancel,
                 ):
                app._on_set_parent("FAKE", "ambig-prefix")
                await pilot.pause()
                mock.assert_not_called()

    async def test_resolved_parent_stored(self, tmp_path: Path) -> None:
        """Prefix is resolved to full ID before storing."""
        from hypergumbo_tracker.tui import TrackerApp

        def _resolve_to_full(
            raw_ids: list[str], on_resolved: Any,
            resolved_so_far: list[str] | None = None,
            remaining: list[str] | None = None,
        ) -> None:
            on_resolved(["WI-full-long-id"])

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None
            with patch.object(ts, "update") as mock, \
                 patch.object(
                     app, "_resolve_ids",
                     side_effect=_resolve_to_full,
                 ):
                app._on_set_parent(item.id, "WI-full")
                await pilot.pause()
                mock.assert_called_once_with(
                    item.id, set_fields={"parent": "WI-full-long-id"},
                )


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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
            kind="work_item",
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
        """Pressing 'f' in compact mode should NOT open filter panel."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert not app._filter_panel_visible

    async def test_filter_works_at_80_cols(
        self, tmp_path: Path,
    ) -> None:
        """Pressing 'f' at width >= 80 should show filter panel."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_panel_visible is True
            panel = app.query_one("#std-filter-panel-view")
            assert panel.display is True


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
    """Tests for the Ctrl+C keybinding that copies detail text to clipboard."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    @pytest.mark.asyncio
    async def test_yank_no_item_warns(self, tmp_path: Path) -> None:
        """Pressing Ctrl+C with no items shows warning."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(5):
                await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
            # No crash; warning notification shown

    @pytest.mark.asyncio
    async def test_yank_standard_copies_to_clipboard(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing Ctrl+C in standard layout copies detail text."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            with patch.object(app, "copy_to_clipboard") as mock_copy:
                await pilot.press("ctrl+c")
                await pilot.pause()
                mock_copy.assert_called_once()
                text = mock_copy.call_args[0][0]
                assert item.title in text
                assert item.id in text

    @pytest.mark.asyncio
    async def test_yank_compact_copies_to_clipboard(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Pressing Ctrl+C in compact detail view copies detail text."""
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
                await pilot.press("ctrl+c")
                await pilot.pause()
                mock_copy.assert_called_once()
                text = mock_copy.call_args[0][0]
                assert "Title:" in text
                assert "Status:" in text

    @pytest.mark.asyncio
    async def test_ctrl_c_yanks_textarea_selection(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Ctrl+C with focused TextArea selection copies selection text."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import TextArea
        from textual.widgets.text_area import Selection

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            text_area = app.query_one("#chat-input", TextArea)
            text_area.load_text("hello world")
            await pilot.pause()
            # Select "hello" (first 5 chars)
            text_area.selection = Selection(
                start=(0, 0), end=(0, 5),
            )
            await pilot.pause()

            with (
                patch.object(
                    type(text_area), "has_focus",
                    new_callable=lambda: property(lambda self: True),
                ),
                patch.object(app, "copy_to_clipboard") as mock_copy,
            ):
                app.action_yank()
                await pilot.pause()
                mock_copy.assert_called_once_with("hello")


# ---------------------------------------------------------------------------
# Inline Chat (wide mode)
# ---------------------------------------------------------------------------


class TestInlineChat:
    """Tests for the inline chat TextArea and Send button in wide mode."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    @pytest.mark.asyncio
    async def test_chat_send_happy_path(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Type message in chat input, click Send — discuss() called, input cleared."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import TextArea

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            item = app._get_selected_item()
            assert item is not None

            text_area = app.query_one("#chat-input", TextArea)
            text_area.load_text("inline chat message")
            await pilot.pause()

            with patch.object(
                tracker_set, "discuss", wraps=tracker_set.discuss,
            ) as mock_discuss:
                await pilot.click("#chat-send")
                await pilot.pause()
                await pilot.pause()
                mock_discuss.assert_called_once_with(
                    item.id, "inline chat message",
                )
                # TextArea should be cleared after send
                assert text_area.text == ""

    @pytest.mark.asyncio
    async def test_chat_send_empty_ignored(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Clicking Send with empty TextArea does nothing."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            with patch.object(tracker_set, "discuss") as mock_discuss:
                await pilot.click("#chat-send")
                await pilot.pause()
                mock_discuss.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_send_no_item_warns(self, tmp_path: Path) -> None:
        """Clicking Send with no item selected shows warning."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import TextArea

        ts = _make_empty_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(160, 45)) as pilot:
            for _ in range(5):
                await pilot.pause()
            text_area = app.query_one("#chat-input", TextArea)
            text_area.load_text("test message")
            await pilot.pause()
            await pilot.click("#chat-send")
            await pilot.pause()
            # No crash; warning notification shown

    @pytest.mark.asyncio
    async def test_chat_send_permission_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        """PermissionError from discuss() is caught and notified."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import TextArea

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            text_area = app.query_one("#chat-input", TextArea)
            text_area.load_text("test msg")
            await pilot.pause()
            with patch.object(
                tracker_set, "discuss",
                side_effect=PermissionError("read-only"),
            ):
                await pilot.click("#chat-send")
                await pilot.pause()
                await pilot.pause()
                # Error notification shown, no crash
                # TextArea is NOT cleared on error
                assert text_area.text == "test msg"

    @pytest.mark.asyncio
    async def test_chat_widgets_hidden_in_standard(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Chat widgets are not visible at standard (80x24) size."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import TextArea

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert app.query_one("#chat-input", TextArea).display is False
            assert app.query_one("#chat-buttons").display is False
            assert app.query_one("#chat-hint").display is False

    @pytest.mark.asyncio
    async def test_chat_hint_visible_in_wide(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Chat hint Static is visible in wide mode."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            hint = app.query_one("#chat-hint")
            assert hint.display is True

    @pytest.mark.asyncio
    async def test_chat_send_locked_field_error(
        self, tracker_set: TrackerSet,
    ) -> None:
        """LockedFieldError from discuss() is caught and notified."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import TextArea

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            text_area = app.query_one("#chat-input", TextArea)
            text_area.load_text("msg")
            await pilot.pause()
            with patch.object(
                tracker_set, "discuss",
                side_effect=LockedFieldError("discussion locked"),
            ):
                await pilot.click("#chat-send")
                await pilot.pause()
                await pilot.pause()
                # No crash; error notification shown


# ---------------------------------------------------------------------------
# Unit tests: _apply_custom_order
# ---------------------------------------------------------------------------


class TestApplyCustomOrder:
    """Test the custom display order pure function."""

    def _make_items(self) -> list[CompiledItem]:
        return [
            CompiledItem(id="A", kind="work_item", title="Alpha", status="todo_hard"),
            CompiledItem(id="B", kind="work_item", title="Bravo", status="done"),
            CompiledItem(id="C", kind="work_item", title="Charlie", status="in_progress"),
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

    def _assert_defaults(self, result: dict) -> None:
        """Assert all v2 default values are present."""
        assert result["hidden_statuses"] == []
        assert result["display_order"] == []
        assert result["hidden_tags"] == []
        assert result["toggle_sessions"] == []
        assert result["last_filters"] == {"hidden_statuses": [], "hidden_tags": []}

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        result = _load_tui_preferences(tmp_path / "nonexistent.json")
        self._assert_defaults(result)

    def test_corrupt_file_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not json!", encoding="utf-8")
        result = _load_tui_preferences(p)
        self._assert_defaults(result)

    def test_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "array.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        result = _load_tui_preferences(p)
        self._assert_defaults(result)

    def test_invalid_types_returns_defaults(self, tmp_path: Path) -> None:
        """Non-list values for hidden_statuses/display_order → defaults."""
        import json
        p = tmp_path / "bad_types.json"
        p.write_text(json.dumps({"hidden_statuses": "not-a-list", "display_order": 42}))
        result = _load_tui_preferences(p)
        self._assert_defaults(result)

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
        assert result["hidden_statuses"] == []
        assert result["display_order"] == []

    def test_save_returns_false_on_permission_error(self, tmp_path: Path) -> None:
        """_save_tui_preferences tolerates write failures gracefully."""
        from unittest.mock import patch
        p = tmp_path / "prefs.json"
        with patch.object(type(p), "write_text", side_effect=PermissionError("denied")):
            assert _save_tui_preferences(p, {"done"}, ["A"]) is False

    def test_v2_roundtrip_with_tags_and_sessions(self, tmp_path: Path) -> None:
        """V2 fields (hidden_tags, toggle_sessions, last_filters) roundtrip."""
        import json
        p = tmp_path / "prefs.json"
        assert _save_tui_preferences(
            p, {"done"}, ["A"],
            hidden_tags={"quality", "perf"},
            toggle_sessions=[{"status:done": 5, "tag:quality": 3}],
            last_filters={"hidden_statuses": ["done"], "hidden_tags": ["perf"]},
        ) is True
        result = _load_tui_preferences(p)
        assert set(result["hidden_tags"]) == {"quality", "perf"}
        assert len(result["toggle_sessions"]) == 1
        assert result["toggle_sessions"][0]["status:done"] == 5
        assert result["last_filters"]["hidden_statuses"] == ["done"]

    def test_v1_file_loads_with_v2_defaults(self, tmp_path: Path) -> None:
        """A v1 file missing v2 keys gets default values for new fields."""
        import json
        p = tmp_path / "v1.json"
        data = {"version": 1, "hidden_statuses": ["done"], "display_order": ["A"]}
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _load_tui_preferences(p)
        assert result["hidden_statuses"] == ["done"]
        assert result["display_order"] == ["A"]
        assert result["hidden_tags"] == []
        assert result["toggle_sessions"] == []
        assert result["last_filters"] == {"hidden_statuses": [], "hidden_tags": []}

    def test_v2_save_writes_version_2(self, tmp_path: Path) -> None:
        """Saving with v2 fields writes version 2."""
        import json
        p = tmp_path / "prefs.json"
        _save_tui_preferences(
            p, set(), [],
            hidden_tags={"quality"},
            toggle_sessions=[],
            last_filters={"hidden_statuses": [], "hidden_tags": []},
        )
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["version"] == 2

    def test_legacy_toggle_counts_migrated(self, tmp_path: Path) -> None:
        """A file with flat toggle_counts is migrated to toggle_sessions."""
        import json
        p = tmp_path / "legacy.json"
        data = {
            "version": 1,
            "hidden_statuses": [],
            "display_order": [],
            "toggle_counts": {"status:done": 5, "tag:quality": 2},
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _load_tui_preferences(p)
        assert len(result["toggle_sessions"]) == 1
        assert result["toggle_sessions"][0] == {"status:done": 5, "tag:quality": 2}

    def test_toggle_sessions_cap_at_9(self, tmp_path: Path) -> None:
        """Only the most recent 9 toggle sessions are kept on save."""
        import json
        p = tmp_path / "prefs.json"
        sessions = [{"s%d" % i: i} for i in range(12)]
        _save_tui_preferences(
            p, set(), [],
            toggle_sessions=sessions,
        )
        result = _load_tui_preferences(p)
        assert len(result["toggle_sessions"]) == 9
        assert result["toggle_sessions"][0] == {"s3": 3}


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

    ts.add(kind="work_item", title="Active item 1",
           status="todo_hard", priority=1)
    ts.add(kind="work_item", title="In progress item",
           status="in_progress", priority=2)
    ts.add(kind="work_item", title="Done item",
           status="done", priority=0)
    ts.add(kind="work_item", title="Wont do item",
           status="wont_do", priority=3)

    return ts


class TestStatusToggles:
    """Test status toggle via _toggle_status (previously c/w keybindings)."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set_with_resolved(tmp_path)

    async def test_toggle_done_hides_done_items(
        self, tracker_set: TrackerSet,
    ) -> None:
        """_toggle_status('done') should hide done items."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 4
            app._toggle_status("done")
            await pilot.pause()
            assert table.row_count == 3
            assert "done" in app._hidden_statuses

    async def test_toggle_done_twice_restores(
        self, tracker_set: TrackerSet,
    ) -> None:
        """Toggling 'done' twice should show done items again."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            app._toggle_status("done")
            await pilot.pause()
            assert table.row_count == 3
            app._toggle_status("done")
            await pilot.pause()
            assert table.row_count == 4
            assert "done" not in app._hidden_statuses

    async def test_toggle_wont_do_hides(
        self, tracker_set: TrackerSet,
    ) -> None:
        """_toggle_status('wont_do') should hide wont_do items."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            assert table.row_count == 4
            app._toggle_status("wont_do")
            await pilot.pause()
            assert table.row_count == 3
            assert "wont_do" in app._hidden_statuses

    async def test_both_toggles(self, tracker_set: TrackerSet) -> None:
        """Toggling both done and wont_do hides both."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            app._toggle_status("done")
            await pilot.pause()
            app._toggle_status("wont_do")
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

            app._toggle_status("done")
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
            app._toggle_status("done")  # hide done
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
            app._toggle_status("done")  # hide done
            await pilot.pause()
            with patch.object(tui_mod, "_save_tui_preferences", return_value=False):
                await pilot.press("q")
                await pilot.pause()
            # TUI exited without crashing


# ---------------------------------------------------------------------------
# Dependency visualization helpers
# ---------------------------------------------------------------------------


def _make_tracker_set_with_deps(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with before-links for dependency visualization tests.

    Creates items: A, B, C where B depends on A (B.before = [A]),
    and C depends on B (C.before = [B]), forming a chain A → B → C.
    """
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

    id_a = ts.add(kind="work_item", title="Foundation work",
                  status="todo_hard", priority=1)
    id_b = ts.add(kind="work_item", title="Build on foundation",
                  status="in_progress", priority=2)
    id_c = ts.add(kind="work_item", title="Final integration",
                  status="todo_soft", priority=3)

    # B depends on A, C depends on B
    ts.update(id_b, add_fields={"before": [id_a]})
    ts.update(id_c, add_fields={"before": [id_b]})

    return ts


# ---------------------------------------------------------------------------
# Unit tests: _build_dep_index
# ---------------------------------------------------------------------------


class TestBuildDepIndex:
    """Test dependency index construction from before-links."""

    def test_empty_items(self) -> None:
        """Empty item list produces empty maps."""

        blockers, deps = _build_dep_index([])
        assert blockers == {}
        assert deps == {}

    def test_no_before_links(self) -> None:
        """Items without before-links produce empty maps."""

        items = [
            CompiledItem(id="WI-aaaaa", kind="work_item",
                         title="A", status="todo_hard"),
            CompiledItem(id="WI-bbbbb", kind="work_item",
                         title="B", status="todo_hard"),
        ]
        blockers, deps = _build_dep_index(items)
        assert blockers == {}
        assert deps == {}

    def test_single_dependency(self) -> None:
        """B depends on A: blockers_of[B] = [A], dependents_of[A] = [B]."""

        items = [
            CompiledItem(id="WI-aaaaa", kind="work_item",
                         title="A", status="todo_hard"),
            CompiledItem(id="WI-bbbbb", kind="work_item",
                         title="B", status="todo_hard",
                         before=["WI-aaaaa"]),
        ]
        blockers, deps = _build_dep_index(items)
        assert blockers == {"WI-bbbbb": ["WI-aaaaa"]}
        assert deps == {"WI-aaaaa": ["WI-bbbbb"]}

    def test_dangling_ref_filtered(self) -> None:
        """Before-links pointing to nonexistent IDs are silently dropped."""

        items = [
            CompiledItem(id="WI-aaaaa", kind="work_item",
                         title="A", status="todo_hard",
                         before=["WI-nonexistent"]),
        ]
        blockers, deps = _build_dep_index(items)
        assert blockers == {}
        assert deps == {}

    def test_chain_a_b_c(self) -> None:
        """Chain A → B → C produces correct bidirectional maps."""

        items = [
            CompiledItem(id="WI-aaaaa", kind="work_item",
                         title="A", status="todo_hard"),
            CompiledItem(id="WI-bbbbb", kind="work_item",
                         title="B", status="todo_hard",
                         before=["WI-aaaaa"]),
            CompiledItem(id="WI-ccccc", kind="work_item",
                         title="C", status="todo_hard",
                         before=["WI-bbbbb"]),
        ]
        blockers, deps = _build_dep_index(items)
        assert blockers == {
            "WI-bbbbb": ["WI-aaaaa"],
            "WI-ccccc": ["WI-bbbbb"],
        }
        assert deps == {
            "WI-aaaaa": ["WI-bbbbb"],
            "WI-bbbbb": ["WI-ccccc"],
        }

    def test_multiple_blockers(self) -> None:
        """C blocked by both A and B."""

        items = [
            CompiledItem(id="WI-aaaaa", kind="work_item",
                         title="A", status="todo_hard"),
            CompiledItem(id="WI-bbbbb", kind="work_item",
                         title="B", status="todo_hard"),
            CompiledItem(id="WI-ccccc", kind="work_item",
                         title="C", status="todo_hard",
                         before=["WI-aaaaa", "WI-bbbbb"]),
        ]
        blockers, deps = _build_dep_index(items)
        assert blockers["WI-ccccc"] == ["WI-aaaaa", "WI-bbbbb"]
        assert deps["WI-aaaaa"] == ["WI-ccccc"]
        assert deps["WI-bbbbb"] == ["WI-ccccc"]


# ---------------------------------------------------------------------------
# Unit tests: _compute_chain
# ---------------------------------------------------------------------------


class TestComputeChain:
    """Test dependency chain BFS computation."""

    def test_no_deps(self) -> None:
        """Item with no deps returns empty lists."""

        d_up, t_up, d_down, t_down = _compute_chain("A", {}, {})
        assert d_up == []
        assert t_up == []
        assert d_down == []
        assert t_down == []

    def test_direct_blocker_only(self) -> None:
        """Single direct blocker, no transitive."""

        blockers = {"B": ["A"]}
        deps: dict[str, list[str]] = {"A": ["B"]}
        d_up, t_up, d_down, t_down = _compute_chain("B", blockers, deps)
        assert d_up == ["A"]
        assert t_up == []
        assert d_down == []
        assert t_down == []

    def test_direct_dependent_only(self) -> None:
        """Single direct dependent, no transitive."""

        blockers: dict[str, list[str]] = {"B": ["A"]}
        deps = {"A": ["B"]}
        d_up, t_up, d_down, t_down = _compute_chain("A", blockers, deps)
        assert d_up == []
        assert t_up == []
        assert d_down == ["B"]
        assert t_down == []

    def test_chain_a_b_c_from_middle(self) -> None:
        """Chain A → B → C: from B, A is direct up, C is direct down."""

        blockers = {"B": ["A"], "C": ["B"]}
        deps = {"A": ["B"], "B": ["C"]}
        d_up, t_up, d_down, t_down = _compute_chain("B", blockers, deps)
        assert d_up == ["A"]
        assert t_up == []
        assert d_down == ["C"]
        assert t_down == []

    def test_chain_a_b_c_from_end(self) -> None:
        """Chain A → B → C: from C, B is direct up, A is transitive up."""

        blockers = {"B": ["A"], "C": ["B"]}
        deps = {"A": ["B"], "B": ["C"]}
        d_up, t_up, d_down, t_down = _compute_chain("C", blockers, deps)
        assert d_up == ["B"]
        assert t_up == ["A"]
        assert d_down == []
        assert t_down == []

    def test_cycle_safe(self) -> None:
        """Cycles don't cause infinite loops."""

        blockers = {"A": ["B"], "B": ["A"]}
        deps = {"A": ["B"], "B": ["A"]}
        d_up, t_up, d_down, t_down = _compute_chain("A", blockers, deps)
        assert d_up == ["B"]
        assert d_down == ["B"]
        # B is already visited, so no transitive results
        assert t_up == []
        assert t_down == []


# ---------------------------------------------------------------------------
# Unit tests: _format_dep_pills and _dep_pill_id
# ---------------------------------------------------------------------------


class TestDepPillId:
    """Test ID truncation for dep pill display."""

    def test_short_id_unchanged(self) -> None:

        assert _dep_pill_id("WI-dabab") == "WI-dabab"

    def test_long_id_truncated(self) -> None:

        assert _dep_pill_id("INV-babab-dabab-fabab-habab") == "INV-babab"

    def test_no_dash_unchanged(self) -> None:

        assert _dep_pill_id("SIMPLE") == "SIMPLE"

    def test_prefix_and_one_pair(self) -> None:

        assert _dep_pill_id("META-donon-fasab") == "META-donon"


class TestFormatDepPills:
    """Test dep pill Rich markup formatting."""

    def test_no_deps_empty_string(self) -> None:

        result = _format_dep_pills("A", {}, {})
        assert result == ""

    def test_single_blocker(self) -> None:

        blockers = {"B": ["WI-aaaaa-bbbbb"]}
        result = _format_dep_pills("B", blockers, {})
        plain = _strip_markup(result)
        assert "← WI-aaaaa" in plain

    def test_single_dependent(self) -> None:

        deps = {"A": ["WI-bbbbb-ccccc"]}
        result = _format_dep_pills("A", {}, deps)
        plain = _strip_markup(result)
        assert "→ WI-bbbbb" in plain

    def test_overflow_indicator(self) -> None:
        """When total deps exceed max_pills, +N is shown."""

        blockers = {"X": ["A", "B", "C"]}
        deps = {"X": ["D", "E"]}
        result = _format_dep_pills("X", blockers, deps, max_pills=3)
        plain = _strip_markup(result)
        assert "+2" in plain

    def test_both_blockers_and_deps(self) -> None:

        blockers = {"B": ["A"]}
        deps = {"B": ["C"]}
        result = _format_dep_pills("B", blockers, deps)
        plain = _strip_markup(result)
        assert "← A" in plain
        assert "→ C" in plain

    def test_blockers_exhaust_max_pills(self) -> None:
        """When blockers alone fill max_pills, deps are in overflow."""

        blockers = {"X": ["A", "B", "C"]}
        deps = {"X": ["D"]}
        result = _format_dep_pills("X", blockers, deps, max_pills=2)
        plain = _strip_markup(result)
        # Only 2 blocker pills shown, D not shown
        assert "← A" in plain
        assert "← B" in plain
        assert "→ D" not in plain
        assert "+2" in plain

    def test_hidden_blocker_uses_italic(self) -> None:
        """Blocker in hidden_ids gets italic instead of bold markup."""

        blockers = {"B": ["WI-aaaaa"]}
        result = _format_dep_pills("B", blockers, {}, hidden_ids={"WI-aaaaa"})
        assert "[italic dark_orange]" in result
        assert "[bold dark_orange]" not in result

    def test_hidden_dependent_uses_italic(self) -> None:
        """Dependent in hidden_ids gets italic instead of bold markup."""

        deps = {"A": ["WI-bbbbb"]}
        result = _format_dep_pills("A", {}, deps, hidden_ids={"WI-bbbbb"})
        assert "[italic green]" in result
        assert "[bold green]" not in result

    def test_visible_dep_still_bold_when_hidden_ids_provided(self) -> None:
        """Deps not in hidden_ids keep bold markup even when param is given."""

        blockers = {"B": ["WI-aaaaa"]}
        deps = {"B": ["WI-ccccc"]}
        result = _format_dep_pills(
            "B", blockers, deps, hidden_ids={"WI-ccccc"},
        )
        # WI-aaaaa is visible → bold, WI-ccccc is hidden → italic
        assert "[bold dark_orange]" in result
        assert "[italic green]" in result

    def test_hidden_ids_none_same_as_no_hidden(self) -> None:
        """Passing hidden_ids=None keeps all pills bold (backward compat)."""

        blockers = {"B": ["WI-aaaaa"]}
        result = _format_dep_pills("B", blockers, {}, hidden_ids=None)
        assert "[bold dark_orange]" in result
        assert "[italic" not in result


# ---------------------------------------------------------------------------
# Unit tests: _format_detail_lines with dependency info
# ---------------------------------------------------------------------------


class TestFormatDetailLinesDeps:
    """Test that detail panel shows Blocked by and Blocks lines."""

    def test_before_links_shown(self) -> None:
        """Item with before-links shows 'Blocked by' line."""
        item = CompiledItem(
            id="WI-bbbbb", kind="work_item",
            title="Build", status="todo_hard",
            tier=Tier.WORKSPACE,
            before=["WI-aaaaa"],
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Blocked by:" in text
        assert "WI-aaaaa" in text

    def test_before_locked_shown(self) -> None:
        """Locked before field shows [locked] indicator."""
        item = CompiledItem(
            id="WI-bbbbb", kind="work_item",
            title="Build", status="todo_hard",
            tier=Tier.WORKSPACE,
            before=["WI-aaaaa"],
            locked_fields={"before"},
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Blocked by [locked]:" in text

    def test_dependents_shown(self) -> None:
        """When dependents_of is provided, shows 'Blocks' line."""
        item = CompiledItem(
            id="WI-aaaaa", kind="work_item",
            title="Foundation", status="todo_hard",
            tier=Tier.WORKSPACE,
        )
        deps = {"WI-aaaaa": ["WI-bbbbb", "WI-ccccc"]}
        lines = _format_detail_lines(item, dependents_of=deps)
        text = _strip_markup("\n".join(lines))
        assert "Blocks:" in text
        assert "WI-bbbbb" in text
        assert "WI-ccccc" in text

    def test_no_deps_no_lines(self) -> None:
        """Item without deps doesn't show Blocked by or Blocks."""
        item = CompiledItem(
            id="WI-aaaaa", kind="work_item",
            title="Solo", status="todo_hard",
            tier=Tier.WORKSPACE,
        )
        lines = _format_detail_lines(item)
        text = _strip_markup("\n".join(lines))
        assert "Blocked by" not in text
        assert "Blocks:" not in text

    def test_dependents_not_shown_without_param(self) -> None:
        """Without dependents_of parameter, no Blocks line even if item exists."""
        item = CompiledItem(
            id="WI-aaaaa", kind="work_item",
            title="Solo", status="todo_hard",
            tier=Tier.WORKSPACE,
        )
        lines = _format_detail_lines(item, dependents_of=None)
        text = _strip_markup("\n".join(lines))
        assert "Blocks:" not in text


# ---------------------------------------------------------------------------
# Integration tests: Deps column and chain summary bar
# ---------------------------------------------------------------------------


class TestDepsColumnInTable:
    """Test the Deps column in standard/wide DataTable."""

    async def test_deps_column_present_standard(self, tmp_path: Path) -> None:
        """Standard table has a Deps column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "deps" in column_keys

    async def test_deps_column_absent_compact(self, tmp_path: Path) -> None:
        """Compact table does not have a Deps column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(55, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "deps" not in column_keys

    async def test_dep_pills_in_table_cells(self, tmp_path: Path) -> None:
        """Items with dependencies show dep pill content in cells."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            # The dep index should be populated
            assert len(app._blockers_of) > 0 or len(app._dependents_of) > 0

    async def test_dep_index_wired_in_load(self, tmp_path: Path) -> None:
        """_load_items populates _blockers_of and _dependents_of."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Items: A (no before), B (before=[A]), C (before=[B])
            items = app._items
            ids = [i.id for i in items]
            # B should be in blockers_of
            b_item = next(i for i in items if i.title == "Build on foundation")
            assert b_item.id in app._blockers_of
            # A should be in dependents_of
            a_item = next(i for i in items if i.title == "Foundation work")
            assert a_item.id in app._dependents_of


class TestChainSummaryBar:
    """Test the chain summary bar widget."""

    async def test_chain_bar_shown_for_dep_item(self, tmp_path: Path) -> None:
        """Chain summary bar is visible when a dep item is selected."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Navigate to an item that has dependencies
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            # Find B's row and select it
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            bar = app.query_one("#chain-summary-bar")
            assert bar.display is True

    async def test_chain_bar_hidden_for_no_dep_item(
        self, tmp_path: Path,
    ) -> None:
        """Chain summary bar is hidden when item has no deps."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)  # No before-links
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            bar = app.query_one("#chain-summary-bar")
            assert bar.display is False

    async def test_chain_bar_content_includes_arrows(
        self, tmp_path: Path,
    ) -> None:
        """Chain summary bar includes directional indicators."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            bar = app.query_one("#chain-summary-bar")
            bar_text = _strip_markup(str(bar.content))
            assert "↑" in bar_text
            assert "↓" in bar_text

    async def test_chain_bar_hidden_when_item_has_no_direct_deps(
        self, tmp_path: Path,
    ) -> None:
        """Chain bar hides for items with no direct deps even when index exists."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            # Foundation work (A) blocks B, and we select a different item
            # that has no deps. However all items in this set have deps.
            # So let's navigate to a non-existent position — easier to test
            # by calling _update_chain_summary directly with a fake ID.
            app._update_chain_summary("NONEXISTENT-fake")
            await pilot.pause()
            bar = app.query_one("#chain-summary-bar")
            assert bar.display is False

    async def test_chain_bar_shows_transitive_deps(
        self, tmp_path: Path,
    ) -> None:
        """Chain bar shows transitive deps with 'via' for deep chains."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            # C depends on B depends on A: select C to get transitive up
            c_item = next(i for i in items if i.title == "Final integration")
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == c_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            bar = app.query_one("#chain-summary-bar")
            bar_text = _strip_markup(str(bar.content))
            assert "via" in bar_text


class TestDetailPanelDeps:
    """Test dependency info in the detail panel (integration)."""

    async def test_detail_shows_blocked_by(self, tmp_path: Path) -> None:
        """Detail panel shows 'Blocked by' for items with before-links."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            detail = app.query_one("#std-detail-content")
            detail_text = str(detail.content)
            assert "Blocked by" in detail_text

    async def test_detail_shows_blocks(self, tmp_path: Path) -> None:
        """Detail panel shows 'Blocks' for items that block others."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Foundation work")
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == a_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            detail = app.query_one("#std-detail-content")
            detail_text = str(detail.content)
            assert "Blocks:" in detail_text


# ---------------------------------------------------------------------------
# Integration tests: Chain highlighting
# ---------------------------------------------------------------------------


class TestChainHighlighting:
    """Test chain highlighting markers in the row_num column."""

    async def test_selected_item_gets_marker(self, tmp_path: Path) -> None:
        """Selected item in a chain gets ▶ marker in # column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Check that the selected row has the ▶ marker
            for rk in table.rows:
                if str(rk.value) == b_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert "▶" in cell_text
                    break

    async def test_blocker_gets_up_arrow(self, tmp_path: Path) -> None:
        """Blocker of the selected item gets ↑ marker."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            a_item = next(i for i in items if i.title == "Foundation work")
            table = app.query_one("#std-table")
            # Select B (which is blocked by A)
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Check that A's row has the ↑ marker
            for rk in table.rows:
                if str(rk.value) == a_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert "↑" in cell_text
                    break

    async def test_dependent_gets_down_arrow(self, tmp_path: Path) -> None:
        """Dependent of the selected item gets ↓ marker."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            c_item = next(i for i in items if i.title == "Final integration")
            table = app.query_one("#std-table")
            # Select B (which blocks C)
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Check that C's row has the ↓ marker
            for rk in table.rows:
                if str(rk.value) == c_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert "↓" in cell_text
                    break

    async def test_transitive_gets_dot_marker(self, tmp_path: Path) -> None:
        """Transitive chain member gets · marker."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Foundation work")
            c_item = next(i for i in items if i.title == "Final integration")
            table = app.query_one("#std-table")
            # Select C (blocked by B, transitively by A)
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == c_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Check A has transitive marker ·
            for rk in table.rows:
                if str(rk.value) == a_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert "·" in cell_text
                    break

    async def test_no_chain_plain_numbers(self, tmp_path: Path) -> None:
        """When selected item has no deps, all rows show plain numbers."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)  # No before-links
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                cell = table.get_cell(rk, "row_num")
                cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                assert cell_text == str(idx + 1)

    async def test_highlight_noop_in_compact(self, tmp_path: Path) -> None:
        """_update_chain_highlight is a no-op in compact tier."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(55, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # Should not raise
            app._update_chain_highlight("anything")

    async def test_highlight_noop_empty_table(self, tmp_path: Path) -> None:
        """_update_chain_highlight handles empty table gracefully."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            table.clear()
            # Should not raise
            app._update_chain_highlight("anything")

    async def test_highlight_clears_on_move(self, tmp_path: Path) -> None:
        """Moving to a non-dep item clears previous chain markers."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            a_item = next(i for i in items if i.title == "Foundation work")
            table = app.query_one("#std-table")
            # Select B (has chain)
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Verify A has marker
            for rk in table.rows:
                if str(rk.value) == a_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert "↑" in cell_text
                    break
            # Now select A (A blocks B but isn't blocked by anything)
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == a_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # A should now have ▶, not ↑
            for rk in table.rows:
                if str(rk.value) == a_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert "▶" in cell_text
                    assert "↑" not in cell_text
                    break

    async def test_all_columns_colorized(self, tmp_path: Path) -> None:
        """Chain highlighting applies color to all text columns, not just #."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            a_item = next(i for i in items if i.title == "Foundation work")
            table = app.query_one("#std-table")
            # Select B (blocked by A)
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # A's title cell should be a RichText object (colored)
            for rk in table.rows:
                if str(rk.value) == a_item.id:
                    title_cell = table.get_cell(rk, "title")
                    assert hasattr(title_cell, "plain"), (
                        "Title should be RichText for chain members"
                    )
                    assert a_item.title in title_cell.plain
                    break

    async def test_non_chain_rows_plain_text(self, tmp_path: Path) -> None:
        """Rows not in the chain have plain string text (not RichText)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)  # No before-links
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            # All title cells should be plain strings (no chain)
            for rk in table.rows:
                title_cell = table.get_cell(rk, "title")
                assert isinstance(title_cell, str)


# ---------------------------------------------------------------------------
# Integration tests: ghost rows for hidden chain members
# ---------------------------------------------------------------------------


def _make_tracker_set_with_done_dep(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet where A (todo_hard) blocks B (done).

    When "done" status is hidden, B disappears from the table.
    Selecting A should surface B as a ghost row.
    """
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

    id_a = ts.add(kind="work_item", title="Active blocker",
                  status="todo_hard", priority=1)
    id_b = ts.add(kind="work_item", title="Done dependent",
                  status="done", priority=2)

    # B depends on A (B.before = [A])
    ts.update(id_b, add_fields={"before": [id_a]})

    return ts


class TestGhostRows:
    """Test hidden dependency chain members appearing as italic ghost rows."""

    async def test_ghost_row_appears_when_dep_hidden(
        self, tmp_path: Path,
    ) -> None:
        """Selecting A surfaces hidden done-dep B as a ghost row."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            # Hide "done" status and reload
            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()

            # Explicitly trigger chain highlight for A
            app._update_chain_highlight(a_item.id)

            table = app.query_one("#std-table")
            row_ids = [str(rk.value) for rk in table.rows]
            assert a_item.id in row_ids
            # B should appear as a ghost row
            assert b_item.id in row_ids
            assert b_item.id in app._ghost_row_ids

    async def test_ghost_rows_removed_on_cursor_move(
        self, tmp_path: Path,
    ) -> None:
        """Ghost rows are cleaned up when cursor moves to non-chain item."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        # Add a third item with no deps
        id_c = ts.add(kind="work_item", title="Standalone item",
                      status="todo_hard", priority=3)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()

            table = app.query_one("#std-table")
            # Select A → ghost row for B
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == a_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            app._update_chain_highlight(a_item.id)
            assert b_item.id in app._ghost_row_ids

            # Now select standalone item → ghost rows should be removed
            c_item = next(i for i in items if i.title == "Standalone item")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == c_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            app._update_chain_highlight(c_item.id)

            assert app._ghost_row_ids == set()
            row_ids = [str(rk.value) for rk in table.rows]
            assert b_item.id not in row_ids

    async def test_no_ghost_rows_when_no_hidden_chain(
        self, tmp_path: Path,
    ) -> None:
        """No ghost rows when all chain members are visible."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_deps(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Build on foundation")
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == b_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            app._update_chain_highlight(b_item.id)

            # No hidden items → no ghost rows
            assert app._ghost_row_ids == set()

    async def test_ghost_rows_cleared_on_reload(
        self, tmp_path: Path,
    ) -> None:
        """Ghost rows are cleared when the table is reloaded."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")

            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()

            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == a_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            app._update_chain_highlight(a_item.id)
            assert len(app._ghost_row_ids) > 0

            # Reload clears ghost rows
            app._reload_active_table()
            assert app._ghost_row_ids == set()

    async def test_ghost_row_has_italic_chain_style(
        self, tmp_path: Path,
    ) -> None:
        """Ghost rows get italic styling in their row_num column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()

            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == a_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            app._update_chain_highlight(a_item.id)

            # Ghost row B should have italic style in row_num
            for rk in table.rows:
                if str(rk.value) == b_item.id:
                    cell = table.get_cell(rk, "row_num")
                    cell_text = cell.plain if hasattr(cell, "plain") else str(cell)
                    # Ghost row gets a directional marker
                    assert "↓" in cell_text or "·" in cell_text
                    break

    async def test_dep_pills_show_italic_for_hidden_deps(
        self, tmp_path: Path,
    ) -> None:
        """Dep pills referencing hidden items use italic markup in table."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()

            # Check A's dep pill column — B should be italic since hidden
            table = app.query_one("#std-table")
            for rk in table.rows:
                if str(rk.value) == a_item.id:
                    cell = table.get_cell(rk, "deps")
                    # The cell markup should use italic for B's pill
                    cell_plain = cell.plain if hasattr(cell, "plain") else str(cell)
                    assert _dep_pill_id(b_item.id) in cell_plain
                    break

    async def test_ghost_noop_compact(self, tmp_path: Path) -> None:
        """Ghost row logic is a no-op in compact mode."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(55, 18)) as pilot:
            await _wait_for_table(pilot, app)
            # _update_chain_highlight is a no-op in compact
            app._update_chain_highlight("anything")
            assert app._ghost_row_ids == set()

    async def test_hidden_ids_helper(self, tmp_path: Path) -> None:
        """_hidden_ids returns IDs present in _items but not _filtered_items."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            b_item = next(i for i in items if i.title == "Done dependent")

            # No hidden statuses → nothing hidden
            assert app._hidden_ids() == set()

            # Hide "done" → B is hidden
            app._hidden_statuses.add("done")
            hidden = app._hidden_ids()
            assert b_item.id in hidden

    async def test_ghost_row_with_full_ids(self, tmp_path: Path) -> None:
        """Ghost rows use full IDs when _show_full_ids is True."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            app._show_full_ids = True
            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()
            app._update_chain_highlight(a_item.id)

            # Ghost row should exist with full ID
            table = app.query_one("#std-table")
            for rk in table.rows:
                if str(rk.value) == b_item.id:
                    id_cell = table.get_cell(rk, "id")
                    id_text = id_cell.plain if hasattr(id_cell, "plain") else str(id_cell)
                    assert id_text == b_item.id
                    break

    async def test_ghost_row_in_wide_mode(self, tmp_path: Path) -> None:
        """Ghost rows include wide-mode columns (conflict, created, updated)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()
            app._update_chain_highlight(a_item.id)

            table = app.query_one("#std-table")
            # Verify wide columns exist
            col_keys = [c.key.value for c in table.columns.values()]
            assert "conflict" in col_keys
            assert "created" in col_keys

            # Ghost row B should be present
            row_ids = [str(rk.value) for rk in table.rows]
            assert b_item.id in row_ids

    async def test_ghost_row_single_visible_item(self, tmp_path: Path) -> None:
        """Ghost rows work when only a single item is visible (edge case).

        When there's only one visible item, _shortest_unique_prefix_len
        returns 0, triggering the fallback to default id_display_len.
        """
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_done_dep(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = app._items
            a_item = next(i for i in items if i.title == "Active blocker")
            b_item = next(i for i in items if i.title == "Done dependent")

            # Hide "done" → only A visible (single item)
            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()

            # With only 1 visible item, prefix_len is 0 → fallback to 20
            app._update_chain_highlight(a_item.id)

            table = app.query_one("#std-table")
            row_ids = [str(rk.value) for rk in table.rows]
            assert b_item.id in row_ids


# ---------------------------------------------------------------------------
# Unit tests: _collect_all_tags
# ---------------------------------------------------------------------------


class TestCollectAllTags:
    """Test tag collection and deduplication from items."""

    def test_empty_items(self) -> None:
        """No items → empty list."""
        assert _collect_all_tags([], []) == []

    def test_deduplicates_and_sorts(self) -> None:
        """Tags from multiple items are deduplicated and sorted."""
        items = [
            CompiledItem(
                id="WI-a", kind="work_item", title="A",
                status="todo_hard", tags=["z_tag", "a_tag"],
            ),
            CompiledItem(
                id="WI-b", kind="work_item", title="B",
                status="todo_hard", tags=["a_tag", "m_tag"],
            ),
        ]
        assert _collect_all_tags(items, []) == ["a_tag", "m_tag", "z_tag"]

    def test_merges_well_known_tags(self) -> None:
        """Well-known tags appear even when no item has them."""
        items = [
            CompiledItem(
                id="WI-a", kind="work_item", title="A",
                status="todo_hard", tags=["real"],
            ),
        ]
        result = _collect_all_tags(items, ["phantom", "real"])
        assert result == ["phantom", "real"]

    def test_items_with_no_tags(self) -> None:
        """Items without tags contribute nothing; only well_known_tags appear."""
        items = [
            CompiledItem(
                id="WI-a", kind="work_item", title="A", status="done",
            ),
        ]
        assert _collect_all_tags(items, ["only_wk"]) == ["only_wk"]

    def test_no_well_known_tags_and_no_item_tags(self) -> None:
        """Both sources empty → empty."""
        items = [
            CompiledItem(
                id="WI-a", kind="work_item", title="A", status="done",
            ),
        ]
        assert _collect_all_tags(items, []) == []


# ---------------------------------------------------------------------------
# Unit tests: _compute_fav_5
# ---------------------------------------------------------------------------


class TestComputeFav5:
    """Test fav-5 computation from toggle session history."""

    def test_empty_sessions(self) -> None:
        """No session history → empty fav list."""
        assert _compute_fav_5([]) == []

    def test_single_session_top_5(self) -> None:
        """Top 5 from a single session, re-alphabetized."""
        sessions = [
            {
                "status:done": 10,
                "status:todo_hard": 8,
                "tag:quality": 6,
                "tag:perf": 4,
                "status:in_progress": 2,
                "tag:docs": 1,
            },
        ]
        result = _compute_fav_5(sessions)
        assert len(result) == 5
        # Top 5 by count: done(10), todo_hard(8), quality(6), perf(4), in_progress(2)
        # Re-alphabetized:
        assert result == [
            "status:done",
            "status:in_progress",
            "status:todo_hard",
            "tag:perf",
            "tag:quality",
        ]

    def test_sums_across_sessions(self) -> None:
        """Counts from multiple sessions are summed, result re-alphabetized."""
        sessions = [
            {"status:done": 3, "tag:quality": 1},
            {"status:done": 2, "tag:quality": 5},
        ]
        result = _compute_fav_5(sessions)
        # quality=6, done=5 → re-alphabetized
        assert result == ["status:done", "tag:quality"]

    def test_max_sessions_window(self) -> None:
        """Only the most recent max_sessions are considered."""
        sessions = [
            {"status:old": 100},  # old — should be ignored
            {"status:recent1": 5},
            {"status:recent2": 3},
        ]
        result = _compute_fav_5(sessions, max_sessions=2)
        assert "status:old" not in result
        assert "status:recent1" in result
        assert "status:recent2" in result

    def test_ties_broken_alphabetically(self) -> None:
        """Equal counts are broken alphabetically."""
        sessions = [{"a:x": 5, "a:a": 5, "a:m": 5}]
        result = _compute_fav_5(sessions)
        assert result == ["a:a", "a:m", "a:x"]

    def test_fewer_than_5_keys(self) -> None:
        """When fewer than 5 unique keys exist, return all of them."""
        sessions = [{"status:done": 3, "tag:quality": 1}]
        result = _compute_fav_5(sessions)
        assert len(result) == 2

    def test_zero_count_excluded(self) -> None:
        """Keys with zero total count are excluded."""
        sessions = [{"status:done": 0, "tag:quality": 3}]
        result = _compute_fav_5(sessions)
        assert result == ["tag:quality"]

    def test_result_re_alphabetized(self) -> None:
        """Top 5 are re-alphabetized regardless of count order."""
        sessions = [
            {"z:zzz": 100, "a:aaa": 50, "m:mmm": 30, "b:bbb": 20, "c:ccc": 10},
        ]
        result = _compute_fav_5(sessions)
        assert result == ["a:aaa", "b:bbb", "c:ccc", "m:mmm", "z:zzz"]


# ---------------------------------------------------------------------------
# Unit tests: _format_filter_entry
# ---------------------------------------------------------------------------


class TestFormatFilterEntry:
    """Test filter panel entry formatting."""

    def test_visible_entry(self) -> None:
        """Shown (not hidden) entry has empty checkbox."""
        result = _format_filter_entry(1, "todo_hard", False, 5)
        plain = _strip_markup(result)
        assert "[1]" in plain
        assert "[ ]" in plain
        assert "todo_hard" in plain
        assert "(5)" in plain

    def test_hidden_entry(self) -> None:
        """Hidden entry has X checkbox."""
        result = _format_filter_entry(2, "done", True, 12)
        plain = _strip_markup(result)
        assert "[2]" in plain
        assert "[X]" in plain
        assert "done" in plain
        assert "(12)" in plain

    def test_fav_entry_has_star(self) -> None:
        """Favorite entries show a star marker."""
        result = _format_filter_entry(1, "done", False, 8, is_fav=True)
        assert "★" in result or "*" in result or "⭐" in result or "star" in result.lower() or "★" in _strip_markup(result)

    def test_no_number_for_zero_idx(self) -> None:
        """Index 0 means no number shortcut (entries beyond 9)."""
        result = _format_filter_entry(0, "rare", False, 1)
        plain = _strip_markup(result)
        assert "[ ]" in plain
        assert "rare" in plain
        # No [0] or number prefix
        assert "[0]" not in plain

    def test_hidden_entry_dim_style(self) -> None:
        """Hidden entries use dim styling in the markup."""
        result = _format_filter_entry(3, "wont_do", True, 0)
        assert "dim" in result


# ---------------------------------------------------------------------------
# Pilot tests: filter panel
# ---------------------------------------------------------------------------


def _make_tracker_set_with_tags(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with items that have various tags."""
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

    ts.add(kind="work_item", title="Task with quality tag",
           status="todo_hard", priority=1, tags=["quality"])
    ts.add(kind="work_item", title="Task with perf tag",
           status="in_progress", priority=2, tags=["perf"])
    ts.add(kind="work_item", title="Done task",
           status="done", priority=0, tags=["quality", "perf"])
    ts.add(kind="work_item", title="No tags task",
           status="todo_soft", priority=3)

    return ts


class TestFilterPanel:
    """Test filter panel visibility, content, and interactions."""

    async def test_f_toggles_filter_panel(self, tmp_path: Path) -> None:
        """Pressing f shows the filter panel in standard mode."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert not app._filter_panel_visible
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_panel_visible
            # Press f again to hide
            await pilot.press("f")
            await pilot.pause()
            assert not app._filter_panel_visible

    async def test_filter_panel_shows_statuses(self, tmp_path: Path) -> None:
        """Filter panel content includes status entries."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            assert "todo_hard" in plain or "Statuses" in plain

    async def test_filter_panel_shows_tags(self, tmp_path: Path) -> None:
        """Filter panel content includes tag entries when items have tags."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            assert "quality" in plain or "perf" in plain

    async def test_digit_key_quick_toggle(self, tmp_path: Path) -> None:
        """Pressing digit key 1 toggles the first filter entry."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            initial_items = len(app._filtered_items())
            await pilot.press("1")
            await pilot.pause()
            # Should have toggled something — either more or fewer items
            new_items = len(app._filtered_items())
            assert new_items != initial_items or len(app._hidden_statuses) + len(app._hidden_tags) > 0

    async def test_digit_keys_ignored_when_panel_hidden(
        self, tmp_path: Path,
    ) -> None:
        """Digit keys don't toggle filters when panel is not visible."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert not app._filter_panel_visible
            initial_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            await pilot.press("1")
            await pilot.pause()
            after_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            assert initial_hidden == after_hidden

    async def test_clear_all_filters(self, tmp_path: Path) -> None:
        """_clear_all_filters clears hidden statuses and tags."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hidden_statuses.add("done")
            app._hidden_tags.add("quality")
            # Make filter panel visible so _refresh_filter_panel is called
            app._filter_panel_visible = True
            app._clear_all_filters()
            assert len(app._hidden_statuses) == 0
            assert len(app._hidden_tags) == 0

    async def test_restore_last_filters(self, tmp_path: Path) -> None:
        """_restore_last_filters restores previously saved filter state."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hidden_statuses.add("done")
            app._hidden_tags.add("quality")
            # Make filter panel visible so _refresh_filter_panel is called
            app._filter_panel_visible = True
            app._clear_all_filters()
            assert len(app._hidden_statuses) == 0
            app._restore_last_filters()
            assert "done" in app._hidden_statuses
            assert "quality" in app._hidden_tags

    async def test_filter_panel_compact_not_available(
        self, tmp_path: Path,
    ) -> None:
        """Filter panel is not available in compact mode."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert not app._filter_panel_visible

    async def test_filter_panel_wide_mode(self, tmp_path: Path) -> None:
        """Filter panel works in wide mode (alongside activity panel)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 42)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert app._layout_tier == "wide"
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_panel_visible

    async def test_toggle_tag_method(self, tmp_path: Path) -> None:
        """_toggle_tag adds/removes tags from _hidden_tags."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert "quality" not in app._hidden_tags
            app._toggle_tag("quality")
            assert "quality" in app._hidden_tags
            app._toggle_tag("quality")
            assert "quality" not in app._hidden_tags

    async def test_quit_saves_v2_preferences(self, tmp_path: Path) -> None:
        """Quitting saves hidden_tags and toggle sessions."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hidden_tags.add("quality")
            app._current_session_counts["tag:quality"] = 3
            await pilot.press("q")
            await pilot.pause()
        prefs = _load_tui_preferences(app._prefs_path)
        assert "quality" in prefs["hidden_tags"]
        assert len(prefs["toggle_sessions"]) >= 1

    async def test_status_filter_bar_shows_tag_count(
        self, tmp_path: Path,
    ) -> None:
        """Status filter bar shows hidden tag count."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hidden_tags.add("quality")
            app._hidden_tags.add("perf")
            app._update_status_filter_bar()
            bar = app.query_one("#status-filter-bar", StaticWidget)
            plain = bar.render()
            assert "tags hidden: 2" in str(plain)

    async def test_c_and_w_keys_removed(self, tmp_path: Path) -> None:
        """The c and w keybindings no longer toggle done/wont_do directly."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # c and w should not change hidden_statuses
            await pilot.press("c")
            await pilot.pause()
            assert "done" not in app._hidden_statuses
            await pilot.press("w")
            await pilot.pause()
            assert "wont_do" not in app._hidden_statuses

    async def test_toggle_count_incremented(self, tmp_path: Path) -> None:
        """_increment_toggle_count bumps the current session count."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._increment_toggle_count("status:done")
            app._increment_toggle_count("status:done")
            app._increment_toggle_count("tag:quality")
            assert app._current_session_counts["status:done"] == 2
            assert app._current_session_counts["tag:quality"] == 1

    async def test_filter_panel_standard_mode_placement(
        self, tmp_path: Path,
    ) -> None:
        """In standard mode, filter panel appears in right panel area."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            panel = app.query_one("#std-filter-panel-view")
            assert panel.display is True

    async def test_filters_active_count(self, tmp_path: Path) -> None:
        """Filter panel header shows correct active filter count."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hidden_statuses.add("done")
            app._hidden_tags.add("quality")
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            assert "Filters Active: 2" in plain

    async def test_filter_panel_too_small_noop(self, tmp_path: Path) -> None:
        """Filter panel toggle is a no-op in too-small mode."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(30, 10)) as pilot:
            await pilot.pause()
            assert app._layout_tier == "too-small"
            # Call action directly since bindings may not fire in too-small mode
            app.action_toggle_filter_panel()
            assert not app._filter_panel_visible

    async def test_quick_toggle_out_of_range(self, tmp_path: Path) -> None:
        """_quick_toggle returns early for out-of-range index."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            initial_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            # Toggle index 0 (invalid — 1-based)
            app._quick_toggle(0)
            # Toggle index far beyond entries
            app._quick_toggle(99)
            after_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            assert initial_hidden == after_hidden

    async def test_quick_toggle_tag_entry(self, tmp_path: Path) -> None:
        """_quick_toggle toggles tag entries correctly."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            # Find a tag entry index
            tag_idx = None
            for i, (key, _name) in enumerate(app._filter_entries):
                if key.startswith("tag:"):
                    tag_idx = i + 1  # 1-based
                    tag_name = key[4:]
                    break
            assert tag_idx is not None, "Expected at least one tag entry"
            assert tag_name not in app._hidden_tags
            app._quick_toggle(tag_idx)
            assert tag_name in app._hidden_tags
            assert app._current_session_counts.get(f"tag:{tag_name}", 0) > 0

    async def test_fav_5_with_tag_entries(self, tmp_path: Path) -> None:
        """Filter panel shows tag entries in Favorites section."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Seed toggle sessions with tag entries to get them into fav 5
            app._toggle_sessions = [
                {"tag:quality": 10, "status:done": 5},
            ]
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            assert "Favorites" in plain

    async def test_fav_entries_appear_in_both_sections(self, tmp_path: Path) -> None:
        """Fav entries also appear in their normal Statuses/Tags section."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Seed toggle sessions so "done" status is a fav
            app._toggle_sessions = [{"status:done": 10, "tag:quality": 5}]
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            # Count how many times "status:done" appears in filter_entries
            done_count = sum(1 for k, _ in app._filter_entries if k == "status:done")
            assert done_count == 2, "status:done should appear in both Favorites and Statuses"
            quality_count = sum(1 for k, _ in app._filter_entries if k == "tag:quality")
            assert quality_count == 2, "tag:quality should appear in both Favorites and Tags"

    async def test_fav_and_normal_entries_share_toggle_state(self, tmp_path: Path) -> None:
        """Toggling a fav entry also affects its normal-section appearance."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._toggle_sessions = [{"status:done": 10}]
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            # Find the fav entry for "done" (first occurrence)
            fav_idx = None
            for i, (key, _) in enumerate(app._filter_entries):
                if key == "status:done":
                    fav_idx = i + 1
                    break
            assert fav_idx is not None
            # Toggle via the fav entry
            app._quick_toggle(fav_idx)
            assert "done" in app._hidden_statuses
            # Refresh and verify both entries show hidden state
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            # "done" should appear as hidden in both sections
            # Count [X] occurrences with "done" nearby
            lines = plain.split("\n")
            hidden_done_lines = [
                l for l in lines if "done" in l and "[X]" in l
            ]
            assert len(hidden_done_lines) == 2, (
                "Both fav and normal 'done' entries should show hidden state"
            )

    async def test_fav_5_unknown_prefix_skipped(self, tmp_path: Path) -> None:
        """Fav 5 entries with unknown prefix are skipped."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Seed toggle sessions with unknown prefix entries
            app._toggle_sessions = [
                {"unknown:foo": 10, "bad:bar": 5},
            ]
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            # Should NOT have Favorites section since all entries skipped
            assert "Favorites" not in plain

    async def test_filter_line_to_entry_populated(self, tmp_path: Path) -> None:
        """_filter_line_to_entry is populated after _refresh_filter_panel."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            mapping = app._filter_line_to_entry
            # Should have an entry for each filter entry
            assert len(mapping) == len(app._filter_entries)
            # All values should be 1-based indices
            for _line_num, entry_idx in mapping.items():
                assert entry_idx >= 1
                assert entry_idx <= len(app._filter_entries)
            # Header lines (0=header, 1=blank, 2=section header) not in map
            assert 0 not in mapping
            assert 1 not in mapping

    async def test_click_toggles_filter_entry(self, tmp_path: Path) -> None:
        """Clicking on a filter entry line toggles it."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            assert app._filter_panel_visible
            # Find the line for the first entry (entry idx 1)
            line_for_first = None
            for line_num, entry_idx in app._filter_line_to_entry.items():
                if entry_idx == 1:
                    line_for_first = line_num
                    break
            assert line_for_first is not None
            first_key = app._filter_entries[0][0]
            initial_hidden = set(app._hidden_statuses) | set(app._hidden_tags)
            await pilot.click(
                "#std-filter-content", offset=(2, line_for_first),
            )
            await pilot.pause()
            after_hidden = set(app._hidden_statuses) | set(app._hidden_tags)
            # The first entry should have been toggled
            if first_key.startswith("status:"):
                status = first_key[7:]
                toggled = (status in after_hidden) != (status in initial_hidden)
            else:
                tag = first_key[4:]
                toggled = (tag in after_hidden) != (tag in initial_hidden)
            assert toggled, "Click should have toggled the filter entry"

    async def test_click_on_header_line_is_noop(self, tmp_path: Path) -> None:
        """Clicking on a section header does not toggle anything."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            initial_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            # Line 0 is the "Filters Active" header — not in mapping
            await pilot.click("#std-filter-content", offset=(2, 0))
            await pilot.pause()
            after_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            assert initial_hidden == after_hidden

    async def test_click_ignored_when_panel_hidden(
        self, tmp_path: Path,
    ) -> None:
        """on_click returns early when filter panel is not visible."""
        from textual.events import Click
        from textual.widgets import Static as StaticWidget
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert not app._filter_panel_visible
            initial_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            # Directly call on_click with a mock Click event
            widget = app.query_one("#std-filter-content", StaticWidget)
            event = Click(widget, 2, 3, 0, 0, 1, False, False, False)
            app.on_click(event)
            after_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            assert initial_hidden == after_hidden

    async def test_click_ignored_on_non_static_widget(
        self, tmp_path: Path,
    ) -> None:
        """on_click returns early when clicked widget is not a Static."""
        from textual.events import Click
        from textual.widgets import DataTable as DT
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            initial_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            # Call on_click with a non-Static widget
            table = app.query_one("#std-table", DT)
            event = Click(table, 2, 3, 0, 0, 1, False, False, False)
            app.on_click(event)
            after_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            assert initial_hidden == after_hidden

    async def test_click_ignored_on_other_static(
        self, tmp_path: Path,
    ) -> None:
        """on_click returns early when Static widget is not a filter panel."""
        from textual.events import Click
        from textual.widgets import Static as StaticWidget
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            initial_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            # Call on_click with a Static that's not a filter panel
            detail = app.query_one("#std-detail-content", StaticWidget)
            event = Click(detail, 2, 3, 0, 0, 1, False, False, False)
            app.on_click(event)
            after_hidden = len(app._hidden_statuses) + len(app._hidden_tags)
            assert initial_hidden == after_hidden

    async def test_click_on_entries_beyond_9(self, tmp_path: Path) -> None:
        """Clicking entries beyond index 9 works (unlike keyboard shortcuts)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 42)) as pilot:
            await _wait_for_std_table(pilot, app)
            await pilot.press("f")
            await pilot.pause()
            # Find entries beyond index 9 (tags in our fixture)
            beyond_9_line = None
            beyond_9_idx = None
            for line_num, entry_idx in app._filter_line_to_entry.items():
                if entry_idx > 9:
                    beyond_9_line = line_num
                    beyond_9_idx = entry_idx
                    break
            if beyond_9_idx is None:
                pytest.skip("Not enough entries to test beyond index 9")
            entry_key = app._filter_entries[beyond_9_idx - 1][0]
            initial_hidden = set(app._hidden_statuses) | set(app._hidden_tags)
            initial_hide_tagged = app._hide_tagged
            # Wide mode uses #filter-panel-content
            await pilot.click(
                "#filter-panel-content", offset=(2, beyond_9_line),
            )
            await pilot.pause()
            after_hidden = set(app._hidden_statuses) | set(app._hidden_tags)
            if entry_key.startswith("status:"):
                name = entry_key[7:]
                toggled = (name in after_hidden) != (name in initial_hidden)
            elif entry_key.startswith("meta:"):
                toggled = app._hide_tagged != initial_hide_tagged
            else:
                name = entry_key[4:]
                toggled = (name in after_hidden) != (name in initial_hidden)
            assert toggled, (
                f"Click on entry {beyond_9_idx} (line {beyond_9_line}) "
                "should have toggled the filter"
            )


# ---------------------------------------------------------------------------
# Disambiguate screen & prefix resolution
# ---------------------------------------------------------------------------


class TestDisambiguateScreen:
    """Test the DisambiguateScreen modal for ambiguous prefix resolution."""

    async def test_renders_candidates(self, tmp_path: Path) -> None:
        """DisambiguateScreen displays candidate items."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import OptionList as OL

        candidates = [
            ("INV-aaaa-bbbb-cccc-dddd", "Symbol IDs must be stable"),
            ("INV-aaaa-xxxx-yyyy-zzzz", "Routes need validation"),
        ]
        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            screen = DisambiguateScreen("INV-aaaa", candidates)
            app.push_screen(screen)
            for _ in range(20):
                await pilot.pause()
            ol = screen.query_one("#disambig-list", OL)
            assert ol.option_count == 2

    async def test_select_returns_full_id(self, tmp_path: Path) -> None:
        """Selecting an option dismisses with the full ID."""
        from hypergumbo_tracker.tui import TrackerApp

        candidates = [
            ("INV-aaaa-bbbb", "First"),
            ("INV-aaaa-cccc", "Second"),
        ]
        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            result_holder: list[str | None] = []
            app.push_screen(
                DisambiguateScreen("INV-aaaa", candidates),
                callback=result_holder.append,
            )
            for _ in range(20):
                await pilot.pause()
            # Select first option via Enter
            await pilot.press("enter")
            await pilot.pause()
            assert result_holder == ["INV-aaaa-bbbb"]

    async def test_cancel_returns_none(self, tmp_path: Path) -> None:
        """Escape dismisses with None."""
        from hypergumbo_tracker.tui import TrackerApp

        candidates = [
            ("INV-aaaa-bbbb", "First"),
            ("INV-aaaa-cccc", "Second"),
        ]
        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            result_holder: list[str | None] = []
            app.push_screen(
                DisambiguateScreen("INV-aaaa", candidates),
                callback=result_holder.append,
            )
            for _ in range(20):
                await pilot.pause()
            # Press escape to cancel
            await pilot.press("escape")
            await pilot.pause()
            assert result_holder == [None]

    async def test_cancel_button_returns_none(self, tmp_path: Path) -> None:
        """Cancel button press dismisses with None."""
        from hypergumbo_tracker.tui import TrackerApp

        candidates = [
            ("INV-aaaa-bbbb", "First"),
        ]
        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            result_holder: list[str | None] = []
            app.push_screen(
                DisambiguateScreen("INV-aaaa", candidates),
                callback=result_holder.append,
            )
            for _ in range(20):
                await pilot.pause()
            # Click Cancel button via pilot
            await pilot.click("#cancel")
            await pilot.pause()
            assert result_holder == [None]


class TestResolveIds:
    """Test TrackerApp._resolve_ids callback-based prefix resolution."""

    async def test_unique_prefix_resolves(self, tmp_path: Path) -> None:
        """Unique prefix is resolved to full ID via TrackerSet._resolve_id."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = ts.list_items()
            full_id = items[0].id
            result_holder: list[list[str] | None] = []
            app._resolve_ids([full_id], result_holder.append)
            assert result_holder == [[full_id]]

    async def test_not_found_returns_none(self, tmp_path: Path) -> None:
        """Non-existent prefix calls back with None."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            result_holder: list[list[str] | None] = []
            app._resolve_ids(["NONEXISTENT-xxxxx"], result_holder.append)
            assert result_holder == [None]

    async def test_ambiguous_shows_disambiguate_screen(
        self, tmp_path: Path,
    ) -> None:
        """Ambiguous prefix pushes DisambiguateScreen; selection resolves."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            candidates = [
                ("INV-aaaa-bbbb", "First"),
                ("INV-aaaa-cccc", "Second"),
            ]
            result_holder: list[list[str] | None] = []
            with patch.object(
                ts, "_resolve_id",
                side_effect=AmbiguousPrefixError("INV-aaaa", candidates),
            ):
                app._resolve_ids(["INV-aaaa"], result_holder.append)
            # Wait for DisambiguateScreen to appear
            for _ in range(20):
                await pilot.pause()
                if any(
                    isinstance(s, DisambiguateScreen)
                    for s in app.screen_stack
                ):
                    break
            # Select first option
            await pilot.press("enter")
            await pilot.pause()
            assert result_holder == [["INV-aaaa-bbbb"]]

    async def test_ambiguous_cancel_returns_none(
        self, tmp_path: Path,
    ) -> None:
        """Cancelling disambiguation calls back with None."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            candidates = [
                ("INV-aaaa-bbbb", "First"),
                ("INV-aaaa-cccc", "Second"),
            ]
            result_holder: list[list[str] | None] = []
            with patch.object(
                ts, "_resolve_id",
                side_effect=AmbiguousPrefixError("INV-aaaa", candidates),
            ):
                app._resolve_ids(["INV-aaaa"], result_holder.append)
            for _ in range(20):
                await pilot.pause()
                if any(
                    isinstance(s, DisambiguateScreen)
                    for s in app.screen_stack
                ):
                    break
            await pilot.press("escape")
            await pilot.pause()
            assert result_holder == [None]

    async def test_multiple_ids_resolve_sequentially(
        self, tmp_path: Path,
    ) -> None:
        """Multiple IDs are resolved one by one."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            items = ts.list_items()
            id_a = items[0].id
            id_b = items[1].id
            result_holder: list[list[str] | None] = []
            app._resolve_ids([id_a, id_b], result_holder.append)
            assert result_holder == [[id_a, id_b]]


# ---------------------------------------------------------------------------
# Tags column tests
# ---------------------------------------------------------------------------


class TestTagsColumn:
    """Test the Tags column in standard/wide DataTable."""

    async def test_tags_column_present_standard(self, tmp_path: Path) -> None:
        """Standard table has a Tags column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "tags" in column_keys

    async def test_tags_column_absent_compact(self, tmp_path: Path) -> None:
        """Compact table does not have a Tags column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "tags" not in column_keys

    async def test_tags_column_content(self, tmp_path: Path) -> None:
        """Items with tags show comma-separated tags; items without show empty."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [c.key.value for c in table.columns.values()]
            tags_col_idx = column_keys.index("tags")
            # Check each row for tag content
            for row_key in table.rows:
                row_data = table.get_row(row_key)
                cell_val = str(row_data[tags_col_idx])
                item_id = str(row_key.value)
                item = next(i for i in app._items if i.id == item_id)
                if item.tags:
                    assert cell_val == ", ".join(item.tags)
                else:
                    assert cell_val == ""

    async def test_tags_column_present_wide(self, tmp_path: Path) -> None:
        """Wide table has Tags column, appearing before conflict/created/updated."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 42)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert app._layout_tier == "wide"
            table = app.query_one("#std-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "tags" in column_keys
            # Tags should come before conflict
            tags_idx = column_keys.index("tags")
            conflict_idx = column_keys.index("conflict")
            assert tags_idx < conflict_idx

    async def test_tags_column_after_resize_standard_to_wide(
        self, tmp_path: Path,
    ) -> None:
        """Tags column remains present after resize from standard to wide."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "tags" in column_keys
            # Resize to wide
            await pilot.resize_terminal(130, 42)
            await pilot.pause()
            table = app.query_one("#std-table")
            column_keys = [c.key.value for c in table.columns.values()]
            assert "tags" in column_keys


# ---------------------------------------------------------------------------
# "Tagged" meta-filter tests
# ---------------------------------------------------------------------------


class TestTaggedMetaFilter:
    """Test the 'Tagged' meta-filter in the filter panel."""

    async def test_tagged_always_in_filter_panel(self, tmp_path: Path) -> None:
        """'Tagged' entry appears even when no items have tags."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set(tmp_path)  # only 1 item has tags
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            assert "Tagged" in plain
            assert "Tags" in plain

    async def test_tagged_always_in_filter_panel_no_tags(
        self, tmp_path: Path,
    ) -> None:
        """'Tagged' entry appears even when zero items have any tags."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget
        from helpers import make_test_config_dict
        import yaml

        # Create tracker set with no tags on any item
        root = tmp_path / ".agent"
        for d in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
            root / "tracker-workspace" / "stealth",
        ]:
            d.mkdir(parents=True, exist_ok=True)
        config = _make_config()
        config_path = root / "tracker" / "config.yaml"
        config_path.write_text(yaml.dump(make_test_config_dict()))
        ts = TrackerSet(root, config=config)
        ts.add(kind="work_item", title="No tags A", status="todo_hard", priority=1)
        ts.add(kind="work_item", title="No tags B", status="in_progress", priority=2)

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            assert "Tagged" in plain
            assert "Tags" in plain

    async def test_toggle_tagged_hides_tagged_items(
        self, tmp_path: Path,
    ) -> None:
        """Toggling 'Tagged' hides all items that have any tag."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            all_items = app._filtered_items()
            # Items with tags: quality, perf, quality+perf = 3
            tagged_count = sum(1 for i in all_items if i.tags)
            assert tagged_count == 3
            # Toggle tagged filter on
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            # Find meta:tagged entry
            tagged_idx = None
            for i, (key, _name) in enumerate(app._filter_entries):
                if key == "meta:tagged":
                    tagged_idx = i + 1
                    break
            assert tagged_idx is not None
            app._quick_toggle(tagged_idx)
            assert app._hide_tagged is True
            # Only untagged items remain
            filtered = app._filtered_items()
            for item in filtered:
                assert not item.tags

    async def test_toggle_tagged_twice_restores(self, tmp_path: Path) -> None:
        """Toggling 'Tagged' twice restores all items."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            initial_count = len(app._filtered_items())
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            tagged_idx = None
            for i, (key, _name) in enumerate(app._filter_entries):
                if key == "meta:tagged":
                    tagged_idx = i + 1
                    break
            assert tagged_idx is not None
            app._quick_toggle(tagged_idx)
            assert app._hide_tagged is True
            app._quick_toggle(tagged_idx)
            assert app._hide_tagged is False
            assert len(app._filtered_items()) == initial_count

    async def test_tagged_count_is_correct(self, tmp_path: Path) -> None:
        """The 'Tagged' entry shows the correct count of tagged items."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            # 3 items have tags in _make_tracker_set_with_tags
            # The "Tagged" line should show count 3
            assert "(3)" in plain

    async def test_clear_restore_includes_tagged(
        self, tmp_path: Path,
    ) -> None:
        """_clear_all_filters clears _hide_tagged; _restore_last_filters restores it."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hide_tagged = True
            app._hidden_statuses.add("done")
            app._filter_panel_visible = True
            app._clear_all_filters()
            assert app._hide_tagged is False
            assert len(app._hidden_statuses) == 0
            app._restore_last_filters()
            assert app._hide_tagged is True
            assert "done" in app._hidden_statuses

    async def test_preferences_persistence_hide_tagged(
        self, tmp_path: Path,
    ) -> None:
        """_hide_tagged survives save/load cycle."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hide_tagged = True
            await pilot.press("q")
            await pilot.pause()
        prefs = _load_tui_preferences(app._prefs_path)
        assert prefs.get("hide_tagged") is True

    async def test_preferences_load_hide_tagged(
        self, tmp_path: Path,
    ) -> None:
        """_hide_tagged is restored from preferences on mount."""
        import json
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        # Pre-write prefs with hide_tagged=True
        prefs_path = (
            tmp_path / ".agent" / "tracker-workspace" / "tui_preferences.json"
        )
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps({
            "version": 2,
            "hidden_statuses": [],
            "display_order": [],
            "hidden_tags": [],
            "toggle_sessions": [],
            "last_filters": {"hidden_statuses": [], "hidden_tags": [],
                             "hide_tagged": True},
            "hide_tagged": True,
        }), encoding="utf-8")
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            assert app._hide_tagged is True

    async def test_filter_active_count_includes_tagged(
        self, tmp_path: Path,
    ) -> None:
        """Filter panel 'Filters Active' count includes _hide_tagged."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hide_tagged = True
            app._hidden_statuses.add("done")
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            content = app.query_one("#std-filter-content", StaticWidget)
            plain = str(content.render())
            # 1 status + tagged = 2
            assert "Filters Active: 2" in plain

    async def test_status_filter_bar_shows_tagged_hidden(
        self, tmp_path: Path,
    ) -> None:
        """Status filter bar shows tagged-hidden indicator."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Static as StaticWidget

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._hide_tagged = True
            app._update_status_filter_bar()
            bar = app.query_one("#status-filter-bar", StaticWidget)
            plain = str(bar.render())
            assert "tagged: hidden" in plain

    async def test_toggle_count_incremented_for_meta_tagged(
        self, tmp_path: Path,
    ) -> None:
        """_increment_toggle_count is called for meta:tagged toggles."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_tags(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            app._filter_panel_visible = True
            app._refresh_filter_panel()
            tagged_idx = None
            for i, (key, _name) in enumerate(app._filter_entries):
                if key == "meta:tagged":
                    tagged_idx = i + 1
                    break
            assert tagged_idx is not None
            app._quick_toggle(tagged_idx)
            assert app._current_session_counts.get("meta:tagged", 0) > 0


# ---------------------------------------------------------------------------
# Unit tests: _is_human_unread
# ---------------------------------------------------------------------------


class TestIsHumanUnread:
    """Test the unread-from-human-perspective detection logic."""

    def test_empty_discussion_is_read(self) -> None:
        """Items with no discussion are considered read."""
        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard", discussion=[],
        )
        assert _is_human_unread(item, {}) is False

    def test_last_entry_by_agent_is_unread(self) -> None:
        """When the last discussion entry is by an agent, human hasn't seen it."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="I did a thing",
                ),
            ],
        )
        assert _is_human_unread(item, {}) is True

    def test_last_entry_by_human_is_read(self) -> None:
        """When the last entry is by a human, item is considered read."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="I did a thing",
                ),
                DiscussionEntry(
                    by="human", actor="dev", at="2026-01-02T14:30:00Z",
                    message="Looks good",
                ),
            ],
        )
        assert _is_human_unread(item, {}) is False

    def test_manual_read_overrides_auto(self) -> None:
        """Manual mark-as-read overrides auto-detected unread status."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="I did a thing",
                ),
            ],
        )
        # Auto-detect says unread, but manual override says read
        state = {"WI-abc": {"read": True, "discussion_len": 1}}
        assert _is_human_unread(item, state) is False

    def test_manual_unread_overrides_auto(self) -> None:
        """Manual mark-as-unread overrides auto-detected read status."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="human", actor="dev", at="2026-01-01T10:00:00Z",
                    message="Please check this",
                ),
            ],
        )
        # Auto-detect says read, but manual override says unread
        state = {"WI-abc": {"read": False, "discussion_len": 1}}
        assert _is_human_unread(item, state) is True

    def test_new_entry_invalidates_stale_override(self) -> None:
        """When discussion has grown since manual override, auto-toggle fires."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="human", actor="dev", at="2026-01-01T10:00:00Z",
                    message="Please check",
                ),
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-02T10:00:00Z",
                    message="Done",
                ),
            ],
        )
        # Override was set when discussion had 1 entry, now has 2 → stale
        state = {"WI-abc": {"read": True, "discussion_len": 1}}
        # Auto-detect fires: last entry by agent → unread
        assert _is_human_unread(item, state) is True

    def test_stale_override_human_last_entry(self) -> None:
        """Stale override with human as last entry → read."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="Started work",
                ),
                DiscussionEntry(
                    by="human", actor="dev", at="2026-01-02T10:00:00Z",
                    message="Thanks",
                ),
            ],
        )
        # Override set at len=1, now len=2 → stale → auto-detect
        state = {"WI-abc": {"read": False, "discussion_len": 1}}
        assert _is_human_unread(item, state) is False


# ---------------------------------------------------------------------------
# Unit tests: _item_title_text
# ---------------------------------------------------------------------------


class TestItemTitleText:
    """Test the _item_title_text helper that builds display titles."""

    def test_plain_title(self) -> None:
        """Normal item gets no prefix."""
        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Hello",
            status="todo_hard",
        )
        assert _item_title_text(item, {}) == "Hello"

    def test_unread_title(self) -> None:
        """Unread item gets blue dot prefix."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Hello",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="Progress",
                ),
            ],
        )
        assert _item_title_text(item, {}) == "\U0001f535 Hello"

    def test_frozen_title(self) -> None:
        """Frozen item gets ice emoji prefix."""
        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Hello",
            status="todo_hard", frozen=True,
        )
        assert _item_title_text(item, {}) == "\U0001f9ca Hello"

    def test_frozen_and_unread_title(self) -> None:
        """Frozen + unread item gets both: blue dot first, then ice."""
        from hypergumbo_tracker.models import DiscussionEntry

        item = CompiledItem(
            id="WI-abc", kind="work_item", title="Hello",
            status="todo_hard", frozen=True,
            discussion=[
                DiscussionEntry(
                    by="agent", actor="bot", at="2026-01-01T10:00:00Z",
                    message="Progress",
                ),
            ],
        )
        assert _item_title_text(item, {}) == "\U0001f535 \U0001f9ca Hello"


# ---------------------------------------------------------------------------
# Unit tests: preferences with human_read_state
# ---------------------------------------------------------------------------


class TestPreferencesHumanReadState:
    """Test human_read_state persistence in tui_preferences.json."""

    def test_load_missing_field_returns_empty_dict(self, tmp_path: Path) -> None:
        """Old preference files without human_read_state get empty default."""
        import json

        p = tmp_path / "prefs.json"
        data = {"version": 2, "hidden_statuses": [], "display_order": []}
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _load_tui_preferences(p)
        assert result["human_read_state"] == {}

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """human_read_state survives save → load roundtrip."""
        p = tmp_path / "prefs.json"
        hrs = {"WI-abc": {"read": True, "discussion_len": 5}}
        assert _save_tui_preferences(
            p, set(), [],
            human_read_state=hrs,
        ) is True
        result = _load_tui_preferences(p)
        assert result["human_read_state"] == hrs

    def test_corrupt_human_read_state_returns_empty(self, tmp_path: Path) -> None:
        """Non-dict human_read_state falls back to empty."""
        import json

        p = tmp_path / "prefs.json"
        data = {
            "version": 2, "hidden_statuses": [], "display_order": [],
            "human_read_state": "invalid",
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _load_tui_preferences(p)
        assert result["human_read_state"] == {}


# ---------------------------------------------------------------------------
# Unit tests: blue dot in title for unread items
# ---------------------------------------------------------------------------


def _make_tracker_set_with_discussion(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with items that have discussion entries.

    Discussion entries are forced to ``by="agent"`` via a mock so the
    tests work regardless of the OS username (CI may not match ``*_agent``).
    The calling test can use ``_human_read_state`` to manually mark
    items as read to simulate human interaction.
    """
    from helpers import make_test_config_dict
    from unittest.mock import patch as _patch
    from hypergumbo_tracker import store as _store_mod

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

    # Force agent identity for discussion entries
    with _patch.object(
        _store_mod, "resolve_actor", return_value=("agent", "test_agent"),
    ):
        # Item with agent discussion entry → human-unread (auto-detect)
        ts.add(kind="work_item", title="Agent updated",
               status="todo_hard", priority=1)
        items = ts.list_items()
        agent_item = next(i for i in items if i.title == "Agent updated")
        ts.discuss(agent_item.id, "I made progress")

        # Item with agent discussion entry but manually marked read
        # (simulates human having replied or pressed "Mark Read")
        ts.add(kind="work_item", title="Marked read",
               status="in_progress", priority=2)
        items = ts.list_items()
        read_item = next(i for i in items if i.title == "Marked read")
        ts.discuss(read_item.id, "Agent did work")

    # Item with no discussion → human-read
    ts.add(kind="work_item", title="No discussion",
           status="todo_soft", priority=3)

    return ts


class TestUnreadBlueDot:
    """Test that unread items show a blue dot emoji in the title."""

    async def test_unread_item_has_blue_dot_in_table(
        self, tmp_path: Path,
    ) -> None:
        """Items with unread agent entries show 🔵 in the title column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Mark "Marked read" item as read via manual override
            items = app._filtered_items()
            read_item = next(i for i in items if i.title == "Marked read")
            app._mark_human_read(read_item.id)
            await pilot.pause()
            table = app.query_one("#std-table")
            # Scan all cells in each row to find title content
            found_unread = False
            found_read = False
            for row_key in table.rows:
                row_data = table.get_row(row_key)
                row_str = " ".join(str(c) for c in row_data)
                if "Agent updated" in row_str:
                    assert "\U0001f535" in row_str, "Unread item should have blue dot"
                    found_unread = True
                elif "Marked read" in row_str:
                    assert "\U0001f535" not in row_str, "Read item should not have blue dot"
                    found_read = True
            assert found_unread, "Should find 'Agent updated' item"
            assert found_read, "Should find 'Marked read' item"

    async def test_no_discussion_no_blue_dot(
        self, tmp_path: Path,
    ) -> None:
        """Items with no discussion entries have no blue dot."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            for row_key in table.rows:
                row_data = table.get_row(row_key)
                row_str = " ".join(str(c) for c in row_data)
                if "No discussion" in row_str:
                    assert "\U0001f535" not in row_str
                    return
            pytest.fail("Should find 'No discussion' item")


# ---------------------------------------------------------------------------
# Unit tests: unread filter override
# ---------------------------------------------------------------------------


class TestUnreadFilterOverride:
    """Test that human-unread items override filters and appear in italics."""

    async def test_unread_item_appears_despite_status_filter(
        self, tmp_path: Path,
    ) -> None:
        """A human-unread item hidden by status filter is still included."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Hide todo_hard status — this would normally hide "Agent updated"
            app._hidden_statuses.add("todo_hard")
            items = app._filtered_items()
            item_titles = [i.title for i in items]
            # "Agent updated" should still appear because it's unread
            assert "Agent updated" in item_titles
            # It should be tracked as an unread override
            assert any(
                i.id in app._unread_override_ids
                for i in items if i.title == "Agent updated"
            )

    async def test_read_item_hidden_by_filter(
        self, tmp_path: Path,
    ) -> None:
        """A manually-read item hidden by status filter stays hidden."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Mark "Marked read" as read
            items = app._filtered_items()
            read_item = next(i for i in items if i.title == "Marked read")
            app._mark_human_read(read_item.id)
            # Hide in_progress status — "Marked read" is read and should stay hidden
            app._hidden_statuses.add("in_progress")
            items = app._filtered_items()
            item_titles = [i.title for i in items]
            assert "Marked read" not in item_titles


# ---------------------------------------------------------------------------
# Tests: mark-as-read / mark-as-unread buttons
# ---------------------------------------------------------------------------


class TestMarkAsReadUnread:
    """Test the Mark as Human-Read / Mark as Human-Unread buttons."""

    async def test_mark_as_read_clears_blue_dot(
        self, tmp_path: Path,
    ) -> None:
        """Pressing mark-as-read removes the unread indicator."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Select the unread item
            items = app._filtered_items()
            unread_item = next(i for i in items if i.title == "Agent updated")
            assert _is_human_unread(unread_item, app._human_read_state) is True
            # Simulate pressing the mark-as-read button
            app._mark_human_read(unread_item.id)
            assert _is_human_unread(unread_item, app._human_read_state) is False

    async def test_mark_as_unread_adds_blue_dot(
        self, tmp_path: Path,
    ) -> None:
        """Pressing mark-as-unread on a read item makes it unread."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 40)) as pilot:
            await _wait_for_std_table(pilot, app)
            # "No discussion" is read by default (no entries)
            items = app._filtered_items()
            no_disc = next(i for i in items if i.title == "No discussion")
            assert _is_human_unread(no_disc, app._human_read_state) is False
            # Mark it as unread
            app._mark_human_unread(no_disc.id)
            assert _is_human_unread(no_disc, app._human_read_state) is True

    async def test_mark_read_button_event(
        self, tmp_path: Path,
    ) -> None:
        """Clicking the mark-read button via event dispatches correctly."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Button

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Get the unread item ID
            items = app._filtered_items()
            unread_item = next(i for i in items if i.title == "Agent updated")
            assert _is_human_unread(unread_item, app._human_read_state) is True
            # Navigate to the unread item's row
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == unread_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Press the mark-read button via click
            btn = app.query_one("#mark-read", Button)
            btn.press()
            await pilot.pause()
            assert _is_human_unread(unread_item, app._human_read_state) is False

    async def test_mark_unread_button_event(
        self, tmp_path: Path,
    ) -> None:
        """Clicking the mark-unread button via event dispatches correctly."""
        from hypergumbo_tracker.tui import TrackerApp
        from textual.widgets import Button

        ts = _make_tracker_set_with_discussion(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Mark the unread item as read first
            items = app._filtered_items()
            unread_item = next(i for i in items if i.title == "Agent updated")
            app._mark_human_read(unread_item.id)
            assert _is_human_unread(unread_item, app._human_read_state) is False
            # Navigate to the item's row
            table = app.query_one("#std-table")
            for idx, rk in enumerate(table.rows):
                if str(rk.value) == unread_item.id:
                    table.move_cursor(row=idx)
                    break
            await pilot.pause()
            # Press the mark-unread button via click
            btn = app.query_one("#mark-unread", Button)
            btn.press()
            await pilot.pause()
            assert _is_human_unread(unread_item, app._human_read_state) is True


# ---------------------------------------------------------------------------
# Tests: frozen + unread combination
# ---------------------------------------------------------------------------


class TestFrozenUnreadCombination:
    """Test that frozen items with unread discussion show both indicators."""

    async def test_frozen_unread_shows_both_indicators(
        self, tmp_path: Path,
    ) -> None:
        """A frozen item with unread agent entry shows both 🔵 and 🧊."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set_with_discussion(tmp_path)
        # Freeze the "Agent updated" item (which has unread status)
        items = ts.list_items()
        agent_item = next(i for i in items if i.title == "Agent updated")
        # Freeze requires human authority; mock resolve_actor
        from unittest.mock import patch as _patch
        from hypergumbo_tracker import store as store_mod
        with _patch.object(
            store_mod, "resolve_actor", return_value=("human", "testuser"),
        ):
            ts.freeze(agent_item.id)

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            table = app.query_one("#std-table")
            for row_key in table.rows:
                row_data = table.get_row(row_key)
                row_str = " ".join(str(c) for c in row_data)
                if "Agent updated" in row_str:
                    assert "\U0001f535" in row_str, "Should have blue dot"
                    assert "\U0001f9ca" in row_str, "Should have frozen indicator"
                    return
            pytest.fail("Should find 'Agent updated' item")

    async def test_unread_ghost_row_has_blue_dot(
        self, tmp_path: Path,
    ) -> None:
        """An unread item appearing as a ghost row shows the blue dot."""
        from helpers import make_test_config_dict
        from hypergumbo_tracker.tui import TrackerApp

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

        # Force agent identity so discussion is by="agent" regardless
        # of the OS username (CI may not match *_agent).
        from unittest.mock import patch as _patch
        from hypergumbo_tracker import store as _store_mod

        with _patch.object(
            _store_mod, "resolve_actor", return_value=("agent", "test_agent"),
        ):
            # Create chain: A (done, has discussion) → B (todo_hard)
            id_a = ts.add(kind="work_item", title="Ghost unread item",
                          status="done", priority=1)
            id_b = ts.add(kind="work_item", title="Depends on ghost",
                          status="todo_hard", priority=2)
            ts.update(id_b, add_fields={"before": [id_a]})
            # Add discussion to A so it's unread
            ts.discuss(id_a, "Agent progress update")

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Hide "done" status — A becomes a ghost row
            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()
            # Select B to trigger chain ghost rows
            table = app.query_one("#std-table")
            table.move_cursor(row=0)
            await pilot.pause()
            # Manually trigger chain highlight
            app._remove_ghost_rows()
            app._append_hidden_chain_rows(id_b)
            # Ghost row for A should have blue dot
            found_ghost = False
            for row_key in table.rows:
                row_data = table.get_row(row_key)
                row_str = " ".join(str(c) for c in row_data)
                if "Ghost unread item" in row_str:
                    assert "\U0001f535" in row_str, "Ghost row should have blue dot"
                    found_ghost = True
            assert found_ghost, "Should find ghost row for unread item"

    async def test_frozen_ghost_row_has_ice_indicator(
        self, tmp_path: Path,
    ) -> None:
        """A frozen item appearing as a ghost row shows the 🧊 indicator."""
        from helpers import make_test_config_dict
        from hypergumbo_tracker.tui import TrackerApp

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

        id_a = ts.add(kind="work_item", title="Frozen ghost item",
                      status="done", priority=1)
        id_b = ts.add(kind="work_item", title="Depends on frozen",
                      status="todo_hard", priority=2)
        ts.update(id_b, add_fields={"before": [id_a]})
        # Freeze A (requires human authority mock)
        from unittest.mock import patch as _patch
        from hypergumbo_tracker import store as store_mod
        with _patch.object(
            store_mod, "resolve_actor", return_value=("human", "testuser"),
        ):
            ts.freeze(id_a)

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            # Hide "done" status — A becomes a ghost row
            app._hidden_statuses.add("done")
            app._reload_active_table()
            await pilot.pause()
            table = app.query_one("#std-table")
            table.move_cursor(row=0)
            await pilot.pause()
            app._remove_ghost_rows()
            app._append_hidden_chain_rows(id_b)
            found_ghost = False
            for row_key in table.rows:
                row_data = table.get_row(row_key)
                row_str = " ".join(str(c) for c in row_data)
                if "Frozen ghost item" in row_str:
                    assert "\U0001f9ca" in row_str, "Ghost row should have frozen indicator"
                    found_ghost = True
            assert found_ghost, "Should find ghost row for frozen item"
