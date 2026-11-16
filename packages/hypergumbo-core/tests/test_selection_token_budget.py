# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the selection.token_budget module."""

from hypergumbo_core.selection.token_budget import (
    CHARS_PER_TOKEN,
    DEFAULT_TIERS,
    TOKENS_BEHAVIOR_MAP_OVERHEAD,
    TOKENS_PER_NODE_OVERHEAD,
    estimate_tokens,
    estimate_json_tokens,
    truncate_to_tokens,
    parse_tier_spec,
)


class TestEstimateTokens:
    """Tests for estimate_tokens function."""

    def test_empty_string(self):
        """Empty string returns 0."""
        assert estimate_tokens("") == 0

    def test_short_string(self):
        """Short strings return at least 1."""
        assert estimate_tokens("hi") >= 1

    def test_longer_string(self):
        """Longer strings scale with length."""
        short = estimate_tokens("hello")
        long = estimate_tokens("hello world this is a longer string")
        assert long > short

    def test_ceiling_division(self):
        """Uses ceiling division for conservative estimate."""
        # 5 chars should be 2 tokens (ceiling of 5/4)
        result = estimate_tokens("abcde")
        assert result == 2

    def test_exact_multiple(self):
        """Exact multiples work correctly."""
        # 8 chars should be 2 tokens (8/4)
        result = estimate_tokens("abcdefgh")
        assert result == 2


class TestEstimateJsonTokens:
    """Tests for estimate_json_tokens function."""

    def test_empty_dict(self):
        """Empty dict returns zero or minimal token count."""
        result = estimate_json_tokens({})
        # {} is only 2 chars, which is 0 tokens at 4 chars/token
        assert result >= 0
        assert result < 10

    def test_simple_dict(self):
        """Simple dict returns reasonable count."""
        result = estimate_json_tokens({"name": "test", "value": 123})
        assert result > 5

    def test_larger_dict_more_tokens(self):
        """Larger dicts have more tokens."""
        small = estimate_json_tokens({"a": 1})
        large = estimate_json_tokens({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
        assert large > small


class TestTruncateToTokens:
    """Tests for truncate_to_tokens function."""

    def test_short_text_unchanged(self):
        """Text within budget is unchanged."""
        text = "Hello world"
        result = truncate_to_tokens(text, 1000)
        assert result == text

    def test_truncates_long_text(self):
        """Long text is truncated."""
        text = "a" * 1000
        result = truncate_to_tokens(text, 10)
        assert len(result) < len(text)

    def test_keeps_whole_sections(self):
        """Truncation keeps whole sections when possible."""
        text = """# Title

## Section 1
Content for section 1.

## Section 2
Content for section 2.

## Section 3
Content for section 3.
"""
        # Budget that fits title and first section
        result = truncate_to_tokens(text, 30)
        # Should include title and at least first section
        assert "# Title" in result or "## Section 1" in result

    def test_paragraph_fallback(self):
        """Falls back to paragraph splitting when no sections."""
        text = """First paragraph.

Second paragraph.

Third paragraph."""
        result = truncate_to_tokens(text, 10)
        assert len(result) < len(text)


class TestTruncationRespectsCodeFences:
    """A line inside a fenced code block is not a section header and not a
    paragraph break (WI-papup).

    A README that shows example output holds ``## `` lines that are text of
    the example. Taken for headers, they cut the README in the middle of its
    own example and left the fence open.
    """

    README = (
        "# Tool\n\nWhat the tool does, in a paragraph of prose.\n\n"
        "## Example\n\nRun it and you get:\n\n"
        "```\n## Overview\n\nsome output\n\n## Entry Points\n\nmore output\n```\n\n"
        "## Install\n\n" + "Install it like so. " * 30 + "\n"
    )

    def test_a_heading_inside_a_fence_is_not_a_cut_point(self):
        """Budget enough for everything up to "## Install": the cut is there,
        not at a "## " line of the example."""
        budget = (self.README.index("## Install") + 20) // CHARS_PER_TOKEN
        result = truncate_to_tokens(self.README, budget)
        assert result.endswith("more output\n```")
        assert result.count("```") == 2
        assert "## Install" not in result

    def test_a_budget_that_ends_inside_the_fence_cuts_before_its_section(self):
        """The example's section does not fit, so it is left out whole; the
        result never ends inside the code block."""
        budget = (self.README.index("## Entry Points") + 10) // CHARS_PER_TOKEN
        result = truncate_to_tokens(self.README, budget)
        assert result == "# Tool\n\nWhat the tool does, in a paragraph of prose."
        assert "```" not in result

    def test_tilde_fences_and_an_unclosed_fence(self):
        text = ("Intro paragraph.\n\n~~~\n## not a heading\n\nstill code\n"
                + "x = 1\n" * 80)
        result = truncate_to_tokens(text, 30)
        assert result == "Intro paragraph."

    def test_a_blank_line_inside_a_fence_is_not_a_paragraph_break(self):
        """No headers at all: the paragraph rule also stays out of the fence."""
        text = ("First paragraph.\n\nSecond paragraph.\n\n"
                "```\nline one\n\nline two\n\nline three\n```\n\n" + "Tail. " * 60)
        budget = (text.index("line three") + 2) // CHARS_PER_TOKEN
        result = truncate_to_tokens(text, budget)
        assert result == "First paragraph.\n\nSecond paragraph."

    def test_a_fence_marker_only_closes_its_own_kind(self):
        """A ``~~~`` line inside a backtick fence is code. If it closed the
        fence, the ``## `` line after it would be a header and the cut would
        land there, inside the block."""
        text = ("Intro.\n\n```\n~~~\n## still code\n\n" + "x = 1\n" * 30
                + "~~~\n```\n\n## Real\n\n" + "Body. " * 80)
        budget = (text.index("## still code") + 40) // CHARS_PER_TOKEN
        result = truncate_to_tokens(text, budget)
        assert result == "Intro."

    def test_a_preamble_longer_than_the_budget_is_cut_to_it(self):
        """The text before the first header used to be returned whole,
        however long."""
        text = ("# Title\n\n" + "\n\n".join(f"Paragraph {i} of the intro." for i in range(40))
                + "\n\n## First section\n\nBody.\n")
        result = truncate_to_tokens(text, 50)
        assert len(result) <= 50 * CHARS_PER_TOKEN
        assert result.startswith("# Title\n\nParagraph 0")
        assert result.endswith("of the intro.")

    def test_text_without_fences_is_cut_as_before(self):
        """Control: whole sections, in order, while they fit."""
        text = "# T\n\n## One\n" + "a " * 40 + "\n\n## Two\n" + "b " * 40 + "\n\n## Three\n" + "c " * 40
        result = truncate_to_tokens(text, (text.index("## Three") + 4) // CHARS_PER_TOKEN)
        assert "## One" in result and "## Two" in result and "## Three" not in result


class TestParseTierSpec:
    """Tests for parse_tier_spec function."""

    def test_parse_k_suffix(self):
        """Parses '4k' correctly."""
        assert parse_tier_spec("4k") == 4000

    def test_parse_uppercase_k(self):
        """Parses '16K' (uppercase) correctly."""
        assert parse_tier_spec("16K") == 16000

    def test_parse_decimal_k(self):
        """Parses '1.5k' correctly."""
        assert parse_tier_spec("1.5k") == 1500

    def test_parse_raw_number(self):
        """Parses raw number correctly."""
        assert parse_tier_spec("64000") == 64000

    def test_parse_with_whitespace(self):
        """Handles whitespace."""
        assert parse_tier_spec("  4k  ") == 4000


class TestConstants:
    """Tests for module constants."""

    def test_chars_per_token_reasonable(self):
        """CHARS_PER_TOKEN is a reasonable value."""
        assert 3 <= CHARS_PER_TOKEN <= 5

    def test_default_tiers_exist(self):
        """DEFAULT_TIERS is non-empty."""
        assert len(DEFAULT_TIERS) > 0

    def test_default_tiers_parseable(self):
        """All DEFAULT_TIERS can be parsed."""
        for tier in DEFAULT_TIERS:
            value = parse_tier_spec(tier)
            assert value > 0

    def test_overhead_constants_reasonable(self):
        """Overhead constants are reasonable values."""
        assert TOKENS_PER_NODE_OVERHEAD > 0
        assert TOKENS_BEHAVIOR_MAP_OVERHEAD > 0
