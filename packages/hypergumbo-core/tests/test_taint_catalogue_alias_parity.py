# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-potuf: an alias applied to one half of a catalogue pair must be applied
to the other.

THE DEFECT. ``_derive_auto_imports_from_io_primitives`` iterates the FILE STEMS
in ``io_primitives/`` — fifteen of them — and buckets each derived entry under
``catalog.language``. ``_CATALOG_ALIASES`` maps ``typescript -> javascript`` and
``groovy -> java``, and NEITHER key has a stem of its own. So neither language
is ever visited by that loop, and both derive **zero** sinks.

``load_catalog("typescript")`` already returns javascript's rows — the alias
resolution works. The loop simply never asks for it.

WHY THAT IS A LIVE FALSE-CONFIRM CHANNEL AND NOT A COSMETIC GAP. Measured on a
real ``.ts`` file: the JS/TS analyzer emits symbol and edge ids prefixed
``typescript:``, so production asks ``sinks_for_language("typescript")`` and
gets ``[]``. ``fs.writeFileSync`` — a genuine ``fs_write`` — matches nothing.
TypeScript's six taint SOURCES are hand-written and DO key under ``typescript``,
so the two halves of one language disagree about what that language is called:
a flow can start and can never arrive.

THE PRECEDENT IS ALREADY IN THE TREE, IN THE SIBLING ARM. ``cli.py`` keys the
BOUNDARY catalogue under both names for exactly this reason, with a comment
naming both aliases:

    catalogs[lang] = catalog
    # Also key by the catalog's base language so edge-prefix lookups work when
    # the catalog's base differs from the Symbol's language (e.g. catalog
    # aliases like typescript->javascript or groovy->java).
    if catalog.language != lang:
        catalogs[catalog.language] = catalog

So this restores a symmetry the codebase already believes in on one arm and
forgot on the other — which is the invariant's own wording.

THE TRAP THE ITEM FILED AGAINST ITSELF, and why every assertion here is about
DERIVED COUNTS rather than about files:

    RE-EVAL TRIGGER: adding a typescript.yaml sends its rows to javascript and
    changes NOTHING — check the derived counts, not the file listing.

A test asserting "a typescript catalogue exists" would pass under the bug.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.taint import load_builtin_taint_catalog


@pytest.fixture(scope="module")
def catalog():
    return load_builtin_taint_catalog()


class TestBothHalvesResolveUnderTheSameName:
    """A flow needs a source AND a sink under the language the EDGES carry."""

    @pytest.mark.parametrize("language", ["typescript", "groovy"])
    def test_an_aliased_language_derives_sinks(
        self, catalog, language: str,
    ) -> None:
        assert catalog.sinks_for_language(language), (
            f"{language} derives ZERO sinks, so no flow can complete in it and "
            f"every {language} taint verdict is vacuous"
        )

    @pytest.mark.parametrize("language", ["typescript", "groovy"])
    def test_an_aliased_language_derives_sources(
        self, catalog, language: str,
    ) -> None:
        assert catalog.sources_for_language(language)

    def test_typescript_resolves_to_javascripts_rows_not_a_subset(
        self, catalog,
    ) -> None:
        """TypeScript I/O IS JavaScript I/O — ``fs.writeFileSync`` is one
        primitive, not two — so the alias must deliver the whole surface.

        Asserted as a superset rather than equality because typescript ALSO
        carries hand-written sources of its own (``crypto.subtle.decrypt``),
        and an equality test would force a choice between the two halves.
        """
        ts_sinks = {s.qualified_name for s in
                    catalog.sinks_for_language("typescript")}
        js_sinks = {s.qualified_name for s in
                    catalog.sinks_for_language("javascript")}
        assert js_sinks, "the javascript control is empty — instrument problem"
        assert js_sinks <= ts_sinks

    def test_typescripts_own_hand_written_sources_survive_the_alias(
        self, catalog,
    ) -> None:
        """The other direction, and the one a careless fix breaks.

        Resolving reads through the alias WITHOUT merging would hand back
        javascript's entries and drop typescript's six hand-written sources —
        trading one silent half-coverage for the mirror image of it.
        """
        labels = {s.taint_label for s in
                  catalog.sources_for_language("typescript")}
        assert "plaintext" in labels, (
            "typescript's hand-written crypto sources were lost when the "
            "alias was applied"
        )


class TestTheNonAliasedLanguagesAreUnCHANGED:
    """The control. This change must move exactly two languages.

    Without it, "typescript now has sinks" is equally consistent with a fix and
    with a bucketing bug that smeared every catalogue across every language.
    """

    @pytest.mark.parametrize(
        "language", ["python", "javascript", "go", "java", "c", "rust"],
    )
    def test_a_language_with_its_own_stem_keeps_its_own_rows(
        self, catalog, language: str,
    ) -> None:
        modules = {s.module for s in catalog.sinks_for_language(language)}
        assert modules, f"{language} lost its sinks entirely"

    def test_python_did_not_acquire_javascript_rows(self, catalog) -> None:
        py = {s.qualified_name for s in catalog.sinks_for_language("python")}
        js = {s.qualified_name for s in
              catalog.sinks_for_language("javascript")}
        assert not (js <= py), (
            "python now contains javascript's whole sink surface — the "
            "derivation is bucketing by the wrong key"
        )

    def test_java_is_not_polluted_by_its_groovy_alias(self, catalog) -> None:
        """groovy resolves TO java; java must not gain anything in return.

        The alias is directional, and a symmetric implementation would be a
        silent widening of java's surface.
        """
        java = {s.qualified_name for s in catalog.sinks_for_language("java")}
        groovy = {s.qualified_name for s in
                  catalog.sinks_for_language("groovy")}
        assert java == groovy, (
            "groovy should be java's surface exactly — no more, no less"
        )
