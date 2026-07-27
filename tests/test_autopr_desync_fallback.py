# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-hubod auto-pr/merge desync resilience.

Codeberg's recurring "database representation out of synchronization" desync
breaks the AGit (``refs/for/``) proc-receive hook — which does the DB writes
that create a PR — producing 504 / "fail to run proc-receive hook" on push,
and HTTP 405 ("Please try again later") on the merge endpoint. A *plain*
branch push (``refs/heads/<branch>``, post-receive only) is resilient AND its
post-receive recomputes the DB representation, clearing the desync.

WI-hubod adds two resilience seams (both human-approved, 2026-06-20):

1. ``auto-pr`` push fallback — when the AGit push fails, fall back to a plain
   branch push + an API PR-create (``create_pr`` in ``lib/forgejo-api.sh``),
   reusing any PR a partial AGit push already made, before giving up to a vPR.
2. ``do_merge`` resync-retry — when the merge endpoint fails with a desync
   signature on the final attempt, do one plain branch push to resync the DB
   and retry the merge once.

These tests cover the new ``lib/forgejo-api.sh`` functions directly (the lib is
sourceable, so its functions are unit-tested with mocked ``api_post`` /
``_check_pr_merged``), the full ``auto-pr`` fallback flow via subprocess with
the ``AUTO_PR_SIMULATE_DESYNC`` seam, and structural invariants by grepping
the scripts. End-to-end testing against a live forge is out of scope.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


def _run_lib(body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Source forgejo-api.sh and run a bash snippet against its functions."""
    full_env = {"PATH": "/usr/bin:/bin"}
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", "-c", f"source '{FORGEJO_LIB}' >/dev/null 2>&1\n{body}"],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ----------------------------------------------------------------------
# create_pr
# ----------------------------------------------------------------------
class TestCreatePr:
    def test_seam_returns_number(self) -> None:
        r = _run_lib(
            'AUTOPR_TEST_CREATE_PR=4321 create_pr h b t body; '
            'echo "RC=$? NUM=$CREATED_PR_NUM"'
        )
        assert "RC=0 NUM=4321" in r.stdout, r.stdout + r.stderr

    def test_seam_fail_returns_nonzero(self) -> None:
        r = _run_lib('AUTOPR_TEST_CREATE_PR=fail create_pr h b t body; echo "RC=$?"')
        assert "RC=1" in r.stdout, r.stdout + r.stderr

    def test_real_path_extracts_number_from_api_response(self) -> None:
        # Redefine api_post AFTER sourcing so create_pr uses the mock.
        r = _run_lib(
            'api_post() { API_RESPONSE='"'"'{"number": 99}'"'"'; API_HTTP_CODE=201; return 0; }\n'
            'create_pr mybranch dev "my title" "the body"\n'
            'echo "RC=$? NUM=$CREATED_PR_NUM"'
        )
        assert "RC=0 NUM=99" in r.stdout, r.stdout + r.stderr

    def test_real_path_payload_has_head_base_title(self, tmp_path: Path) -> None:
        cap = tmp_path / "payload.json"
        body = (
            'api_post() { printf "%s" "$2" > CAPFILE; '
            'API_RESPONSE='"'"'{"number": 5}'"'"'; return 0; }\n'
            'create_pr mybranch dev "my title" "the body"\n'
            'echo "RC=$?"'
        ).replace("CAPFILE", str(cap))
        r = _run_lib(body)
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        payload = json.loads(cap.read_text())
        assert payload == {
            "title": "my title",
            "body": "the body",
            "head": "mybranch",
            "base": "dev",
        }, payload

    def test_real_path_api_failure_returns_nonzero(self) -> None:
        r = _run_lib(
            'api_post() { return 1; }\n'
            'create_pr b dev t body; echo "RC=$?"'
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr


# ----------------------------------------------------------------------
# _merge_failure_is_desync
# ----------------------------------------------------------------------
class TestMergeFailureIsDesync:
    def test_http_405_is_desync(self) -> None:
        r = _run_lib('_merge_failure_is_desync 405 ""; echo "RC=$?"')
        assert "RC=0" in r.stdout, r.stdout

    def test_http_000_is_desync(self) -> None:
        r = _run_lib('_merge_failure_is_desync 000 ""; echo "RC=$?"')
        assert "RC=0" in r.stdout, r.stdout

    def test_try_again_body_is_desync(self) -> None:
        r = _run_lib('_merge_failure_is_desync 200 "Please try again later"; echo "RC=$?"')
        assert "RC=0" in r.stdout, r.stdout

    def test_proc_receive_body_is_desync(self) -> None:
        r = _run_lib('_merge_failure_is_desync 200 "fail to run proc-receive hook"; echo "RC=$?"')
        assert "RC=0" in r.stdout, r.stdout

    def test_normal_200_is_not_desync(self) -> None:
        r = _run_lib('_merge_failure_is_desync 200 "ok"; echo "RC=$?"')
        assert "RC=1" in r.stdout, r.stdout

    def test_422_validation_is_not_desync(self) -> None:
        r = _run_lib('_merge_failure_is_desync 422 "validation failed"; echo "RC=$?"')
        assert "RC=1" in r.stdout, r.stdout


# ----------------------------------------------------------------------
# _attempt_desync_resync_push (seam only — real push needs a live remote)
# ----------------------------------------------------------------------
class TestAttemptResyncPush:
    def test_seam_ok(self) -> None:
        r = _run_lib('AUTOPR_TEST_RESYNC_PUSH=ok _attempt_desync_resync_push; echo "RC=$?"')
        assert "RC=0" in r.stdout, r.stdout

    def test_seam_fail(self) -> None:
        r = _run_lib('AUTOPR_TEST_RESYNC_PUSH=fail _attempt_desync_resync_push; echo "RC=$?"')
        assert "RC=1" in r.stdout, r.stdout


# ----------------------------------------------------------------------
# do_merge resync-retry integration (mocked api_post / _check_pr_merged)
# ----------------------------------------------------------------------
class TestDoMergeResyncRetry:
    _COUNTER_MOCK = (
        'CNT=$(mktemp); echo 0 > "$CNT"\n'
        'api_post() {\n'
        '  local n; n=$(cat "$CNT"); n=$((n+1)); echo "$n" > "$CNT"\n'
        '  if [[ $n -lt 4 ]]; then API_HTTP_CODE=405; '
        'API_RESPONSE='"'"'{"message":"Please try again later"}'"'"'; return 1\n'
        '  else API_HTTP_CODE=200; API_RESPONSE='"'"'{"merged":true}'"'"'; return 0; fi\n'
        '}\n'
        '_check_pr_merged() { local n; n=$(cat "$CNT"); [[ $n -ge 4 ]]; }\n'
        '_pr_landed_in_base() { return 1; }\n'  # default: not landed (overridable)
        'sleep() { :; }\n'
        'API_BASE=http://test; BASE_BRANCH=dev\n'
    )

    def test_resync_recovers_merge_on_desync(self) -> None:
        """405 thrice, then a resync push + retry succeeds → do_merge returns 0."""
        r = _run_lib(
            self._COUNTER_MOCK
            + '_attempt_desync_resync_push() { echo RESYNC_CALLED; return 0; }\n'
            + 'do_merge 123 title desc sha; echo "RC=$?"'
        )
        assert "RESYNC_CALLED" in r.stdout, r.stdout
        assert "Merged after DB resync" in r.stdout, r.stdout
        assert "RC=0" in r.stdout, r.stdout

    def test_resync_recovers_via_git_ground_truth_when_pr_flag_lags(self) -> None:
        """The "reports-failure-but-succeeds" case: the merge endpoint keeps
        405ing and the PR `merged` flag never flips, but the ref FF-landed —
        git ground truth (_pr_landed_in_base) must recognize the success."""
        r = _run_lib(
            'api_post() { API_HTTP_CODE=405; '
            'API_RESPONSE='"'"'{"message":"Please try again later"}'"'"'; return 1; }\n'
            '_check_pr_merged() { return 1; }\n'      # PR record stays unmerged (lagging)
            '_attempt_desync_resync_push() { echo RESYNC_CALLED; return 0; }\n'
            '_pr_landed_in_base() { return 0; }\n'    # git: SHA is in origin/dev
            'sleep() { :; }\n'
            'API_BASE=http://test; BASE_BRANCH=dev\n'
            'do_merge 123 title desc sha; echo "RC=$?"'
        )
        assert "RESYNC_CALLED" in r.stdout, r.stdout
        assert "Merged after DB resync" in r.stdout, r.stdout
        assert "RC=0" in r.stdout, r.stdout

    def test_resync_succeeds_but_not_landed_gives_up(self) -> None:
        """Resync push succeeds but neither the PR record nor git confirms the
        merge → do_merge must still return 1 (no false success)."""
        r = _run_lib(
            'api_post() { API_HTTP_CODE=405; '
            'API_RESPONSE='"'"'{"message":"Please try again later"}'"'"'; return 1; }\n'
            '_check_pr_merged() { return 1; }\n'
            '_attempt_desync_resync_push() { echo RESYNC_CALLED; return 0; }\n'
            '_pr_landed_in_base() { return 1; }\n'    # git: NOT in base
            'sleep() { :; }\n'
            'API_BASE=http://test; BASE_BRANCH=dev\n'
            'do_merge 123 title desc sha; echo "RC=$?"'
        )
        assert "RESYNC_CALLED" in r.stdout, r.stdout
        assert "Merge failed after" in r.stdout, r.stdout
        assert "RC=1" in r.stdout, r.stdout

    def test_resync_push_failure_gives_up(self) -> None:
        """If the resync push fails, do_merge still returns 1 (no false success)."""
        r = _run_lib(
            'api_post() { API_HTTP_CODE=405; '
            'API_RESPONSE='"'"'{"message":"Please try again later"}'"'"'; return 1; }\n'
            '_check_pr_merged() { return 1; }\n'
            'sleep() { :; }\n'
            'API_BASE=http://test; BASE_BRANCH=dev\n'
            '_attempt_desync_resync_push() { echo RESYNC_CALLED; return 1; }\n'
            'do_merge 123 title desc sha; echo "RC=$?"'
        )
        assert "RESYNC_CALLED" in r.stdout, r.stdout
        assert "Merge failed after" in r.stdout, r.stdout
        assert "RC=1" in r.stdout, r.stdout

    def test_non_desync_failure_does_not_resync(self) -> None:
        """A 422 validation error must NOT trigger the desync resync-retry."""
        r = _run_lib(
            'api_post() { API_HTTP_CODE=422; '
            'API_RESPONSE='"'"'{"message":"validation failed"}'"'"'; return 1; }\n'
            '_check_pr_merged() { return 1; }\n'
            'sleep() { :; }\n'
            'API_BASE=http://test; BASE_BRANCH=dev\n'
            '_attempt_desync_resync_push() { echo RESYNC_CALLED; return 0; }\n'
            'do_merge 123 title desc sha; echo "RC=$?"'
        )
        assert "RESYNC_CALLED" not in r.stdout, r.stdout
        assert "RC=1" in r.stdout, r.stdout


# ----------------------------------------------------------------------
# auto-pr full-flow fallback (subprocess, fake repo)
# ----------------------------------------------------------------------
def _init_fake_repo(tmp_path: Path, branch: str = "feature") -> Path:
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(fake_root), check=True, capture_output=True)

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("commit", "--allow-empty", "-m", "desync-fallback-marker")
    run("remote", "add", "origin", "https://codeberg.org/test/repo.git")
    if branch != "dev":
        run("checkout", "-q", "-b", branch)
    return fake_root


def _run_autopr(fake_root: Path, *args: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for k in ("FORGEJO_USER", "FORGEJO_TOKEN", "AUTO_PR_SIMULATE_OUTAGE"):
        env.pop(k, None)
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(AUTO_PR), *args],
        cwd=str(fake_root),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _desync_env() -> dict[str, str]:
    # FORGEJO_API_BASE -> a closed local port so the pre-push dedup find_open_pr
    # fails instantly (connection refused) instead of hitting the live forge.
    return {
        "FORGEJO_USER": "u",
        "FORGEJO_TOKEN": "t",
        "AUTO_PR_SKIP_MANIFEST": "1",
        "AUTO_PR_SIMULATE_DESYNC": "1",
        "FORGEJO_API_BASE": "http://127.0.0.1:1",
        "API_TIMEOUT": "2",
    }


class TestAutoPrDesyncFallbackFlow:
    def test_fallback_creates_pr_via_api_no_vpr(self, tmp_path: Path) -> None:
        """AGit push 'fails' but the branch-push + API create_pr fallback wins."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        env = _desync_env()
        env["AUTO_PR_SIMULATE_DESYNC_FALLBACK"] = "ok"
        env["AUTOPR_TEST_CREATE_PR"] = "7777"
        r = _run_autopr(fake, "--title", "fix: x", "--description", "y", extra_env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Created PR #7777 via Forgejo API" in r.stdout, r.stdout
        assert "fallback identified PR #7777" in r.stdout, r.stdout
        # The fallback path must NOT queue a vPR.
        assert not (fake / ".git" / "PR_QUEUE").exists(), "fallback should not queue a vPR"

    def test_fallback_branch_push_failure_queues_vpr(self, tmp_path: Path) -> None:
        """A genuine outage (branch push also fails) still falls back to a vPR."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        env = _desync_env()
        env["AUTO_PR_SIMULATE_DESYNC_FALLBACK"] = "fail"
        r = _run_autopr(fake, "--title", "fix: x", "--description", "y", extra_env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (fake / ".git" / "PR_QUEUE").exists(), "branch-push failure should queue a vPR"


# ----------------------------------------------------------------------
# Structural invariants (guard against accidental revert)
# ----------------------------------------------------------------------
class TestStructuralInvariants:
    def test_forgejo_lib_has_create_pr_and_resync_helpers(self) -> None:
        text = FORGEJO_LIB.read_text()
        assert "create_pr() {" in text
        assert "_merge_failure_is_desync() {" in text
        assert "_attempt_desync_resync_push() {" in text
        # do_merge must call the resync-retry on a desync failure, and verify
        # via git ground truth (_pr_landed_in_base) not only the lagging PR flag.
        assert "_merge_failure_is_desync" in text
        assert "Merged after DB resync" in text
        assert "_pr_landed_in_base" in text

    def test_auto_pr_wires_branch_push_fallback(self) -> None:
        text = AUTO_PR.read_text()
        assert "_autopr_branch_push_fallback() {" in text
        # The fallback must run in the push-failure path, before queue_vpr.
        assert "_autopr_branch_push_fallback \"$push_remote\"" in text
        assert "AUTOPR_FALLBACK_USED=true" in text
        # The identification block must skip when the fallback set PR_NUM.
        assert 'if [[ "$AUTOPR_FALLBACK_USED" != "true" ]]; then' in text
