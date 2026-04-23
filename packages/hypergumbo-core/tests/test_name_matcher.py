# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for NameMatcher (WI-pisab-furis, Level 2 of WI-zigah response)."""

from hypergumbo_core.name_matcher import NameMatcher


# ---------------------------------------------------------------------------
# Canonical mode
# ---------------------------------------------------------------------------

def test_canonical_exact_match() -> None:
    m = NameMatcher("pydantic.BaseModel")
    assert m.matches("pydantic.BaseModel")


def test_canonical_bare_source_matches_qualified_pattern() -> None:
    """Source=BaseModel (from pydantic import) matches pattern=pydantic.BaseModel."""
    m = NameMatcher("pydantic.BaseModel")
    assert m.matches("BaseModel")


def test_canonical_qualified_source_matches_bare_pattern() -> None:
    """Source=pydantic.BaseModel matches pattern=BaseModel (terminal-only)."""
    m = NameMatcher("BaseModel")
    assert m.matches("pydantic.BaseModel")


def test_canonical_deeper_source_matches_shallower_pattern() -> None:
    """Source=pydantic.main.BaseModel matches pattern=main.BaseModel."""
    m = NameMatcher("main.BaseModel")
    assert m.matches("pydantic.main.BaseModel")


def test_canonical_terminal_mismatch_rejects() -> None:
    m = NameMatcher("pydantic.BaseModel")
    assert not m.matches("pydantic.OtherModel")


def test_canonical_unrelated_module_rejects() -> None:
    m = NameMatcher("pydantic.BaseModel")
    assert not m.matches("django.db.models.Model")


def test_canonical_partial_prefix_does_not_match() -> None:
    """Substring-without-segment-boundary must not match."""
    m = NameMatcher("Model")
    # "BaseModel" ends in "Model" as a substring, but not as a dotted segment.
    assert not m.matches("BaseModel")


# ---------------------------------------------------------------------------
# Regex mode
# ---------------------------------------------------------------------------

def test_regex_mode_anchored_matches_raw() -> None:
    m = NameMatcher(r"^app\.(get|post)$")
    assert m.matches("app.get")
    assert m.matches("app.post")
    assert not m.matches("app.put")


def test_regex_mode_falls_back_to_terminal_segment() -> None:
    """Anchored pattern matches the terminal segment of a qualified source."""
    m = NameMatcher(r"^get$")
    assert m.matches("app.get")


def test_regex_mode_no_fallback_when_terminal_still_does_not_match() -> None:
    m = NameMatcher(r"^put$")
    assert not m.matches("app.get")


def test_regex_mode_unqualified_source() -> None:
    """When source has no dots, terminal fallback is a no-op."""
    m = NameMatcher(r"^get$")
    assert m.matches("get")
    assert not m.matches("post")


# ---------------------------------------------------------------------------
# match() API for capture-group use
# ---------------------------------------------------------------------------

def test_match_returns_match_object_for_regex_mode() -> None:
    m = NameMatcher(r"^(app|router)\.(get|post)$")
    result = m.match("app.get")
    assert result is not None
    assert result.group(1) == "app"
    assert result.group(2) == "get"


def test_match_falls_back_to_terminal_segment() -> None:
    m = NameMatcher(r"^(get|post)$")
    result = m.match("app.get")
    assert result is not None
    assert result.group(1) == "get"


def test_match_returns_none_when_no_match() -> None:
    m = NameMatcher(r"^put$")
    assert m.match("app.get") is None


def test_match_returns_none_when_unqualified_source_mismatches() -> None:
    """Source has no dots and doesn't match pattern — no fallback to try."""
    m = NameMatcher(r"^put$")
    assert m.match("get") is None


def test_match_returns_none_in_canonical_mode() -> None:
    """Canonical-mode patterns don't carry a regex — match() returns None."""
    m = NameMatcher("pydantic.BaseModel")
    assert m.match("pydantic.BaseModel") is None
