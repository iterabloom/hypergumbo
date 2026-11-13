# SPDX-License-Identifier: AGPL-3.0-or-later
"""A resolved edge with no path evidence must not match by bare name (INV-fazim).

THE CHANNEL. ``_match_propagation_entry``'s ``is_resolved`` arm compares a
catalogue entry's declared module against the dst symbol's own path, normalised
to module shape (WI-damir). When the dst carries no path evidence — an
``external``-shaped or malformed id — that comparison has nothing to judge on.
Falling through to ``return hits[0]`` there would be an UNGATED bare-name match
with no receiver verification at all: the match that reports caddy's
``func Log()`` as a logging sink and d3's ``log()`` — the LOGARITHM — as
``console.log``, reached through a different door. So the arm refuses.

ADR-0037 ruling 4 is what makes it consequential rather than cosmetic: the
resolution verdict is read from ``Edge.is_resolved``, NOT from the
``:unresolved`` dst suffix, so a consumer cannot defend itself by string-checking
the dst. The flag is authoritative by design, and a producer that sets it wrongly
lands here.

WHY REFUSING IS SAFE. Refusing is not one-directional: the same index backs
SANITIZER registration, and losing a sanitizer match loses a barrier, which
moves flows the opposite way from losing a sink. It is safe because production
inputs do not reach the branch: ``finalize``'s edge-resolution sub-step sets
``is_resolved=True`` iff the dst is an in-repo node (whose path slot is a real
path), and the producer half is closed at every go.py emit site. Checking that
needs a probe on production's real inputs, classified with production's own
``_module_from_symbol_path``, plus a positive control: counting "edges with
is_resolved=True at an external path slot" over the final graph is zero by
finalize's own definition whether or not the branch is reachable, and a zero
from a probe that never attached looks like a zero from a branch never taken.

WHAT THIS TEST IS FOR. "Unreached" is not "unreachable": any producer, in any
language, that sets ``is_resolved=True`` on a pathless dst lands here, and
nothing else would catch it. These tests are that catch.
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
    STILL match — that arm (WI-damir) carries real findings.
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
