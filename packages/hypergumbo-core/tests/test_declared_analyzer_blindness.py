# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean verdict on a language whose analyzer cannot see external
instance-method calls must SAY SO (owner ruling 2026-08-23, "declare the
blindness").

THE PROBLEM THIS CLOSES. ``untyped_receiver_sites`` disclosed the sink call
sites whose receiver could not be typed — but it can only disclose an edge that
EXISTS. kotlin and javascript emit no external instance-method call edge at
all, so there was nothing to disclose and the verdict came out a bare
``confirmed``: silence that reads as safety over 232 catalogued method-kind
sinks (kotlin 181 of its 186 primitives, javascript 51 of 187).

The edge set cannot tell the two cases apart — "this repository contains no
external method calls" and "this analyzer never emits them" produce the same
empty set and opposite verdicts. So the fact is DECLARED, dated, with its
measurement, in :mod:`hypergumbo_core.analyzer_disclosure`, and the coverage
gate consults it.

WHY A CAVEAT AND NOT A WITHHOLD. kotlin and javascript DO emit call edges, just
not this shape. "I examined everything I could see, except this whole
construct" is a qualified opinion (ADR-0016 §4), and ``inconclusive`` would say
the analysis formed no view at all — false, and less useful to a reader. The
verdict still reads clean; the exit code moves 0 -> 3; the sentence names the
language and the scale.
"""

from hypergumbo_core.analyzer_disclosure import (
    DECLARATIONS,
    emits_external_method_call_edges,
    method_call_blind_languages,
)
from hypergumbo_core.io_boundary import (
    BoundaryMap,
    IoBoundaryCatalog,
    IoPrimitive,
    load_catalog,
)
from hypergumbo_core.verify_claims import (
    CAVEAT_ANALYZER_METHOD_CALL_BLIND,
    Claim,
    compute_boundary_coverage,
    verify_claim,
)

import glob
import os
from pathlib import Path


def _catalogued_languages() -> set[str]:
    import hypergumbo_core.io_boundary as iob
    root = Path(iob.__file__).parent / "io_primitives"
    return {os.path.basename(p)[:-5] for p in glob.glob(str(root / "*.yaml"))}


class TestTheDeclarationIsFailClosed:
    """An undeclared language is BLIND, not assumed fine."""

    def test_an_undeclared_language_does_not_emit(self) -> None:
        """A missing entry is "nobody measured", and the only safe reading of
        that for a gate whose other branch produces a clean security verdict is
        that the analysis may not have looked."""
        assert emits_external_method_call_edges("a-language-nobody-measured") is False

    def test_every_language_with_method_sinks_is_declared(self) -> None:
        """The fail-closed default is a safety net, not a substitute for
        measuring. A language arriving with method-kind sinks and no
        declaration fails HERE, where someone must go and measure it, rather
        than shipping a caveat nobody intended on every verdict.

        Driven from the shipped catalogues so it cannot drift.
        """
        undeclared = {
            lang for lang in _catalogued_languages()
            if any(getattr(p, "kind", None) == "method"
                   for p in load_catalog(lang).primitives)
            and lang not in DECLARATIONS
        }
        assert not undeclared, (
            f"languages declaring method-kind I/O sinks with no analyzer "
            f"declaration: {sorted(undeclared)}. Measure whether the analyzer "
            f"emits an external instance-method call edge and add a dated "
            f"entry to analyzer_disclosure.DECLARATIONS."
        )

    def test_every_declaration_carries_a_date_and_evidence(self) -> None:
        """An undated, unsourced ``emits=True`` is indistinguishable from a
        guess, and a guess in that direction produces a silent clean verdict."""
        for lang, decl in DECLARATIONS.items():
            assert decl.measured, lang
            assert len(decl.evidence) > 20, (lang, decl.evidence)


class TestWhichLanguagesTheCaveatIsAbout:
    """Both conditions are load-bearing and each excludes a different false
    positive."""

    def test_a_blind_language_absent_from_the_repo_raises_nothing(self) -> None:
        assert method_call_blind_languages(set(), {"kotlin"}) == []

    def test_a_language_with_no_method_sinks_raises_nothing(self) -> None:
        """``c`` / ``cpp`` / ``bash`` declare no method-kind sink at all, so an
        inability to see method calls cannot cost them anything — and a caveat
        that is always there is discounted by its reader."""
        assert method_call_blind_languages({"c"}, set()) == []

    def test_a_present_blind_language_with_method_sinks_is_named(self) -> None:
        assert method_call_blind_languages(
            {"kotlin", "java"}, {"kotlin", "java"},
        ) == ["kotlin"]


def _catalog(language: str) -> IoBoundaryCatalog:
    """A minimal catalogue with one METHOD-kind sink, so the language counts as
    one whose blindness matters."""
    return IoBoundaryCatalog(
        language=language,
        primitives=[IoPrimitive(boundary="net_send", module="net.Socket",
                                name="write", kind="method")],
        stdlib_modules=frozenset({"net"}),
        module_completeness={"net": "2026-08-23"},
    )


def _claim(boundary: str = "net_send") -> Claim:
    return Claim(id="C", text="t", constraint_boundary=boundary,
                 constraint_must_not_exist=True)


class TestTheVerdict:

    def test_a_clean_verdict_on_a_blind_language_is_not_bare(self) -> None:
        """THE POINT OF THE WHOLE PR. kotlin emits a call edge for the
        constructor and none for the method, so the analysis looks busy and
        sees none of the 181 method sinks its own catalogue declares."""
        # The dst names a module the fixture catalogue ENUMERATES: an
        # unenumerated one trips the uncatalogued-module gate and the verdict
        # is withheld before any caveat is reached, which would make this test
        # pass for the wrong reason on a later refactor.
        edges = [{
            "src": "kotlin:app/Main.kt:1-3:leak:function",
            "dst": "kotlin:net:0-0:Socket:external_symbol",
            "type": "calls", "line": 2,
        }]
        coverage = compute_boundary_coverage(
            edges, {"kotlin"}, {"kotlin": _catalog("kotlin")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        cav = next(c for c in verdict.caveats
                   if c["kind"] == CAVEAT_ANALYZER_METHOD_CALL_BLIND)
        assert cav["entries"] == ["kotlin"]
        assert "kotlin" in cav["detail"]

    def test_a_sighted_language_keeps_a_bare_confirmed(self) -> None:
        """FALSIFIABILITY CONTROL. Without this the test above is satisfied by
        a caveat that fires on everything, which a reader learns to ignore."""
        edges = [{
            "src": "java:app/Main.java:1-3:leak:method",
            "dst": "java:net:0-0:Socket:external_symbol",
            "type": "calls", "line": 2,
        }]
        coverage = compute_boundary_coverage(
            edges, {"java"}, {"java": _catalog("java")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_the_caveat_rides_alongside_the_untyped_receiver_one(self) -> None:
        """A polyglot repo can be blind in one language AND untyped in another;
        ``_merge_caveat`` exists because a second writer overwriting the first
        is this module's documented failure (INV-virat)."""
        edges = [
            {"src": "kotlin:app/Main.kt:1-3:leak:function",
             "dst": "kotlin:net:0-0:Socket:external_symbol",
             "type": "calls", "line": 2},
            {"src": "java:app/A.java:1-3:f:method",
             "dst": "java:external:0-0:write:external_symbol",
             "type": "calls", "line": 2,
             "meta": {"call_construct": "method"}},
        ]
        coverage = compute_boundary_coverage(
            edges, {"kotlin", "java"},
            {"kotlin": _catalog("kotlin"), "java": _catalog("java")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_ANALYZER_METHOD_CALL_BLIND in kinds, kinds
        assert len(kinds) >= 2, kinds


class TestTheCaveatSentence:
    """The prose is the product here — a machine-readable ``entries`` list
    nobody can read is not a disclosure."""

    def test_it_names_the_language_and_the_scale(self) -> None:
        """"Some calls were not seen" is unactionable; "kotlin, 181 of 186
        catalogued primitives are method-kind" tells a reader whether this
        verdict is worth anything for their repository. The counts come from
        the SHIPPED catalogue, so they cannot go stale against it."""
        from hypergumbo_core.verify_claims import (
            _analyzer_method_call_blind_caveat,
        )
        cav = _analyzer_method_call_blind_caveat(["kotlin"])
        assert "kotlin (" in cav["detail"]
        assert "method-kind" in cav["detail"]
        assert "Declared 2026-08-23" in cav["detail"]

    def test_two_blind_languages_read_as_plural(self) -> None:
        from hypergumbo_core.verify_claims import (
            _analyzer_method_call_blind_caveat,
        )
        cav = _analyzer_method_call_blind_caveat(["javascript", "kotlin"])
        assert "languages whose analyzer" in cav["detail"]
        assert cav["entries"] == ["javascript", "kotlin"]

    def test_an_undeclared_language_still_renders(self) -> None:
        """The fail-closed path reaches this builder too: a language blind
        because NOBODY MEASURED IT has no declaration to date, and the sentence
        must still be a sentence rather than a KeyError on a security
        disclosure."""
        from hypergumbo_core.verify_claims import (
            _analyzer_method_call_blind_caveat,
        )
        cav = _analyzer_method_call_blind_caveat(["a-language-nobody-measured"])
        assert "Declared" not in cav["detail"]
        assert "a-language-nobody-measured" in cav["detail"]
