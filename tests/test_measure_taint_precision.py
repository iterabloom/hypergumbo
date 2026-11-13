# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adjudication collector must carry the tool's OWN disclosure forward.

``verify-claims`` emits ``walk_verdict`` and ``walk_blocked_by`` on every
evidence row. For a ``structural`` finding it emits ``walk_verdict:
"unavailable"`` — the propagator stamps it deliberately, and its comment says
why: blank would conflate "no walk was possible" with "this record predates
the field".

The collector builds each row (``flows_from_payload``) from an EXPLICIT key
list, so a field left off that list is dropped silently. Dropping these two
leaves a packet that tells the adjudicator a source reaches a sink in N hops,
prints the route, and withholds the tool's own statement that NO DATAFLOW WALK
RAN and the route is only call-graph reachability. The verdicts are not thereby
wrong — the rubric says to judge the SOURCE and treat the route as a hint — but
the adjudicator spends effort refuting routes the tool already flagged as
unwalked, and the packet hides how much of the population rests on no walk at
all (``structural`` findings can be most of it).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from helpers_code_tree import (
    CHOICE_ENV,
    FAKE_EXIT,
    MARKER,
    analysis_package_names,
    fake_tree,
    own_pythonpath,
    run_script,
)

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
        """``walk_blocked_by`` is the field that partitions the blocked walks
        by reason (cross_function / source_not_tracked / sink_before_source).
        Dropping it makes that partition unrecoverable from a flow file."""
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
        findings that DO carry a confirmed dependence as unwalked. This
        pins that the field is carried FAITHFULLY, not defaulted."""
        rows = collector.flows_from_payload(
            _payload(analysis_method="ddg", confidence="precise",
                     walk_verdict="reached"), "ArkLib")
        assert rows[0]["walk_verdict"] == "reached"
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
    """A population that lives entirely in test code must be reachable by
    some arm of this collector.

    ``verify-claims`` excludes test/fixture-sourced flows by default
    (WI-bifob). If the collector cannot pass the flag that lifts that
    exclusion, then on such a population its "no new situations" and "the
    rule does nothing" are the same number -- a blind instrument, not a null
    result. The flag is passed through to PRODUCTION's parser rather than
    reinterpreted here, which is the same discipline the surrounding function
    already documents (history: WI-zamud).
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


class TestTheLiftedCapDoesNotOutliveTheRun:
    """``run_verify_claims`` lifts the evidence cap and, for the no-collapse
    arm, replaces the collapse; both are module globals of production code.
    Run in-process, they must be back as found when it returns, or any later
    caller in the same process (a test relying on the 100-row cap) reads the
    instrument's settings instead of production's (INV-nuvib).
    """

    def _run(self, collector: ModuleType, monkeypatch, *, raises: bool):
        import hypergumbo_core.cli as cli_mod
        from hypergumbo_core import taint as taint_mod
        from hypergumbo_core import verify_claims as vc_mod

        during: dict[str, object] = {}

        def fake_cmd(args):
            during["cap"] = vc_mod._MAX_EVIDENCE_ROWS
            during["collapse"] = taint_mod.collapse_unadjudicated_flows
            if raises:
                raise RuntimeError("verify-claims failed")
            print("{}")
            return 0

        monkeypatch.setattr(cli_mod, "cmd_verify_claims", fake_cmd)
        before = (vc_mod._MAX_EVIDENCE_ROWS,
                  taint_mod.collapse_unadjudicated_flows)
        if raises:
            with pytest.raises(RuntimeError):
                collector.run_verify_claims(
                    Path("/repo"), Path("/claims.yaml"), no_collapse=True)
        else:
            collector.run_verify_claims(
                Path("/repo"), Path("/claims.yaml"), no_collapse=True)
        after = (vc_mod._MAX_EVIDENCE_ROWS,
                 taint_mod.collapse_unadjudicated_flows)
        return before, during, after

    def test_the_run_sees_the_lifted_cap(
        self, collector: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reach: the instrument still does what it exists to do."""
        before, during, _ = self._run(collector, monkeypatch, raises=False)
        assert during["cap"] == collector.EVIDENCE_LIMIT
        assert during["collapse"] is not before[1]

    def test_both_globals_are_restored_on_return(
        self, collector: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before, _, after = self._run(collector, monkeypatch, raises=False)
        assert after[0] == before[0]
        assert after[1] is before[1]

    def test_both_globals_are_restored_when_the_run_raises(
        self, collector: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before, _, after = self._run(collector, monkeypatch, raises=True)
        assert after[0] == before[0]
        assert after[1] is before[1]


# --------------------------------------------------------------------------
# WI-tumog: which tree's analyzer runs is CHOSEN, never inherited silently.
# --------------------------------------------------------------------------
def _shell_flows(tmp_path: Path) -> tuple[Path, Path]:
    """A one-flow ``collect`` output whose packet reaches bash.py's sets.

    A bash ``environ`` source renders through ``shell_env_sites``, which
    imports ``hypergumbo_lang_mainstream.bash`` (WI-binod) -- so the packet
    path imports a LANGUAGE package as well as core, the mixed-tree case.
    """
    repos = tmp_path / "repos"
    (repos / "r").mkdir(parents=True)
    (repos / "r" / "s.sh").write_text(
        '#!/bin/bash\necho "$HOME"\ncurl "$URL"\n', encoding="utf-8",
    )
    flows = tmp_path / "flows"
    flows.mkdir()
    flow = {
        "repo": "r", "flow_id": "f1", "claim_id": "host-secret-no-network",
        "analysis_method": "structural", "collapsed_flow_count": 1,
        "source_primitive": "environ", "source_boundary": "env_read",
        "source_file": "s.sh", "source_lines": [1, 3],
        "source_symbol": "bash:s.sh:1-3:s.sh:file",
        "sink_name": "curl", "sink_module": "", "hops": 0,
        "path": ["bash:s.sh:1-3:s.sh:file"],
    }
    (flows / "flows-r.jsonl").write_text(json.dumps(flow) + "\n", encoding="utf-8")
    return flows, repos


class TestTheCodeUnderTestIsChosenNotInherited:
    """Inserting this script's own tree ahead of PYTHONPATH whenever the copy
    being run is not the editable-install tree runs the subject analyzer in
    BOTH arms of an A/B, which then reports a 0 delta, exit 0 (history:
    WI-valav). A fake package on PYTHONPATH that announces itself and exits 97
    makes the imported copy observable; each test pins one replacement for the
    silent outcome, on ``collect`` AND ``packet`` (WI-binod's import)."""

    @pytest.mark.parametrize("command", ["collect", "packet"])
    def test_a_foreign_pythonpath_is_refused_not_silently_ignored(
        self, tmp_path: Path, command: str,
    ) -> None:
        fake = fake_tree(tmp_path / "fake", ["hypergumbo_core"])
        flows, repos = _shell_flows(tmp_path)
        args = (
            ["collect", "--repo", str(repos / "r"), "--out", str(tmp_path / "o")]
            if command == "collect" else
            ["packet", "--flows", str(flows), "--out", str(tmp_path / "o"),
             "--repo-root", str(repos)]
        )
        proc = run_script(SCRIPT, args, pythonpath=fake)
        assert proc.returncode == 2, (proc.returncode, proc.stdout[:400])
        assert CHOICE_ENV in proc.stderr
        assert fake in proc.stderr
        assert MARKER not in proc.stderr
        assert proc.stdout == ""
        assert not (tmp_path / "o").exists(), "nothing may be written"

    def test_a_pythonpath_arm_that_covers_only_core_is_a_mixed_tree(
        self, tmp_path: Path,
    ) -> None:
        """Choosing PYTHONPATH when it provides core but not the language
        packages would analyse with one tree's core and another's analyzers.
        That is neither arm, so it is refused before anything is imported."""
        fake = fake_tree(tmp_path / "fake", ["hypergumbo_core"])
        flows, repos = _shell_flows(tmp_path)
        proc = run_script(
            SCRIPT,
            ["packet", "--flows", str(flows), "--out", str(tmp_path / "o"),
             "--repo-root", str(repos)],
            pythonpath=fake, choice="pythonpath",
        )
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-400:])
        assert "MIXED" in proc.stderr
        assert MARKER not in proc.stderr

    def test_choosing_pythonpath_imports_the_pythonpath_copy(
        self, tmp_path: Path,
    ) -> None:
        fake = fake_tree(tmp_path / "fake", analysis_package_names())
        flows, repos = _shell_flows(tmp_path)
        proc = run_script(
            SCRIPT,
            ["packet", "--flows", str(flows), "--out", str(tmp_path / "o"),
             "--repo-root", str(repos)],
            pythonpath=fake, choice="pythonpath",
        )
        assert proc.returncode == FAKE_EXIT, (proc.returncode, proc.stderr[-400:])
        assert MARKER in proc.stderr
        assert "code under test" in proc.stdout
        assert fake in proc.stdout.splitlines()[0]

    def test_choosing_script_renders_with_this_tree_and_says_so_first(
        self, tmp_path: Path,
    ) -> None:
        fake = fake_tree(tmp_path / "fake", analysis_package_names())
        flows, repos = _shell_flows(tmp_path)
        out = tmp_path / "o"
        proc = run_script(
            SCRIPT,
            ["packet", "--flows", str(flows), "--out", str(out),
             "--repo-root", str(repos)],
            pythonpath=fake, choice="script",
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        assert MARKER not in proc.stderr
        lines = proc.stdout.splitlines()
        assert "code under test" in lines[0] and str(REPO_ROOT) in lines[0]
        for name in analysis_package_names():
            assert any(name in ln and str(REPO_ROOT) in ln for ln in lines), name
        assert "$HOME" in (out / "r.md").read_text(encoding="utf-8")

    def test_collect_names_the_tree_before_the_positive_control(
        self, tmp_path: Path,
    ) -> None:
        """The board's shape (PYTHONPATH pinned to this tree) is no conflict,
        and the provenance precedes every number ``collect`` prints."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("import os\nprint(os.environ['X'])\n")
        proc = run_script(
            SCRIPT,
            ["collect", "--repo", str(repo), "--out", str(tmp_path / "o")],
            pythonpath=own_pythonpath(),
            extra_env={"XDG_CACHE_HOME": str(tmp_path / "xdg")},
        )
        assert proc.returncode == 0, proc.stderr[-600:]
        lines = proc.stdout.splitlines()
        assert "code under test" in lines[0] and str(REPO_ROOT) in lines[0]
        control = next(i for i, ln in enumerate(lines) if "POSITIVE CONTROL" in ln)
        assert control > 0
