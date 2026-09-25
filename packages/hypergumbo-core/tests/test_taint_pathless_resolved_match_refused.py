# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fazim: a resolved edge with no path evidence must not match by bare name.

THE CHANNEL. ``_match_propagation_entry``'s ``is_resolved`` arm compares a
catalogue entry's declared module against the dst symbol's own path, normalised
to module shape (WI-damir). When the dst carries no path evidence — an
``external``-shaped or malformed id — that comparison has nothing to judge on,
and the arm used to fall through to ``return hits[0]``: an UNGATED bare-name
match with no receiver verification at all. That is the same ungated return
WI-damir measured 30 of 30 false on a 9-repo cohort (caddy's ``func Log()``
reported as a logging sink 18 times; d3's ``log()`` — the LOGARITHM — reported
as ``console.log``), reached through a different door.

ADR-0037 ruling 4 is what made it consequential rather than cosmetic: the
resolution verdict is read from ``Edge.is_resolved``, NOT from the
``:unresolved`` dst suffix, so a consumer cannot defend itself by string-checking
the dst. The flag is authoritative by design, and a producer that sets it wrongly
lands here.

WHY IT IS SAFE TO REFUSE, measured rather than assumed. This item's thread
required a NON-TAUTOLOGICAL instrument before anything touched this line, because
both obvious measurements are tautologies: ``finalize``'s edge-resolution
sub-step sets ``is_resolved=True`` iff the dst is an in-repo node (whose path slot
is a real path), so counting "edges with is_resolved=True at an external path
slot" returns zero whether or not the branch is reachable — it measures
finalize's own definition. The thread also required a positive control, since a
zero from a probe that never attached is indistinguishable from a zero from a
branch never taken.

Both were done on dev 09921c57a1, observing production's real inputs during real
``verify-claims`` runs and classifying them with production's OWN
``_module_from_symbol_path``:

    repo      calls    is_resolved=True    escape condition
    sops       7,738            2,592            0
    grype     25,178            9,380            0
    act       10,696            3,222            0
    poetry    24,660            9,666            0
    winston    1,936            1,190            0
    knex      28,214           22,466            0
    TOTAL     98,422           48,516            0

Four languages (go, python, javascript, typescript, plus bash). The counted
condition is a SUPERSET of the branch (it ignores the non-empty-``hits``
requirement), so it is conservative in the direction that matters. A positive
control driving the branch deliberately fires it, so the zeros are meaningful.

Because the line never executes on that corpus, changing what it returns cannot
change those repos' findings — the direction warning this item carries (the same
index backs SANITIZER registration, and losing a sanitizer match loses a barrier,
which moves flows the opposite way from losing a sink) is answered by the count
rather than argued around.

WHAT THIS TEST IS FOR. The producer half is closed at every go.py emit site, and
the consumer branch is now demonstrably unreached. But "unreached today" is not
"unreachable": any producer, in any language, that sets ``is_resolved=True`` on a
pathless dst lands here, and nothing would catch it. These tests are that catch.
"""
from __future__ import annotations

from hypergumbo_core.taint import (
    TaintSink,
    _match_propagation_entry,
    _module_from_symbol_path,
)


def _sink(name: str, module: str) -> TaintSink:
    return TaintSink(
        zone="logging", trust_level="untrusted",
        module=module, name=name, kind="function",
    )


def test_resolved_dst_with_external_placeholder_path_does_not_match():
    """The filed channel: `external` in the path slot names no module to judge on."""
    entry = _sink("RoundTrip", "net/http")
    index = {"RoundTrip": [entry]}
    dst = "go:external:0-0:RoundTrip:unresolved"

    # Premise check, using production's own resolver rather than asserting the
    # shape by hand: this dst really does carry no path evidence.
    assert not _module_from_symbol_path(dst)

    got = _match_propagation_entry(
        index, dst, frozenset(), None, is_resolved=True, language="",
    )
    assert got is None, (
        "a resolved edge whose dst carries no path evidence matched a catalogue "
        "entry by bare name alone — the ungated hits[0] escape is back"
    )


def test_resolved_dst_with_malformed_id_does_not_match():
    """The second door: an id _extract_callee_module cannot parse lands here too."""
    entry = _sink("write", "os")
    index = {"write": [entry]}
    dst = "write"  # no colons at all — nothing to extract a module from

    assert not _module_from_symbol_path(dst)

    got = _match_propagation_entry(
        index, dst, frozenset(), None, is_resolved=True, language="",
    )
    assert got is None


def test_resolved_dst_with_a_real_path_still_matches():
    """POSITIVE CONTROL. Refusing everything would pass the two tests above.

    A resolved dst whose path normalises to the entry's declared module must
    STILL match — that is the arm WI-damir built and it carries real findings.
    Without this the tests above are satisfied by a function that returns None
    unconditionally.
    """
    entry = _sink("cmd_sketch", "hypergumbo_core.cli")
    index = {"cmd_sketch": [entry]}
    dst = "python:packages/hypergumbo-core/src/hypergumbo_core/cli.py:10-20:cmd_sketch:function"

    assert _module_from_symbol_path(dst), "premise: this dst DOES carry path evidence"

    got = _match_propagation_entry(
        index, dst, frozenset(), None, is_resolved=True, language="",
    )
    assert got is entry


def test_entry_declaring_no_module_still_matches_on_a_real_path():
    """Second positive control: the no-declared-module legacy arm is untouched.

    An entry that declares no module carries no evidence to contradict a match,
    and ``_match_propagation_entry`` preserves legacy behaviour for it. That arm
    is separate from the pathless escape and must not be collaterally closed.
    """
    entry = _sink("cmd_sketch", "")
    index = {"cmd_sketch": [entry]}
    dst = "python:packages/hypergumbo-core/src/hypergumbo_core/cli.py:10-20:cmd_sketch:function"

    got = _match_propagation_entry(
        index, dst, frozenset(), None, is_resolved=True, language="",
    )
    assert got is entry
