# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the qualified-name axis (ADR-0032).

Mirror of ``test_protocol_origins.py`` for the qualified-name axis. The
axis declares a per-language separator policy rather than an enumerable
value set (ADR-0024 §4 lightweight pattern), so the invariants are
structural — every language entry must have a non-empty separator, the
accessor must agree with the source-of-truth dict, and the axis must be
wired into the static-AST linter.
"""
from __future__ import annotations

from hypergumbo_core.qualified_name_axis import (
    QUALIFIED_NAME_SEPARATORS,
    all_qualified_name_languages,
    separator_for_language,
)


def test_separators_are_non_empty_strings() -> None:
    """Every declared separator must be a non-empty string. An empty
    separator would produce ambiguous qualified names (``"a"`` could
    mean ``a`` or ``a.b`` depending on the parser convention).
    """
    for lang, sep in QUALIFIED_NAME_SEPARATORS.items():
        assert isinstance(sep, str), (
            f"Separator for {lang!r} is {type(sep).__name__}, expected str"
        )
        assert len(sep) >= 1, (
            f"Separator for {lang!r} is empty; declare a non-empty separator"
        )


def test_separator_for_language_returns_known() -> None:
    """``separator_for_language`` returns the declared separator for
    known languages and ``None`` for languages absent from the policy."""
    assert separator_for_language("python") == "."
    assert separator_for_language("rust") == "::"
    assert separator_for_language("php") == "\\"
    assert separator_for_language("not-a-real-language-shibboleth") is None


def test_all_qualified_name_languages_matches_dict_keys() -> None:
    """The accessor returns the frozenset of dict keys, no surprises."""
    assert all_qualified_name_languages() == frozenset(
        QUALIFIED_NAME_SEPARATORS.keys()
    )


def test_separator_set_is_small_and_documented() -> None:
    """Sanity: the separator policy covers a small set of known values.
    If a future PR adds a separator outside this set, the test will
    fail and prompt a deliberate review of whether the new separator
    is structurally distinct from the existing ones.
    """
    declared_separators = set(QUALIFIED_NAME_SEPARATORS.values())
    # The three canonical separators in practice: dot, double-colon,
    # backslash. Future additions (e.g., ``/`` for path-style) require
    # explicit allowlist here.
    canonical = {".", "::", "\\"}
    unexpected = declared_separators - canonical
    assert not unexpected, (
        f"Unexpected separator(s): {unexpected}. If genuinely new, extend "
        f"the canonical set in this test with a one-line rationale."
    )


def test_axis_wired_into_multi_value_field_axis() -> None:
    """``qualified-name`` axis is wired into the static-AST validator
    so ``# axis: qualified-name`` annotations on dataclass fields pass
    the lint."""
    from hypergumbo_core.multi_value_field_axis import _known_axes

    axes = _known_axes()
    assert "qualified-name" in axes
    assert callable(axes["qualified-name"])
    assert axes["qualified-name"]() == all_qualified_name_languages()


def test_canonical_languages_have_declared_policy() -> None:
    """The 10 named languages in the ADR-0032 Phase 4 PR4 population
    plan must have declared separators so the Phase 4 work has a value
    to populate ``Symbol.qualified_name`` with."""
    population_plan_languages = {
        "go", "rust", "typescript", "javascript", "java",
        "csharp", "ruby", "php", "kotlin", "swift",
    }
    declared = all_qualified_name_languages()
    missing = population_plan_languages - declared
    assert not missing, (
        f"ADR-0032 Phase 4 PR4 names {sorted(missing)} as analyzer targets "
        "but qualified_name_axis has no declared separator. Add entries to "
        "QUALIFIED_NAME_SEPARATORS."
    )
