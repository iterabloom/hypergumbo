# SPDX-License-Identifier: AGPL-3.0-or-later
"""The taint census and the coverage check must share a population (INV-sarum).

``_taint_blind_reason`` runs two checks that have to agree about what
"present in this repo" means:

1. the language census, built from behavior-map NODES;
2. ``compute_boundary_coverage``, which walks production-scoped EDGES.

BOTH must be production-scoped. Scoping only the EDGE list leaves the NODE
census that decides which languages are *supported* unscoped, so a language
present only as test fixtures counts as present (check 1) while its edges are
deliberately excluded (check 2), and the pair reads as analyzer blindness:
"supported language(s) ... were analyzed but produced no call edges" — for
every language whose files all live under a test or fixture directory.

This is INV-motos' shape: sharing the *predicate* is not enough when the
callers run it over different *populations*.

WHICH DIRECTION IS CORRECT is a threat-model question, answered rather than
assumed. ``SECURITY.md``'s scope statement (WI-kozos)
declares that the audit covers the INSTALLED CLI, so a fixture is not part of
what the claims describe and the census must be production-scoped, in parity
with WI-bifob's flow filter.

THE OBVIOUS FIX WOULD BE WRONG, which is why the second test below matters
more than the first. Narrowing the supported set to "languages that produced
scoped call edges" also makes it impossible to flag a language that IS
production code and emits nothing — e.g. Kotlin, whose catalogued sinks the
analyzer mostly misses. That would convert the F69.A1 blindness signal into a
silent pass. The narrowing must be by PRESENCE IN PRODUCTION SOURCE, never by
having produced edges.
"""

from hypergumbo_core.cli import _census_languages, _taint_blind_reason
from hypergumbo_core.io_boundary import load_catalog


def _catalogs(*langs: str) -> dict:
    """Real catalogues, because an empty dict blocks on "made calls but
    have no I/O catalog" — a DIFFERENT gate, which would make both tests
    below pass for a reason unrelated to the census."""
    return {lang: load_catalog(lang) for lang in langs}


def _node(lang: str, path: str) -> dict:
    return {"id": f"{lang}:{path}:1-2:f:function", "language": lang,
            "path": path}


def _call(lang: str, path: str, dst: str) -> dict:
    return {"src": f"{lang}:{path}:1-2:f:function", "dst": dst,
            "type": "calls"}


# -- the census itself -----------------------------------------------


def test_census_drops_a_fixture_only_language():
    nodes = [
        _node("python", "src/app.py"),
        _node("go", "tests/fixtures/sample.go"),
    ]
    assert _census_languages(nodes) == {"python"}


def test_census_keeps_a_language_with_any_production_file():
    """One production file is enough — a language is not fixture-only just
    because most of its files are fixtures."""
    nodes = [
        _node("go", "tests/fixtures/sample.go"),
        _node("go", "cmd/server/main.go"),
    ]
    assert _census_languages(nodes) == {"go"}


def test_census_can_be_widened_the_same_way_the_flow_filter_is():
    """``--include-non-production-sources`` must widen BOTH, or the two can
    disagree in the other direction."""
    nodes = [_node("go", "tests/fixtures/sample.go")]
    assert _census_languages(nodes, include_non_production=True) == {"go"}


def test_a_synthetic_external_node_is_not_evidence_of_presence():
    """An ``<external>`` node is not production source.

    ``<external>`` is the sentinel for a reference TARGET rather than a
    source file. Counting every node whose path does not classify as a
    test would keep a fixture-only language in the census on the strength
    of the ``<external>`` nodes its fixtures reference — exactly the
    languages this census must drop.

    An ``<external>`` node exists BECAUSE a fixture referenced it. Reading it
    as evidence that the language is present in shipped code inverts its
    meaning.
    """
    nodes = [{"id": "go:<external>:0-0:Open:unresolved", "language": "go",
              "path": "<external>"}]
    assert _census_languages(nodes) == set()


def test_a_node_with_no_path_at_all_is_not_evidence_either():
    """Same reasoning: a path-less node names no source file, so it cannot
    show that a language is present in production source."""
    assert _census_languages([{"id": "go:::f:function", "language": "go"}]) == set()


def test_one_real_production_file_is_enough():
    """The other direction — the narrowing must not eat a real language."""
    nodes = [{"id": "go:<external>:0-0:Open:unresolved", "language": "go",
              "path": "<external>"},
             _node("go", "cmd/server/main.go")]
    assert _census_languages(nodes) == {"go"}


def test_census_ignores_nodes_with_no_language():
    assert _census_languages([{"id": "x", "path": "src/a.py"}]) == set()


# -- the two checks agree --------------------------------------------


def test_a_fixture_only_language_no_longer_blocks():
    """The INV-sarum repro: go exists only under tests/fixtures, so its edges
    are excluded from the scoped list and it must not be counted present."""
    nodes = [_node("python", "src/app.py"),
             _node("go", "tests/fixtures/sample.go")]
    edges = [_call("python", "src/app.py", "python:os:0-0:listdir:unresolved"),
             _call("go", "tests/fixtures/sample.go", "go:os:0-0:Open:unresolved")]
    reason, _ = _taint_blind_reason(
        has_taint_claims=True,
        unsupported_taint_languages=[],
        raw_edges=edges,
        taint_supported_languages=_census_languages(nodes),
        catalogs=_catalogs("python", "go"),
    )
    assert reason is None or "go" not in reason, reason


def test_a_PRODUCTION_language_that_emits_nothing_STILL_blocks():
    """The control that keeps the fix honest (F69.A1 / INV-linub).

    Kotlin here is production code with a catalogue that produced no call
    edges — precisely the case the check exists to catch. If this ever passes
    silently, the narrowing has eaten the signal it was meant to preserve and
    the tool reports blindness as safety.
    """
    nodes = [_node("python", "src/app.py"),
             _node("kotlin", "src/main/kotlin/App.kt")]
    edges = [_call("python", "src/app.py", "python:os:0-0:listdir:unresolved")]
    reason, _ = _taint_blind_reason(
        has_taint_claims=True,
        unsupported_taint_languages=[],
        raw_edges=edges,
        taint_supported_languages=_census_languages(nodes),
        catalogs=_catalogs("python", "kotlin"),
    )
    assert reason is not None and "kotlin" in reason, (
        f"a production language emitting no call edges must still block; "
        f"got {reason!r}"
    )
