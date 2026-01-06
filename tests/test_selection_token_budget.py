"""Tests for the selection.token_budget module."""

from hypergumbo.selection.token_budget import (
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
