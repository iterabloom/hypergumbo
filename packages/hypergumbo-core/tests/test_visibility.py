# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical visibility axis (INV-jusot)."""

from hypergumbo_core.visibility import (
    META_VISIBILITY_TO_VISIBILITY,
    MODIFIER_TO_VISIBILITY,
    SIGNAL_DEFAULT,
    SIGNAL_LANGUAGE_MODIFIER,
    SIGNAL_NAME_CONVENTION,
    VISIBILITY_LEVELS,
    VISIBILITY_MODIFIER_TERMS,
    all_known_visibility_levels,
    compute_visibility,
)


class TestVisibilityVocabulary:
    def test_levels_are_the_five_canonical(self) -> None:
        assert VISIBILITY_LEVELS == frozenset(
            {"public", "private", "protected", "internal", "package"}
        )

    def test_all_known_returns_the_levels(self) -> None:
        assert all_known_visibility_levels() == VISIBILITY_LEVELS
        assert isinstance(all_known_visibility_levels(), frozenset)

    def test_every_modifier_maps_to_a_canonical_level(self) -> None:
        assert set(MODIFIER_TO_VISIBILITY.values()) <= VISIBILITY_LEVELS

    def test_strip_terms_are_the_pure_visibility_subset(self) -> None:
        # The strip set is a PROPER subset of the visibility mapping — it
        # excludes re_exported (a re-export marker that only *implies* public
        # visibility), so the re-export fact survives in modifiers.
        assert VISIBILITY_MODIFIER_TERMS < frozenset(MODIFIER_TO_VISIBILITY)
        assert "re_exported" not in VISIBILITY_MODIFIER_TERMS
        assert "re_exported" in MODIFIER_TO_VISIBILITY  # still a visibility signal
        # The pure-visibility terms observed on the self-corpus are stripped.
        assert {
            "private", "public", "exported", "pub", "external", "unexported",
        } <= VISIBILITY_MODIFIER_TERMS
        # ...and a non-visibility modifier is NOT in the set.
        assert "static" not in VISIBILITY_MODIFIER_TERMS

    def test_reexported_still_computes_public_visibility(self) -> None:
        assert compute_visibility(
            modifiers=["re_exported"], name="X", language="python"
        )[0] == "public"


class TestComputeVisibility:
    def test_language_modifier_wins(self) -> None:
        # Polarity normalisation per language.
        assert compute_visibility(modifiers=["private"], name="x", language="python") == (
            "private", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(modifiers=["pub"], name="x", language="rust") == (
            "public", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(modifiers=["exported"], name="X", language="go") == (
            "public", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(modifiers=["external"], name="t", language="solidity") == (
            "public", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(modifiers=["unexported"], name="x", language="go") == (
            "private", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(modifiers=["protected"], name="x", language="java") == (
            "protected", SIGNAL_LANGUAGE_MODIFIER)

    def test_modifier_wins_over_name_convention(self) -> None:
        # A Python symbol whose analyzer already stamped 'private' resolves
        # via the modifier, not the (redundant) name path.
        assert compute_visibility(
            modifiers=["private"], name="_helper", language="python"
        ) == ("private", SIGNAL_LANGUAGE_MODIFIER)

    def test_non_visibility_modifier_does_not_decide(self) -> None:
        # 'static' carries no visibility — falls through to the default.
        assert compute_visibility(modifiers=["static"], name="foo", language="java") == (
            "public", SIGNAL_DEFAULT)

    def test_python_underscore_is_private_by_name(self) -> None:
        assert compute_visibility(modifiers=[], name="_helper", language="python") == (
            "private", SIGNAL_NAME_CONVENTION)
        # Qualified method name — only the final segment carries the rule.
        assert compute_visibility(modifiers=[], name="Service._impl", language="python") == (
            "private", SIGNAL_NAME_CONVENTION)

    def test_python_dunder_is_not_private(self) -> None:
        assert compute_visibility(modifiers=[], name="__init__", language="python") == (
            "public", SIGNAL_DEFAULT)
        assert compute_visibility(modifiers=[], name="Cls.__repr__", language="python") == (
            "public", SIGNAL_DEFAULT)

    def test_python_public_name_defaults_public(self) -> None:
        assert compute_visibility(modifiers=[], name="run", language="python") == (
            "public", SIGNAL_DEFAULT)

    def test_underscore_name_only_applies_to_python(self) -> None:
        # A leading underscore in a non-Python language carries no PEP-8
        # convention; without a modifier it defaults to public.
        assert compute_visibility(modifiers=[], name="_x", language="go") == (
            "public", SIGNAL_DEFAULT)
        assert compute_visibility(modifiers=[], name="_x", language=None) == (
            "public", SIGNAL_DEFAULT)


class TestMetaVisibilitySignal:
    def test_meta_values_map_to_canonical(self) -> None:
        assert set(META_VISIBILITY_TO_VISIBILITY.values()) <= VISIBILITY_LEVELS
        # Apex's broadest access modifier folds to public.
        assert META_VISIBILITY_TO_VISIBILITY["global"] == "public"

    def test_legacy_meta_visibility_folds(self) -> None:
        # Apex / Clojure expressed visibility via meta['visibility']; the
        # finalize pass passes it here.
        assert compute_visibility(
            modifiers=[], name="Foo", language="apex", meta_visibility="global"
        ) == ("public", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(
            modifiers=[], name="Foo", language="apex", meta_visibility="private"
        ) == ("private", SIGNAL_LANGUAGE_MODIFIER)
        assert compute_visibility(
            modifiers=[], name="ns/foo", language="clojure", meta_visibility="private"
        ) == ("private", SIGNAL_LANGUAGE_MODIFIER)

    def test_modifier_wins_over_meta_visibility(self) -> None:
        assert compute_visibility(
            modifiers=["public"], name="x", language="apex", meta_visibility="private"
        ) == ("public", SIGNAL_LANGUAGE_MODIFIER)

    def test_unknown_meta_visibility_falls_through(self) -> None:
        # An unrecognised meta value doesn't decide; falls to the default.
        assert compute_visibility(
            modifiers=[], name="x", language="apex", meta_visibility="weird"
        ) == ("public", SIGNAL_DEFAULT)
