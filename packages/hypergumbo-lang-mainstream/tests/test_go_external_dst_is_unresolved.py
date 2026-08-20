# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fazim: an ``external`` dst carries no module evidence, so it is not resolved.

THE INVARIANT. ``go:external:0-0:<callee>:unresolved`` names a call the analyzer
could not attribute to any package: the path slot is the ``external`` placeholder
and the kind slot literally reads ``unresolved``. ``Edge.create`` defaults
``is_resolved=True``, so every emit site that stays silent about the flag mints a
"resolved" edge onto exactly that dst.

WHY THE FLAG IS CONSEQUENTIAL, not cosmetic. ADR-0037 ruling 4 makes
``Edge.is_resolved`` authoritative and forbids consumers from re-deriving the
verdict by string-checking the dst — so a downstream gate cannot defend itself.
``taint._match_propagation_entry`` branches on ``is_resolved``, computes
``_module_from_symbol_path(dst)``, finds no path evidence for the ``external``
placeholder, and takes the documented legacy escape ``if not path_module: return
hits[0]`` — an UNGATED bare-name match. ``gate_named_entry``, which refuses an
untyped method call, is never reached. That is the door WI-damir records as
measured 30-of-30 FALSE on the nine-repo cohort (caddy's own ``func Log()``
reported as a logging sink 18 times).

THE FOUR SITES, AND WHY THIS FILE EXISTS. Two of go.py's six ``external`` emit
sites already state ``is_resolved=False`` — the field-chain guard and the WI-jopar
receiver guard, the latter with a comment naming INV-fazim explicitly. THREE DID
NOT, and each is reachable by a distinct receiver shape, because the two guards
that do state the flag both consume a *tracked local identifier* receiver and so
shadow the others for the obvious test input. Reproduced live before the fix:

    items[0].Handle()   -> is_resolved=True  conf=0.50  resolution_quality=ambiguous
    items[0].doWork()   -> is_resolved=True  conf=0.85  visibility=unexported
    r.Body.Close()      -> is_resolved=True  conf=0.85  receiver=stdlib

A parity test over the emit sites is the load-bearing assertion here rather than
three independent expectations: this is the fourth time in this subsystem that N
places which should share one rule had drifted, and a fifth site added tomorrow
must fail this file rather than ship silently.

BLAST RADIUS IS STATED IN THE PR, NOT CLAIMED HERE. ``finalize``'s edge-resolution
sub-step independently flips ``is_resolved`` for an ``external`` dst and (since
PR #248) re-derives confidence from its own verdict, so the shipped behaviour-map
may already be correct end-to-end and this is a producer-side latent fix. That is
the INV-fokik shape and it is worth fixing anyway — the analyzer stage is consumed
directly by tests, by ``dst_ref`` derivation, and by anything that reads edges
before finalize — but no recall win is claimed from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent (WI-jolif).

    A ``try/except Exception: pytest.skip`` cannot tell "grammar missing" from
    "my probe is wrong", and that exact shape hid an ``AttributeError`` long
    enough that three integration tests never executed once. No handler here.
    """
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


def _analyze(tmp_path: Path, source: str):
    from hypergumbo_lang_mainstream.go import analyze_go

    repo = tmp_path / "fakerepo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
    (repo / "main.go").write_text(source)
    analysis = analyze_go(repo)
    assert not analysis.skipped
    return analysis


def _external_call_edges(analysis) -> list:
    return [
        e for e in analysis.edges
        if e.edge_type == "calls" and ":external:0-0:" in e.dst
    ]


#: Ambiguity guard (2+ in-repo candidates for one method name). The receiver is an
#: INDEX EXPRESSION so the WI-jopar identifier guard does not consume it first.
_AMBIGUOUS = '''\
package main

type A struct{}

func (a A) Handle() {}

type B struct{}

func (b B) Handle() {}

func main() {
    items := getItems()
    items[0].Handle()
}
'''

#: Go visibility guard: a lowercase method on an untracked receiver.
_UNEXPORTED = '''\
package main

func main() {
    items := getItems()
    items[0].doWork()
}
'''

#: Stdlib-interface guard: ``Close`` on a FIELD receiver (``resp.Body.Close()``),
#: the shape the guard's own comment is written about.
_STDLIB_INTERFACE = '''\
package main

type R struct{ Body B }

type B struct{}

func main() {
    var r R
    r.Body.Close()
}
'''


class TestEveryExternalDstIsUnresolved:
    """One rule, asserted at every site that can emit the placeholder."""

    @pytest.mark.parametrize(("source", "callee", "marker"), [
        (_AMBIGUOUS, "Handle", "resolution_quality"),
        (_UNEXPORTED, "doWork", "visibility"),
        (_STDLIB_INTERFACE, "Close", "receiver"),
    ])
    def test_external_dst_is_not_reported_resolved(
        self, tmp_path: Path, go_available, source: str, callee: str, marker: str,
    ) -> None:
        analysis = _analyze(tmp_path, source)
        edges = [
            e for e in _external_call_edges(analysis)
            if e.dst.split(":")[-2] == callee
        ]
        assert edges, (
            f"no external call edge for {callee}; got "
            f"{sorted({e.dst for e in analysis.edges if e.edge_type == 'calls'})}"
        )
        for edge in edges:
            # The marker pins WHICH guard emitted it, so a refactor that quietly
            # routes this shape through a different (already-correct) branch
            # cannot make the test pass without the site under test being fixed.
            assert marker in (edge.meta or {}), (
                f"expected the {marker!r} guard to emit this edge; meta={edge.meta}"
            )
            assert edge.is_resolved is False, (
                f"{edge.dst} carries the 'external' path placeholder and the "
                f"'unresolved' kind slot, but is reported is_resolved=True — which "
                f"is authoritative under ADR-0037 ruling 4 and routes taint's "
                f"propagation lookup into the ungated bare-name fallback"
            )

    def test_no_external_dst_anywhere_claims_resolution(
        self, tmp_path: Path, go_available,
    ) -> None:
        """THE PARITY ASSERTION. Not "these three sites", but "any site".

        A sixth emit site added later, or an existing one whose guard order
        changes, has to satisfy this without anyone remembering to extend the
        parametrisation above.
        """
        combined = _AMBIGUOUS.replace("func main()", "func mainA()") + "\n" + \
            _UNEXPORTED.replace("package main\n", "").replace(
                "func main()", "func mainB()") + "\n" + \
            _STDLIB_INTERFACE.replace("package main\n", "").replace(
                "func main()", "func mainC()")
        analysis = _analyze(tmp_path, combined)
        offenders = [
            (e.dst, e.meta) for e in _external_call_edges(analysis)
            if e.is_resolved is not False
        ]
        assert offenders == [], (
            f"external dsts reported as resolved: {offenders}"
        )

    def test_route_mount_flag_mirrors_the_branch_not_a_constant(
        self, tmp_path: Path, go_available,
    ) -> None:
        """The one site whose dst may be resolved OR external, asserted BOTH ways.

        ``r.Mount("/api", apiRoutes())`` resolves to an in-repo symbol; the same
        call with a handler this repo does not define falls back to the
        ``external`` placeholder. Blanket-setting ``is_resolved=False`` here would
        be as wrong as inheriting the ``True`` default, and a test that exercised
        only the external branch would pass for the blanket fix. Both branches are
        therefore asserted from ONE analysis of ONE file.
        """
        source = '''\
package main

import "github.com/go-chi/chi/v5"

func apiRoutes() *chi.Mux { return chi.NewRouter() }

func setup() {
    r := chi.NewRouter()
    r.Mount("/api/v1", apiRoutes())
    r.Mount("/ext/v1", externalRoutes())
}
'''
        analysis = _analyze(tmp_path, source)
        mounts = {
            e.dst: e.is_resolved for e in analysis.edges
            if (e.meta or {}).get("framework_dispatch") == "route_mount"
        }
        assert len(mounts) == 2, f"expected both mount edges, got {mounts}"
        resolved = {d: r for d, r in mounts.items() if ":external:0-0:" not in d}
        external = {d: r for d, r in mounts.items() if ":external:0-0:" in d}
        assert resolved and all(resolved.values()), (
            f"an in-repo mount handler must stay resolved: {resolved}"
        )
        assert external and not any(external.values()), (
            f"an unknown mount handler carries the external placeholder and must "
            f"not claim resolution: {external}"
        )

    def test_the_fixture_actually_exercises_three_distinct_guards(
        self, tmp_path: Path, go_available,
    ) -> None:
        """NON-VACUITY FLOOR. Three sources that all fell through to ONE
        already-correct guard would pass every assertion above while testing
        nothing — which is exactly what the first draft of these fixtures did
        (all three were consumed by the WI-jopar identifier guard, whose
        ``receiver: external`` marker already states the flag)."""
        markers = set()
        for source in (_AMBIGUOUS, _UNEXPORTED, _STDLIB_INTERFACE):
            sub = tmp_path / f"case{len(markers)}"
            sub.mkdir()
            analysis = _analyze(sub, source)
            for edge in _external_call_edges(analysis):
                meta = edge.meta or {}
                for key in ("resolution_quality", "visibility", "receiver"):
                    if key in meta:
                        markers.add(f"{key}={meta[key]}")
        assert markers == {
            "resolution_quality=ambiguous",
            "visibility=unexported",
            "receiver=stdlib",
        }, f"fixtures collapsed onto the wrong guards: {sorted(markers)}"
