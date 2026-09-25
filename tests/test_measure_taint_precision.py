# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adjudication collector must carry the tool's OWN disclosure forward.

WHAT WENT WRONG, and it cost two measurements. ``verify-claims`` emits
``walk_verdict`` and ``walk_blocked_by`` on every evidence row. For a
``structural`` finding it emits ``walk_verdict: "unavailable"`` — the
propagator stamps it deliberately, and its comment says why: blank would
conflate "no walk was possible" with "this record predates the field".

``cmd_collect`` builds its row from an EXPLICIT key list and both fields were
absent from it. So every adjudication packet ever built from this collector
told the adjudicator that a source reaches a sink in N hops, printed the route,
and silently withheld the tool's own statement that NO DATAFLOW WALK RAN and
the route is call-graph reachability.

MEASURED CONSEQUENCE. Measurement 0006's ``sample/sample-112.json`` has neither
key, so its two independent 16-agent panels judged 112 situations without it.
A later blind panel on rabbitmq spent its whole effort refuting a 16-hop route
— correctly, and reaching FALSE POSITIVE by reading source, which is the
rubric's bar — that the tool had already flagged as unwalked. The verdicts are
not thereby wrong: the rubric says to judge the SOURCE and treat the route as a
hint. What was wasted is the adjudicator's effort, and what was hidden is how
much of the population rests on no walk at all — 72.3% ``structural`` on the
16-repo cohort, against 1.5% with a confirmed dependence.

0006 named its own packet builder as defect family F and said "Fix before
reuse". This is that fix, for the field that mattered most.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "measure-taint-precision.py"


def _load() -> ModuleType:
    """Load the SHIPPED script, not a copy of its logic.

    The filename carries hyphens, so it is not importable by name even with a
    ``.py`` suffix. Same reason as ``helpers_measurement_frame``: a copy is
    free to drift from what actually runs.
    """
    spec = importlib.util.spec_from_loader(
        "measure_taint_precision",
        importlib.machinery.SourceFileLoader(
            "measure_taint_precision", str(SCRIPT)),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def collector() -> ModuleType:
    return _load()


def _payload(**ev_extra: object) -> dict[str, object]:
    """One violated verdict carrying one evidence row."""
    ev = {
        "source_symbol": "erlang:a.erl:1-9:f/1:function",
        "sink_symbol": "erlang:io:0-0:format:external_symbol",
        "source_primitive": "get_env",
        "sink_primitive": "format",
        "analysis_method": "structural",
        "confidence": "approximate",
        "path": ["erlang:a.erl:1-9:f/1:function",
                 "erlang:b.erl:2-8:g/0:function"],
    }
    ev.update(ev_extra)
    return {"verdicts": [
        {"verdict": "violated", "claim_id": "host-secret-no-logging",
         "evidence": [ev]},
    ]}


class TestTheWalkDisclosureSurvivesCollection:
    def test_an_unwalked_finding_says_so(self, collector: ModuleType) -> None:
        """THE DEFECT. A structural finding's row must carry the tool's own
        ``unavailable``, so an adjudicator reading the packet knows the printed
        route is reachability rather than a demonstrated value route."""
        rows = collector.flows_from_payload(
            _payload(walk_verdict="unavailable"), "rabbitmq")
        assert rows[0]["walk_verdict"] == "unavailable"

    def test_a_blocked_walk_carries_its_reason(
        self, collector: ModuleType,
    ) -> None:
        """``walk_blocked_by`` is the field measurement 0007 partitioned the
        blocked walks with (cross_function 76.3% / source_not_tracked 17.3% /
        sink_before_source 6.5%). Dropping it makes that partition
        unrecoverable from a flow file."""
        rows = collector.flows_from_payload(
            _payload(walk_verdict="not_attempted",
                     walk_blocked_by="cross_function"), "beads")
        assert rows[0]["walk_verdict"] == "not_attempted"
        assert rows[0]["walk_blocked_by"] == "cross_function"

    def test_a_confirmed_walk_is_not_downgraded(
        self, collector: ModuleType,
    ) -> None:
        """CONTROL, and the one that makes the others mean something.

        A change that stamped every row "unavailable" would pass the two tests
        above and be worthless — worse than worthless, since it would label the
        1.5% of findings that DO carry a confirmed dependence as unwalked. This
        pins that the field is carried FAITHFULLY, not defaulted."""
        rows = collector.flows_from_payload(
            _payload(analysis_method="ddg", confidence="precise",
                     walk_verdict="confirmed"), "ArkLib")
        assert rows[0]["walk_verdict"] == "confirmed"
        assert rows[0]["analysis_method"] == "ddg"

    def test_an_absent_disclosure_stays_absent_rather_than_inventing_one(
        self, collector: ModuleType,
    ) -> None:
        """A map written before the field existed says nothing about the walk,
        and the collector must not put a word in its mouth. ``None`` is
        distinguishable by a consumer; ``"unavailable"`` would be a claim the
        producer never made — the same absence-means-two-things error the
        propagator's own comment exists to prevent."""
        rows = collector.flows_from_payload(_payload(), "tmux")
        assert rows[0]["walk_verdict"] is None
        assert rows[0]["walk_blocked_by"] is None


class TestTheNonProductionSourceArmIsReachable:
    """WI-zamud: a population that lives entirely in test code is invisible to
    every arm this collector could previously produce.

    ``verify-claims`` excludes test/fixture-sourced flows by default
    (WI-bifob), and ``run_verify_claims`` hardcoded its argv, so the flag that
    lifts that exclusion could not be reached at all. On such a population the
    collector's "no new situations" and "the rule does nothing" are the same
    number -- a blind instrument, not a null result. The flag is passed through
    to PRODUCTION's parser rather than reinterpreted here, which is the same
    discipline the surrounding function already documents.
    """

    def _capture_argv(self, collector: ModuleType, monkeypatch, **kwargs):
        seen: dict[str, object] = {}

        def fake_cmd(args):
            seen["args"] = args
            print("{}")
            return 0

        import hypergumbo_core.cli as cli_mod
        monkeypatch.setattr(cli_mod, "cmd_verify_claims", fake_cmd)
        collector.run_verify_claims(Path("/repo"), Path("/claims.yaml"), **kwargs)
        return seen["args"]

    def test_the_flag_is_off_by_default(
        self, collector: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = self._capture_argv(collector, monkeypatch)
        assert args.include_non_production_sources is False

    def test_the_flag_reaches_productions_own_parser(
        self, collector: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = self._capture_argv(
            collector, monkeypatch, include_non_production=True,
        )
        assert args.include_non_production_sources is True

    def test_collect_exposes_the_flag(self, collector: ModuleType) -> None:
        """The arm is reachable from the COMMAND LINE, not just the function --
        an option no arm script can pass is not an instrument."""
        parser = collector.build_arg_parser()
        args = parser.parse_args([
            "collect", "--repo", "/r", "--out", "/o",
            "--include-non-production-sources",
        ])
        assert args.include_non_production_sources is True
