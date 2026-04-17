# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for linkers/_concept_utils.py (has_concept / get_concept).

Covers the production shape (``list[dict]``) emitted by
``framework_patterns.py`` and the defensive edge cases (``meta`` missing
or empty, bare-string entries that must be rejected per INV-tuzub)."""

from __future__ import annotations

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers._concept_utils import get_concept, has_concept


def _sym(meta: dict | None) -> Symbol:
    return Symbol(
        id="python:app.py:1-5:f:function",
        name="f",
        kind="function",
        language="python",
        path="app.py",
        span=Span(1, 5, 0, 0),
        meta=meta,
    )


class TestHasConcept:
    def test_matches_production_shape(self) -> None:
        sym = _sym({"concepts": [{"concept": "middleware", "framework": "express"}]})
        assert has_concept(sym, "middleware") is True

    def test_no_match_when_concept_name_differs(self) -> None:
        sym = _sym({"concepts": [{"concept": "route"}]})
        assert has_concept(sym, "middleware") is False

    def test_multiple_concepts_one_matches(self) -> None:
        sym = _sym({"concepts": [{"concept": "route"}, {"concept": "middleware"}]})
        assert has_concept(sym, "middleware") is True

    def test_missing_meta(self) -> None:
        assert has_concept(_sym(None), "middleware") is False

    def test_empty_meta(self) -> None:
        assert has_concept(_sym({}), "middleware") is False

    def test_empty_concepts_list(self) -> None:
        assert has_concept(_sym({"concepts": []}), "middleware") is False

    def test_bare_string_entry_rejected(self) -> None:
        """Regression for INV-tuzub: bare strings must not match."""
        sym = _sym({"concepts": ["middleware"]})
        assert has_concept(sym, "middleware") is False

    def test_mixed_dict_and_string_only_dict_counts(self) -> None:
        sym = _sym({"concepts": ["middleware", {"concept": "middleware"}]})
        assert has_concept(sym, "middleware") is True


class TestGetConcept:
    def test_returns_full_dict(self) -> None:
        sym = _sym(
            {"concepts": [{"concept": "route", "path": "/users", "method": "GET"}]}
        )
        result = get_concept(sym, "route")
        assert result == {"concept": "route", "path": "/users", "method": "GET"}

    def test_returns_first_match_when_duplicates(self) -> None:
        sym = _sym(
            {
                "concepts": [
                    {"concept": "route", "path": "/a"},
                    {"concept": "route", "path": "/b"},
                ]
            }
        )
        result = get_concept(sym, "route")
        assert result is not None
        assert result["path"] == "/a"

    def test_returns_none_when_absent(self) -> None:
        sym = _sym({"concepts": [{"concept": "middleware"}]})
        assert get_concept(sym, "route") is None

    def test_returns_none_when_meta_missing(self) -> None:
        assert get_concept(_sym(None), "route") is None

    def test_returns_none_when_only_bare_strings(self) -> None:
        """Regression for INV-tuzub."""
        sym = _sym({"concepts": ["route"]})
        assert get_concept(sym, "route") is None
