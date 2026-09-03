# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for the GitHub arm of ``scripts/ci-debug`` (PR-C2).

Under GitHub + self-hosted Woodpecker, GitHub Actions is disabled, so
``/actions/runs`` returns an empty ``workflow_runs`` (HTTP 200) — the ``runs``
subcommand would silently print nothing. The only agent-readable CI signal is
the Woodpecker commit-STATUS at ``/commits/{sha}/status`` (context
``ci/woodpecker/pr/woodpecker``). The github arm degrades ``runs`` to render
those status entries as pseudo-runs. ``status`` / ``pr-status`` / ``logs`` are
already shape-compatible (``logs`` degrades inside the lib's
``_github_fetch_job_log``); those tests are regression guards.

Dormant (default forgejo while Codeberg is origin), forced here via
``HYPERGUMBO_FORGE_BACKEND=github``. Bash contributes no Python coverage.
"""

from __future__ import annotations

import json

from _forge_github_harness import bindir_with_fakes, calls, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}

_STATUS_OK = json.dumps({
    "state": "success",
    "statuses": [
        {"state": "success", "context": "ci/woodpecker/pr/woodpecker",
         "target_url": "https://ci.example.test/build/9"},
    ],
})
_STATUS_EMPTY = json.dumps({"state": "", "statuses": [], "total_count": 0})


def _urls(logs):
    return [c["url"] or "" for c in calls(logs["curl"])]


class TestRunsGitHub:
    def test_runs_renders_commit_status_as_pseudo_runs(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, logs = run_script(
            "ci-debug", repo, ("runs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "ci/woodpecker/pr/woodpecker" in r.stdout
        assert "success" in r.stdout
        urls = _urls(logs)
        assert any("/commits/" in u and "/status" in u for u in urls)
        # GitHub Actions is disabled — the arm must NOT hit /actions/runs.
        assert not any("/actions/runs" in u for u in urls)

    def test_runs_empty_status_is_graceful(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("runs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_EMPTY}],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "No CI status reported" in r.stdout


class TestRunsForgejoUnchanged:
    def test_forgejo_runs_hits_actions_runs(self, tmp_path):
        repo = fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        body = json.dumps({"workflow_runs": [
            {"run_number": 5, "status": "success",
             "head_sha": "abcdef1234567890", "display_title": "CI"},
        ]})
        r, logs = run_script(
            "ci-debug", repo, ("runs",),
            fixtures=[{"match": "/actions/runs", "code": 200, "body": body}],
            bindir=bindir,
        )
        assert r.returncode == 0
        assert any("/actions/runs?limit=300" in u for u in _urls(logs))
        assert "#5" in r.stdout


class TestStatusShapeCompat:
    def test_status_github_renders_overall(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("status",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "Overall: success" in r.stdout
        assert "ci/woodpecker/pr/woodpecker" in r.stdout

    def test_pr_status_github_renders(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        pr = json.dumps({
            "state": "open", "merged": False, "title": "My PR",
            "head": {"sha": "abc1234567", "ref": "feature"},
            "base": {"ref": "dev"},
        })
        r, _ = run_script(
            "ci-debug", repo, ("pr-status", "42"),
            fixtures=[
                {"match": "/pulls/42", "code": 200, "body": pr},
                {"match": "/commits/", "code": 200, "body": _STATUS_OK},
            ],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "My PR" in r.stdout
        assert "Overall: success" in r.stdout
        # No `mergeable` key in this fixture, so the tri-state reader must say
        # so rather than rendering an empty value that reads as "false".
        assert "Mergeable: absent" in r.stdout

    def test_pr_status_renders_uncomputed_mergeability_as_null(self, tmp_path):
        """A GitHub `mergeable: null` must surface as `null`, not blank.

        This is the field auto-pr's Scenario B gate branches on, and GitHub
        reports it as null while mergeability is still being computed. Until
        pr-status carried it, the gate's live input was unobservable through
        any approved script — which is why INV-rahib Surface 1 sat unverified
        against the real backend. Rendering it as an empty string would put
        the gap straight back.
        """
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        pr = json.dumps({
            "state": "open", "merged": False, "title": "Async PR",
            "mergeable": None,
            "head": {"sha": "abc1234567", "ref": "feature"},
            "base": {"ref": "dev"},
        })
        r, _ = run_script(
            "ci-debug", repo, ("pr-status", "42"),
            fixtures=[
                {"match": "/pulls/42", "code": 200, "body": pr},
                {"match": "/commits/", "code": 200, "body": _STATUS_OK},
            ],
            env=_GH, bindir=bindir,
        )
        assert r.returncode == 0
        assert "Mergeable: null" in r.stdout, (
            f"uncomputed mergeability must be distinguishable from false:\n"
            f"{r.stdout}"
        )
        assert "Mergeable: false" not in r.stdout


class TestLogsDegradation:
    def test_logs_github_degrades_to_pointer(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("logs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert "Could not retrieve log" in r.stdout
        assert "Cloudflare Access" in r.stderr
        assert "https://ci.example.test/build/9" in r.stderr


# WI-zavut: two gates report on the same commit, so "which pipeline?" is a
# real question and the job name is the only answer to it. Before this, the
# target_url resolver took the FIRST status carrying one and broke out of the
# loop; the job name was applied later, to pick a STEP inside the pipeline
# that had already been chosen wrongly. Measured on dev 45280d90, which
# carries push/woodpecker (success) beside cron/full-suite (failure):
# `ci-debug logs cron/full-suite 45280d90` returned the PUSH transcript
# ("396 passed"), and so did every other job name tried. The cron gate's log
# had therefore never been read by anyone, while the gate reported FAILURE.
_STATUS_TWO_GATES = json.dumps({
    "state": "failure",
    "statuses": [
        {"state": "success", "context": "ci/woodpecker/push/woodpecker",
         "target_url": "https://ci.example.test/repos/1/pipeline/100"},
        {"state": "failure", "context": "ci/woodpecker/cron/full-suite",
         "target_url": "https://ci.example.test/repos/1/pipeline/200"},
    ],
})


class TestLogPipelineSelection:
    """Which PIPELINE the log comes from, not which step within it."""

    def _run(self, tmp_path, args):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, args,
            fixtures=[{"match": "/commits/", "code": 200,
                       "body": _STATUS_TWO_GATES}],
            env=_GH, bindir=bindir,
        )
        return r

    def test_named_job_selects_its_own_pipeline(self, tmp_path):
        r = self._run(tmp_path, ("logs", "cron/full-suite"))
        assert "pipeline/200" in r.stderr, (
            "asking for the cron gate must resolve the CRON pipeline; "
            f"got: {r.stderr}"
        )
        assert "pipeline/100" not in r.stderr

    def test_named_job_matches_on_the_short_name_too(self, tmp_path):
        """Operators type 'full-suite', not the full context string."""
        r = self._run(tmp_path, ("logs", "full-suite"))
        assert "pipeline/200" in r.stderr, r.stderr

    def test_no_job_name_lands_on_the_gate_that_FAILED(self, tmp_path):
        """The whole point of reaching for a log is that something broke.

        Defaulting to the first status meant `ci-debug logs` with no
        argument returned the GREEN pipeline's transcript while a different
        gate was red — the same wrong answer, reached without even a typo to
        blame. This mirrors the step-level rule already in the file.
        """
        r = self._run(tmp_path, ("logs",))
        assert "pipeline/200" in r.stderr, r.stderr

    def test_unmatched_job_name_still_degrades_rather_than_dying(self, tmp_path):
        """An unknown name must not resolve to nothing at all — fall back to
        the failed gate, which is the best available answer."""
        r = self._run(tmp_path, ("logs", "no-such-job"))
        assert "pipeline/200" in r.stderr, r.stderr

    def test_unmatched_job_name_SAYS_it_substituted(self, tmp_path):
        """Degrading is fine; degrading SILENTLY is not.

        The fallback above is the right behaviour and stays. What was missing
        is that the caller is never told the transcript is not the gate they
        named, so a substituted log reads exactly like an answer.
        """
        r = self._run(tmp_path, ("logs", "no-such-job"))
        combined = r.stdout + r.stderr
        assert "no-such-job" in combined, (
            "the unmatched name must be echoed back so the substitution is "
            f"visible:\n{combined}"
        )
        assert "ci/woodpecker/cron/full-suite" in combined, (
            "the gate actually fetched must be named:\n" + combined
        )


# The commit that exposed this: dev ea0d6a83ab carries push/woodpecker
# (success) and NOTHING else — the cron gate never ran on it. Asking for
# cron/full-suite matched no status, fell past the failed-gate rule (there is
# no failed gate) to `statuses[0]`, and returned the PUSH pipeline's GREEN
# transcript at rc=0. Read without checking the test count, that is a cron
# gate reporting success on a commit it never ran on.
_STATUS_ONLY_GREEN_PUSH = json.dumps({
    "state": "success",
    "statuses": [
        {"state": "success", "context": "ci/woodpecker/push/woodpecker",
         "target_url": "https://ci.example.test/repos/1/pipeline/100"},
    ],
})


class TestAbsentGateIsNotSilentlySubstituted:
    """Asking for a gate that did not run must never look like an answer."""

    def _run(self, tmp_path, args):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, args,
            fixtures=[{"match": "/commits/", "code": 200,
                       "body": _STATUS_ONLY_GREEN_PUSH}],
            env=_GH, bindir=bindir,
        )
        return r

    def test_absent_gate_does_not_pass_off_a_green_pipeline_as_the_named_one(
        self, tmp_path
    ):
        r = self._run(tmp_path, ("logs", "cron/full-suite"))
        combined = r.stdout + r.stderr
        assert "cron/full-suite" in combined and (
            "push/woodpecker" in combined
        ), (
            "when the named gate is absent the caller must be told both what "
            f"was asked for and what was returned instead:\n{combined}"
        )

    def test_a_step_name_is_not_reported_as_an_unmatched_gate(self, tmp_path):
        """One name is tried as a gate and then as a step.

        Operators pass step names ('test-agent-infra') as often as gate names,
        and for those the gate-level lookup ALWAYS falls back. Warning there
        would fire on a correct, everyday call — and a warning that cries wolf
        on the common path is worth less than no warning, because the reader
        learns to skip it. Verified against the live tree: asking for the gate
        'cron/full-suite' on a commit that HAS it must stay silent even though
        no step carries that name.
        """
        r = self._run(tmp_path, ("logs", "push/woodpecker"))
        assert "Nothing named" not in (r.stdout + r.stderr), (
            "the gate WAS matched; nothing was substituted:\n"
            f"{r.stdout}{r.stderr}"
        )

    def test_no_job_name_on_an_all_green_commit_is_not_a_substitution(
        self, tmp_path
    ):
        """The control: with no name asked for, nothing is substituted, so
        the warning must NOT fire. A warning on every ordinary call would be
        noise that trains the operator to ignore it."""
        r = self._run(tmp_path, ("logs",))
        assert "Nothing named" not in (r.stdout + r.stderr), (
            "unnamed fetch on a single-gate commit substituted nothing; "
            f"it must not warn:\n{r.stdout}{r.stderr}"
        )

    def test_single_gate_behaviour_is_unchanged(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        r, _ = run_script(
            "ci-debug", repo, ("logs",),
            fixtures=[{"match": "/commits/", "code": 200, "body": _STATUS_OK}],
            env=_GH, bindir=bindir,
        )
        assert "https://ci.example.test/build/9" in r.stderr


class TestStatusNamesEachStep:
    """INV-bozid, the MASKING half, on ``status``.

    The per-PR pipeline is one workflow too, so ``ci/woodpecker/pr/woodpecker:
    failure`` names none of lint / mypy / pytest / build-grammars. The
    pipeline API carries each step's own state; ``status`` renders it under
    the job line whenever a status points at a Woodpecker pipeline and the
    credentials are set. ``cron-status`` does the same for cron verdicts —
    see test_ci_debug_cron_status.py for the full matrix (error/skipped,
    matrix legs, refused fetch); this pins that ``status`` shares the path.
    """

    _WP = {
        "WOODPECKER_SERVER": "https://wp.example",
        "WOODPECKER_TOKEN": "wtok",
        "CF_ACCESS_CLIENT_ID": "cfid",
        "CF_ACCESS_CLIENT_SECRET": "cfsecret",
    }

    def test_status_renders_the_steps_behind_a_woodpecker_status(self, tmp_path):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = bindir_with_fakes(tmp_path)
        status = json.dumps({
            "state": "failure",
            "statuses": [
                {"state": "failure", "context": "ci/woodpecker/pr/woodpecker",
                 "target_url": "https://wp.example/repos/1/pipeline/7/1"},
            ],
        })
        pipeline = json.dumps({"workflows": [{
            "pid": 1, "name": "woodpecker",
            "children": [
                {"id": 1, "name": "lint", "state": "success", "exit_code": 0},
                {"id": 2, "name": "mypy", "state": "success", "exit_code": 0},
                {"id": 3, "name": "pytest", "state": "failure", "exit_code": 1},
            ],
        }]})
        r, _ = run_script(
            "ci-debug", repo, ("status",),
            fixtures=[
                {"match": "/commits/", "code": 200, "body": status},
                {"match": "GET https://wp.example/api/repos/1/pipelines/7",
                 "code": 200, "body": pipeline},
            ],
            env={**_GH, **self._WP}, bindir=bindir,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "ci/woodpecker/pr/woodpecker: failure" in r.stdout
        assert "FAIL pytest (exit 1)" in r.stdout, r.stdout
        assert "OK   lint" in r.stdout, r.stdout
        assert "OK   mypy" in r.stdout, r.stdout

    def test_status_without_credentials_says_the_steps_were_not_read(
        self, tmp_path,
    ):
        repo = fake_repo(tmp_path, "https://github.com/o/r.git")
        status = json.dumps({
            "state": "failure",
            "statuses": [
                {"state": "failure", "context": "ci/woodpecker/pr/woodpecker",
                 "target_url": "https://wp.example/repos/1/pipeline/7/1"},
            ],
        })
        r, logs = run_script(
            "ci-debug", repo, ("status",),
            fixtures=[{"match": "/commits/", "code": 200, "body": status}],
            env=_GH, bindir=bindir_with_fakes(tmp_path),
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "per-step verdicts not read" in r.stdout, r.stdout
        assert not any("wp.example/api" in u for u in _urls(logs))
