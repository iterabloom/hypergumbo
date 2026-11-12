# SPDX-License-Identifier: AGPL-3.0-or-later
"""The taint catalogue must inherit parent io_primitives, exactly as io-boundaries does.

``io_boundary._CATALOG_PARENTS`` declares the inheriting languages — ``cpp <- c``,
``kotlin <- java``, ``scala <- java``, ``elixir <- erlang`` — and
``io_boundary.load_catalog`` honours it by merging the parent catalogue. The
taint catalogue is derived from the same YAML by
``_derive_auto_imports_from_io_primitives``. Loading a child file directly
(``IoBoundaryCatalog.from_yaml(path)``) inherits NOTHING: the child is left
with a small fraction of its parent's sources and sinks (C++ with a handful of
primitives against C's dozens) and without the parent's ``ambiguous_names``,
the short-name collision guard — so it has BOTH less to match against AND a
weaker guard against false matches. Two consumers of one data file must agree
about inheritance (L50).

WHY THE EVIDENCE IS CATALOGUE-LEVEL. A cohort with no cpp, kotlin, scala or
elixir source cannot observe this difference at all (L59: before running an
A/B, check that a path exists by which the difference reaches the number), so
the property is asserted directly on the loaded catalogues rather than
inferred from a cohort.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import _CATALOG_PARENTS, load_catalog
from hypergumbo_core.taint import load_builtin_taint_catalog

PARENTED = sorted(_CATALOG_PARENTS)


@pytest.fixture(scope="module")
def taint_catalog():
    return load_builtin_taint_catalog()


class TestParentedLanguagesInheritTheirParent:
    """The property, per language, so a failure names the language."""

    @pytest.mark.parametrize("lang", PARENTED)
    def test_ambiguous_names_match_io_boundary(self, lang, taint_catalog) -> None:
        """The two consumers must agree on the collision guard.

        INV-mivud's statement is explicit that the list must work for *either*
        consumer — "the io-boundaries chain matcher or taint propagation".
        """
        io_names = {n for n in load_catalog(lang).ambiguous_names if isinstance(n, str)}
        taint_names = set(taint_catalog.ambiguous_names_for_language(lang))
        missing = io_names - taint_names
        assert not missing, (
            f"{lang}: taint is missing {len(missing)} ambiguous_names that "
            f"io-boundaries has, inherited from {_CATALOG_PARENTS[lang]}: "
            f"{sorted(missing)[:10]}"
        )

    @pytest.mark.parametrize("lang", PARENTED)
    def test_taint_entries_cover_the_parent_surface(self, lang, taint_catalog) -> None:
        """A child must have at least as many taint entries as its parent.

        Stated as a floor rather than an exact count so that adding a primitive
        to either file does not make this test wrong — the defect being pinned
        is total non-inheritance, where the child has a small fraction of the
        parent's surface.
        """
        parent = _CATALOG_PARENTS[lang]
        n_child = (len(taint_catalog.sources_for_language(lang))
                   + len(taint_catalog.sinks_for_language(lang)))
        n_parent = (len(taint_catalog.sources_for_language(parent))
                    + len(taint_catalog.sinks_for_language(parent)))
        assert n_child >= n_parent, (
            f"{lang} has {n_child} taint entries but inherits from {parent} "
            f"which has {n_parent}; the parent catalogue is not being merged"
        )

    def test_cpp_is_not_running_taint_on_three_primitives(
        self, taint_catalog,
    ) -> None:
        """The headline instance, pinned by name.

        Without the parent merge C++ has only a few taint entries against C's
        dozens. A regression here is not a small drift — it is C++ taint
        analysis silently becoming inert, the ADR-0017 failure family.
        """
        n = (len(taint_catalog.sources_for_language("cpp"))
             + len(taint_catalog.sinks_for_language("cpp")))
        assert n >= 60, f"cpp has only {n} taint entries; parent merge is not applied"


class TestTheGuardIsWellFormed:
    """A YAML 1.1 trap that silently voids an entry."""

    @pytest.mark.parametrize("lang", ["javascript", "scala"])
    def test_no_boolean_leaked_into_ambiguous_names(self, lang) -> None:
        """``- on`` parses as the BOOLEAN True under YAML 1.1, not the string.

        ``javascript.yaml`` and ``scala.yaml`` each list ``on`` in
        ``ambiguous_names``, and it must be quoted: a bare ``on`` loads as
        ``True``, so the string ``"on"`` is never suppressed while the list
        *looks* correct. A text grep cannot reliably see this — the entry reads
        ``on`` either way — so the assertion is made against the LOADED value.
        """
        names = load_catalog(lang).ambiguous_names
        bad = [n for n in names if not isinstance(n, str)]
        assert not bad, (
            f"{lang}: ambiguous_names contains non-string {bad!r} — a bare "
            f"on/off/yes/no in YAML 1.1 becomes a bool, so that entry "
            f"suppresses nothing"
        )

    def test_no_language_leaks_a_non_string(self) -> None:
        """The whole-catalogue form, so no other file can introduce it."""
        from pathlib import Path

        from hypergumbo_core.taint import _IO_PRIMITIVES_DIR
        offenders = {}
        for yaml_path in sorted(Path(_IO_PRIMITIVES_DIR).glob("*.yaml")):
            cat = load_catalog(yaml_path.stem)
            bad = [n for n in cat.ambiguous_names if not isinstance(n, str)]
            if bad:
                offenders[yaml_path.name] = bad
        assert not offenders, f"non-string ambiguous_names entries: {offenders}"
