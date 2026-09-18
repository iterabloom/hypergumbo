# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-juzig / INV-nidul: the analyzer registry speaks the taxonomy's vocabulary.

These tests run against the REAL registry (entry-point discovery, every
installed language package), not a cleared one — they are the enumeration
that proves the gate holds over the whole population rather than over a
synthetic registration. Every discovered analyzer must sit in exactly one of
three declared states:

* ``taxonomy``        — every language is a ``taxonomy.LANGUAGES`` key;
* ``no_taxonomy_spec`` — a real language the taxonomy has no LanguageSpec
  for (the WI-futin coverage gap); NONE of its languages may be a taxonomy
  key, or the declaration is stale;
* ``no_language``     — not a language pass (synthesis over many formats);
  ``languages == []``.

The named entries pin the four registrations the row prescribes; a count is
never asserted on its own (a registry populated by import side-effect is
empty until something imports it — assert a NAMED entry).
"""
from __future__ import annotations

from hypergumbo_core.analyze.registry import (
    LANGUAGE_STATE_NO_LANGUAGE,
    LANGUAGE_STATE_NO_SPEC,
    LANGUAGE_STATE_TAXONOMY,
    ensure_discovered,
    get_all_analyzers,
    get_analyzer,
)
from hypergumbo_core.catalog import all_known_languages
from hypergumbo_core.taxonomy import LANGUAGES


def _discovered() -> dict[str, object]:
    ensure_discovered()
    reg = {a.name: a for a in get_all_analyzers()}
    # Named sentinels from three different packages: proves discovery ran.
    for name in ("python", "make", "rust", "rust_analyzer", "manifest_targets", "gleam"):
        assert name in reg, f"registry missing {name!r}: discovery did not run"
    return reg


class TestEveryAnalyzerIsInADeclaredState:
    def test_taxonomy_state_languages_are_taxonomy_keys(self) -> None:
        offenders = [
            (a.name, a.languages)
            for a in _discovered().values()
            if a.language_state == LANGUAGE_STATE_TAXONOMY
            and not set(a.languages) <= set(LANGUAGES)
        ]
        assert offenders == []

    def test_no_spec_state_languages_are_absent_from_the_taxonomy(self) -> None:
        """A stale declaration (spec landed, declaration kept) is a defect."""
        offenders = [
            (a.name, a.languages)
            for a in _discovered().values()
            if a.language_state == LANGUAGE_STATE_NO_SPEC
            and (not a.languages or set(a.languages) & set(LANGUAGES))
        ]
        assert offenders == []

    def test_no_language_state_has_no_languages(self) -> None:
        offenders = [
            (a.name, a.languages)
            for a in _discovered().values()
            if a.language_state == LANGUAGE_STATE_NO_LANGUAGE and a.languages
        ]
        assert offenders == []

    def test_no_analyzer_language_equals_a_non_language_name(self) -> None:
        """The two phantoms and the one synonym, by name."""
        langs: set[str] = set()
        for a in _discovered().values():
            langs.update(a.languages)
        assert "rust_analyzer" not in langs
        assert "manifest_targets" not in langs
        assert "make" not in langs
        assert "makefile" in langs


class TestTheFourPrescribedDeclarations:
    """WI-juzig fix shape #3, one assertion per registration."""

    def test_rust_analyzer_is_a_scip_backend_for_rust(self) -> None:
        _discovered()
        reg = get_analyzer("rust_analyzer")
        assert reg is not None
        assert reg.languages == ["rust"]
        assert reg.backend == "scip"

    def test_rust_is_the_tree_sitter_backend_for_rust(self) -> None:
        _discovered()
        reg = get_analyzer("rust")
        assert reg is not None
        assert reg.languages == ["rust"]
        assert reg.backend == "tree-sitter"

    def test_two_backends_for_rust_are_now_distinguishable_in_the_registry(self) -> None:
        """The ADR-0057 §10 premise: the registry can tell they are two
        producers for ONE language, which it could not while
        ``rust_analyzer`` was its own language."""
        _discovered()
        claimants = sorted(
            a.name for a in get_all_analyzers() if "rust" in a.languages
        )
        assert claimants == ["rust", "rust_analyzer"]
        backends = {get_analyzer(n).backend for n in claimants}  # type: ignore[union-attr]
        assert backends == {"scip", "tree-sitter"}

    def test_make_speaks_the_taxonomy_name_makefile(self) -> None:
        _discovered()
        reg = get_analyzer("make")
        assert reg is not None
        assert reg.languages == ["makefile"]
        assert reg.backend == "tree-sitter"

    def test_manifest_targets_declares_no_language(self) -> None:
        _discovered()
        reg = get_analyzer("manifest_targets")
        assert reg is not None
        assert reg.language_state == LANGUAGE_STATE_NO_LANGUAGE
        assert reg.languages == []


class TestLanguageAxisCatalogHasNoPhantoms:
    def test_all_known_languages_drops_the_phantoms_and_keeps_makefile(self) -> None:
        _discovered()
        known = all_known_languages()
        assert "rust_analyzer" not in known
        assert "manifest_targets" not in known
        assert "make" not in known
        assert "makefile" in known
        # The coverage-gap languages stay: they ARE languages a symbol can carry.
        assert "gleam" in known
        assert "ansible" in known
