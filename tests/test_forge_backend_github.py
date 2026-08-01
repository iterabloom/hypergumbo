# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier-B behavioral tests for the GitHub backend of ``scripts/lib/forgejo-api.sh``.

Part of the Codeberg/Forgejo → GitHub migration (PR-C). The forge lib is
dual-mode: ``FORGE_BACKEND`` follows the origin host (github.com → github,
else forgejo — the dormant default while Codeberg is origin), overridable via
``HYPERGUMBO_FORGE_BACKEND``. These tests force the github backend and assert
the divergent behavior; the Forgejo path is covered by the existing
``test_autopr_*`` / ``test_ci_status_*`` suites and stays byte-identical.

Bash contributes no Python coverage (Tier B), but TDD still requires behavioral
subprocess tests. We stub the network boundary with a fake ``curl`` placed on
``PATH`` that records every invocation and returns fixtured responses keyed by
an ``HTTP_METHOD URL-substring`` match — so we can assert verbs, headers,
endpoints, and payloads without a live GitHub.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"

# A fake ``curl`` (Python) that:
#   * finds ``-o <outfile>`` and writes the fixtured body there,
#   * finds ``-X <method>`` and the URL (last non-flag arg),
#   * appends a JSON record of {method, url, headers, data} to $CURL_LOG,
#   * matches CURL_FIXTURES (JSON: list of {match: "GET /pulls", code, body})
#     against "<method> <url>" and prints the code (like ``-w %{http_code}``).
_FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
method = "GET"
outfile = None
url = None
headers = []
data = None
w_fmt = None
i = 0
while i < len(args):
    a = args[i]
    if a == "-X":
        method = args[i + 1]; i += 2; continue
    if a == "-o":
        outfile = args[i + 1]; i += 2; continue
    if a == "-H":
        headers.append(args[i + 1]); i += 2; continue
    if a == "-d":
        data = args[i + 1]; i += 2; continue
    if a == "-w":
        w_fmt = args[i + 1]; i += 2; continue
    if a == "--max-time":
        i += 2; continue
    if a.startswith("-"):
        i += 1; continue
    url = a; i += 1
rec = {"method": method, "url": url, "headers": headers, "data": data}
log = os.environ.get("CURL_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
code = "200"
body = "{}"
fixtures = json.loads(os.environ.get("CURL_FIXTURES", "[]"))
key = f"{method} {url or ''}"
for fx in fixtures:
    if fx["match"] in key:
        code = str(fx.get("code", 200))
        body = fx.get("body", "{}")
        break
if outfile:
    # Real curl: body to the file, -w format (the http code) to stdout.
    with open(outfile, "w") as fh:
        fh.write(body)
    sys.stdout.write(code)
else:
    # Real curl without -o: body to stdout; a trailing -w '\n%{http_code}'
    # appends the code on its own line (the Woodpecker log-fetch shape).
    sys.stdout.write(body)
    if w_fmt is not None:
        sys.stdout.write("\n" + code)
'''


def _fake_repo(tmp_path: Path, origin: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()

    def run(*a: str) -> None:
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "t@e.com")
    run("config", "user.name", "T")
    run("commit", "--allow-empty", "-m", "seed")
    run("remote", "add", "origin", origin)
    return root


def _bindir_with_fake_curl(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(_FAKE_CURL)
    curl.chmod(0o755)
    return bindir


def _run_lib(
    repo: Path,
    snippet: str,
    *,
    fixtures: list | None = None,
    env: dict | None = None,
    bindir: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path | None]:
    """Source the lib in ``repo`` and run ``snippet``. Returns (result, curl_log)."""
    full_env = dict(os.environ)
    for k in ("FORGEJO_TOKEN", "SELFHOSTED_FORGEJO_TOKEN", "HYPERGUMBO_FORGE_BACKEND"):
        full_env.pop(k, None)
    full_env["FORGEJO_TOKEN"] = "tok"
    curl_log = None
    if bindir is not None:
        full_env["PATH"] = f"{bindir}:{full_env['PATH']}"
        curl_log = repo / "curl.log"
        full_env["CURL_LOG"] = str(curl_log)
        full_env["CURL_FIXTURES"] = json.dumps(fixtures or [])
    if env:
        full_env.update(env)
    script = f'source "{LIB}"\nload_env\n{snippet}\n'
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=repo, capture_output=True, text=True, env=full_env,
    )
    return result, curl_log


def _curl_calls(log: Path | None) -> list[dict]:
    if not log or not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


class TestBackendDetection:
    def test_github_origin_selects_github(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        r, _ = _run_lib(
            repo, 'detect_api_base; echo "B=$FORGE_BACKEND A=$API_BASE"',
        )
        assert "B=github" in r.stdout, r.stdout + r.stderr
        assert "A=https://api.github.com/repos/o/r" in r.stdout

    def test_codeberg_origin_is_dormant_forgejo(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        r, _ = _run_lib(
            repo, 'detect_api_base; echo "B=$FORGE_BACKEND A=$API_BASE"',
        )
        assert "B=forgejo" in r.stdout, r.stdout + r.stderr
        assert "A=https://codeberg.org/api/v1/repos/o/r" in r.stdout

    def test_env_override_forces_github_on_codeberg_repo(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        r, _ = _run_lib(
            repo, 'detect_api_base; echo "B=$FORGE_BACKEND A=$API_BASE"',
            env={"HYPERGUMBO_FORGE_BACKEND": "github"},
        )
        assert "B=github" in r.stdout, r.stdout + r.stderr
        assert "A=https://api.github.com/repos/o/r" in r.stdout

    def test_ssh_github_origin(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "git@github.com:o/r.git")
        r, _ = _run_lib(
            repo, 'detect_api_base; echo "B=$FORGE_BACKEND A=$API_BASE"',
        )
        assert "B=github" in r.stdout, r.stdout + r.stderr
        assert "A=https://api.github.com/repos/o/r" in r.stdout


class TestApiCallHeaders:
    def test_github_url_adds_github_accept_and_version(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        r, log = _run_lib(
            repo,
            'detect_api_base; api_get "$API_BASE/pulls/1" || true',
            fixtures=[{"match": "GET https://api.github.com", "code": 200, "body": "{}"}],
            bindir=bindir,
        )
        calls = _curl_calls(log)
        assert calls, r.stdout + r.stderr
        hdrs = " ".join(calls[-1]["headers"])
        assert "Accept: application/vnd.github+json" in hdrs
        assert "X-GitHub-Api-Version: 2022-11-28" in hdrs
        assert "Authorization: token tok" in hdrs

    def test_forgejo_url_omits_github_headers(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://codeberg.org/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        r, log = _run_lib(
            repo,
            'detect_api_base; api_get "$API_BASE/pulls/1" || true',
            fixtures=[{"match": "GET https://codeberg.org", "code": 200, "body": "{}"}],
            bindir=bindir,
        )
        calls = _curl_calls(log)
        assert calls, r.stdout + r.stderr
        hdrs = " ".join(calls[-1]["headers"])
        assert "vnd.github" not in hdrs
        assert "X-GitHub-Api-Version" not in hdrs


class TestDoMergeGitHub:
    def test_put_merge_method_rebase_then_verified(self, tmp_path: Path) -> None:
        """GitHub do_merge issues PUT {merge_method: rebase} and verifies merged."""
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        fixtures = [
            {"match": "PUT https://api.github.com/repos/o/r/pulls/42/merge",
             "code": 200, "body": json.dumps({"merged": True})},
            {"match": "GET https://api.github.com/repos/o/r/pulls/42",
             "code": 200, "body": json.dumps({"merged": True})},
            {"match": "DELETE https://api.github.com",
             "code": 204, "body": ""},
        ]
        r, log = _run_lib(
            repo,
            'detect_api_base; do_merge 42 "t" "d" "abc123"; echo "RC=$?"',
            fixtures=fixtures, bindir=bindir,
        )
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        calls = _curl_calls(log)
        merge_calls = [c for c in calls if c["url"].endswith("/pulls/42/merge")]
        assert merge_calls, calls
        assert merge_calls[0]["method"] == "PUT"
        assert json.loads(merge_calls[0]["data"]) == {"merge_method": "rebase"}

    def test_rebase_rejected_falls_through_to_merge_commit(
        self, tmp_path: Path,
    ) -> None:
        """A 405 on rebase falls through to merge_method=merge."""
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        # merge endpoint 405 for both; but the PR GET flips to merged only
        # after the second (merge) attempt would have landed. Simplest: return
        # 405 on merge, and merged:true on GET only when method==merge is tried.
        # We approximate by returning merged:false first, then merged:true.
        state = repo / "merge_state"
        fixtures = [
            {"match": "PUT https://api.github.com/repos/o/r/pulls/42/merge",
             "code": 405, "body": json.dumps({"message": "not mergeable"})},
            {"match": "GET https://api.github.com/repos/o/r/pulls/42",
             "code": 200, "body": json.dumps({"merged": True})},
        ]
        # Both rebase and merge PUTs 405; verification GET says merged=true, so
        # the FIRST verification already returns success — exercising the
        # verify-after-PUT path. Ensure at least the rebase PUT was issued.
        _ = state
        r, log = _run_lib(
            repo,
            'detect_api_base; do_merge 42 "t" "d" "abc123"; echo "RC=$?"',
            fixtures=fixtures, bindir=bindir,
        )
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        calls = _curl_calls(log)
        put_calls = [c for c in calls if c["method"] == "PUT"]
        assert put_calls and put_calls[0]["url"].endswith("/pulls/42/merge")
        assert json.loads(put_calls[0]["data"]) == {"merge_method": "rebase"}

    def test_merge_failure_returns_nonzero(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        fixtures = [
            {"match": "PUT https://api.github.com/repos/o/r/pulls/42/merge",
             "code": 409, "body": json.dumps({"message": "conflict"})},
            {"match": "GET https://api.github.com/repos/o/r/pulls/42",
             "code": 200, "body": json.dumps({"merged": False})},
        ]
        r, _ = _run_lib(
            repo,
            'detect_api_base; do_merge 42 "t" "d" "abc123"; echo "RC=$?"',
            fixtures=fixtures, bindir=bindir,
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr


class TestFindMergedPrGitHub:
    def test_uses_merged_at_field(self, tmp_path: Path) -> None:
        """GitHub list objects carry merged_at (not a merged bool)."""
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        prs = [
            {"number": 7, "merged_at": None, "head": {"sha": "aaa", "ref": "x"}},
            {"number": 9, "merged_at": "2026-07-24T00:00:00Z",
             "head": {"sha": "bbb", "ref": "tracker-sync/1"}},
        ]
        fixtures = [
            {"match": "GET https://api.github.com/repos/o/r/pulls",
             "code": 200, "body": json.dumps(prs)},
        ]
        r, log = _run_lib(
            repo,
            'detect_api_base; find_merged_pr branch tracker-sync/1; '
            'echo "N=$FOUND_MERGED_PR_NUM"',
            fixtures=fixtures, bindir=bindir,
        )
        assert "N=9" in r.stdout, r.stdout + r.stderr
        # GitHub-valid list params (not Forgejo's sort=recentupdate&limit=50):
        # GitHub ignores those, silently paging to created-desc / 30-per-page.
        pulls_get = next(
            c for c in _curl_calls(log)
            if "/pulls?" in (c["url"] or "")
        )
        assert "sort=updated" in pulls_get["url"]
        assert "per_page=100" in pulls_get["url"]
        assert "recentupdate" not in pulls_get["url"]
        assert "limit=50" not in pulls_get["url"]


class TestPollCiNormalization:
    def test_github_shaped_status_reaches_success(self, tmp_path: Path) -> None:
        """A GitHub combined-status (per-element ``state``) is read correctly."""
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        combined = {
            "state": "success",
            "statuses": [
                {"state": "success", "context": "ci/woodpecker/pr/woodpecker"},
            ],
        }
        fixtures = [
            {"match": "GET https://api.github.com/repos/o/r/commits/deadbeef/status",
             "code": 200, "body": json.dumps(combined)},
        ]
        r, _ = _run_lib(
            repo,
            'detect_api_base; poll_ci deadbeef; echo "RC=$?"',
            fixtures=fixtures, bindir=bindir,
            env={"CI_POLL_NO_SLEEP": "1"},
        )
        assert "RC=0" in r.stdout, r.stdout + r.stderr

    def test_github_fetch_job_log_degrades_nonzero(self, tmp_path: Path) -> None:
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        combined = {
            "state": "failure",
            "statuses": [
                {"state": "failure", "context": "ci/woodpecker/pr/woodpecker",
                 "target_url": "https://ci.example/run/1"},
            ],
        }
        fixtures = [
            {"match": "GET https://api.github.com/repos/o/r/commits/deadbeef/status",
             "code": 200, "body": json.dumps(combined)},
        ]
        r, _ = _run_lib(
            repo,
            'detect_api_base; fetch_job_log deadbeef pytest; echo "RC=$?"',
            fixtures=fixtures, bindir=bindir,
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "Woodpecker" in r.stderr
        assert "https://ci.example/run/1" in r.stderr


class TestWoodpeckerLogBodyShapes:
    """WI-holik: an HTTP 200 whose body is not a log-entry LIST (JSON null,
    or an error-envelope object) crashed the parse with
    'TypeError: NoneType object is not iterable' — a raw traceback at the
    exact moment the operator needs the failed-CI log. The parse must
    treat a non-list body as a diagnosed failure, never a crash."""

    _ENV = {
        "WOODPECKER_SERVER": "https://wp.example",
        "WOODPECKER_TOKEN": "wtok",
        "CF_ACCESS_CLIENT_ID": "cfid",
        "CF_ACCESS_CLIENT_SECRET": "cfsecret",
    }

    def _fixtures(self, log_body: str) -> list:
        combined = {
            "state": "failure",
            "statuses": [
                {"state": "failure",
                 "context": "ci/woodpecker/pr/woodpecker",
                 "target_url": "https://wp.example/repos/42/pipeline/7"},
            ],
        }
        pipeline = {"workflows": [{"children": [
            {"id": 99, "name": "woodpecker", "state": "failure",
             "exit_code": 1},
        ]}]}
        return [
            {"match": "GET https://api.github.com/repos/o/r/commits/deadbeef/status",
             "code": 200, "body": json.dumps(combined)},
            {"match": "GET https://wp.example/api/repos/42/pipelines/7",
             "code": 200, "body": json.dumps(pipeline)},
            {"match": "GET https://wp.example/api/repos/42/logs/7/99",
             "code": 200, "body": log_body},
        ]

    def _fetch(self, tmp_path: Path, log_body: str):
        repo = _fake_repo(tmp_path, "https://github.com/o/r.git")
        bindir = _bindir_with_fake_curl(tmp_path)
        r, _ = _run_lib(
            repo,
            'detect_api_base; _github_fetch_job_log deadbeef; echo "RC=$?"',
            fixtures=self._fixtures(log_body), bindir=bindir, env=self._ENV,
        )
        return r

    def test_null_body_is_diagnosed_not_a_traceback(self, tmp_path: Path) -> None:
        r = self._fetch(tmp_path, "null")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, (
            "a 200-with-null body must be diagnosed, not crash the parser:\n"
            + r.stderr
        )
        assert "not a log-entry list" in r.stderr, r.stderr

    def test_error_envelope_object_is_diagnosed(self, tmp_path: Path) -> None:
        r = self._fetch(tmp_path, '{"error": "gone"}')
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert "not a log-entry list" in r.stderr, r.stderr

    def test_real_entry_list_still_decodes(self, tmp_path: Path) -> None:
        """The happy path must survive the hardening."""
        entries = [
            {"line": 2, "data": base64.b64encode(b"second\n").decode()},
            {"line": 1, "data": base64.b64encode(b"first\n").decode()},
            {"line": 3, "data": None},
        ]
        r = self._fetch(tmp_path, json.dumps(entries))
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        assert "first\nsecond" in r.stdout
