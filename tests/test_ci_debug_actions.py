# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``--actions`` arm of ``scripts/ci-debug`` (WI-pubur).

The release workflow runs on GitHub Actions; everything else runs on
Woodpecker, which is all the default arm can read. ``--actions`` adds four
read-only views: ``runs``, ``status``, ``logs``, ``artifact``. These tests
drive the script through the fake-curl harness and pin:

* the REQUESTS -- which endpoint each view asks, with ``per_page``;
* the RENDERING -- jobs, steps, conclusions and durations;
* the JOB CHOICE for ``logs`` -- first failed by default, exact name, unique
  substring, and an ambiguous name that lists its candidates;
* the ALLOWLIST RULE -- logs and artifacts come back as a redirect, which is
  followed only when the target host is listed in ``ALLOWED_WEBSITES.md``;
  otherwise the host is named, the exit is 3, and no request reaches it;
* the ARTIFACT SAFETY -- extraction only into a new empty directory, and a
  zip member that escapes it is refused before anything is written.
"""

# covers: scripts/ci-debug, tests/_forge_github_harness.py, ALLOWED_WEBSITES.md
from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

from _forge_github_harness import bindir_with_fakes, calls, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}

_RUNS = json.dumps({"workflow_runs": [{
    "id": 39068691320, "run_number": 12, "status": "completed", "conclusion": "failure",
    "event": "workflow_dispatch", "head_branch": "dev", "head_sha": "5f6079d871abcdef",
    "created_at": "2026-10-17T20:54:29Z", "path": ".github/workflows/release.yml",
    "display_title": "Release",
}]})
_RUN = json.dumps({
    "id": 1, "run_number": 12, "name": "Release", "status": "completed", "conclusion": "failure",
    "event": "workflow_dispatch", "head_branch": "dev", "head_sha": "5f6079d871abcdef",
})
_JOBS = json.dumps({"jobs": [
    {"id": 10, "name": "security-audit", "status": "completed", "conclusion": "success",
     "started_at": "2026-10-17T20:54:30Z", "completed_at": "2026-10-17T20:55:39Z",
     "steps": [{"name": "Set up job", "conclusion": "success",
                "started_at": "2026-10-17T20:54:30Z", "completed_at": "2026-10-17T20:54:31Z"}]},
    {"id": 11, "name": "io-manifest", "status": "completed", "conclusion": "failure",
     "started_at": "2026-10-17T20:54:28Z", "completed_at": "2026-10-17T20:56:21Z",
     "steps": [{"name": "Install strace", "conclusion": "success",
                "started_at": "2026-10-17T20:54:41Z", "completed_at": "2026-10-17T20:54:50Z"},
               {"name": "Trace the corpus", "conclusion": "failure",
                "started_at": "2026-10-17T20:55:05Z", "completed_at": "2026-10-17T20:56:21Z"}]},
    {"id": 12, "name": "build-and-publish", "status": "completed", "conclusion": "skipped",
     "started_at": None, "completed_at": None, "steps": []},
]})
_ARTIFACTS = json.dumps({"artifacts": [
    {"id": 9, "name": "io-manifest", "size_in_bytes": 123, "expired": False},
    {"id": 8, "name": "old", "size_in_bytes": 1, "expired": True},
]})
_BLOB = "https://productionresultssa0.blob.core.windows.net/actions-results/x?sig=abc"
_LISTED = "https://api.github.com/not-really-but-listed/x"


def _zip_b64(members: dict[str, str]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return base64.b64encode(buf.getvalue()).decode()


_counter = iter(range(1, 10**6))


def _run(tmp_path, argv, fixtures, *, origin="https://github.com/o/r.git", env=_GH):
    # fake_repo builds <base>/repo and refuses an existing one; a test that
    # runs the script twice gets a fresh base each time.
    base = tmp_path / f"call{next(_counter)}"
    base.mkdir()
    repo = fake_repo(base, origin)
    bindir = bindir_with_fakes(base)
    r, logs = run_script("ci-debug", repo, argv, fixtures=fixtures, env=env, bindir=bindir)
    return r, [c["url"] or "" for c in calls(logs["curl"])]


# --- runs ---------------------------------------------------------------------


def test_runs_for_a_workflow_asks_that_workflows_runs(tmp_path):
    r, urls = _run(tmp_path, ("runs", "--actions", "release.yml"),
                   [{"match": "/actions/workflows/release.yml/runs", "code": 200, "body": _RUNS}])
    assert r.returncode == 0, r.stderr
    assert any(u.endswith("/actions/workflows/release.yml/runs?per_page=10") for u in urls), urls
    assert "run 39068691320" in r.stdout
    assert "failure" in r.stdout and "workflow_dispatch" in r.stdout and "release.yml" in r.stdout
    assert "@5f6079d8" in r.stdout


def test_runs_without_a_workflow_lists_all_runs_with_the_given_limit(tmp_path):
    r, urls = _run(tmp_path, ("--actions", "runs", "", "3"),
                   [{"match": "/actions/runs?", "code": 200, "body": _RUNS}])
    assert r.returncode == 0, r.stderr
    assert any(u.endswith("/actions/runs?per_page=3") for u in urls), urls


def test_runs_with_nothing_to_list_says_so(tmp_path):
    r, _ = _run(tmp_path, ("runs", "--actions"),
                [{"match": "/actions/runs?", "code": 200, "body": json.dumps({"workflow_runs": []})}])
    assert r.returncode == 0
    assert "No runs." in r.stdout


# --- status -------------------------------------------------------------------


def test_status_renders_the_run_its_jobs_steps_and_durations(tmp_path):
    r, urls = _run(tmp_path, ("status", "--actions", "1"), [
        {"match": "/actions/runs/1/jobs", "code": 200, "body": _JOBS},
        {"match": "/actions/runs/1", "code": 200, "body": _RUN},
    ])
    assert r.returncode == 0, r.stderr
    assert any(u.endswith("/actions/runs/1") for u in urls)
    assert any(u.endswith("/actions/runs/1/jobs?per_page=100") for u in urls)
    out = r.stdout
    assert "run 1 #12 Release: completed/failure" in out
    assert "✅ security-audit: success (69s)" in out
    assert "❌ io-manifest: failure (113s)" in out
    assert "FAIL  Trace the corpus  76s" in out
    assert "build-and-publish: skipped" in out


def test_status_without_a_run_id_is_a_usage_error(tmp_path):
    r, urls = _run(tmp_path, ("status", "--actions"), [])
    assert r.returncode == 1
    assert "status --actions <run-id>" in r.stderr
    assert urls == []


# --- logs: job choice ---------------------------------------------------------


def _log_fixtures(location: str, extra: list | None = None):
    return [
        {"match": "/actions/runs/1/jobs", "code": 200, "body": _JOBS},
        {"match": "/actions/jobs/10/logs", "code": 302, "location": location},
        {"match": "/actions/jobs/11/logs", "code": 302, "location": location},
        *(extra or []),
    ]


def test_logs_picks_the_first_failed_job_by_default(tmp_path):
    r, urls = _run(tmp_path, ("logs", "--actions", "1"), _log_fixtures(_BLOB))
    assert any(u.endswith("/actions/jobs/11/logs") for u in urls), urls
    assert "job 'io-manifest'" in r.stderr


def test_logs_takes_an_exact_or_unique_substring_name(tmp_path):
    _, urls = _run(tmp_path, ("logs", "--actions", "1", "security-audit"), _log_fixtures(_BLOB))
    assert any(u.endswith("/actions/jobs/10/logs") for u in urls), urls
    _, urls = _run(tmp_path, ("logs", "--actions", "1", "MANIFEST"), _log_fixtures(_BLOB))
    assert any(u.endswith("/actions/jobs/11/logs") for u in urls), urls


def test_logs_with_an_ambiguous_name_lists_the_candidates_and_fetches_nothing(tmp_path):
    r, urls = _run(tmp_path, ("logs", "--actions", "1", "i"), _log_fixtures(_BLOB))
    assert r.returncode == 1
    assert "ambiguous" in r.stderr
    assert "security-audit" in r.stderr and "io-manifest" in r.stderr and "build-and-publish" in r.stderr
    assert not any("/logs" in u for u in urls)


def test_logs_with_an_unknown_name_names_the_jobs(tmp_path):
    r, _ = _run(tmp_path, ("logs", "--actions", "1", "nope"), _log_fixtures(_BLOB))
    assert r.returncode == 1
    assert "no job matches 'nope'" in r.stderr


# --- logs and artifacts: the allowlist rule -----------------------------------


def test_logs_refuses_a_redirect_to_an_unlisted_host_and_never_requests_it(tmp_path):
    r, urls = _run(tmp_path, ("logs", "--actions", "1"), _log_fixtures(_BLOB))
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert "productionresultssa0.blob.core.windows.net" in r.stderr
    assert "ALLOWED_WEBSITES.md" in r.stderr
    assert not any("blob.core.windows.net" in u for u in urls), urls
    assert r.stdout == ""


def test_logs_follows_a_redirect_to_a_listed_host_and_prints_the_log(tmp_path):
    r, urls = _run(tmp_path, ("logs", "--actions", "1"), _log_fixtures(
        _LISTED, [{"match": f"GET {_LISTED}", "code": 200, "body": "line one\nline two\n"}]))
    assert r.returncode == 0, r.stderr
    assert r.stdout == "line one\nline two\n"
    assert _LISTED in urls


def test_the_allowlist_match_covers_a_parent_domain_but_not_a_lookalike(tmp_path):
    repo = fake_repo(tmp_path, "https://github.com/o/r.git")
    script = Path(__file__).resolve().parent.parent / "scripts" / "ci-debug"
    probe = (
        f'source <(sed -n "/^_actions_host_allowlisted()/,/^}}/p" "{script}"); '
        f'SCRIPT_DIR="{script.parent}"; '
        'for h in api.github.com sub.raw.githubusercontent.com notgithub.com evilgithub.com; do '
        'if _actions_host_allowlisted "$h"; then echo "$h yes"; else echo "$h no"; fi; done'
    )
    import subprocess
    out = subprocess.run(["bash", "-c", probe], cwd=repo, capture_output=True, text=True).stdout
    assert "api.github.com yes" in out
    assert "sub.raw.githubusercontent.com yes" in out
    assert "notgithub.com no" in out
    assert "evilgithub.com no" in out


# --- artifact -----------------------------------------------------------------


def test_artifact_downloads_follows_a_listed_redirect_and_extracts_into_a_new_dir(tmp_path):
    dest = tmp_path / "out" / "iom"
    r, urls = _run(tmp_path, ("artifact", "--actions", "1", "io-manifest", str(dest)), [
        {"match": "/actions/runs/1/artifacts", "code": 200, "body": _ARTIFACTS},
        {"match": "/actions/artifacts/9/zip", "code": 302, "location": _LISTED},
        {"match": f"GET {_LISTED}", "code": 200,
         "body_b64": _zip_b64({"observed.txt": "write\t<cache>/hypergumbo/...\n", "trace/a.trace": "x"})},
    ])
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (dest / "observed.txt").read_text() == "write\t<cache>/hypergumbo/...\n"
    assert (dest / "trace" / "a.trace").read_text() == "x"
    assert "Extracted 2 file(s)" in r.stdout
    assert any(u.endswith("/actions/artifacts/9/zip") for u in urls)


def test_artifact_refuses_an_unlisted_redirect_with_exit_3(tmp_path):
    dest = tmp_path / "iom"
    r, urls = _run(tmp_path, ("artifact", "--actions", "1", "io-manifest", str(dest)), [
        {"match": "/actions/runs/1/artifacts", "code": 200, "body": _ARTIFACTS},
        {"match": "/actions/artifacts/9/zip", "code": 302, "location": _BLOB},
    ])
    assert r.returncode == 3
    assert "blob.core.windows.net" in r.stderr
    assert not any("blob.core.windows.net" in u for u in urls)
    assert not dest.exists() or not any(dest.iterdir())


def test_artifact_refuses_a_zip_member_that_escapes_the_destination(tmp_path):
    dest = tmp_path / "iom"
    r, _ = _run(tmp_path, ("artifact", "--actions", "1", "io-manifest", str(dest)), [
        {"match": "/actions/runs/1/artifacts", "code": 200, "body": _ARTIFACTS},
        {"match": "/actions/artifacts/9/zip", "code": 302, "location": _LISTED},
        {"match": f"GET {_LISTED}", "code": 200,
         "body_b64": _zip_b64({"ok.txt": "fine", "../evil.txt": "no"})},
    ])
    assert r.returncode == 1
    assert "escapes the destination" in r.stderr and "evil.txt" in r.stderr
    assert not (tmp_path / "evil.txt").exists()
    assert not (dest / "ok.txt").exists(), "nothing is extracted when any member is bad"


def test_artifact_refuses_a_non_empty_destination_before_downloading(tmp_path):
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "keep").write_text("x")
    r, urls = _run(tmp_path, ("artifact", "--actions", "1", "io-manifest", str(dest)), [])
    assert r.returncode == 1
    assert "not an empty directory" in r.stderr
    assert urls == []


def test_artifact_names_the_available_artifacts_on_a_miss_and_refuses_an_expired_one(tmp_path):
    r, _ = _run(tmp_path, ("artifact", "--actions", "1", "nope", str(tmp_path / "d1")),
                [{"match": "/actions/runs/1/artifacts", "code": 200, "body": _ARTIFACTS}])
    assert r.returncode == 1 and "available: io-manifest, old" in r.stderr
    r, _ = _run(tmp_path, ("artifact", "--actions", "1", "old", str(tmp_path / "d2")),
                [{"match": "/actions/runs/1/artifacts", "code": 200, "body": _ARTIFACTS}])
    assert r.returncode == 1 and "expired" in r.stderr


def test_artifact_without_the_flag_points_at_actions(tmp_path):
    r, urls = _run(tmp_path, ("artifact", "1", "io-manifest", str(tmp_path / "d")), [])
    assert r.returncode == 1
    assert "artifact --actions" in r.stderr
    assert urls == []


# --- backend guard and the default arm ----------------------------------------


def test_actions_requires_the_github_backend(tmp_path):
    r, urls = _run(tmp_path, ("runs", "--actions"), [], origin="https://codeberg.org/o/r.git", env={})
    assert r.returncode == 1
    assert "--actions reads GitHub Actions" in r.stderr
    assert urls == []


def test_without_the_flag_the_default_arm_is_untouched(tmp_path):
    status = json.dumps({"state": "success", "statuses": [
        {"state": "success", "context": "ci/woodpecker/push/woodpecker", "target_url": ""}]})
    r, urls = _run(tmp_path, ("runs",), [{"match": "/commits/", "code": 200, "body": status}])
    assert r.returncode == 0, r.stderr
    assert "ci/woodpecker/push/woodpecker" in r.stdout
    assert not any("/actions/" in u for u in urls)
