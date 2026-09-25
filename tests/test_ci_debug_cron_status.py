# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-bozid (b): the CI reader must find a cron verdict on a NON-HEAD commit.

THE DEFECT. A Woodpecker cron status attaches to whichever commit was ``dev``'s
tip WHEN THE CRON FIRED, and that commit is not HEAD by the time anyone looks.
``ci-debug status`` does ``sha=$(git rev-parse HEAD)`` and renders only HEAD's
statuses — so the project's own CI reader reported "green" while carrying NO
information about the last cron verdict, in either direction. INV-bobor's
regression was CORRECTLY DETECTED by the cron arm on 2026-08-19 and then sat on
green ``dev`` for a day and a half, because the detection landed on a commit
nobody queries.

``cron-status`` walks back over the ref and reports the most recent verdict per
workflow, whatever commit carries it.

THREE THINGS THIS FILE PINS, each of which was found by running it rather than
by reasoning about it:

1. SILENCE IS NOT GREEN. A workflow with no verdict in the window reports
   ``NO VERDICT`` and exits **2** — a distinct code from failure, because a
   reader that cannot tell "passed" from "never ran" is the defect the command
   exists to remove. Collapsing the two into success is the false-all-clear
   direction.

2. A MATRIX WORKFLOW PUBLISHES ONE CONTEXT PER LEG. ``nightly.yml``'s
   py3.10-3.13 matrix reports ``ci/woodpecker/cron/nightly/1`` … ``/4`` and NO
   bare ``ci/woodpecker/cron/nightly``. The first cut expected the bare context,
   found nothing, and reported a live workflow as having no verdict. Legs are
   grouped under their declaring workflow and named individually — INV-libib
   records that the four legs "return one identical log and cannot be addressed
   individually", so naming them is the readable half of that.

3. THE EXPECTED SET IS DERIVED FROM THE TREE, AND COMMENTS ARE NOT DECLARATIONS.
   ``woodpecker.yml`` DOCUMENTS its cron siblings in prose ("full-suite.yml …
   (push-to-dev + ``event: cron`` named …)"), so a bare grep for ``event: cron``
   enrolled a workflow that declares no cron and then reported it as a gate with
   no verdict. Measured on the live tree before the fix.

Run against the real repository on the day it landed, the command immediately
surfaced a red gate ``status`` could not see: ``ci/woodpecker/cron/nightly``
leg 1 FAILING at a commit 16 back. That is this item's thesis, demonstrated by
the instrument built for it.

Bash contributes no Python coverage.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _forge_github_harness import bindir_with_fakes, calls, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}


def _commits(repo: Path, n: int) -> list[str]:
    """Add ``n`` empty commits and return every sha, newest first."""
    for i in range(n):
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"c{i}"],
            cwd=repo, check=True, capture_output=True,
        )
    out = subprocess.run(
        ["git", "log", "--format=%H"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.split()
    return out


def _workflows(repo: Path, *, with_cron: list[str], commented_only: str | None = None):
    """Write ``.woodpecker/`` files: real cron declarations, plus optionally one
    whose ONLY mention of ``event: cron`` is inside a comment."""
    wd = repo / ".woodpecker"
    wd.mkdir(exist_ok=True)
    for name in with_cron:
        (wd / f"{name}.yml").write_text(
            "when:\n  - event: cron\n    cron: " + name + "\nsteps:\n  - name: t\n",
        )
    if commented_only:
        (wd / f"{commented_only}.yml").write_text(
            "# SIBLINGS: full-suite.yml rides `event: cron` named \"full-suite\".\n"
            "when:\n  - event: [push, pull_request]\nsteps:\n  - name: t\n",
        )


def _status(*contexts_states) -> str:
    return json.dumps({
        "state": "success",
        "statuses": [
            {"context": c, "state": s, "target_url": f"https://ci.test/{c}"}
            for c, s in contexts_states
        ],
    })


_EMPTY = json.dumps({"state": "", "statuses": [], "total_count": 0})


class TestAVerdictOnANonHeadCommitIsFound:
    """The whole point: HEAD carries nothing, an older commit carries the cron."""

    def test_it_reports_a_cron_verdict_three_commits_back(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["full-suite"])
        shas = _commits(repo, 4)
        carrier = shas[3]

        r, _ = run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "10"),
            fixtures=[
                {"match": f"/commits/{carrier}/status", "code": 200,
                 "body": _status(("ci/woodpecker/cron/full-suite", "success"))},
                {"match": "/commits/", "code": 200, "body": _EMPTY},
            ],
            env=_GH, bindir=bindir_with_fakes(tmp_path),
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "ci/woodpecker/cron/full-suite" in r.stdout
        assert "success" in r.stdout
        assert carrier[:8] in r.stdout, "the carrying commit is not named"
        assert "commit(s) back" in r.stdout, "the verdict's age is not reported"


class TestSilenceIsNotGreen:
    """A gate with no readable answer must not read as a passing one."""

    def test_no_verdict_in_window_exits_two_and_says_so(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["full-suite"])
        _commits(repo, 3)

        r, _ = run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "5"),
            fixtures=[{"match": "/commits/", "code": 200, "body": _EMPTY}],
            env=_GH, bindir=bindir_with_fakes(tmp_path),
        )
        assert r.returncode == 2, (
            f"expected the distinct no-verdict code, got {r.returncode}"
        )
        assert "NO VERDICT" in r.stdout
        assert "Not green and not red" in r.stdout

    def test_a_failing_gate_exits_one(self, tmp_path):
        """Distinct from 2: failure is a READ answer, absence is not."""
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["full-suite"])
        shas = _commits(repo, 2)

        r, _ = run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "5"),
            fixtures=[
                {"match": f"/commits/{shas[0]}/status", "code": 200,
                 "body": _status(("ci/woodpecker/cron/full-suite", "failure"))},
                {"match": "/commits/", "code": 200, "body": _EMPTY},
            ],
            env=_GH, bindir=bindir_with_fakes(tmp_path),
        )
        assert r.returncode == 1
        assert "FAIL" in r.stdout


class TestMatrixLegsGroupUnderTheirWorkflow:
    """``nightly`` publishes ``.../nightly/1`` … ``/4`` and no bare context."""

    def test_one_red_leg_makes_the_workflow_red_and_is_named(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["nightly"])
        shas = _commits(repo, 2)

        r, _ = run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "5"),
            fixtures=[
                {"match": f"/commits/{shas[0]}/status", "code": 200,
                 "body": _status(
                     ("ci/woodpecker/cron/nightly/1", "failure"),
                     ("ci/woodpecker/cron/nightly/2", "success"),
                     ("ci/woodpecker/cron/nightly/3", "success"),
                     ("ci/woodpecker/cron/nightly/4", "success"),
                 )},
                {"match": "/commits/", "code": 200, "body": _EMPTY},
            ],
            env=_GH, bindir=bindir_with_fakes(tmp_path),
        )
        assert r.returncode == 1, "a red leg did not make the workflow red"
        assert "4 matrix legs" in r.stdout
        assert "leg 1" in r.stdout, "the failing leg is not individually named"
        assert "NO VERDICT" not in r.stdout, (
            "legs were not matched to their declaring workflow, so a live "
            "workflow was reported as having no verdict"
        )


class TestTheExpectedSetIsDerivedFromTheTree:
    """A comment mentioning ``event: cron`` is prose, not a declaration."""

    def test_a_commented_mention_does_not_enrol_a_workflow(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["full-suite"], commented_only="woodpecker")
        shas = _commits(repo, 2)

        r, _ = run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "5"),
            fixtures=[
                {"match": f"/commits/{shas[0]}/status", "code": 200,
                 "body": _status(("ci/woodpecker/cron/full-suite", "success"))},
                {"match": "/commits/", "code": 200, "body": _EMPTY},
            ],
            env=_GH, bindir=bindir_with_fakes(tmp_path),
        )
        assert r.returncode == 0, (
            "a workflow whose only 'event: cron' is in a COMMENT was enrolled "
            "and then reported as a gate with no verdict"
        )
        assert "cron/woodpecker" not in r.stdout


# ---------------------------------------------------------------------------
# INV-bozid, the MASKING half.
#
# A Woodpecker workflow publishes ONE commit status for all of its steps, so
# a red ``full-suite`` names no step, and one chronically-red step makes every
# sibling's verdict unreadable for as long as it stays red. The 2026-09-03
# 01:00 firing was the shape exactly: aggregate ``failure``, and the transcript
# read ``1 failed, 25483 passed`` — a stale line citation in ONE test, with
# ``self-tree-validation``, ``self-claims-gate`` and ``test-agent-infra`` all
# green and all invisible from the status.
#
# The pipeline API carries every step's own state, and the log fetcher already
# read it to pick the failed step. These pin that ``cron-status`` RENDERS it,
# so a gate's verdict is readable beside its siblings' from the instrument the
# project uses to ask whether dev is green — the item's statement, without
# splitting the workflow into per-gate files (each of which would need its own
# clone and grammar build, and a twelve-hour cron cycle to validate).
# ---------------------------------------------------------------------------

_WP = {
    "WOODPECKER_SERVER": "https://wp.example",
    "WOODPECKER_TOKEN": "wtok",
    "CF_ACCESS_CLIENT_ID": "cfid",
    "CF_ACCESS_CLIENT_SECRET": "cfsecret",
}


def _status_at(*rows) -> str:
    """``rows`` are ``(context, state, target_url)``."""
    return json.dumps({
        "state": "failure",
        "statuses": [
            {"context": c, "state": s, "target_url": u} for c, s, u in rows
        ],
    })


def _pipeline(*workflows) -> str:
    """``workflows`` are ``(pid, name, [(step, state, exit_code), ...])`` —
    the shape ``GET /api/repos/{r}/pipelines/{n}`` returns."""
    return json.dumps({"workflows": [
        {
            "pid": pid, "name": name,
            "children": [
                {"id": 100 * pid + i, "name": n, "state": st, "exit_code": ec}
                for i, (n, st, ec) in enumerate(steps)
            ],
        }
        for pid, name, steps in workflows
    ]})


_FULL_SUITE_2189 = [
    ("prepare-git", "success", 0),
    ("build-grammars", "success", 0),
    ("test-all-packages", "failure", 1),
    ("self-tree-validation", "success", 0),
    ("self-claims-gate", "success", 0),
    ("test-agent-infra", "success", 0),
]


def _step_lines(stdout: str) -> list[str]:
    """Only STEP lines: the workflow line and the leg line carry the same marks
    but name a context or a commit age, which no step line does."""
    return [
        ln.strip() for ln in stdout.splitlines()
        if ln.strip()[:4] in ("OK  ", "FAIL", "ERR ", "SKIP", "KILL", "... ")
        and "ci/woodpecker/" not in ln and "commit(s) back" not in ln
    ]


class TestAGateIsReadableBesideItsSiblings:
    """One aggregate bit in, one line per step out."""

    def _run(self, tmp_path, *, statuses, pipeline_fixtures, env):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["full-suite"])
        shas = _commits(repo, 2)
        return run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "5"),
            fixtures=[
                {"match": f"/commits/{shas[0]}/status", "code": 200,
                 "body": statuses},
                *pipeline_fixtures,
                {"match": "/commits/", "code": 200, "body": _EMPTY},
            ],
            env=env, bindir=bindir_with_fakes(tmp_path),
        )

    def test_every_step_verdict_is_named_beside_the_one_aggregate_bit(
        self, tmp_path,
    ):
        r, _ = self._run(
            tmp_path,
            statuses=_status_at((
                "ci/woodpecker/cron/full-suite", "failure",
                "https://wp.example/repos/1/pipeline/2189/1",
            )),
            pipeline_fixtures=[{
                "match": "GET https://wp.example/api/repos/1/pipelines/2189",
                "code": 200, "body": _pipeline((1, "full-suite", _FULL_SUITE_2189)),
            }],
            env={**_GH, **_WP},
        )
        assert r.returncode == 1, r.stdout + r.stderr
        assert "FAIL test-all-packages (exit 1)" in r.stdout, r.stdout
        # The three siblings the aggregate bit was hiding.
        for sibling in ("self-tree-validation", "self-claims-gate",
                        "test-agent-infra"):
            assert f"OK   {sibling}" in r.stdout, (sibling, r.stdout)
        assert len(_step_lines(r.stdout)) == 6, r.stdout

    def test_an_errored_or_skipped_step_is_not_reported_as_passed(
        self, tmp_path,
    ):
        """ERROR IS NOT FAILURE, and neither is SKIPPED — INV-bobor's third
        delivery failure: a pipeline dying inside a gate must not read like
        the gate passing."""
        r, _ = self._run(
            tmp_path,
            statuses=_status_at((
                "ci/woodpecker/cron/full-suite", "failure",
                "https://wp.example/repos/1/pipeline/9/1",
            )),
            pipeline_fixtures=[{
                "match": "GET https://wp.example/api/repos/1/pipelines/9",
                "code": 200,
                "body": _pipeline((1, "full-suite", [
                    ("build-grammars", "success", 0),
                    ("self-claims-gate", "error", None),
                    ("test-agent-infra", "skipped", None),
                ])),
            }],
            env={**_GH, **_WP},
        )
        assert "ERR  self-claims-gate" in r.stdout, r.stdout
        assert "SKIP test-agent-infra" in r.stdout, r.stdout
        assert "OK   self-claims-gate" not in r.stdout
        assert "OK   test-agent-infra" not in r.stdout

    def test_a_matrix_leg_shows_only_its_own_steps(self, tmp_path):
        """nightly's four legs share one pipeline; the trailing number on a
        leg's target_url is the WORKFLOW index (WI-solob), and each leg must
        render its own workflow's steps, not the union."""
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        _workflows(repo, with_cron=["nightly"])
        shas = _commits(repo, 2)
        r, _ = run_script(
            "ci-debug", repo, ("cron-status", "HEAD", "5"),
            fixtures=[
                {"match": f"/commits/{shas[0]}/status", "code": 200,
                 "body": _status_at(
                     ("ci/woodpecker/cron/nightly/1", "failure",
                      "https://wp.example/repos/1/pipeline/5/1"),
                     ("ci/woodpecker/cron/nightly/2", "success",
                      "https://wp.example/repos/1/pipeline/5/2"),
                 )},
                {"match": "GET https://wp.example/api/repos/1/pipelines/5",
                 "code": 200,
                 "body": _pipeline(
                     (1, "py3.10", [("tests", "failure", 1)]),
                     (2, "py3.11", [("tests", "success", 0)]),
                 )},
                {"match": "/commits/", "code": 200, "body": _EMPTY},
            ],
            env={**_GH, **_WP}, bindir=bindir_with_fakes(tmp_path),
        )
        lines = [ln.strip() for ln in r.stdout.splitlines()]
        leg1 = next(i for i, ln in enumerate(lines) if ln.startswith("FAIL leg 1"))
        leg2 = next(i for i, ln in enumerate(lines) if ln.startswith("OK   leg 2"))
        between = [ln for ln in lines[leg1:leg2] if ln.startswith(("FAIL tests", "OK   tests"))]
        after = [ln for ln in lines[leg2:] if ln.startswith(("FAIL tests", "OK   tests"))]
        assert between == ["FAIL tests (exit 1)"], (between, r.stdout)
        assert after == ["OK   tests"], (after, r.stdout)


class TestAnUnreadStepListSaysWhatItCouldNotRead:
    """An honest empty names what was searched (LIVE rule 6). A reader who
    sees only the aggregate must be told WHY the steps are absent — never
    left to assume there were none."""

    def _statuses(self):
        return _status_at((
            "ci/woodpecker/cron/full-suite", "failure",
            "https://wp.example/repos/1/pipeline/2189/1",
        ))

    def test_missing_credentials_are_named_once(self, tmp_path):
        r, logs = TestAGateIsReadableBesideItsSiblings()._run(
            tmp_path, statuses=self._statuses(), pipeline_fixtures=[],
            env=_GH,  # no WOODPECKER_* / CF_ACCESS_*
        )
        assert r.returncode == 1, r.stdout + r.stderr  # the aggregate still reads
        assert "WOODPECKER_SERVER" in r.stdout, r.stdout
        assert r.stdout.count("per-step verdicts not read") == 1, r.stdout
        # And nothing was fetched from a host we had no credentials for.
        assert not any("wp.example/api" in (c["url"] or "")
                       for c in calls(logs["curl"]))

    def test_a_refused_pipeline_fetch_names_the_http_code(self, tmp_path):
        r, _ = TestAGateIsReadableBesideItsSiblings()._run(
            tmp_path, statuses=self._statuses(),
            pipeline_fixtures=[{
                "match": "GET https://wp.example/api/repos/1/pipelines/2189",
                "code": 403, "body": "",
            }],
            env={**_GH, **_WP},
        )
        assert "per-step verdicts not read" in r.stdout, r.stdout
        assert "403" in r.stdout, r.stdout
        assert not _step_lines(r.stdout), (
            "no step line may be manufactured from a refused fetch", r.stdout,
        )
