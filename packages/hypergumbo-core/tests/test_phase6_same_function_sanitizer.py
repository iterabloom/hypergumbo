# SPDX-License-Identifier: AGPL-3.0-or-later
"""Same-function sanitization — WI-fasub, and INV-karud clause (b)'s limits.

THE DEFECT. A sanitizer called in the same function as the taint source did not
neutralize the flow. The barrier in ``_reachability_past_sanitizers`` exempts
the BFS seed — it must, because the seed is the taint origin and has to stay
reachable — and that exemption also means a barrier called *from* the seed
function is never consulted. So::

    def handler(token):
        plain = decrypt(token)     # source
        safe = encrypt(plain)      # sanitizer — ignored
        write(safe)                # sink

reported an unsanitized flow about code that visibly sanitizes. Encrypt-then-
write in one function is an ordinary shape, so this was systematic.

WHY THE FIX LIVES IN THE DDG PASS ONLY. Deciding it needs statement ordering
inside the seed function: did the sanitizer consume the tainted value, and did
what came out of it reach the sink? Call-graph reachability cannot express that
question — both calls have the same caller, so the graph is identical whichever
order they occur in. ``stmt_defuse`` (PR #203) supplies the ordering and reaches
``propagate_taint_ddg`` alone; ``propagate_taint_structural`` has no ordering at
all and therefore CANNOT honour same-function sanitization for any language.
That is a permanent scope limit, not a deferral, and
``test_structural_pass_cannot_honour_same_function_sanitization`` pins it so it
stays disclosed rather than becoming folklore.

THE EVIDENCE BAR. Marking a flow ``sanitized`` suppresses it from a claim's
violation set, so it is the expensive direction to get wrong and needs positive
evidence, exactly as §3a's removal arm does. Two walks must agree: the
unrestricted walk finds a data dependence source→sink AND the walk that treats
the sanitizer call as a barrier finds none *while accounting for every step*.
The barrier walk's ``None`` — the value escaped tracked ground — never earns the
label, because "I lost it" is not "you protected it".
"""
from __future__ import annotations

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    TaintSanitizer,
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
    propagate_taint_structural,
)

SOURCES = [TaintSource(
    taint_label="plaintext", module="crypto", name="decrypt", kind="function",
)]
SINKS = [TaintSink(
    zone="relay", trust_level="untrusted", module="net", name="send",
    kind="function",
)]
SANITIZERS = [TaintSanitizer(
    input_taint="plaintext", output_taint="ciphertext",
    qualified_name="crypto.encrypt",
)]

_FN = "python:a.py:1-9:handler:function"


def _call(dst_name: str, line: int) -> dict:
    """One call edge out of ``handler``, at a known line."""
    return {
        "src": _FN,
        "dst": f"python:external:0-0:{dst_name}:unresolved",
        "type": "calls",
        "line": line,
    }


def _ddg(*edges: tuple[str, int, int]) -> list[DdgEdge]:
    """``(variable, def_line, use_line)`` triples, keyed on the one function."""
    return [
        DdgEdge(
            variable=var, def_block="bb_0", def_line=dline,
            use_block="bb_0", use_line=uline, symbol_id=_FN,
        )
        for var, dline, uline in edges
    ]


def _stmts(*rows: tuple[int, tuple[str, ...], tuple[str, ...]]) -> dict:
    """``(line, defines, uses)`` statement rows for the one function."""
    return {_FN: list(rows)}


# The canonical fixture, shared so that every assertion below is about the one
# thing that differs between tests rather than about four unrelated fixtures.
#
#   1  plain = decrypt(token)
#   2  safe  = encrypt(plain)
#   3  send(safe)
_CANON_DDG = _ddg(("plain", 1, 2), ("safe", 2, 3))
_CANON_STMTS = _stmts((2, ("safe",), ("plain",)))
_CANON_CALLS = [_call("decrypt", 1), _call("encrypt", 2), _call("send", 3)]


class TestSameFunctionSanitizer:
    """WI-fasub: the seed exemption swallowed the barrier."""

    def test_same_function_sanitizer_neutralizes_the_flow(self) -> None:
        """The filed repro. Source, sanitizer and sink all in ``handler``.

        The tainted value defined at line 1 is consumed at line 2 by the
        sanitizer, and what reaches the sink at line 3 is the sanitizer's
        output. Every data route from source to sink passes through the
        barrier, so the flow is protected.
        """
        findings = propagate_taint_ddg(
            _CANON_DDG, _CANON_CALLS, SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is True
        assert findings[0].verdict == "confirmed_safe"

    def test_fixture_reaches_the_sink_without_the_sanitizer(self) -> None:
        """Non-vacuity floor (L17): the same fixture must violate unsanitized.

        Without this, a fixture that cannot reach its sink at all would satisfy
        any assertion about what the sanitizer does to it — which is precisely
        how ``test_sanitized_path_is_reported_as_sanitized`` passed for four
        months while exercising nothing.
        """
        findings = propagate_taint_ddg(
            _CANON_DDG, _CANON_CALLS, SOURCES, SINKS, [],
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].verdict == "violated"

    def test_forfeiting_function_does_NOT_earn_sanitized(self) -> None:
        """WI-joluk, wired. An unseen call site cannot protect a flow.

        This is the ONE production consumer the coverage gate changes. The
        §3a arm tests ``is True``, so ``False`` and ``None`` already collapse
        there; here a ``False`` earns ``sanitized`` and a sanitized flow is
        dropped from the claim's violation set. So a ``False`` produced from a
        function whose def/use extractor did not see part of the body
        SUPPRESSES A REAL VIOLATION — the expensive direction for a security
        tool.

        The pair is the argument: the canonical fixture above earns
        ``sanitized`` on identical inputs, and the only thing changed here is
        that ``_FN`` is declared to forfeit. So the downgrade is caused by the
        gate rather than by a fixture that stopped reaching its sink.

        Direction: strictly FEWER suppressions, hence strictly MORE surviving
        violations. That is why this could land before removal authority
        exists.
        """
        findings = propagate_taint_ddg(
            _CANON_DDG, _CANON_CALLS, SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
            forfeit_refutation={_FN},
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].verdict == "violated"

    def test_forfeiting_an_UNRELATED_function_changes_nothing(self) -> None:
        """The set is keyed by the SOURCE function, not consulted globally.

        Without this, an implementation that forfeited whenever the set was
        merely non-empty would pass the test above and silently suppress every
        sanitizer in the repo the moment one function anywhere was uncovered.
        """
        findings = propagate_taint_ddg(
            _CANON_DDG, _CANON_CALLS, SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
            forfeit_refutation={"go:other.go:1-2:somebody_else:function"},
        )
        assert findings[0].sanitized is True

    def test_confirmed_flow_keeps_its_precise_label_when_sanitized(self) -> None:
        """Sanitization relabels the flow; it does not downgrade the walk.

        ``sanitized`` and ``confidence`` answer different questions — "is this
        protected" versus "how well do we know it is a flow at all" — and the
        data dependence was confirmed by the same walk either way.
        """
        findings = propagate_taint_ddg(
            _CANON_DDG, _CANON_CALLS, SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
        )
        assert findings[0].confidence == "precise"
        assert findings[0].analysis_method == "ddg"


class TestSameFunctionSanitizerSoundness:
    """The label may only be earned on positive evidence.

    Each test here is a route by which a flow reaches the sink WITHOUT being
    protected, or a state in which protection cannot be established. All must
    stay ``sanitized=False``: suppressing a real violation is the expensive
    direction for a security tool.
    """

    def test_bypass_route_in_the_same_function_still_violates(self) -> None:
        """A second, unprotected use of the raw value wins.

            1  plain = decrypt(token)
            2  safe  = encrypt(plain)
            3  send(safe)
            4  send(plain)          <- raw

        Structurally identical to ``test_unsanitized_route_wins_when_both
        _exist`` one level down: unsanitized beats sanitized whenever both
        routes exist.
        """
        findings = propagate_taint_ddg(
            _ddg(("plain", 1, 2), ("safe", 2, 3), ("plain", 1, 4)),
            [*_CANON_CALLS, _call("send", 4)],
            SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].verdict == "violated"

    def test_sanitizer_before_the_source_does_not_neutralize(self) -> None:
        """Ordering is the whole point of the fix.

            1  safe  = encrypt(other)   <- sanitizes something else
            2  plain = decrypt(token)
            3  send(plain)

        The sanitizer runs, and the tainted value never goes near it. A
        line-number heuristic would need an explicit ordering test; the def-use
        walk gets this for free, because ``plain`` is simply never used at the
        barrier line.
        """
        findings = propagate_taint_ddg(
            _ddg(("plain", 2, 3)),
            [_call("encrypt", 1), _call("decrypt", 2), _call("send", 3)],
            SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_stmts((1, ("safe",), ("other",))),
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False

    def test_escaped_value_is_not_claimed_sanitized(self) -> None:
        """An escape on any route forfeits the claim.

            1  plain = decrypt(token)
            2  safe  = encrypt(plain)
            3  send(safe)
            4  bucket.append(plain)     <- leaves tracked ground

        Line 4 consumes the raw value into a container ADR-0017 §7b excludes
        from analysis, so the barrier walk returns ``None``: there is a route
        we cannot account for. "I lost track of it" is not "you protected it",
        which is L58 applied to the sanitization question rather than to
        removal.
        """
        findings = propagate_taint_ddg(
            _ddg(("plain", 1, 2), ("safe", 2, 3), ("plain", 1, 4)),
            _CANON_CALLS,
            SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False

    def test_no_confirmed_dependence_means_no_sanitization_claim(self) -> None:
        """If the walk never confirmed the flow, it cannot vouch for it either.

        The sink is called at line 3 and nothing tainted is used there, so the
        unrestricted walk confirms no dependence. The flow is still REPORTED —
        §3a is confirm-only and inclusion stays with call-graph reachability —
        but a flow whose data path was never established is not one we can
        certify as protected.
        """
        findings = propagate_taint_ddg(
            _ddg(("plain", 1, 2)),
            _CANON_CALLS,
            SOURCES, SINKS, SANITIZERS,
            ddg_symbols={_FN}, stmt_defuse=_CANON_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].analysis_method == "ddg_mixed"

    def test_sanitizer_without_ddg_coverage_is_unchanged(self) -> None:
        """No reaching-def data for the function → no ordering → no claim."""
        findings = propagate_taint_ddg(
            _CANON_DDG, _CANON_CALLS, SOURCES, SINKS, SANITIZERS,
            ddg_symbols=set(), stmt_defuse=_CANON_STMTS,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].analysis_method == "structural"


class TestStructuralPassLimit:
    """The structural propagator cannot honour same-function sanitization.

    Not a bug to be fixed later. ``propagate_taint_structural`` decides
    reachability on the call graph alone, where "handler calls encrypt" and
    "handler calls send" are two edges with no order between them, and no
    amount of work on that pass can recover one. Every language without a
    def/use extractor — which is most of the catalogue — is served by this pass.
    """

    def test_structural_pass_cannot_honour_same_function_sanitization(
        self,
    ) -> None:
        """Characterization, deliberately asserting the LIMIT.

        Same program as the DDG fixture, run through the pass that has no
        statement data. It reports the flow unsanitized, and a reader who takes
        that at face value is being misled — which is why the limit is
        published in the ``sanitizer_scope`` block rather than left to be
        rediscovered.
        """
        edges = [
            {"src": _FN, "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": _FN, "dst": "python:external:0-0:encrypt:unresolved",
             "type": "calls", "line": 2},
            {"src": _FN, "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 3},
        ]
        findings = propagate_taint_structural(edges, SOURCES, SINKS, SANITIZERS)
        assert len(findings) == 1
        assert findings[0].sanitized is False

    def test_structural_pass_still_honours_a_downstream_sanitizer(self) -> None:
        """The limit is same-function only — the barrier itself still works.

        Non-vacuity in the other direction: without this, deleting the barrier
        entirely would satisfy the test above.
        """
        edges = [
            {"src": _FN, "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": _FN, "dst": "python:a.py:20-29:store:function",
             "type": "calls", "line": 2},
            {"src": "python:a.py:20-29:store:function",
             "dst": "python:external:0-0:encrypt:unresolved",
             "type": "calls", "line": 21},
            {"src": "python:a.py:20-29:store:function",
             "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 22},
        ]
        findings = propagate_taint_structural(edges, SOURCES, SINKS, SANITIZERS)
        assert len(findings) == 1
        assert findings[0].sanitized is True
